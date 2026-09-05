"""Task 5.2 Success Criteria 2 and 5 — the tick, and what it declines to send.

Both criteria are about *counting*, which is why they are provable here without a socket:

- **Criterion 5**, "serialisation happens once per symbol per tick, verified by
  instrumentation", is `Hub.serialisations`. If encoding were per subscriber rather than per
  channel, this number would scale with the client count — so the test attaches one client and
  then two hundred and asserts it did not move.
- **Criterion 2**, "a deliberately slowed client receives a lower frame rate and demonstrably
  affects no other", is the `busy` flag. A subscriber whose writes never complete is skipped,
  and the *demonstrably* is the second half: a healthy peer alongside it receives exactly one
  frame per tick, not one fewer.

`Subscriber` takes its send as a coroutine, so a "slow client" here is a coroutine that never
returns — which is a far more faithful stand-in for a stalled browser tab than a sleep, and
does not make the suite take eight seconds to say so.
"""

from __future__ import annotations

import asyncio

import pytest

from config.settings import Settings, Symbol
from contracts.v1.generated.contracts import Fill, OrderAccepted, Side, Tif
from services.fanout.conflation import Conflator
from services.fanout.state import MarketState
from services.fanout.subscribers import Hub, Subscriber

BUY, SELL = int(Side.BUY), int(Side.SELL)
QAA = Symbol(symbol_id=1, name="QAA", tick_size_ticks=1, lot_size=1)
QAB = Symbol(symbol_id=2, name="QAB", tick_size_ticks=1, lot_size=1)


@pytest.fixture
def conflation_settings(settings: Settings) -> Settings:
    import dataclasses

    return dataclasses.replace(settings, symbols=(QAA, QAB))


def _accepted(order_id, user, symbol_id, side, price, qty):
    return OrderAccepted.new(
        timestamp_ns=1, order_id=order_id, client_order_id=order_id, user_id=user,
        price_ticks=price, qty=qty, symbol_id=symbol_id, side=int(side), tif=int(Tif.GTC),
    )


def _fill(maker, taker, symbol_id, price, qty, aggressor=SELL):
    return Fill.new(
        timestamp_ns=2, maker_order_id=maker, taker_order_id=taker,
        maker_user_id=10, taker_user_id=11, price_ticks=price, qty=qty,
        symbol_id=symbol_id, aggressor_side=int(aggressor),
    )


class Recorder:
    """A subscriber's socket: keeps every frame, and never blocks."""

    def __init__(self) -> None:
        self.frames: list[str] = []

    async def __call__(self, frame: str) -> None:
        self.frames.append(frame)


class Stalled:
    """A socket that accepts a write and never completes it. A tab that stopped painting."""

    def __init__(self) -> None:
        self.started = 0

    async def __call__(self, frame: str) -> None:
        self.started += 1
        await asyncio.Event().wait()


async def _settle() -> None:
    """Let the writer tasks run. Two turns: one to pick the batch up, one to finish it."""
    for _ in range(4):
        await asyncio.sleep(0)


def _attach(hub: Hub, send, channels: set[str], *, user_id: int = 1) -> Subscriber:
    subscriber = Subscriber(user_id=user_id, send=send)
    subscriber.start()
    hub.add(subscriber)
    hub.subscribe(subscriber, channels)
    return subscriber


# --- Success Criterion 5 ------------------------------------------------------------------------


@pytest.mark.anyio
async def test_a_channel_is_serialised_once_however_many_clients_are_watching(
    conflation_settings: Settings,
):
    """The criterion, stated as arithmetic.

    Two hundred clients on one channel is the load Success Criterion 1 names. If the encode
    were inside the per-subscriber loop this number would be two hundred times larger, and the
    process would spend its life in `json.dumps` producing two hundred identical strings.
    """
    state = MarketState()
    state.apply(_accepted(1, 10, 1, BUY, 100, 5), stream_id="1-1")
    hub = Hub()
    conflator = Conflator(hub=hub, state=state, settings=conflation_settings)

    one = _attach(hub, Recorder(), {"book:QAA:l2"})
    conflator.tick()
    after_one = hub.serialisations

    for index in range(200):
        _attach(hub, Recorder(), {"book:QAA:l2"}, user_id=100 + index)
    state.dirty.add(1)
    conflator.tick()

    assert after_one == 1, "one dirty channel, one encode"
    assert hub.serialisations - after_one == 1, "201 clients, still one encode"
    await _settle()
    assert len(one._market) == 0


