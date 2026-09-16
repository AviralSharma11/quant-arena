"""The end-of-week-2 integration point, over a real Redis stream.

Appendix D.2: *"Gateway stops calling the stub engine and writes to the real stream; the naive
model consumes it."* Task 2.1 built the first half. These tests are the second half, and the
proof that the loop closes:

    POST /orders ──► qa.inbound ──► matcher ──► qa.outbound ──► ledger

Against the real store on logical database 15, never a fake — the point of the task is that
records survive a real stream, and a fake would pass while the property was false.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from redis.asyncio import Redis

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from config.settings import Settings  # noqa: E402
from contracts.v1.generated.contracts import (  # noqa: E402
    CancelOrder,
    ConfigureReplay,
    Fill,
    OrderAccepted,
    OrderCancelled,
    Side,
    SubmitOrder,
    Tif,
    ReplayConfigured,
)
from services.gateway.streams import (  # noqa: E402
    HaltState,
    StreamProducer,
    read_records,
)
from services.matcher.runner import Matcher

pytestmark = pytest.mark.anyio

BUY, SELL = int(Side.BUY), int(Side.SELL)


def submit(*, coid, user, side, price, qty, symbol=1, tif=int(Tif.GTC), ts=1) -> SubmitOrder:
    return SubmitOrder.new(
        timestamp_ns=ts, client_order_id=coid, user_id=user, symbol_id=symbol,
        side=side, tif=tif, price_ticks=price, qty=qty,
    )


async def feed(settings: Settings, records, redis: Redis | None = None) -> Redis:
    """Put records on the inbound stream the way the gateway does — one batching producer."""
    redis = redis or Redis.from_url(settings.redis_url, decode_responses=False)
    producer = StreamProducer(
        redis, HaltState(), batch_max=settings.stream_batch_max
    )
    producer.start()
    for record in records:
        await producer.append(settings.stream_inbound, record)
    await producer.stop()
    return redis


async def drain(matcher: Matcher) -> None:
    """Run the matcher until the inbound stream is exhausted."""
    while await matcher.step():
        pass


async def outbound(redis: Redis, settings: Settings) -> list:
    records, last_id = [], "0-0"
    while True:
        batch = await read_records(
            redis, settings.stream_outbound, last_id=last_id, count=100, block_ms=20
        )
        if not batch:
            return records
        records.extend(item.record for item in batch)
        last_id = batch[-1].stream_id


async def test_a_crossing_pair_produces_one_fill_on_the_outbound_stream(
    settings: Settings, clean_redis
):
    """Criterion 1: the loop closes. Orders go in on one stream, a fill comes out on another."""
    redis = await feed(settings, [
        submit(coid=1, user=10, side=BUY, price=100, qty=5, ts=1),
        submit(coid=2, user=20, side=SELL, price=100, qty=5, ts=2),
    ])
    matcher = Matcher(redis, settings)
    matcher.producer.start()
    await drain(matcher)
    await matcher.producer.stop()

    records = await outbound(redis, settings)
    await redis.aclose()

    assert [type(r).__name__ for r in records] == ["OrderAccepted", "OrderAccepted", "Fill"]
    fill = records[-1]
    assert fill.maker_order_id == 1 and fill.taker_order_id == 2
    assert fill.price_ticks == 100 and fill.qty == 5
    # `seq` is stamped from the Redis stream id on read, never authored (Open Issue 003).
    assert fill.seq_ms > 0


async def test_replay_configuration_is_ordered_and_recovered_once(
    settings: Settings, clean_redis
):
    config = ConfigureReplay.new(
        timestamp_ns=1,
        client_order_id=0,
        real_seconds_per_simulated_minute=1,
        config_hash_hi=11,
        config_hash_lo=22,
    )
    redis = await feed(settings, [config, submit(
        coid=1, user=10, side=BUY, price=100, qty=5, ts=2
    )])
    first = Matcher(redis, settings)
    first.producer.start()
    await drain(first)
    await first.producer.stop()

    before = await outbound(redis, settings)
    assert [type(record).__name__ for record in before] == [
        "ReplayConfigured", "OrderAccepted"
    ]

    second = Matcher(redis, settings)
    assert await second.recover() == 2
    assert second.last_recovery_seconds is not None
    assert second.last_recovery_seconds >= 0
    assert await outbound(redis, settings) == before
    await redis.aclose()

    replayed_config = before[0]
    assert isinstance(replayed_config, ReplayConfigured)
    assert replayed_config.config_hash_hi == 11
    assert replayed_config.config_hash_lo == 22


async def test_the_gateway_writes_and_the_matcher_answers(settings: Settings, clean_redis):
    """The gateway's own append path, consumed by the matcher — the seam itself.

    The gateway is the single producer and `XADD`s a `SubmitOrder`; nothing about the matcher
    is special-cased for tests, it simply reads what the gateway wrote.
    """
    redis = await feed(settings, [submit(coid=7, user=42, side=BUY, price=250, qty=3)])
    matcher = Matcher(redis, settings)
    matcher.producer.start()
    await drain(matcher)
    await matcher.producer.stop()

    records = await outbound(redis, settings)
    await redis.aclose()

    assert len(records) == 1
    accepted = records[0]
    assert isinstance(accepted, OrderAccepted)
    assert accepted.client_order_id == 7 and accepted.user_id == 42
    # Engine-assigned, and the gateway could not have known it at append time.
    assert accepted.order_id == 1


async def test_a_cancel_reaches_the_book_and_comes_back_as_cancelled(
    settings: Settings, clean_redis
):
    """Criterion 4."""
    redis = await feed(settings, [
        submit(coid=1, user=10, side=BUY, price=100, qty=5, ts=1),
        CancelOrder.new(timestamp_ns=2, client_order_id=2, user_id=10, target_client_order_id=1),
    ])
    matcher = Matcher(redis, settings)
    matcher.producer.start()
    await drain(matcher)
    await matcher.producer.stop()

    records = await outbound(redis, settings)
    assert isinstance(records[-1], OrderCancelled)
    assert records[-1].order_id == 1 and records[-1].remaining_qty == 5
    assert matcher.matcher.resting() == []
    await redis.aclose()


async def test_restarting_the_matcher_rebuilds_the_book_and_re_emits_nothing(
    settings: Settings, clean_redis
):
    """Criterion 3, and the one that matters most.

    There are no snapshots (Open Issue 018 §13.1), so recovery is a full replay from `0-0`.
    A replay that re-appended its outbound records would duplicate fills that moved real
    positions, so the anchor count is what stops it. Both halves are asserted here.
    """
    redis = await feed(settings, [
        submit(coid=1, user=10, side=BUY, price=100, qty=5, ts=1),
        submit(coid=2, user=11, side=BUY, price=99, qty=4, ts=2),
        submit(coid=3, user=20, side=SELL, price=100, qty=2, ts=3),
    ])
    first = Matcher(redis, settings)
    first.producer.start()
    await drain(first)
    await first.producer.stop()

    before = await outbound(redis, settings)
    book_before = first.matcher.resting()

    # Kill it. A second matcher, with nothing in memory, recovers from the streams alone.
    second = Matcher(redis, settings)
    replayed = await second.recover()

    assert replayed == 3
    assert second.matcher.resting() == book_before
    assert second.matcher.next_order_id == first.matcher.next_order_id

    # And it appended nothing while recovering.
    assert await outbound(redis, settings) == before

    # From here it carries on where it stopped, with the ids continuing rather than restarting.
    await feed(settings, [submit(coid=4, user=30, side=SELL, price=99, qty=1, ts=4)], redis)
    second.producer.start()
    await drain(second)
    await second.producer.stop()

    after = await outbound(redis, settings)
    await redis.aclose()

    new_records = after[len(before):]
    assert [type(r).__name__ for r in new_records] == ["OrderAccepted", "Fill"]
    assert new_records[0].order_id == 4


async def test_recovery_after_a_crash_mid_stream_answers_the_unanswered_records(
    settings: Settings, clean_redis
):
    """The matcher stopped with work outstanding. Recovery finishes it exactly once."""
    redis = await feed(settings, [
        submit(coid=1, user=10, side=BUY, price=100, qty=5, ts=1),
        submit(coid=2, user=20, side=SELL, price=100, qty=5, ts=2),
    ])
    # A matcher that handles only the first record, then dies.
    partial = Matcher(redis, settings, batch_size=1)
    partial.producer.start()
    assert await partial.step() == 1
    await partial.producer.stop()

    assert len(await outbound(redis, settings)) == 1

    recovered = Matcher(redis, settings)
    assert await recovered.recover() == 1
    recovered.producer.start()
    await drain(recovered)
    await recovered.producer.stop()

    records = await outbound(redis, settings)
    await redis.aclose()

    # Exactly one accepted per order, and exactly one fill. Nothing replayed twice.
    assert [type(r).__name__ for r in records] == ["OrderAccepted", "OrderAccepted", "Fill"]
    assert len([r for r in records if isinstance(r, Fill)]) == 1


async def test_two_symbols_never_cross_across_the_stream(settings: Settings, clean_redis):
    redis = await feed(settings, [
        submit(coid=1, user=10, side=BUY, price=100, qty=5, symbol=3, ts=1),
        submit(coid=2, user=20, side=SELL, price=100, qty=5, symbol=7, ts=2),
    ])
    matcher = Matcher(redis, settings)
    matcher.producer.start()
    await drain(matcher)
    await matcher.producer.stop()

    records = await outbound(redis, settings)
    await redis.aclose()
    assert not [r for r in records if isinstance(r, Fill)]
