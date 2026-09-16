"""Task 7.4 — B2, the end-to-end benchmark. HTTP in, WebSocket out, open loop.

B2 is the number that reflects what a user experiences, and it is reported *separately* from B1
(the native engine) and B3 (fan-out connection scaling). Open Issue 012 §3 is explicit that
conflating them is how credibility is lost, so this script measures one thing and never prints
another benchmark's number.

## Why open loop, and what that actually means here

The standard load generator sends a request, waits for the response, and sends the next. If the
system stalls for 200 ms the generator stalls with it, so the requests that *would have arrived*
during the stall are never issued and their latency is never recorded. The stall is erased from
the measurement and p99 looks excellent. That is **coordinated omission**, and Open Issue 012 §2
names avoiding it as the single decision that decides whether these numbers mean anything.

So: every order's send time is computed **before the run starts**, as `t0 + i / rate`. One task
per order waits until its own intended instant and fires regardless of what any other order is
doing. Latency is measured from the **intended** send time, not from the actual one. If the
gateway slows down, nothing here slows down with it — the in-flight count grows and the
measurement shows it, which is the entire point.

`send_slip` is reported alongside every result for the same reason: it is the gap between when an
order was *meant* to be sent and when the harness actually got it onto the wire. If slip is
large, the harness itself has become the bottleneck and the latency figures describe Python's
event loop rather than the exchange. A benchmark that cannot rule that out is not evidence.

## The three latencies, which are three different questions

Every frame the exchange sends back already carries the timestamp needed to time its own path,
so none of this needs an added tracing layer — the event stream is the trace (Open Issue 012 §5).

- **`http_ack`** — intended send to the gateway's `202`. Validation, risk reservation, the Lua
  claim, and the `XADD` that makes the order durable. The acknowledgement carries `seq`, the
  Redis stream id, which *is* the sequence number (Open Issue 003).
- **`private_e2e`** — intended send to the `OrderAccepted` frame landing on the user's private
  WebSocket, measured against the frame's `ts_ns` (the gateway's own stamp on the record). The
  private stream is **never conflated** — private data is never dropped (Open Issue 006) — so
  this is the fast path, and it is what a trader's own blotter feels.
The public L2 feed is **not** measured here. It was, briefly, as receipt minus the `seq` on the
arriving frame — and that was wrong in the flattering direction, because `conflation.py` stamps
a snapshot with `state.last_seq`, the newest record folded into it, so the subtraction gave the
time since the *most recent* update rather than the wait endured by any particular one. It read
5–9 ms against a 50 ms window. Measuring it honestly needs each order's own outbound stream id,
which is `benchmarks/bench_conflation.py`.

Wall-clock `time.time_ns()` is used rather than `perf_counter` wherever a local time is compared
against a Redis stream id or a gateway stamp, because those come from other processes' clocks.
Every process here is on one host, so that comparison is sound; across hosts it would not be, and
the report has to say so.

## Load is spread across accounts on purpose

`max_orders_per_second` is 1,000 per user. A single-account ramp would hit the rate limiter and
then measure the rate limiter, which is a real component but not the one B2 is about. Orders are
therefore dealt round-robin across `--accounts` accounts, and any `429` is counted and reported
rather than retried — a retry would smuggle coordinated omission back in through the side door.

Orders rest far below the market (one tick, one lot) so they never trade. That keeps the fill
path, and the bots' reaction to it, out of a measurement that is about the request path; it also
means a long run leaves a deep book of resting bids, which is stated in the results rather than
cleaned up silently.

    docker compose up -d
    QA_BENCH_PASSWORD=... python benchmarks/bench_e2e.py --rates 100,200,400,800 --seconds 20

Nothing here is a unit test. Results go in `benchmarks/results/7.4-benchmark-report.md` with the
machine, the topology, and the software versions they were taken on.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import httpx
import websockets

GATEWAY = os.environ.get("QA_GATEWAY_URL", "http://localhost:8000")
FANOUT_WS = os.environ.get("QA_FANOUT_WS", "ws://localhost:8001/stream")
PASSWORD = os.environ.get("QA_BENCH_PASSWORD", "benchmark-password-1")

NS_PER_MS = 1_000_000


# -------------------------------------------------------------------------------------------
# Distributions. Never a mean (Open Issue 012 §2) — percentiles by nearest rank on the sorted
# samples, so every figure printed is a latency that actually happened to some order.
# -------------------------------------------------------------------------------------------
@dataclass
class Distribution:
    count: int = 0
    p50: float = 0.0
    p95: float = 0.0
    p99: float = 0.0
    p99_9: float = 0.0
    max: float = 0.0
    min: float = 0.0

    @classmethod
    def of(cls, samples: list[float]) -> "Distribution":
        if not samples:
            return cls()
        ordered = sorted(samples)
        return cls(
            count=len(ordered),
            p50=_percentile(ordered, 0.50),
            p95=_percentile(ordered, 0.95),
            p99=_percentile(ordered, 0.99),
            p99_9=_percentile(ordered, 0.999),
            max=ordered[-1],
            min=ordered[0],
        )

    def as_dict(self) -> dict:
        return {
            "count": self.count,
            "min_ms": round(self.min, 3),
            "p50_ms": round(self.p50, 3),
            "p95_ms": round(self.p95, 3),
            "p99_ms": round(self.p99, 3),
            "p99_9_ms": round(self.p99_9, 3),
            "max_ms": round(self.max, 3),
        }


def _percentile(ordered: list[float], q: float) -> float:
    rank = min(int(q * len(ordered)), len(ordered) - 1)
    return ordered[rank]


# -------------------------------------------------------------------------------------------
# One account: a cookie, an HTTP client, and a WebSocket that listens to its own private feed.
# -------------------------------------------------------------------------------------------
@dataclass
class Sample:
    """What one order's journey looked like. Times are wall-clock nanoseconds."""

    client_order_id: int
    intended_ns: int
    sent_ns: int = 0
    acked_ns: int = 0
    seq: str = ""
    status: int = 0
    private_ns: int = 0


