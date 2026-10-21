"""A matcher's outputs for a batch are durable together or not at all.

The defect: each output was its own `XADD`, so a crash between an order's `OrderAccepted` and
its fills left the acceptance on the stream without them. Recovery counts acceptances to know
how many inputs were answered, so it replayed that order silently and the fills were never
written — the engine's book then disagreed with the ledger and the gateway forever.

Each test fails the transaction for a batch whose taker sweeps several resting orders, and
checks the stream holds none of that batch, and that the matcher then reproduces exactly what an
uninterrupted run writes. Both runners, because the naive model is the specification.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
from redis.asyncio import Redis

from contracts.v1.generated.contracts import Side, SubmitOrder, Tif
from services.gateway.streams import HaltState, StreamProducer, read_records
from services.matcher.adapter import NaiveMatcher
from services.matcher.cpp_runner import CppMatcher
from services.matcher.runner import Matcher

pytestmark = pytest.mark.anyio

ROOT = Path(__file__).resolve().parents[2]


def _engine_path() -> Path | None:
    configured = os.environ.get("QA_CPP_ENGINE_PATH")
    if configured:
        return Path(configured) if Path(configured).exists() else None
    candidate = ROOT / "engine" / "cpp" / "quant_arena_engine"
    return candidate if candidate.exists() else None


@pytest.fixture(params=["naive", "cpp"])
def runner(request, monkeypatch):
    if request.param == "cpp":
        executable = _engine_path()
        if executable is None:
            pytest.skip("build engine/cpp/quant_arena_engine before running C++ worker tests")
        monkeypatch.setenv("QA_CPP_ENGINE_PATH", str(executable))
        return CppMatcher
    return Matcher


def _order(coid: int, user: int, side: Side, price: int, qty: int) -> SubmitOrder:
    return SubmitOrder.new(
        timestamp_ns=coid, client_order_id=coid, user_id=user, symbol_id=1,
        side=side, tif=Tif.GTC, price_ticks=price, qty=qty,
    )


#: Three resting asks, then one buy that takes all three: one input, four outputs.
RESTING = [_order(1, 10, Side.SELL, 100, 2), _order(2, 11, Side.SELL, 101, 2),
           _order(3, 12, Side.SELL, 102, 2)]
SWEEP = [_order(4, 20, Side.BUY, 102, 6)]
AFTER = [_order(5, 21, Side.SELL, 105, 1), _order(6, 22, Side.BUY, 105, 1)]


async def _append(redis, settings, records) -> None:
    producer = StreamProducer(redis, HaltState(), batch_max=settings.stream_batch_max)
    producer.start()
    for record in records:
        await producer.append(settings.stream_inbound, record)
    await producer.stop()


async def _outbound(redis, settings) -> list:
    records, last_id = [], "0-0"
    while True:
        batch = await read_records(redis, settings.stream_outbound, last_id=last_id, count=500, block_ms=20)
        if not batch:
            return [r._replace(seq_ms=0, seq_ord=0) for r in records]
        records.extend(item.record for item in batch)
        last_id = batch[-1].stream_id


def _reference(settings, records) -> list:
    engine = NaiveMatcher(initial_cash_ticks=settings.initial_cash_ticks)
    return [out for record in records for out in engine.apply(record)]


async def _start(matcher) -> None:
    if isinstance(matcher, CppMatcher):
        await matcher.engine.start()
    matcher.producer.start()


async def _drain(matcher) -> None:
    while await matcher.step():
        pass


def _fail_next_transaction(matcher) -> None:
    real = matcher.producer.append_atomic

    async def failing(stream, records):
        matcher.producer.append_atomic = real
        raise ConnectionError("Redis went away mid-batch")

    matcher.producer.append_atomic = failing


async def test_a_failed_batch_leaves_nothing_and_a_restart_writes_it_whole(
    runner, settings, clean_redis
):
    redis = Redis.from_url(settings.redis_url, decode_responses=False)
    await _append(redis, settings, RESTING)
    first = runner(redis, settings)
    await first.recover()
    await _start(first)
    await _drain(first)
    answered = await _outbound(redis, settings)

    await _append(redis, settings, SWEEP)
    _fail_next_transaction(first)
    with pytest.raises(ConnectionError):
        await first.step()
    # Nothing of the sweep reached the stream — not the acceptance, not one fill.
    assert await _outbound(redis, settings) == answered
    await first.stop()

    await _append(redis, settings, AFTER)
    second = runner(redis, settings)
    await second.recover()
    await _start(second)
    await _drain(second)
    await second.stop()

    assert await _outbound(redis, settings) == _reference(settings, RESTING + SWEEP + AFTER)
    await redis.aclose()


async def test_the_live_loop_recovers_instead_of_applying_a_batch_twice(
    runner, settings, clean_redis
):
    """`run` must reconcile after a failed cycle. The naive runner retried `step` on top of a
    book that had already applied the batch, so the same orders rested twice."""
    redis = Redis.from_url(settings.redis_url, decode_responses=False)
    await _append(redis, settings, RESTING)
    matcher = runner(redis, settings)
    await matcher.recover()
    _fail_next_transaction(matcher)
    if isinstance(matcher, CppMatcher):
        await matcher.start()
    else:
        matcher.start()
    await _append(redis, settings, SWEEP + AFTER)

    expected = _reference(settings, RESTING + SWEEP + AFTER)
    for _ in range(200):
        if len(await _outbound(redis, settings)) >= len(expected):
            break
        await asyncio.sleep(0.05)
    await matcher.stop()

    assert await _outbound(redis, settings) == expected
    await redis.aclose()