@pytest.mark.anyio
async def test_every_subscriber_of_a_channel_receives_the_identical_string(
    conflation_settings: Settings,
):
    """Not merely equal — the *same object*. Equality would still hold if each client got its
    own encode, which is the thing being ruled out."""
    state = MarketState()
    state.apply(_accepted(1, 10, 1, BUY, 100, 5), stream_id="1-1")
    hub = Hub()
    conflator = Conflator(hub=hub, state=state, settings=conflation_settings)
    recorders = [Recorder() for _ in range(5)]
    for index, recorder in enumerate(recorders):
        _attach(hub, recorder, {"book:QAA:l2"}, user_id=index)

    conflator.tick()
    await _settle()

    frames = [r.frames[0] for r in recorders]
    assert all(frame is frames[0] for frame in frames)


@pytest.mark.anyio
async def test_a_symbol_that_did_not_move_is_not_re_encoded(conflation_settings: Settings):
    """Conflation sends *current state*, and unchanged state is already current at the client.

    Twenty snapshots a second of a book nobody touched is the cost this saves — which at ten
    symbols and two hundred clients is the difference between a feed and a load test.
    """
    state = MarketState()
    state.apply(_accepted(1, 10, 1, BUY, 100, 5), stream_id="1-1")
    hub = Hub()
    conflator = Conflator(hub=hub, state=state, settings=conflation_settings)
    _attach(hub, Recorder(), {"book:QAA:l2"})

    conflator.tick()
    before = hub.serialisations
    conflator.tick()
    conflator.tick()

    assert hub.serialisations == before


@pytest.mark.anyio
async def test_a_client_joining_a_quiet_market_still_gets_the_book(
    conflation_settings: Settings,
):
    """The one exception, and the reason for it: a subscriber to a symbol nobody is trading
    would otherwise stare at an empty panel indefinitely, which looks exactly like a feed that
    is broken. It is still one encode, and only the joining client receives it."""
    state = MarketState()
    state.apply(_accepted(1, 10, 1, BUY, 100, 5), stream_id="1-1")
    hub = Hub()
    conflator = Conflator(hub=hub, state=state, settings=conflation_settings)
    early = Recorder()
    _attach(hub, early, {"book:QAA:l2"})
    conflator.tick()
    await _settle()

    late = Recorder()
    _attach(hub, late, {"book:QAA:l2"}, user_id=2)
    before = hub.serialisations
    conflator.tick()
    await _settle()

    assert hub.serialisations - before == 1
    assert len(late.frames) == 1
    assert len(early.frames) == 1, "the client that already had it is not sent it again"


# --- Success Criterion 2 ------------------------------------------------------------------------


@pytest.mark.anyio
async def test_a_stalled_client_is_skipped_and_its_neighbour_is_not(
    conflation_settings: Settings,
):
    """The whole of criterion 2 in one test.

    The slow client's writer is stuck inside its first send, so `busy` never clears and every
    later tick skips it. The healthy client alongside it receives one frame per tick — the
    number it would have received had the slow one never connected.
    """
    state = MarketState()
    hub = Hub()
    conflator = Conflator(hub=hub, state=state, settings=conflation_settings)

    healthy, stalled = Recorder(), Stalled()
    fast = _attach(hub, healthy, {"book:QAA:l2"}, user_id=1)
    slow = _attach(hub, stalled, {"book:QAA:l2"}, user_id=2)

    for tick in range(10):
        state.apply(_accepted(tick + 1, 10, 1, BUY, 100 + tick, 5), stream_id=f"1-{tick + 1}")
        conflator.tick()
        await _settle()

    assert len(healthy.frames) == 10, "the healthy client got every tick"
    assert stalled.started == 1, "the stalled client is still inside its first write"
    assert slow.skipped >= 8, "and was skipped for the rest"
    assert fast.skipped == 0


@pytest.mark.anyio
async def test_a_stalled_client_does_not_slow_the_tick(conflation_settings: Settings):
    """The other half of "affects no other": the tick itself must not wait for it.

    Sending is the only thing in this process that can block on a client, which is why it is
    the only thing given its own task. The assertion is that `tick()` returns having merely
    *offered*, with no await on any socket.
    """
    state = MarketState()
    hub = Hub()
    conflator = Conflator(hub=hub, state=state, settings=conflation_settings)
    for index in range(50):
        _attach(hub, Stalled(), {"book:QAA:l2"}, user_id=index)

    state.apply(_accepted(1, 10, 1, BUY, 100, 5), stream_id="1-1")
    conflator.tick()
    await _settle()
    state.dirty.add(1)
    conflator.tick()

    assert conflator.max_tick_seconds < 0.05, conflator.max_tick_seconds


# --- filtering, tape and bars --------------------------------------------------------------------