class Account:
    def __init__(self, index: int, username: str) -> None:
        self.index = index
        self.username = username
        self.cookie = ""
        self.client: httpx.AsyncClient | None = None
        self.socket = None
        #: client_order_id -> receipt time, filled by the private-feed reader.
        self.receipts: dict[int, int] = {}
        self.private_frames = 0

    async def sign_in(self) -> None:
        """Register if new, then always log in.

        The unconditional login is not defensive coding, it is a bug that has already been paid
        for once: `POST /auth/register` does not set a session cookie, so a harness that only
        logs in on a 409 sends every order unauthenticated on its first run, gets a wall of
        401s, and reports a perfectly healthy exchange doing nothing at all
        (`benchmarks/results/5.2-fanout-conflation.md`).
        """
        limits = httpx.Limits(max_connections=512, max_keepalive_connections=512)
        self.client = httpx.AsyncClient(base_url=GATEWAY, timeout=30.0, limits=limits)
        response = await self.client.post(
            "/auth/register", json={"username": self.username, "password": PASSWORD}
        )
        if response.status_code not in (200, 201, 409):
            response.raise_for_status()
        response = await self.client.post(
            "/auth/login", json={"username": self.username, "password": PASSWORD}
        )
        response.raise_for_status()
        jar = "; ".join(f"{name}={value}" for name, value in self.client.cookies.items())
        self.cookie = jar

    async def open_private(self) -> None:
        """Attach the private feed. No `subscribe` — private is routed by user, not asked for."""
        self.socket = await websockets.connect(
            FANOUT_WS, additional_headers={"Cookie": self.cookie}, open_timeout=20
        )

    async def read_private(self, stop: asyncio.Event) -> None:
        """Record when each acknowledgement actually reached this client.

        Keyed by `client_order_id`, which every `OrderAccepted` and `OrderRejected` private
        message carries. A frame for an order this run did not send is ignored rather than
        counted: the account may have resting orders from an earlier run.
        """
        assert self.socket is not None
        while not stop.is_set():
            try:
                raw = await asyncio.wait_for(self.socket.recv(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            except Exception:
                return
            received = time.time_ns()
            try:
                frame = json.loads(raw)
            except (TypeError, ValueError):
                continue
            if frame.get("ch") != "private":
                continue
            self.private_frames += 1
            coid = frame.get("client_order_id")
            if isinstance(coid, int) and coid not in self.receipts:
                self.receipts[coid] = received

    async def close(self) -> None:
        if self.socket is not None:
            await self.socket.close()
        if self.client is not None:
            await self.client.aclose()


class BookObserver:
    """One client on the public L2 feed, counting frames so a dead feed is visible.

    It deliberately does **not** time the public path. A frame's `seq` is the newest record
    folded into that snapshot, so timing against it answers a question nobody asked. The count
    is still worth having: a run where the book feed silently stopped would otherwise look like
    a run where the book feed was fast. `benchmarks/bench_conflation.py` does the timing.
    """

    def __init__(self, cookie: str, channels: list[str]) -> None:
        self.cookie = cookie
        self.channels = channels
        self.socket = None
        self.frames = 0

    async def open(self) -> None:
        self.socket = await websockets.connect(
            FANOUT_WS, additional_headers={"Cookie": self.cookie}, open_timeout=20
        )
        await self.socket.send(json.dumps({"op": "subscribe", "channels": self.channels}))

    async def read(self, stop: asyncio.Event) -> None:
        assert self.socket is not None
        while not stop.is_set():
            try:
                raw = await asyncio.wait_for(self.socket.recv(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            except Exception:
                return
            try:
                frame = json.loads(raw)
            except (TypeError, ValueError):
                continue
            channel = frame.get("ch", "")
            if not channel.startswith("book:"):
                continue
            self.frames += 1

    async def close(self) -> None:
        if self.socket is not None:
            await self.socket.close()


# -------------------------------------------------------------------------------------------
# The open-loop run.
# -------------------------------------------------------------------------------------------
@dataclass
class RateResult:
    rate: int
    seconds: float
    warmup_seconds: float
    attempted: int = 0
    accepted: int = 0
    rate_limited: int = 0
    other_status: dict[str, int] = field(default_factory=dict)
    errors: int = 0
    receipts_matched: int = 0
    http_ack: Distribution = field(default_factory=Distribution)
    private_e2e: Distribution = field(default_factory=Distribution)
    send_slip: Distribution = field(default_factory=Distribution)
    append_hop: Distribution = field(default_factory=Distribution)
    private_tail: Distribution = field(default_factory=Distribution)
    book_frames: int = 0
    achieved_rate: float = 0.0

    def as_dict(self) -> dict:
        return {
            "rate_per_second": self.rate,
            "achieved_rate_per_second": round(self.achieved_rate, 1),
            "seconds": self.seconds,
            "warmup_seconds_discarded": self.warmup_seconds,
            "attempted": self.attempted,
            "accepted": self.accepted,
            "rate_limited_429": self.rate_limited,
            "other_status": self.other_status,
            "transport_errors": self.errors,
            "private_receipts_matched": self.receipts_matched,
            "book_frames": self.book_frames,
            "http_ack": self.http_ack.as_dict(),
            "private_e2e": self.private_e2e.as_dict(),
            "send_slip": self.send_slip.as_dict(),
            "append_hop": self.append_hop.as_dict(),
            "private_tail": self.private_tail.as_dict(),
        }


async def submit(account: Account, sample: Sample, symbol_id: int) -> None:
    """Fire one order at its intended instant. Never waits for any other order."""
    delay = (sample.intended_ns - time.time_ns()) / 1e9
    if delay > 0:
        await asyncio.sleep(delay)
    payload = {
        "client_order_id": sample.client_order_id,
        "symbol_id": symbol_id,
        "side": 1,
        "tif": 1,
        "price_ticks": 1,
        "qty": 1,
    }
    sample.sent_ns = time.time_ns()
    try:
        assert account.client is not None
        response = await account.client.post("/orders", json=payload)
        sample.acked_ns = time.time_ns()
        sample.status = response.status_code
        if response.status_code == 202:
            sample.seq = response.json().get("seq", "")
    except Exception:
        sample.acked_ns = time.time_ns()
        sample.status = -1


async def run_rate(
    accounts: list[Account],
    *,
    rate: int,
    seconds: float,
    warmup_seconds: float,
    symbol_id: int,
    coid_base: int,
    observer: BookObserver | None,
) -> RateResult:
    total = int(rate * (seconds + warmup_seconds))
    start_ns = time.time_ns() + 200_000_000  # 200 ms of headroom to get every task scheduled
    interval_ns = int(1e9 / rate)

    samples: list[Sample] = []
    tasks: list[asyncio.Task] = []
    if observer is not None:
        observer.frames = 0
    for account in accounts:
        account.receipts.clear()

    for i in range(total):
        sample = Sample(client_order_id=coid_base + i, intended_ns=start_ns + i * interval_ns)
        samples.append(sample)
        account = accounts[i % len(accounts)]
        tasks.append(asyncio.create_task(submit(account, sample, symbol_id)))

    await asyncio.gather(*tasks, return_exceptions=True)

    # The private feed is behind the acknowledgements by definition — the record has to reach
    # the engine, come back on the outbound stream and be routed. Give it a moment to land
    # before matching receipts, or the fast path would be scored as if it had never arrived.
    await asyncio.sleep(1.5)

    receipts: dict[int, int] = {}
    for account in accounts:
        receipts.update(account.receipts)

    steady_after_ns = start_ns + int(warmup_seconds * 1e9)
    result = RateResult(rate=rate, seconds=seconds, warmup_seconds=warmup_seconds)
    http_ack: list[float] = []
    private_e2e: list[float] = []
    send_slip: list[float] = []
    append_hop: list[float] = []
    private_tail: list[float] = []

    for sample in samples:
        if sample.intended_ns < steady_after_ns:
            continue
        result.attempted += 1
        if sample.status == 202:
            result.accepted += 1
        elif sample.status == 429:
            result.rate_limited += 1
        elif sample.status == -1:
            result.errors += 1
        else:
            key = str(sample.status)
            result.other_status[key] = result.other_status.get(key, 0) + 1

        if sample.acked_ns:
            http_ack.append((sample.acked_ns - sample.intended_ns) / NS_PER_MS)
        if sample.sent_ns:
            send_slip.append((sample.sent_ns - sample.intended_ns) / NS_PER_MS)
        appended_ns = 0
        if sample.seq and "-" in sample.seq and sample.sent_ns:
            appended_ns = int(sample.seq.split("-", 1)[0]) * NS_PER_MS
            append_hop.append((appended_ns - sample.sent_ns) / NS_PER_MS)
        received = receipts.get(sample.client_order_id)
        if received:
            result.receipts_matched += 1
            private_e2e.append((received - sample.intended_ns) / NS_PER_MS)
            if appended_ns:
                # Anchored on the inbound append, which is the same side of durability that
                # `book_delay` is anchored on. Comparing the two is then a fair question:
                # everything after the log, without conflation (private) and with it (book).
                private_tail.append((received - appended_ns) / NS_PER_MS)

    measured_span = (samples[-1].intended_ns - steady_after_ns) / 1e9 if samples else 0
    result.achieved_rate = result.accepted / measured_span if measured_span > 0 else 0.0
    result.http_ack = Distribution.of(http_ack)
    result.private_e2e = Distribution.of(private_e2e)
    result.send_slip = Distribution.of(send_slip)
    result.append_hop = Distribution.of(append_hop)
    result.private_tail = Distribution.of(private_tail)
    if observer is not None:
        result.book_frames = observer.frames
    return result


def print_results(results: list[RateResult]) -> None:
    print("\nB2 — end-to-end, milliseconds, measured from intended send time (open loop)\n")
    header = (
        f"{'rate':>6} {'ok':>7} {'429':>6} {'err':>5} "
        f"{'ack p50':>9} {'ack p99':>9} {'ack max':>9} "
        f"{'priv p50':>9} {'priv p99':>9} {'ptail p50':>10} {'slip p99':>9}"
    )
    print(header)
    print("-" * len(header))
    for r in results:
        print(
            f"{r.rate:>6} {r.accepted:>7} {r.rate_limited:>6} {r.errors:>5} "
            f"{r.http_ack.p50:>9.2f} {r.http_ack.p99:>9.2f} {r.http_ack.max:>9.2f} "
            f"{r.private_e2e.p50:>9.2f} {r.private_e2e.p99:>9.2f} {r.private_tail.p50:>10.2f} "
            f"{r.send_slip.p99:>9.2f}"
        )
    print(
        "\nack   = intended send to the gateway's 202\n"
        "priv  = intended send to OrderAccepted on the private feed — the user-facing number\n"
        "ptail = inbound append to that same frame: everything after durability, unconflated\n"
        "slip  = the harness's own lateness onto the wire\n"
        "the conflated public feed is measured by benchmarks/bench_conflation.py, which anchors"
        " on each order's\nown outbound id — the only way to get that number right"
    )
    worst_slip = max((r.send_slip.p99 for r in results), default=0.0)
    if worst_slip > 5.0:
        print(
            f"\n!! send_slip p99 reached {worst_slip:.1f} ms — the harness was late onto the "
            "wire at some rate.\n   Latency above that rate describes this event loop, not the "
            "exchange. Re-run with fewer rates\n   per invocation, or from more than one host, "
            "before quoting it."
        )


async def main() -> int:
    parser = argparse.ArgumentParser(description="B2 end-to-end benchmark, open loop.")
    parser.add_argument("--rates", default="50,100,200,400", help="orders/sec steps, comma separated")
    parser.add_argument("--seconds", type=float, default=20.0, help="measured seconds per rate")
    parser.add_argument("--warmup-seconds", type=float, default=5.0, help="discarded at each rate")
    parser.add_argument("--accounts", type=int, default=8, help="accounts to spread load across")
    parser.add_argument("--symbol", default="", help="symbol name (default: the first listed)")
    parser.add_argument("--json", default="", help="write the full result set to this path")
    args = parser.parse_args()

    rates = [int(value) for value in args.rates.split(",") if value.strip()]
    if not rates:
        print("no rates given", file=sys.stderr)
        return 2

    async with httpx.AsyncClient(base_url=GATEWAY, timeout=30.0) as probe:
        response = await probe.get("/symbols")
        response.raise_for_status()
        listed = response.json()["symbols"]
    if not listed:
        print("the gateway lists no symbols", file=sys.stderr)
        return 2
    chosen = next((s for s in listed if s["name"] == args.symbol), listed[0])
    symbol_id, symbol_name = chosen["symbol_id"], chosen["name"]

    accounts = [Account(i, f"bench_e2e_{i}") for i in range(args.accounts)]
    for account in accounts:
        await account.sign_in()
        await account.open_private()

    observer = BookObserver(accounts[0].cookie, [f"book:{symbol_name}:l2"])
    await observer.open()

    stop = asyncio.Event()
    readers = [asyncio.create_task(a.read_private(stop)) for a in accounts]
    readers.append(asyncio.create_task(observer.read(stop)))

    print(
        f"B2 · symbol {symbol_name} (id {symbol_id}) · {len(accounts)} accounts · "
        f"{args.seconds:g}s measured after {args.warmup_seconds:g}s warm-up · rates {rates}"
    )

    results: list[RateResult] = []
    coid_base = time.time_ns() // 1000
    try:
        for rate in rates:
            total = int(rate * (args.seconds + args.warmup_seconds))
            print(f"  rate {rate}/s — {total} orders scheduled ...", flush=True)
            result = await run_rate(
                accounts,
                rate=rate,
                seconds=args.seconds,
                warmup_seconds=args.warmup_seconds,
                symbol_id=symbol_id,
                coid_base=coid_base,
                observer=observer,
            )
            coid_base += total + 1000
            results.append(result)
    finally:
        stop.set()
        await asyncio.gather(*readers, return_exceptions=True)
        await observer.close()
        for account in accounts:
            await account.close()

    print_results(results)

    if args.json:
        payload = {
            "benchmark": "B2",
            "recorded_unix": int(time.time()),
            "gateway": GATEWAY,
            "fanout_ws": FANOUT_WS,
            "symbol": symbol_name,
            "accounts": len(accounts),
            "rates": [r.as_dict() for r in results],
        }
        Path(args.json).write_text(json.dumps(payload, indent=2))
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
