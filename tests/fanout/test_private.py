"""Task 5.2 Success Criterion 4 — a gap in the private stream is detectable.

The criterion has an implicit condition that is easy to miss: detectable *by the client that
ships*. A dense counter on the server proves nothing if the browser reads `seq` differently, and
`contracts/v1/rest_and_ws.md` §3.4 makes that a live risk — its example shows a Redis stream id
where §3.5 and Open Issue 006 §7c require a per-user counter.

So the last test here does not assert about Python. It builds real frames with `PrivateRouter`,
drops one, and runs them through `web/src/stream/gaps.ts` — the actual `SequenceTracker` the
browser uses. If the two sides ever disagree about what `seq` means, that test fails, which is
the only place in the repository where that disagreement is visible at all.
"""

from __future__ import annotations

import dataclasses
import json

import pytest

from config.settings import Settings, Symbol
from contracts.v1.generated.contracts import (
    AccountCreated,
    CancelReason,
    Fill,
    OrderAccepted,
    OrderCancelled,
    OrderRejected,
    RejectReason,
    Side,
    Tif,
)
from services.fanout.private import PrivateRouter
from services.fanout.subscribers import Hub, Subscriber

BUY, SELL = int(Side.BUY), int(Side.SELL)
QAA = Symbol(symbol_id=1, name="QAA", tick_size_ticks=1, lot_size=1)

MAKER, TAKER = 10, 11


@pytest.fixture
def private_settings(settings: Settings) -> Settings:
    return dataclasses.replace(settings, symbols=(QAA,))


class Recorder:
    def __init__(self) -> None:
        self.frames: list[str] = []

    async def __call__(self, frame: str) -> None:
        self.frames.append(frame)


def _attach(hub: Hub, user_id: int) -> tuple[Subscriber, Recorder]:
    recorder = Recorder()
    subscriber = Subscriber(user_id=user_id, send=recorder)
    hub.add(subscriber)
    return subscriber, recorder


def _fill(qty=5, price=100, aggressor=SELL):
    return Fill.new(
        timestamp_ns=7, maker_order_id=1, taker_order_id=2,
        maker_user_id=MAKER, taker_user_id=TAKER, price_ticks=price, qty=qty,
        symbol_id=1, aggressor_side=int(aggressor),
    )


def _accepted(order_id=1, user=MAKER):
    return OrderAccepted.new(
        timestamp_ns=6, order_id=order_id, client_order_id=order_id * 100, user_id=user,
        price_ticks=100, qty=5, symbol_id=1, side=BUY, tif=int(Tif.GTC),
    )


# --- shape ---------------------------------------------------------------------------------------


def test_a_fill_reaches_both_sides_with_the_role_each_played(private_settings: Settings):
    """§3.4: `role` is derived by fan-out from `aggressor_side` and which side this user was
    on, so the client never has to work out which fee it paid. The engine could not tell it —
    it is money-blind (Open Issue 001) and has no idea a fee exists."""
    hub = Hub()
    router = PrivateRouter(hub, private_settings)
    maker, maker_frames = _attach(hub, MAKER)
    taker, taker_frames = _attach(hub, TAKER)

    router.route(_fill(), stream_id="1-1")

    maker_msg = json.loads(maker._private[0])
    taker_msg = json.loads(taker._private[0])
    assert maker_msg["role"] == "maker" and taker_msg["role"] == "taker"
    assert maker_msg["type"] == "Fill" and maker_msg["ch"] == "private"
    assert maker_msg["symbol"] == "QAA"
    assert maker_msg["price_ticks"] == taker_msg["price_ticks"] == 100


def test_a_rejection_carries_a_null_order_id_rather_than_omitting_the_field(
    private_settings: Settings,
):
    """No `order_id` was assigned — the order never reached the book. It is sent as `null`
    because a field that sometimes vanishes has no fixed offset, and `messages.py` promises the
    schema stays binary-ready: fixed field order, no dynamic keys (Open Issue 006 §7e)."""
    hub = Hub()
    router = PrivateRouter(hub, private_settings)
    subscriber, _ = _attach(hub, MAKER)

    router.route(
        OrderRejected.new(
            timestamp_ns=1, client_order_id=99, user_id=MAKER, symbol_id=1,
            reason=int(RejectReason.INSUFFICIENT_CASH),
        ),
        stream_id="1-1",
    )

    message = json.loads(subscriber._private[0])
    assert "order_id" in message and message["order_id"] is None
    assert message["reason"] == int(RejectReason.INSUFFICIENT_CASH)


def test_nothing_is_built_for_a_user_with_no_connection(private_settings: Settings):
    """Most records on a busy stream belong to bots with no browser attached. Building a
    message for nobody is the one cost this loop can avoid entirely, so the membership test
    comes before the encode — and `Hub.serialisations` is how that is checked."""
    hub = Hub()
    router = PrivateRouter(hub, private_settings)

    for index in range(50):
        router.route(_fill(), stream_id=f"1-{index}")

    assert hub.serialisations == 0
    assert router.delivered == 0


