"""The halt signal crossing a process boundary.

`contracts/v1/rest_and_ws.md` §3.6 gives the browser a `halted` frame, and `web/src/stream/
client.ts` has rendered it as its own connection state since Task 5.4c — but until this task
nothing could ever send one. The halt flag is a plain object in gateway process memory (Open
Issue 004 deliberately keeps risk state out of Redis), and fan-out is a different process.

So the gateway publishes it and fan-out reads it. The alternative — fan-out pinging Redis and
calling a failed ping a halt — substitutes one claim for another:

| what the browser reads | what a local ping would mean |
|---|---|
| the gateway cannot durably record orders | fan-out cannot reach Redis |

They come apart in both directions. A hiccup on fan-out's own connection would show HALTED over
a healthy exchange; a store that is readable but not writable — a full disk, an fsync failing
under `appendfsync always` — reads fine while every order is refused. The second is precisely
the case a halt exists for, so the claim has to come from the process making it.
"""

from __future__ import annotations

import json

import pytest
import redis as redis_sync
from redis.asyncio import Redis

from config.settings import Settings
from services.fanout.halt import HaltView
from services.gateway.streams import (
    HALT_KEY,
    HALT_KEY_TTL_INTERVALS,
    HaltReason,
    HaltState,
    publish_halt,
    watch_health,
)


@pytest.fixture
def store(test_settings: Settings):
    client = redis_sync.Redis.from_url(test_settings.redis_url)
    client.delete(HALT_KEY)
    yield client
    client.delete(HALT_KEY)
    client.close()


@pytest.mark.anyio
async def test_the_gateway_publishes_its_halt_and_fan_out_reads_it(
    test_settings: Settings, store
):
    redis = Redis.from_url(test_settings.redis_url, decode_responses=False)
    halt, view = HaltState(), HaltView()
    try:
        await publish_halt(redis, halt, ttl_ms=60_000)
        assert await view.refresh(redis) is False, "healthy is the starting state"
        assert view.halted is False

        halt.halt(HaltReason.REDIS_UNREACHABLE, "connection refused")
        await publish_halt(redis, halt, ttl_ms=60_000)

        assert await view.refresh(redis) is True, "the transition is reported"
        assert view.halted and view.reason == HaltReason.REDIS_UNREACHABLE

        halt.clear()
        await publish_halt(redis, halt, ttl_ms=60_000)
        assert await view.refresh(redis) is True
        assert view.halted is False
    finally:
        await redis.aclose()


@pytest.mark.anyio
async def test_reading_the_same_state_twice_is_not_a_transition(
    test_settings: Settings, store
):
    """One frame per transition, not one per poll. At two polls a second, the alternative is
    an outage made of halt notifications."""
    redis = Redis.from_url(test_settings.redis_url, decode_responses=False)
    halt, view = HaltState(), HaltView()
    try:
        halt.halt(HaltReason.REDIS_UNREACHABLE, "connection refused")
        await publish_halt(redis, halt, ttl_ms=60_000)
        assert await view.refresh(redis) is True
        for _ in range(5):
            assert await view.refresh(redis) is False
        assert view.transitions == 1
    finally:
        await redis.aclose()


@pytest.mark.anyio
async def test_an_absent_key_is_itself_a_halt(test_settings: Settings, store):
    """With nothing publishing, no order can be recorded — so reporting a halt is true rather
    than defensive. It clears on its own within one poll of the gateway returning."""
    redis = Redis.from_url(test_settings.redis_url, decode_responses=False)
    view = HaltView()
    try:
        store.delete(HALT_KEY)
        assert await view.refresh(redis) is True
        assert view.halted and view.reason == HaltReason.GATEWAY_UNREACHABLE
    finally:
        await redis.aclose()


@pytest.mark.anyio
async def test_an_unreadable_key_is_reported_rather_than_ignored(
    test_settings: Settings, store
):
    """Something else owns this key. Guessing at the contents would mean reporting a healthy
    exchange on the strength of a value that could not be read."""
    redis = Redis.from_url(test_settings.redis_url, decode_responses=False)
    view = HaltView()
    try:
        store.set(HALT_KEY, "not json at all")
        assert await view.refresh(redis) is True
        assert view.halted and view.reason == HaltReason.GATEWAY_UNREACHABLE
    finally:
        await redis.aclose()


@pytest.mark.anyio
async def test_the_watchdog_publishes_on_its_own_interval(test_settings: Settings, store):
    """Published from the existing watchdog rather than a second loop. Two loops polling the
    same Redis on the same interval to answer the same question could disagree, and the one
    that lost would be the one the browser was reading."""
    import asyncio

    redis = Redis.from_url(test_settings.redis_url, decode_responses=False)
    halt, stop = HaltState(), asyncio.Event()
    task = asyncio.create_task(
        watch_health(redis, halt, poll_ms=20, stop=stop)
    )
    try:
        for _ in range(50):
            raw = store.get(HALT_KEY)
            if raw is not None:
                break
            await asyncio.sleep(0.02)
        assert raw is not None, "the watchdog published nothing"
        assert json.loads(raw)["halted"] is False
        ttl = store.pttl(HALT_KEY)
        assert 0 < ttl <= 20 * HALT_KEY_TTL_INTERVALS
    finally:
        stop.set()
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
        await redis.aclose()
