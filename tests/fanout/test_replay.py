"""Recovery, against a real Redis stream.

Criterion 2 of 5.2a: killing fan-out and replaying from `0-0` reproduces identical state. Run
against the real store rather than a fake, for the same reason the gateway suite is — the
property under test is that the *stream* is sufficient to rebuild from, and a fake would pass
while that was false.

Recovery here needs none of the care the matcher's does. The matcher **appends** to a stream, so
a naive replay would duplicate fills that moved real positions, which is why it counts anchors.
Fan-out only ever derives a view, and rebuilding a view from the same inputs gives the same
view. That asymmetry is the whole reason `runner.py` is short.
"""

from __future__ import annotations

import pytest
import redis as redis_sync
from redis.asyncio import Redis

from config.settings import Settings
from contracts.v1.generated.contracts import Side, SubmitOrder, Tif
from services.fanout.runner import FanOut
from services.fanout.state import MarketState
from services.gateway.streams import RECORD_FIELD
from services.matcher.adapter import NaiveMatcher

pytestmark = pytest.mark.anyio

BUY, SELL = int(Side.BUY), int(Side.SELL)


def _seed_outbound(settings: Settings) -> tuple[NaiveMatcher, int]:
    """Put a real matcher's output onto the real outbound stream."""
    matcher = NaiveMatcher(initial_cash_ticks=1_000_000)
    inbound = [
        SubmitOrder.new(timestamp_ns=n * 1_000_000_000, client_order_id=n, user_id=n % 4 + 1,
                        symbol_id=1 + n % 2, side=BUY if n % 2 else SELL,
                        tif=int(Tif.GTC), price_ticks=1_000 + (n % 7) - 3, qty=1 + n % 5)
        for n in range(1, 60)
    ]
    client = redis_sync.Redis.from_url(settings.redis_url)
    written = 0
    try:
        for record in inbound:
            for out in matcher.apply(record):
                client.xadd(settings.stream_outbound, {RECORD_FIELD: out.pack()})
                written += 1
    finally:
        client.close()
    return matcher, written


async def test_recovery_rebuilds_the_book_the_matcher_holds(
    test_settings: Settings, clean_redis
):
    matcher, written = _seed_outbound(test_settings)
    assert written, "the seed produced no outbound records"

    redis = Redis.from_url(test_settings.redis_url, decode_responses=False)
    try:
        fanout = FanOut(redis, test_settings)
        replayed = await fanout.recover()
    finally:
        await redis.aclose()

    assert replayed == written

    expected: dict[int, list[tuple[int, int, int, int]]] = {}
    for order_id, symbol_id, side, price, qty in matcher.resting():
        expected.setdefault(symbol_id, []).append((order_id, side, price, qty))
    for symbol_id, rows in expected.items():
        assert fanout.state.book(symbol_id).resting() == sorted(rows), symbol_id


async def test_two_replays_of_the_same_stream_agree(test_settings: Settings, clean_redis):
    """No snapshots and no checkpoints (Open Issue 018 §13.1), so this is the only recovery
    path there is — and it is the same code path as normal operation."""
    _seed_outbound(test_settings)

    snapshots = []
    for _ in range(2):
        redis = Redis.from_url(test_settings.redis_url, decode_responses=False)
        try:
            fanout = FanOut(redis, test_settings)
            await fanout.recover()
            snapshots.append(fanout.state.snapshot())
        finally:
            await redis.aclose()

    assert snapshots[0] == snapshots[1]


async def test_tailing_picks_up_records_written_after_recovery(
    test_settings: Settings, clean_redis
):
    """The live loop resumes from `state.last_seq`, so nothing between recovery and the first
    read is missed and nothing is applied twice."""
    matcher, _ = _seed_outbound(test_settings)

    redis = Redis.from_url(test_settings.redis_url, decode_responses=False)
    try:
        fanout = FanOut(redis, test_settings, poll_block_ms=20)
        await fanout.recover()
        before = fanout.state.records_applied

        client = redis_sync.Redis.from_url(test_settings.redis_url)
        try:
            for out in matcher.apply(SubmitOrder.new(
                timestamp_ns=99 * 1_000_000_000, client_order_id=9_001, user_id=1,
                # Deliberately passive: the seed leaves asks resting near 1,000, and a buy
                # above them would trade on arrival instead of joining the book — which
                # proves the tail applied a *fill*, not that a new order reached the depth.
                symbol_id=1, side=BUY, tif=int(Tif.GTC), price_ticks=900, qty=9,
            )):
                client.xadd(test_settings.stream_outbound, {RECORD_FIELD: out.pack()})
        finally:
            client.close()

        assert await fanout.step() > 0
    finally:
        await redis.aclose()

    assert fanout.state.records_applied > before
    assert [900, 9] in fanout.state.book(1).levels(BUY, 10)


async def test_an_empty_stream_recovers_to_an_empty_state(
    test_settings: Settings, clean_redis
):
    redis = Redis.from_url(test_settings.redis_url, decode_responses=False)
    try:
        fanout = FanOut(redis, test_settings)
        assert await fanout.recover() == 0
    finally:
        await redis.aclose()

    assert fanout.state.snapshot() == MarketState().snapshot()
