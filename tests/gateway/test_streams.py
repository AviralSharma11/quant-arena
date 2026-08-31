"""Task 2.1 — the durable ordered log.

Criterion 1 (monotonic stream IDs), criterion 3 (XREAD batches genuinely), and the batching
producer that makes `appendfsync always` affordable. Criteria 4 and 5 — the halt state — live
in test_halt.py, which stops the real Redis container.
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest
import redis.asyncio as redis_async
from fastapi.testclient import TestClient

from contracts.v1.generated.contracts import (
    CancelOrder,
    SEQ_UNASSIGNED,
    Side,
    SubmitOrder,
    Tif,
)
from services.gateway.streams import (
    ExchangeHalted,
    HaltReason,
    HaltState,
    StreamProducer,
    read_records,
    watch_health,
)

STREAM = "qa.test.inbound"

#: Every test in this module is async; anyio's plugin drives them.
pytestmark = pytest.mark.anyio


def _order(n: int) -> SubmitOrder:
    return SubmitOrder.new(
        timestamp_ns=1_700_000_000_000_000_000 + n, client_order_id=n, user_id=7,
        symbol_id=1, side=int(Side.BUY), tif=int(Tif.GTC), price_ticks=6_412_500, qty=n + 1,
    )


@pytest.fixture
async def redis(settings):
    client = redis_async.Redis.from_url(settings.redis_url, decode_responses=False)
    await client.delete(STREAM)
    yield client
    await client.delete(STREAM)
    await client.aclose()


@pytest.fixture
async def producer(redis, settings):
    p = StreamProducer(redis, HaltState(), maxlen=settings.stream_maxlen, batch_max=256)
    p.start()
    yield p
    await p.stop()


# --- criterion 1: XADD, read back, monotonic ---------------------------------------------


async def test_a_record_is_appended_and_read_back_identically(redis, producer):
    original = _order(1)
    stream_id = await producer.append(STREAM, original)

    records = await read_records(redis, STREAM, "0-0", count=10, block_ms=50)
    assert len(records) == 1
    assert records[0].stream_id == stream_id
    # Everything but seq must survive the round trip untouched.
    assert records[0].record._replace(seq_ms=0, seq_ord=0) == original


async def test_stream_ids_are_monotonic(redis, producer):
    ids = [await producer.append(STREAM, _order(n)) for n in range(20)]
    keys = [tuple(int(p) for p in i.split("-")) for i in ids]
    assert keys == sorted(keys)
    assert len(set(keys)) == len(keys)


async def test_seq_is_stamped_on_read_not_authored_on_write(redis, producer):
    """Open Issue 003: the stream ID *is* the sequence number, and no parallel counter exists.
    A producer cannot know its own ID before XADD returns, so it writes SEQ_UNASSIGNED."""
    original = _order(1)
    assert original.seq_ms == SEQ_UNASSIGNED and original.seq_ord == SEQ_UNASSIGNED

    stream_id = await producer.append(STREAM, original)
    record = (await read_records(redis, STREAM, "0-0", count=10, block_ms=50))[0].record
    ms, ordinal = (int(p) for p in stream_id.split("-"))
    assert (record.seq_ms, record.seq_ord) == (ms, ordinal)


async def test_replaying_re_derives_the_same_sequence_numbers(redis, producer):
    """The reason writing seq on the wire is unnecessary: reading twice gives the same answer."""
    for n in range(5):
        await producer.append(STREAM, _order(n))
    first = [(r.stream_id, r.record) for r in await read_records(redis, STREAM, "0-0", count=10, block_ms=50)]
    second = [(r.stream_id, r.record) for r in await read_records(redis, STREAM, "0-0", count=10, block_ms=50)]
    assert first == second


async def test_mixed_record_types_survive_the_stream(redis, producer):
    await producer.append(STREAM, _order(1))
    await producer.append(
        STREAM,
        CancelOrder.new(timestamp_ns=1, client_order_id=2, user_id=7, target_client_order_id=1),
    )
    kinds = [type(r.record).__name__ for r in await read_records(redis, STREAM, "0-0", count=10, block_ms=50)]
    assert kinds == ["SubmitOrder", "CancelOrder"]


# --- the batching producer, which is what makes `appendfsync always` affordable ------------


async def test_concurrent_appends_are_flushed_as_batches(redis, producer):
    """Group commit: one fsync per batch instead of one per order (Open Issue 003 §8.2).
    Without this, `appendfsync always` measured 1,996 orders/sec and the durability decision
    would have been forced the other way — see benchmarks/results/2.1-stream-durability.md."""
    n = 200
    ids = await asyncio.gather(*(producer.append(STREAM, _order(i)) for i in range(n)))

    assert len(set(ids)) == n
    assert producer.records_written == n
    assert producer.batches_flushed < n, (
        f"{n} appends produced {producer.batches_flushed} flushes — no batching is happening"
    )


async def test_batching_preserves_order(redis, producer):
    """Batching must not reorder. Price-time priority depends on it."""
    n = 100
    await asyncio.gather(*(producer.append(STREAM, _order(i)) for i in range(n)))
    records = await read_records(redis, STREAM, "0-0", count=n, block_ms=50)
    assert [r.record.client_order_id for r in records] == list(range(n))


async def test_a_batch_never_exceeds_batch_max(redis, settings):
    """Asserts the actual bound, not a lower bound on the number of flushes — the earlier
    version of this test would have passed with no limit enforced at all."""
    p = StreamProducer(redis, HaltState(), maxlen=settings.stream_maxlen, batch_max=8)
    p.start()
    try:
        await asyncio.gather(*(p.append(STREAM, _order(i)) for i in range(64)))
        assert p.max_batch <= 8, f"a batch of {p.max_batch} exceeded batch_max of 8"
        assert p.max_batch > 1, "no batching happened at all"
    finally:
        await p.stop()


# --- the producer must fail, never hang ------------------------------------------------------
#
# A hung request is the worst failure available here: the gateway stays up, /health answers
# "ok", and orders simply never return. Each of these was a real hang before the fix.


async def test_an_unexpected_error_fails_the_append_and_the_loop_survives(redis, settings):
    """Only RedisError and connection failures were handled. Anything else killed the flusher,
    and every later append then awaited a future nobody would ever resolve."""

    class Boom(Exception):
        pass

    p = StreamProducer(redis, HaltState(), maxlen=settings.stream_maxlen, batch_max=8)
    p.start()
    try:
        with patch.object(type(redis), "pipeline", side_effect=Boom("unexpected")):
            with pytest.raises(Boom):
                await asyncio.wait_for(p.append(STREAM, _order(1)), timeout=5.0)

        assert p.running, "one bad flush killed the producer loop"
        # And it genuinely recovers, rather than merely staying alive.
        assert await asyncio.wait_for(p.append(STREAM, _order(2)), timeout=5.0)
    finally:
        await p.stop()


async def test_appending_to_a_dead_producer_halts_rather_than_hanging(redis, settings):
    halt = HaltState()
    p = StreamProducer(redis, halt, maxlen=settings.stream_maxlen, batch_max=8)
    p.start()
    p._task.cancel()
    await asyncio.sleep(0.05)

    with pytest.raises(ExchangeHalted) as raised:
        await asyncio.wait_for(p.append(STREAM, _order(1)), timeout=5.0)
    assert raised.value.reason == HaltReason.PRODUCER_STOPPED
    assert halt.halted, "a producer that cannot append must halt the exchange"


async def test_stopping_the_producer_answers_everything_still_queued(redis, settings):
    """Shutdown must not strand a request either."""
    halt = HaltState()
    p = StreamProducer(redis, halt, maxlen=settings.stream_maxlen, batch_max=1)
    p.start()
    pending = [asyncio.create_task(p.append(STREAM, _order(i))) for i in range(20)]
    await asyncio.sleep(0)
    await p.stop()

    results = await asyncio.wait_for(
        asyncio.gather(*pending, return_exceptions=True), timeout=5.0
    )
    for result in results:
        assert isinstance(result, (str, ExchangeHalted)), (
            f"an append was left unresolved on shutdown: {result!r}"
        )


# --- criterion 3: XREAD COUNT n returns genuine batches -------------------------------------


@pytest.mark.parametrize("count", [1, 10, 100])
async def test_xread_count_returns_genuine_batches(redis, producer, count: int):
    total = 100
    await asyncio.gather(*(producer.append(STREAM, _order(i)) for i in range(total)))
    batch = await read_records(redis, STREAM, "0-0", count=count, block_ms=50)
    assert len(batch) == min(count, total), (
        f"XREAD COUNT {count} returned {len(batch)} — it is not batching"
    )


async def test_reading_forward_from_the_last_id_consumes_each_record_once(redis, producer):
    total = 50
    await asyncio.gather(*(producer.append(STREAM, _order(i)) for i in range(total)))
    seen, last = [], "0-0"
    while True:
        batch = await read_records(redis, STREAM, last, count=10, block_ms=50)
        if not batch:
            break
        seen.extend(r.record.client_order_id for r in batch)
        last = batch[-1].stream_id
    assert seen == list(range(total))


# --- trimming --------------------------------------------------------------------------------


async def test_maxlen_trimming_is_applied(redis):
    """MAXLEN ~ keeps the stream bounded. Streams are RAM-resident (Open Issue 003 §8.4), so
    an untrimmed stream is a slow memory leak rather than an immediate failure."""
    p = StreamProducer(redis, HaltState(), maxlen=10, batch_max=4)
    p.start()
    try:
        await asyncio.gather(*(p.append(STREAM, _order(i)) for i in range(200)))
        # `approximate=True` trims at node boundaries, so this is a bound, not an equality.
        assert await redis.xlen(STREAM) < 200
    finally:
        await p.stop()


# --- halt state, in isolation ----------------------------------------------------------------


def test_halt_records_when_it_started_and_clears_completely():
    halt = HaltState()
    assert not halt.halted

    halt.halt(HaltReason.REDIS_UNREACHABLE, "connection refused")
    assert halt.halted and halt.since_ns is not None
    first_since = halt.since_ns

    halt.halt(HaltReason.REDIS_UNREACHABLE, "still down")
    assert halt.since_ns == first_since, "a continuing halt must not reset its start time"

    halt.clear()
    assert not halt.halted and halt.reason is None and halt.since_ns is None


async def test_appending_while_halted_raises_rather_than_queueing(redis, settings):
    halt = HaltState()
    halt.halt(HaltReason.REDIS_UNREACHABLE, "down")
    p = StreamProducer(redis, halt, maxlen=settings.stream_maxlen, batch_max=8)
    p.start()
    try:
        with pytest.raises(ExchangeHalted):
            await p.append(STREAM, _order(1))
    finally:
        await p.stop()


async def test_the_watchdog_clears_a_halt_once_redis_answers(redis, settings):
    """Criterion 5 in miniature: the halt lifts on its own, with nothing restarted."""
    halt = HaltState()
    halt.halt(HaltReason.REDIS_UNREACHABLE, "was down")
    stop = asyncio.Event()
    task = asyncio.create_task(watch_health(redis, halt, poll_ms=20, stop=stop))
    try:
        for _ in range(50):
            await asyncio.sleep(0.02)
            if not halt.halted:
                break
        assert not halt.halted, "the watchdog never lifted the halt"
    finally:
        stop.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
