"""Task 7.4 — market quality under load: spread, depth, and obligation compliance.

This is the measurement Open Issue 012 §4 proposed adding to the list `README.md` names, on the
grounds that **watching the spread widen and market-maker obligations start to breach as load
rises says more than a throughput number does**, and that it is a domain-specific result a
generic web-service benchmark cannot produce.

## What load means here, and what it deliberately does not

Load is resting orders at one tick — the same probe shape as `bench_e2e.py`. They never trade
and never move a price. That is the point: if the load moved the market, the spread would widen
because *this harness* pushed it, and the measurement would be of the benchmark rather than of
the exchange. Everything observed here is the market makers' own quoting, under a system that is
progressively busier.

So the question is narrow and answerable: as throughput rises, do the designated market makers
keep their obligations? A market maker that cannot get its quotes in fast enough shows up three
ways — a wider spread, thinner resting size, and moments with only one side live.

## Where the compliance numbers come from

`services/bots/obligations.py` already samples all three obligations once per quote tick and
emits a structured `obligation_breach` line per breach. Its own docstring anticipates this run:
*"a benchmark run greps for them."* So nothing is instrumented here. The run reads the bots
container's logs afterwards with timestamps, and buckets each breach into the rate step it
happened in — structured logs plus offline analysis, exactly as Open Issue 012 settled, with no
Prometheus and no Grafana anywhere.

    docker compose up -d
    QA_BENCH_PASSWORD=... python benchmarks/bench_market_quality.py --rates 0,100,200,300
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import httpx
import websockets

GATEWAY = os.environ.get("QA_GATEWAY_URL", "http://localhost:8000")
FANOUT_WS = os.environ.get("QA_FANOUT_WS", "ws://localhost:8001/stream")
PASSWORD = os.environ.get("QA_BENCH_PASSWORD", "benchmark-password-1")

BPS = 10_000
NS_PER_MS = 1_000_000


def percentile(ordered: list[float], q: float) -> float:
    rank = min(int(q * len(ordered)), len(ordered) - 1)
    return ordered[rank]


def summarise(samples: list[float]) -> dict:
    if not samples:
        return {"count": 0}
    ordered = sorted(samples)
    return {
        "count": len(ordered),
        "p50": round(percentile(ordered, 0.50), 3),
        "p95": round(percentile(ordered, 0.95), 3),
        "p99": round(percentile(ordered, 0.99), 3),
        "max": round(ordered[-1], 3),
    }


@dataclass
class Quality:
    """What the book looked like, sampled from complete L2 snapshots.

    Complete snapshots are what make this possible at all: every frame carries the whole top of
    book, so a spread is read straight off it with no state to maintain and no chance of a
    missed delta leaving a stale level behind (Open Issue 006).
    """

    spread_bps: list[float] = field(default_factory=list)
    thin_side_qty: list[float] = field(default_factory=list)
    one_sided: int = 0
    snapshots: int = 0

    #: Levels at or below this price are the harness's own probe orders and are removed before
    #: anything is measured. They rest at one tick and never trade, so they accumulate: every
    #: run of this script and of `bench_e2e.py` leaves more of them in the book. On a symbol the
    #: market makers happen not to be quoting, a probe bid at one tick becomes the best bid and
    #: the spread reads in the thousands of basis points — the first run of this measured a p95
    #: of 19,907 bps and was measuring its own litter. Excluding them is not tidying a number
    #: away; it is the difference between measuring the market and measuring the benchmark.
    probe_price: int = 1

    def observe(self, frame: dict, depth: int) -> None:
        bids = [lvl for lvl in (frame.get("bids") or []) if lvl[0] > self.probe_price]
        asks = [lvl for lvl in (frame.get("asks") or []) if lvl[0] > self.probe_price]
        self.snapshots += 1
        if not bids or not asks:
            # A symbol quoting only one side is the `not_two_sided` obligation, seen from the
            # feed rather than from the bot's own sampling. Counted, not scored as a spread.
            self.one_sided += 1
            return
        best_bid, best_ask = bids[0][0], asks[0][0]
        mid = (best_bid + best_ask) / 2
        if mid > 0:
            self.spread_bps.append((best_ask - best_bid) / mid * BPS)
        bid_qty = sum(level[1] for level in bids[:depth])
        ask_qty = sum(level[1] for level in asks[:depth])
        # The thinner side, because an obligation is owed on each side separately — averaging
        # the two would let a fat bid hide an empty offer.
        self.thin_side_qty.append(min(bid_qty, ask_qty))


class BookWatcher:
    def __init__(self, cookie: str, channels: list[str]) -> None:
        self.cookie = cookie
        self.channels = channels
        self.socket = None
        self.quality = Quality()
        self.recording = False
        self.depth = 5

    async def open(self) -> None:
        self.socket = await websockets.connect(
            FANOUT_WS, additional_headers={"Cookie": self.cookie}, open_timeout=30
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
            if not self.recording:
                continue
            try:
                frame = json.loads(raw)
            except (TypeError, ValueError):
                continue
            if str(frame.get("ch", "")).endswith(":l2"):
                self.quality.observe(frame, self.depth)

    async def close(self) -> None:
        if self.socket is not None:
            try:
                await self.socket.close()
            except Exception:
                pass


async def submit_at(client: httpx.AsyncClient, coid: int, intended_ns: int, symbol_id: int,
                    slips: list[float]) -> None:
    """One order at its pre-scheduled instant. Open loop, as in bench_e2e.py — a generator that
    waits for the previous response would quietly stop applying load the moment the system
    slowed, which is the one thing a load test must not do."""
    delay = (intended_ns - time.time_ns()) / 1e9
    if delay > 0:
        await asyncio.sleep(delay)
    slips.append((time.time_ns() - intended_ns) / NS_PER_MS)
    try:
        await client.post(
            "/orders",
            json={
                "client_order_id": coid,
                "symbol_id": symbol_id,
                "side": 1,
                "tif": 1,
                "price_ticks": 1,
                "qty": 1,
            },
        )
    except Exception:
        pass


def read_breaches(since: datetime) -> list[tuple[datetime, str]]:
    """Every obligation breach the bots logged, with its timestamp. Read after the fact."""
    stamp = since.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        output = subprocess.run(
            ["docker", "compose", "logs", "-t", "--since", stamp, "bots"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=120,
        ).stdout
    except Exception:
        return []
    found: list[tuple[datetime, str]] = []
    for line in output.splitlines():
        if "obligation_breach" not in line:
            continue
        head, _, body = line.partition("{")
        if not body:
            continue
        try:
            kind = json.loads("{" + body).get("kind", "unknown")
        except ValueError:
            continue
        when = None
        for token in head.split():
            try:
                when = datetime.fromisoformat(token.replace("Z", "+00:00"))
                break
            except ValueError:
                continue
        if when is not None:
            found.append((when, kind))
    return found


async def main() -> int:
    parser = argparse.ArgumentParser(description="Market quality as throughput rises.")
    parser.add_argument("--rates", default="0,100,200,300", help="0 means an unloaded baseline")
    parser.add_argument("--seconds", type=float, default=20.0)
    parser.add_argument("--accounts", type=int, default=6)
    parser.add_argument("--depth", type=int, default=5, help="levels counted as depth")
    parser.add_argument(
        "--probe-price",
        type=int,
        default=1,
        help="book levels at or below this price are the harness's own and are excluded",
    )
    parser.add_argument("--json", default="")
    args = parser.parse_args()

    rates = [int(v) for v in args.rates.split(",") if v.strip()]
    run_started = datetime.now(timezone.utc)

    clients: list[httpx.AsyncClient] = []
    for index in range(args.accounts):
        client = httpx.AsyncClient(base_url=GATEWAY, timeout=30.0)
        name = f"bench_quality_{index}"
        await client.post("/auth/register", json={"username": name, "password": PASSWORD})
        (await client.post("/auth/login", json={"username": name, "password": PASSWORD})).raise_for_status()
        clients.append(client)

    listed = (await clients[0].get("/symbols")).json()["symbols"]
    symbol_ids = [s["symbol_id"] for s in listed]
    cookie = "; ".join(f"{k}={v}" for k, v in clients[0].cookies.items())
    channels = [f"book:{s['name']}:l2" for s in listed]

    watcher = BookWatcher(cookie, channels)
    watcher.quality = Quality(probe_price=args.probe_price)
    watcher.depth = args.depth
    await watcher.open()
    stop = asyncio.Event()
    reader = asyncio.create_task(watcher.read(stop))

    print(
        f"market quality · {len(channels)} symbols · depth {args.depth} levels · "
        f"{args.seconds:g}s per step · rates {rates}"
    )

    steps = []
    coid_base = time.time_ns() // 1000
    for rate in rates:
        watcher.quality = Quality(probe_price=args.probe_price)
        watcher.recording = True
        began = datetime.now(timezone.utc)
        slips: list[float] = []

        if rate > 0:
            total = int(rate * args.seconds)
            start_ns = time.time_ns() + 200_000_000
            interval_ns = int(1e9 / rate)
            tasks = [
                asyncio.create_task(
                    submit_at(
                        clients[i % len(clients)],
                        coid_base + i,
                        start_ns + i * interval_ns,
                        symbol_ids[i % len(symbol_ids)],
                        slips,
                    )
                )
                for i in range(total)
            ]
            coid_base += total + 1000
            await asyncio.gather(*tasks, return_exceptions=True)
        else:
            await asyncio.sleep(args.seconds)

        ended = datetime.now(timezone.utc)
        watcher.recording = False
        quality = watcher.quality
        ordered_slip = sorted(slips)
        steps.append(
            {
                "rate_per_second": rate,
                "began": began.isoformat(),
                "ended": ended.isoformat(),
                "snapshots": quality.snapshots,
                "one_sided_snapshots": quality.one_sided,
                "spread_bps": summarise(quality.spread_bps),
                "thin_side_depth": summarise(quality.thin_side_qty),
                "send_slip_p99_ms": (
                    round(percentile(ordered_slip, 0.99), 3) if ordered_slip else 0.0
                ),
            }
        )
        print(f"  rate {rate}/s done — {quality.snapshots} snapshots", flush=True)

    stop.set()
    await asyncio.gather(reader, return_exceptions=True)
    await watcher.close()
    for client in clients:
        await client.aclose()

    breaches = read_breaches(run_started)
    for step in steps:
        began = datetime.fromisoformat(step["began"])
        ended = datetime.fromisoformat(step["ended"])
        counts: dict[str, int] = {}
        for when, kind in breaches:
            if began <= when <= ended:
                counts[kind] = counts.get(kind, 0) + 1
        step["obligation_breaches"] = counts

    header = (
        f"\n{'rate':>6} {'snapshots':>10} {'1-sided':>8} {'spread p50':>11} {'spread p95':>11} "
        f"{'depth p50':>10} {'breaches':>9} {'slip p99':>9}"
    )
    print(header)
    print("-" * (len(header) - 1))
    for step in steps:
        total_breaches = sum(step["obligation_breaches"].values())
        print(
            f"{step['rate_per_second']:>6} {step['snapshots']:>10} {step['one_sided_snapshots']:>8} "
            f"{step['spread_bps'].get('p50', 0):>10.1f}b {step['spread_bps'].get('p95', 0):>10.1f}b "
            f"{step['thin_side_depth'].get('p50', 0):>10.0f} {total_breaches:>9} "
            f"{step['send_slip_p99_ms']:>9.2f}"
        )
    kinds = sorted({k for step in steps for k in step["obligation_breaches"]})
    if kinds:
        print("\nbreaches by kind:")
        for kind in kinds:
            row = " ".join(
                f"{step['rate_per_second']}/s={step['obligation_breaches'].get(kind, 0)}"
                for step in steps
            )
            print(f"  {kind:<18} {row}")
    print(
        "\nspread = (best ask − best bid) / mid, in basis points, from complete L2 snapshots,\n"
        "         excluding this harness's own resting probe orders at "
        f"{args.probe_price} tick\n"
        "depth  = resting quantity on the THINNER side over the top levels — an obligation is\n"
        "         owed per side, and averaging would let a fat bid hide an empty offer\n"
        "breaches are the bots' own obligation_breach log lines, bucketed by timestamp"
    )

    if args.json:
        Path(args.json).write_text(
            json.dumps(
                {
                    "benchmark": "market-quality",
                    "recorded_unix": int(time.time()),
                    "symbols": len(channels),
                    "depth_levels": args.depth,
                    "steps": steps,
                },
                indent=2,
            )
        )
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
