"""Task 7.4 — the public feed's conflation delay, anchored per order.

## The measurement this replaces, and why the old one was wrong

`bench_e2e.py` first reported a "book delay" as *frame arrival minus the `seq` on the arriving
L2 frame*. That number was wrong, and wrong in the flattering direction. `conflation.py` stamps
each snapshot with `state.last_seq` — the newest outbound record folded into it — so the
subtraction measures the time since the **most recent** update before the tick fired, not the
wait endured by any particular update. It read 5–9 ms against a 50 ms window that should produce
a ~25 ms median, which is the sort of too-good number that ought to start an investigation
rather than end one.

The honest question is per order: *this* record was appended to the outbound stream at a known
instant; when did the first snapshot containing it reach a browser?

So:

1. A WebSocket on `book:<SYM>:l2` records `(arrival, seq)` for every frame — the timeline.
2. Probe orders go in at a low rate. Each returns its **inbound** stream id in the ack.
3. Afterwards, one forward pass over the outbound stream maps each probe's `client_order_id`
   to the **outbound** stream id of its `OrderAccepted`.
4. For each probe, the first frame in the timeline whose `seq` is at or past that outbound id is
   the first snapshot that could have contained it. Delay is that frame's arrival minus the
   outbound append.

Resolving the outbound ids *after* the run rather than during it keeps the lookup off the path
being measured, and means the probe rate is not set by how fast Redis can be scanned.

The probes rest one tick above zero and never trade. A resting order still emits `BookChanged`,
which marks the symbol dirty, so it earns a frame on the next tick exactly as a touch-moving
order would — and it does so without disturbing a live market to take a measurement.

## What to expect, which is the point of measuring it

Conflation at 20 Hz is a 50 ms window. An update arriving at a uniformly random point within it
waits a uniform 0–50 ms: a median near 25 ms and a p99 near 49.5. A measured distribution that
matches is the design working as specified; one that does not means the tick is not firing when
it is supposed to.

This delay is a deliberate trade, not a defect — 20 Hz was chosen for demo feel in Open Issue
006, and complete snapshots are what make dropping a frame free. It is measured here so the
report can state its size rather than imply it is small.

    docker compose up -d
    QA_BENCH_PASSWORD=... python benchmarks/bench_conflation.py --probes 120 --rate 2
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import httpx
import websockets
from redis.asyncio import Redis

from config.settings import settings as default_settings
from contracts.v1.generated.contracts import OrderAccepted, unpack_any
from services.gateway.streams import RECORD_FIELD

GATEWAY = os.environ.get("QA_GATEWAY_URL", "http://localhost:8000")
FANOUT_WS = os.environ.get("QA_FANOUT_WS", "ws://localhost:8001/stream")
PASSWORD = os.environ.get("QA_BENCH_PASSWORD", "benchmark-password-1")

NS_PER_MS = 1_000_000


def id_key(stream_id: str) -> tuple[int, int]:
    """A stream id compares as (milliseconds, ordinal), never as a string or a float."""
    ms, _, ordinal = str(stream_id).partition("-")
    return int(ms), int(ordinal or 0)


def percentile(ordered: list[float], q: float) -> float:
    rank = min(int(q * len(ordered)), len(ordered) - 1)
    return ordered[rank]


async def measure_skew(redis: Redis, samples: int = 15) -> tuple[float, float]:
    """Offset between Redis's clock and this host's, in milliseconds.

    This measurement is the one place two clocks are subtracted from each other: the outbound
    append time comes from a stream id, which is Redis's clock inside the container, and the
    frame arrival comes from `time.time_ns()` on the host. Any offset between them lands
    directly in every delay below, so it is measured rather than assumed to be zero — a delay
    that comes out negative is otherwise unexplainable, and an unexplained negative is how a
    reader learns to distrust the rest of the table.

    `TIME` is timed from both sides and the reply assumed to have happened at the midpoint of
    the round trip. Samples with an unusually long round trip carry the most uncertainty about
    where that midpoint was, so the median is reported along with the spread.
    """
    offsets: list[float] = []
    for _ in range(samples):
        before = time.time_ns()
        seconds, micros = await redis.time()
        after = time.time_ns()
        redis_ns = int(seconds) * 1_000_000_000 + int(micros) * 1000
        offsets.append((redis_ns - (before + after) // 2) / NS_PER_MS)
        await asyncio.sleep(0.02)
    offsets.sort()
    return offsets[len(offsets) // 2], offsets[-1] - offsets[0]


@dataclass
class Probe:
    client_order_id: int
    inbound_id: str = ""
    outbound_id: str = ""
    delay_ms: float = 0.0


class Timeline:
    """Every L2 frame this client saw, with the moment it arrived."""

    def __init__(self, cookie: str, channel: str) -> None:
        self.cookie = cookie
        self.channel = channel
        self.socket = None
        self.frames: list[tuple[tuple[int, int], int]] = []

    async def open(self) -> None:
        self.socket = await websockets.connect(
            FANOUT_WS, additional_headers={"Cookie": self.cookie}, open_timeout=20
        )
        await self.socket.send(json.dumps({"op": "subscribe", "channels": [self.channel]}))

    async def read(self, stop: asyncio.Event) -> None:
        assert self.socket is not None
        while not stop.is_set():
            try:
                raw = await asyncio.wait_for(self.socket.recv(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            except Exception:
                return
            arrived = time.time_ns()
            try:
                frame = json.loads(raw)
            except (TypeError, ValueError):
                continue
            if frame.get("ch") != self.channel:
                continue
            seq = str(frame.get("seq") or "")
            if "-" not in seq:
                continue
            self.frames.append((id_key(seq), arrived))

    def first_at_or_after(self, stream_id: str) -> int | None:
        """The first frame whose contents could already have included this record."""
        target = id_key(stream_id)
        for seq, arrived in self.frames:
            if seq >= target:
                return arrived
        return None

    async def close(self) -> None:
        if self.socket is not None:
            await self.socket.close()


async def resolve_outbound(
    redis: Redis, stream: str, start_id: str, wanted: dict[int, Probe], budget: int
) -> int:
    """One forward pass mapping each probe's client_order_id to its outbound stream id."""
    cursor = start_id
    scanned = 0
    remaining = dict(wanted)
    while remaining and scanned < budget:
        entries = await redis.xrange(stream, min=f"({cursor}", max="+", count=5000)
        if not entries:
            break
        for raw_id, fields in entries:
            scanned += 1
            cursor = raw_id.decode() if isinstance(raw_id, bytes) else raw_id
            try:
                record = unpack_any(fields[RECORD_FIELD])
            except ValueError:
                continue
            if not isinstance(record, OrderAccepted):
                continue
            probe = remaining.pop(record.client_order_id, None)
            if probe is not None:
                probe.outbound_id = cursor
    return scanned