@pytest.mark.anyio
async def test_a_subscriber_receives_only_the_symbols_it_asked_for(
    conflation_settings: Settings,
):
    state = MarketState()
    hub = Hub()
    conflator = Conflator(hub=hub, state=state, settings=conflation_settings)
    recorder = Recorder()
    _attach(hub, recorder, {"book:QAA:l2"})

    state.apply(_accepted(1, 10, 1, BUY, 100, 5), stream_id="1-1")
    state.apply(_accepted(2, 10, 2, BUY, 200, 5), stream_id="1-2")
    conflator.tick()
    await _settle()

    assert len(recorder.frames) == 1
    assert '"ch":"book:QAA:l2"' in recorder.frames[0]


@pytest.mark.anyio
async def test_every_print_is_delivered_and_none_is_conflated(
    conflation_settings: Settings,
):
    """The tape is batched onto the tick, which is not the same as being conflated: nothing is
    superseded or dropped, the prints are only carried together. §3.3 — a dropped trade is a
    *wrong* tape, not a stale one."""
    state = MarketState()
    hub = Hub()
    conflator = Conflator(hub=hub, state=state, settings=conflation_settings)
    recorder = Recorder()
    _attach(hub, recorder, {"tape:QAA"})

    state.apply(_accepted(1, 10, 1, BUY, 100, 30), stream_id="1-1")
    for index in range(3):
        state.apply(_fill(1, 900 + index, 1, 100, 5), stream_id=f"1-{index + 2}")
    conflator.tick()
    await _settle()

    assert len(recorder.frames) == 3
    assert all('"ch":"tape:QAA"' in frame for frame in recorder.frames)


@pytest.mark.anyio
async def test_an_unsubscribed_symbol_does_not_accumulate_prints_forever(
    conflation_settings: Settings,
):
    """The tape is drained whether or not anybody is listening. Without that, a symbol nobody
    watches grows a list for the life of the process."""
    state = MarketState()
    hub = Hub()
    conflator = Conflator(hub=hub, state=state, settings=conflation_settings)

    state.apply(_accepted(1, 10, 1, BUY, 100, 30), stream_id="1-1")
    for index in range(3):
        state.apply(_fill(1, 900 + index, 1, 100, 5), stream_id=f"1-{index + 2}")
    conflator.tick()

    assert state.tape.pending(1) == []


# --- private data is never dropped ------------------------------------------------------------


@pytest.mark.anyio
async def test_private_messages_are_buffered_past_a_stall_not_skipped():
    """The asymmetry that shapes the whole system, at the last hop.

    A market frame for a busy client is skipped, because the next snapshot supersedes it. A
    private message is not: a lost fill is a user seeing wrong state, which no later message
    repairs. So it queues behind the stall instead of being dropped.
    """
    subscriber = Subscriber(user_id=1, send=Stalled())
    subscriber.start()

    subscriber.offer_private("first")
    subscriber.flush()
    await _settle()

    assert subscriber.busy
    for index in range(10):
        assert subscriber.offer_private(f"queued-{index}") is True
    assert subscriber.offer_market("a book snapshot") is False
    assert subscriber.skipped == 1
    await subscriber.stop()


@pytest.mark.anyio
async def test_a_private_buffer_that_overflows_signals_a_disconnect():
    """Private data is never dropped (Open Issue 006), so the only other option is to stop
    being that client's server. §3.6: `slow_consumer` precedes a server-initiated close, and
    the client re-synchronises over REST — which §3.5 already requires of it after a gap.

    An `Event` rather than a flag, because the connection handler is blocked reading from a
    socket that a stalled client is not writing to. It has to be woken, not polled.
    """
    subscriber = Subscriber(user_id=1, send=Stalled(), private_max=4)
    subscriber.start()
    subscriber.offer_private("first")
    subscriber.flush()
    await _settle()

    accepted = [subscriber.offer_private(f"m{i}") for i in range(6)]

    assert accepted[:4] == [True] * 4
    assert accepted[4:] == [False, False]
    assert subscriber.overflowed.is_set()
    await subscriber.stop()


@pytest.mark.anyio
async def test_private_messages_are_written_ahead_of_a_book_snapshot():
    """Both are owed; only one of them is still true in fifty milliseconds. A fill queued
    behind ten symbols' worth of depth would arrive after data that had already replaced it."""
    recorder = Recorder()
    subscriber = Subscriber(user_id=1, send=recorder)
    subscriber.start()

    subscriber.offer_market("book")
    subscriber.offer_private("fill")
    subscriber.flush()
    await _settle()

    assert recorder.frames == ["fill", "book"]
    await subscriber.stop()
