#!/usr/bin/env python3
"""Task 2.1 Success Criteria 2 and 3 — measure, then choose.

Two questions this answers with numbers rather than opinion:

1. **Is `appendfsync always` affordable?** It flushes on every write, which is ruinous one
   order at a time. The gateway's `StreamProducer` pipelines concurrent appends into a single
   command batch, so the cost is one fsync per batch — group commit, done by Redis rather than
   by hand (Open Issue 003 §8.2). If the measured throughput is unacceptable, the decision is
   to fall back to `everysec` and write the one-second window down *explicitly*.

2. **Does `XREAD COUNT n` actually batch?** The network hop is the floor on latency but not on
   throughput, because the round trip amortises across the batch. Per-record cost is reported
   at n = 1, 10 and 100 so the amortisation is visible instead of assumed.

    python benchmarks/bench_streams.py
    python benchmarks/bench_streams.py --records 20000 --url redis://localhost:6379/0
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from redis.asyncio import Redis  # noqa: E402

from contracts.v1.generated.contracts import Side, SubmitOrder, Tif  # noqa: E402
from services.gateway.streams import (  # noqa: E402
    HaltState,
    StreamProducer,
    read_records,
)

BENCH_STREAM = "qa.bench"


def _order(n: int) -> SubmitOrder:
    return SubmitOrder.new(
        timestamp_ns=time.time_ns(), client_order_id=n, user_id=1, symbol_id=1,
        side=int(Side.BUY), tif=int(Tif.GTC), price_ticks=6_412_500, qty=1,
    )


async def _reset(redis: Redis) -> None:
    await redis.delete(BENCH_STREAM)


async def bench_sequential(redis: Redis, n: int) -> float:
    """One XADD at a time, each awaited. The naive shape, and the worst case for `always`."""
    await _reset(redis)
    start = time.perf_counter()
    for i in range(n):
        await redis.xadd(BENCH_STREAM, {b"r": _order(i).pack()}, maxlen=2_000_000, approximate=True)
    return n / (time.perf_counter() - start)


async def bench_producer(redis: Redis, n: int, batch_max: int) -> tuple[float, float]:
    """Through the real StreamProducer, with n appends in flight at once.

    This is the gateway's actual path: concurrent requests queue, and the flusher drains
    whatever accumulated while the previous pipeline was in flight.
    """
    await _reset(redis)
    producer = StreamProducer(redis, HaltState(), maxlen=2_000_000, batch_max=batch_max)
    producer.start()
    start = time.perf_counter()
    await asyncio.gather(*(producer.append(BENCH_STREAM, _order(i)) for i in range(n)))
    elapsed = time.perf_counter() - start
    await producer.stop()
    mean_batch = producer.records_written / max(producer.batches_flushed, 1)
    return n / elapsed, mean_batch


async def bench_read(redis: Redis, count: int, total: int) -> float:
    """Per-record cost in microseconds at a given XREAD COUNT."""
    last = "0-0"
    read = 0
    start = time.perf_counter()
    while read < total:
        records = await read_records(redis, BENCH_STREAM, last, count=count, block_ms=50)
        if not records:
            break
        last = records[-1].stream_id
        read += len(records)
    elapsed = time.perf_counter() - start
    return (elapsed / read) * 1e6 if read else float("nan")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="redis://localhost:6379/0")
    parser.add_argument("--records", type=int, default=10000)
    parser.add_argument("--sequential-records", type=int, default=2000)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()

    redis = Redis.from_url(args.url, decode_responses=False)
    original = (await redis.config_get("appendfsync")).get("appendfsync", "everysec")
    info = await redis.info("server")
    print(f"redis {info['redis_version']}  ·  appendfsync on entry: {original}\n")

    results: dict[str, dict] = {}
    try:
        for fsync in ("always", "everysec"):
            await redis.config_set("appendfsync", fsync)
            seq = statistics.median(
                [await bench_sequential(redis, args.sequential_records) for _ in range(args.repeats)]
            )
            pipelined, mean_batch = 0.0, 0.0
            samples = []
            for _ in range(args.repeats):
                rate, batch = await bench_producer(redis, args.records, 256)
                samples.append(rate)
                mean_batch = batch
            pipelined = statistics.median(samples)
            results[fsync] = {
                "sequential": seq, "pipelined": pipelined, "mean_batch": mean_batch,
            }
            print(f"appendfsync = {fsync}")
            print(f"  sequential XADD   {seq:>12,.0f} orders/sec")
            print(f"  StreamProducer    {pipelined:>12,.0f} orders/sec"
                  f"   (mean batch {mean_batch:.0f} records/fsync)")
            print(f"  speed-up          {pipelined / seq:>12,.1f}x\n")

        await redis.config_set("appendfsync", "always")
        await bench_producer(redis, args.records, 256)
        print("XREAD COUNT n — per-record cost")
        read_costs = {}
        for count in (1, 10, 100):
            cost = statistics.median([await bench_read(redis, count, args.records) for _ in range(args.repeats)])
            read_costs[count] = cost
            print(f"  COUNT {count:>3}         {cost:>12.2f} µs/record")
        results["read"] = read_costs
    finally:
        await redis.config_set("appendfsync", original)
        await _reset(redis)
        await redis.aclose()

    print(f"\nappendfsync restored to {original} (compose sets it to always at startup)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