async def main() -> int:
    parser = argparse.ArgumentParser(description="Per-order conflation delay on the public feed.")
    parser.add_argument("--probes", type=int, default=120)
    parser.add_argument("--rate", type=float, default=2.0, help="probe orders per second")
    parser.add_argument("--symbol", default="")
    parser.add_argument(
        "--observer",
        choices=("separate", "same"),
        default="separate",
        help="whose socket watches the book: a second account that sends nothing (default), "
        "or the probe account's own connection",
    )
    parser.add_argument("--json", default="")
    args = parser.parse_args()

    settings = default_settings

    async with httpx.AsyncClient(base_url=GATEWAY, timeout=30.0) as client:
        await client.post(
            "/auth/register", json={"username": "bench_conflation", "password": PASSWORD}
        )
        response = await client.post(
            "/auth/login", json={"username": "bench_conflation", "password": PASSWORD}
        )
        response.raise_for_status()
        cookie = "; ".join(f"{k}={v}" for k, v in client.cookies.items())

        listed = (await client.get("/symbols")).json()["symbols"]
        chosen = next((s for s in listed if s["name"] == args.symbol), listed[0])
        symbol_id, symbol_name = chosen["symbol_id"], chosen["name"]

        # The observer is a *different* account by default, and that is a correctness
        # requirement rather than tidiness. Since private frames stopped waiting for the tick,
        # a connection receiving one is briefly `busy`, and a market frame offered to a busy
        # connection is skipped for that tick — the documented policy. Watching the book on the
        # same socket that is being acknowledged therefore measures the probe's own
        # acknowledgement pushing its own book frame into the next window. `--observer same`
        # measures that interaction deliberately; the default measures conflation alone.
        observer_cookie = cookie
        if args.observer == "separate":
            async with httpx.AsyncClient(base_url=GATEWAY, timeout=30.0) as watcher:
                await watcher.post(
                    "/auth/register",
                    json={"username": "bench_conflation_eye", "password": PASSWORD},
                )
                signed = await watcher.post(
                    "/auth/login",
                    json={"username": "bench_conflation_eye", "password": PASSWORD},
                )
                signed.raise_for_status()
                observer_cookie = "; ".join(
                    f"{k}={v}" for k, v in watcher.cookies.items()
                )

        timeline = Timeline(observer_cookie, f"book:{symbol_name}:l2")
        await timeline.open()
        stop = asyncio.Event()
        reader = asyncio.create_task(timeline.read(stop))

        redis = Redis.from_url(settings.redis_url, decode_responses=False)
        tip = await redis.xrevrange(settings.stream_outbound, count=1)
        start_id = (tip[0][0].decode() if tip else "0-0")
        skew_ms, skew_spread = await measure_skew(redis)

        print(
            f"conflation probe · {symbol_name} · {args.probes} orders at {args.rate:g}/s · "
            f"{settings.conflation_hz} Hz tick = {1000 / settings.conflation_hz:.0f} ms window"
            f" · observer: {args.observer} account"
        )
        print(
            f"redis-to-host clock offset: {skew_ms:+.3f} ms median, {skew_spread:.3f} ms spread "
            f"across samples — every delay below carries it"
        )

        probes: list[Probe] = []
        base = time.time_ns() // 1000
        interval = 1.0 / args.rate
        for index in range(args.probes):
            probe = Probe(client_order_id=base + index)
            payload = {
                "client_order_id": probe.client_order_id,
                "symbol_id": symbol_id,
                "side": 1,
                "tif": 1,
                "price_ticks": 1,
                "qty": 1,
            }
            response = await client.post("/orders", json=payload)
            if response.status_code == 202:
                probe.inbound_id = response.json()["seq"]
                probes.append(probe)
            await asyncio.sleep(interval)

        # Let the last probe's frame arrive before the timeline stops growing.
        await asyncio.sleep(1.0)
        stop.set()
        await asyncio.gather(reader, return_exceptions=True)
        await timeline.close()

    wanted = {p.client_order_id: p for p in probes}
    scanned = await resolve_outbound(
        redis, settings.stream_outbound, start_id, wanted, budget=2_000_000
    )
    await redis.aclose()

    delays: list[float] = []
    unresolved = 0
    no_frame = 0
    for probe in probes:
        if not probe.outbound_id:
            unresolved += 1
            continue
        arrived = timeline.first_at_or_after(probe.outbound_id)
        if arrived is None:
            no_frame += 1
            continue
        probe.delay_ms = (arrived - id_key(probe.outbound_id)[0] * NS_PER_MS) / NS_PER_MS
        delays.append(probe.delay_ms)

    if not delays:
        print("no probe produced a measurable delay — is the feed running?", file=sys.stderr)
        return 1

    ordered = sorted(delays)
    window_ms = 1000 / settings.conflation_hz
    print(
        f"\nprobes measured {len(ordered)} of {len(probes)} "
        f"(outbound id unresolved {unresolved}, no frame after it {no_frame})"
    )
    print(f"L2 frames observed: {len(timeline.frames)}, outbound records scanned: {scanned:,}")
    negatives = [d for d in ordered if d < 0]
    if negatives:
        print(
            f"\n!! {len(negatives)} of {len(ordered)} delays came out negative (min "
            f"{ordered[0]:.2f} ms). A frame cannot arrive before the record it contains was\n"
            f"   appended, so this is clock offset between the host and the container, not a "
            f"measurement.\n   It bounds the accuracy of this table at roughly "
            f"{abs(ordered[0]):.0f} ms."
        )
    print()
    print(f"  {'':<14}{'measured':>10}{'expected':>10}   (uniform 0–{window_ms:.0f} ms)")
    for label, q, expected in (
        ("min", 0.0, 0.0),
        ("p50", 0.50, window_ms * 0.50),
        ("p95", 0.95, window_ms * 0.95),
        ("p99", 0.99, window_ms * 0.99),
        ("max", 1.0, window_ms),
    ):
        value = ordered[0] if q == 0.0 else (ordered[-1] if q == 1.0 else percentile(ordered, q))
        print(f"  {label:<14}{value:>9.2f} ms{expected:>8.1f} ms")

    if args.json:
        Path(args.json).write_text(
            json.dumps(
                {
                    "benchmark": "conflation",
                    "recorded_unix": int(time.time()),
                    "symbol": symbol_name,
                    "conflation_hz": settings.conflation_hz,
                    "window_ms": window_ms,
                    "probes_submitted": len(probes),
                    "probes_measured": len(ordered),
                    "observer": args.observer,
                    "l2_frames_observed": len(timeline.frames),
                    "redis_host_clock_offset_ms": round(skew_ms, 3),
                    "redis_host_clock_spread_ms": round(skew_spread, 3),
                    "delays_ms": ordered,
                },
                indent=2,
            )
        )
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