def test_a_symbol_missing_from_the_configuration_is_null_not_an_error(
    private_settings: Settings,
):
    """The retained stream outlives a configuration change, so a replay can meet a symbol that
    has since been delisted. `null` is the honest answer and keeps the field's position."""
    hub = Hub()
    router = PrivateRouter(hub, private_settings)
    subscriber, _ = _attach(hub, MAKER)

    router.route(
        OrderAccepted.new(
            timestamp_ns=1, order_id=1, client_order_id=1, user_id=MAKER,
            price_ticks=1, qty=1, symbol_id=99, side=BUY, tif=int(Tif.GTC),
        ),
        stream_id="1-1",
    )

    assert json.loads(subscriber._private[0])["symbol"] is None


# --- Success Criterion 4 ---------------------------------------------------------------------------


def test_the_sequence_is_dense_per_user_and_not_the_stream_id(private_settings: Settings):
    """The decision, asserted.

    Stream ids count every record on the stream, most of which concern other users, so they are
    dense for nobody. A counter that skipped would make a gap indistinguishable from ordinary
    traffic, and the criterion unmeetable by any implementation.
    """
    hub = Hub()
    router = PrivateRouter(hub, private_settings)
    maker, _ = _attach(hub, MAKER)
    _attach(hub, TAKER)

    for index in range(5):
        # Interleaved with records belonging to somebody else, and with market-only records,
        # both of which advance the stream id and must not advance this user's counter.
        router.route(_accepted(order_id=index + 1, user=TAKER), stream_id=f"1-{index * 3 + 1}")
        router.route(_accepted(order_id=index + 1, user=MAKER), stream_id=f"1-{index * 3 + 2}")

    seqs = [int(json.loads(frame)["seq"]) for frame in maker._private]
    assert seqs == [1, 2, 3, 4, 5]


def test_two_connections_for_one_account_see_the_same_numbers(private_settings: Settings):
    """The counter is per user, not per connection: two tabs are one account. Each sees a dense
    run for as long as it is connected, which is why `StreamClient` resets its tracker on every
    open rather than comparing across a reconnect."""
    hub = Hub()
    router = PrivateRouter(hub, private_settings)
    first, _ = _attach(hub, MAKER)
    second, _ = _attach(hub, MAKER)

    router.route(_accepted(), stream_id="1-1")
    router.route(_accepted(order_id=2), stream_id="1-2")

    assert [json.loads(f)["seq"] for f in first._private] == ["1", "2"]
    assert [json.loads(f)["seq"] for f in second._private] == ["1", "2"]


def test_the_shipped_typescript_client_detects_a_dropped_private_message(
    private_settings: Settings, run_node_script
):
    """The criterion, end to end and across the language boundary.

    Real frames from `PrivateRouter`, one removed, fed to the real `SequenceTracker` from
    `web/src/stream/gaps.ts`. This is the only test that would fail if the two sides ever
    disagreed about what `seq` on `private` means — which is a live possibility while §3.4's
    example shows a stream id and §3.5 requires a dense counter.
    """
    hub = Hub()
    router = PrivateRouter(hub, private_settings)
    subscriber, _ = _attach(hub, MAKER)
    for index in range(6):
        router.route(_accepted(order_id=index + 1), stream_id=f"1-{index + 1}")

    frames = [json.loads(f) for f in subscriber._private]
    with_hole = frames[:2] + frames[3:]

    result = run_node_script(f"""
import {{ SequenceTracker }} from "./gaps.ts";
const complete = {json.dumps(frames)};
const holed = {json.dumps(with_hole)};
function verdicts(messages) {{
  const tracker = new SequenceTracker();
  const seen = messages.map((m) => tracker.observe(m.ch, m.seq));
  return {{ verdicts: seen, gaps: tracker.privateGaps }};
}}
console.log(JSON.stringify({{ complete: verdicts(complete), holed: verdicts(holed) }}));
""")

    assert result["complete"]["gaps"] == 0, "an unbroken run must not look like a loss"
    assert result["holed"]["gaps"] == 1
    assert "gap" in result["holed"]["verdicts"]


def test_account_creation_and_a_cash_credit_reach_the_user(private_settings: Settings):
    """§3.4 lists both. `AccountCreated` matters more than it looks: registration appends it to
    the stream and the response deliberately carries no `cash_ticks` (decided 2026-09-04), so
    this is how a browser learns its opening balance without asking PostgreSQL."""
    hub = Hub()
    router = PrivateRouter(hub, private_settings)
    subscriber, _ = _attach(hub, MAKER)

    router.route(
        AccountCreated.new(
            timestamp_ns=1, client_order_id=0, user_id=MAKER, initial_cash_ticks=1_000_000
        ),
        stream_id="1-1",
    )

    message = json.loads(subscriber._private[0])
    assert message["type"] == "AccountCreated"
    assert message["initial_cash_ticks"] == 1_000_000


def test_a_cancellation_names_what_left_the_book(private_settings: Settings):
    hub = Hub()
    router = PrivateRouter(hub, private_settings)
    subscriber, _ = _attach(hub, MAKER)

    router.route(
        OrderCancelled.new(
            timestamp_ns=1, order_id=4, client_order_id=44, user_id=MAKER,
            remaining_qty=3, symbol_id=1, reason=int(CancelReason.USER_REQUESTED),
        ),
        stream_id="1-1",
    )

    message = json.loads(subscriber._private[0])
    assert message["type"] == "OrderCancelled"
    assert message["remaining_qty"] == 3
