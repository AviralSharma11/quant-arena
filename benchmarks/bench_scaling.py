"""Task 7.4 — connection scaling: how many clients before update delay degrades.

Open Issue 012 §4 defines *real-time update delay* as the time from the engine's output to its
arrival at a client, and connection scaling as raising the client count until that delay
degrades. This sweeps the client count and measures exactly that.

## What is timed, and why it is not the same as §4.1's conflation number

Every frame carries `ts_ns`, stamped by the conflation tick that built it. So

    arrival (host clock) − ts_ns (fan-out's clock)

is the time from *the tick deciding to send* to *this client having it*: encode, the per-client
write, and the socket. It deliberately excludes the conflation wait, which
`bench_conflation.py` measures and which is a property of the 20 Hz choice rather than of the
client count. Mixing the two would hide the thing being looked for here — a fan-out that cannot
keep up is a *write* path that stretches, and a constant 0–50 ms of conflation on top of it
would mask the onset.

Because the two clocks belong to different containers, the host-to-container offset is measured
and printed with every run, exactly as in `bench_conflation.py`.

## The second measurement, which is the one Open Issue 006 actually promised

Fan-out is a separate process so that connection load **cannot reach the order path** (§7b). So
each step also samples order acknowledgement latency from a single trading account while the
sockets are attached. If that number moves with the client count, the separation is not real,
and no amount of fan-out throughput would make up for it.

`max_tick_seconds` from `/health` is read at each step too: a tick that takes longer than the
50 ms interval means the feed is late for everybody, which is the degradation this is hunting
for, observed from inside rather than inferred from the outside.

    docker compose up -d
    QA_BENCH_PASSWORD=... python benchmarks/bench_scaling.py --clients 50,100,200,400
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import httpx
import websockets
from redis.asyncio import Redis

from config.settings import settings as default_settings

GATEWAY = os.environ.get("QA_GATEWAY_URL", "http://localhost:8000")
FANOUT_WS = os.environ.get("QA_FANOUT_WS", "ws://localhost:8001/stream")
FANOUT_HTTP = os.environ.get("QA_FANOUT_URL", "http://localhost:8001")
PASSWORD = os.environ.get("QA_BENCH_PASSWORD", "benchmark-password-1")

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
        "min_ms": round(ordered[0], 3),
        "p50_ms": round(percentile(ordered, 0.50), 3),
        "p95_ms": round(percentile(ordered, 0.95), 3),
        "p99_ms": round(percentile(ordered, 0.99), 3),
        "max_ms": round(ordered[-1], 3),
    }


async def measure_skew(redis: Redis, samples: int = 11) -> float:
    """Redis-to-host clock offset in ms. Frames are stamped inside a container, read outside."""
    offsets: list[float] = []
    for _ in range(samples):
        before = time.time_ns()
        seconds, micros = await redis.time()
        after = time.time_ns()
        redis_ns = int(seconds) * 1_000_000_000 + int(micros) * 1000
        offsets.append((redis_ns - (before + after) // 2) / NS_PER_MS)
        await asyncio.sleep(0.02)
    offsets.sort()
    return offsets[len(offsets) // 2]


class Watcher:
    """One browser. Records how late each frame was, and nothing else."""

    def __init__(self, cookie: str, channels: list[str]) -> None:
        self.cookie = cookie
        self.channels = channels
        self.socket = None
        self.delays_ms: list[float] = []
        self.frames = 0

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
            arrived = time.time_ns()
            try:
                frame = json.loads(raw)
            except (TypeError, ValueError):
                continue
            if not str(frame.get("ch", "")).startswith("book:"):
                continue
            stamped = frame.get("ts_ns")
            if not isinstance(stamped, int):
                continue
            self.frames += 1
            self.delays_ms.append((arrived - stamped) / NS_PER_MS)

    async def close(self) -> None:
        if self.socket is not None:
            try:
                await self.socket.close()
            except Exception:
                pass


async def sample_ack_latency(client: httpx.AsyncClient, *, count: int, symbol_id: int) -> list[float]:
    """One order at a time. The question is what a single acknowledgement costs while the
    sockets are attached, not what the stream can absorb in a batch."""
    samples: list[float] = []
    base = time.time_ns() // 1000
    for index in range(count):
        payload = {
            "client_order_id": base + index,
            "symbol_id": symbol_id,
            "side": 1,
            "tif": 1,
            "price_ticks": 1,
            "qty": 1,
        }
        started = time.perf_counter()
        response = await client.post("/orders", json=payload)
        samples.append((time.perf_counter() - started) * 1000)
        if response.status_code >= 500:
            raise RuntimeError(f"gateway returned {response.status_code}: {response.text}")
    return samples


async def fanout_health() -> dict:
    async with httpx.AsyncClient(base_url=FANOUT_HTTP, timeout=10.0) as client:
        try:
            return (await client.get("/health")).json()
        except Exception:
            return {}


async def run_step(
    cookie: str, count: int, channels: list[str], trader: httpx.AsyncClient, symbol_id: int,
    seconds: float, acks: int,
) -> dict:
    watchers = [Watcher(cookie, channels) for _ in range(count)]
    opened = 0
    refused = 0
    for watcher in watchers:
        try:
            await watcher.open()
            opened += 1
        except Exception:
            refused += 1

    stop = asyncio.Event()
    readers = [asyncio.create_task(w.read(stop)) for w in watchers if w.socket is not None]

    before_health = await fanout_health()
    await asyncio.sleep(1.0)  # let every socket settle before anything is timed
    ack = await sample_ack_latency(trader, count=acks, symbol_id=symbol_id)
    await asyncio.sleep(seconds)
    after_health = await fanout_health()

    stop.set()
    await asyncio.gather(*readers, return_exceptions=True)
    for watcher in watchers:
        await watcher.close()

    delays = [d for w in watchers for d in w.delays_ms]
    frames = [w.frames for w in watchers if w.socket is not None]
    ticks = after_health.get("ticks", 0) - before_health.get("ticks", 0)
    return {
        "clients_requested": count,
        "clients_opened": opened,
        "clients_refused": refused,
        "update_delay": summarise(delays),
        "ack_latency": summarise(ack),
        "frames_min": min(frames) if frames else 0,
        "frames_max": max(frames) if frames else 0,
        "ticks_during_step": ticks,
        "max_tick_seconds": after_health.get("max_tick_seconds"),
        "serialisations_per_tick": (
            round(
                (after_health.get("serialisations", 0) - before_health.get("serialisations", 0))
                / ticks,
                3,
            )
            if ticks
            else None
        ),
    }


async def main() -> int:
    parser = argparse.ArgumentParser(description="Connection scaling and real-time update delay.")
    parser.add_argument("--clients", default="50,100,200,400")
    parser.add_argument("--seconds", type=float, default=10.0, help="measured seconds per step")
    parser.add_argument("--acks", type=int, default=100, help="acknowledgements sampled per step")
    parser.add_argument("--symbols", type=int, default=3, help="symbols each client subscribes to")
    parser.add_argument("--json", default="")
    args = parser.parse_args()

    counts = [int(v) for v in args.clients.split(",") if v.strip()]
    settings = default_settings

    async with httpx.AsyncClient(base_url=GATEWAY, timeout=60.0) as client:
        await client.post("/auth/register", json={"username": "bench_scale", "password": PASSWORD})
        (await client.post("/auth/login", json={"username": "bench_scale", "password": PASSWORD})).raise_for_status()
        cookie = "; ".join(f"{k}={v}" for k, v in client.cookies.items())
        listed = (await client.get("/symbols")).json()["symbols"]
        symbol_id = listed[0]["symbol_id"]
        channels = [f"book:{s['name']}:l2" for s in listed[: args.symbols]]

        redis = Redis.from_url(settings.redis_url, decode_responses=False)
        skew = await measure_skew(redis)
        await redis.aclose()

        print(
            f"connection scaling · {len(channels)} channels each · {args.seconds:g}s per step · "
            f"redis-to-host offset {skew:+.3f} ms"
        )

        results = []
        for count in counts:
            print(f"  opening {count} clients ...", flush=True)
            results.append(
                await run_step(
                    cookie, count, channels, client, symbol_id, args.seconds, args.acks
                )
            )

    header = (
        f"\n{'clients':>8} {'open':>6} {'refused':>8} {'delay p50':>10} {'delay p95':>10} "
        f"{'delay p99':>10} {'delay max':>10} {'ack p50':>9} {'ack p99':>9} {'enc/tick':>9}"
    )
    print(header)
    print("-" * (len(header) - 1))
    for r in results:
        d, a = r["update_delay"], r["ack_latency"]
        print(
            f"{r['clients_requested']:>8} {r['clients_opened']:>6} {r['clients_refused']:>8} "
            f"{d.get('p50_ms', 0):>10.2f} {d.get('p95_ms', 0):>10.2f} {d.get('p99_ms', 0):>10.2f} "
            f"{d.get('max_ms', 0):>10.2f} {a.get('p50_ms', 0):>9.2f} {a.get('p99_ms', 0):>9.2f} "
            f"{str(r['serialisations_per_tick']):>9}"
        )
    print(
        "\ndelay = conflation tick to arrival at the client: encode, per-client write, socket.\n"
        "        The 0-50 ms conflation wait is excluded on purpose — see bench_conflation.py.\n"
        "ack   = a single order acknowledgement sampled while the sockets are attached. Open\n"
        "        Issue 006 §7b says this must not move with the client count."
    )

    if args.json:
        Path(args.json).write_text(
            json.dumps(
                {
                    "benchmark": "B3-scaling",
                    "recorded_unix": int(time.time()),
                    "channels_per_client": channels,
                    "redis_host_clock_offset_ms": round(skew, 3),
                    "steps": results,
                },
                indent=2,
            )
        )
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
