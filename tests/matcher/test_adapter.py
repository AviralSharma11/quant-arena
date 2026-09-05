"""The matching half of the integration point, with no Redis in the way.

`NaiveMatcher` is pure, so everything about *what* the naive model produces is asserted here;
`test_integration.py` covers the stream that carries it.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from contracts.v1.generated.contracts import (  # noqa: E402
    CancelOrder,
    CancelReason,
    Fill,
    OrderAccepted,
    OrderCancelled,
    OrderRejected,
    RejectReason,
    Side,
    SubmitOrder,
    Tif,
)
from services.matcher.adapter import NaiveMatcher  # noqa: E402

BUY, SELL = int(Side.BUY), int(Side.SELL)
GTC, IOC = int(Tif.GTC), int(Tif.IOC)


def submit(
    *, coid: int, user: int, side: int, price: int, qty: int, symbol: int = 1,
    tif: int = GTC, ts: int = 1,
) -> SubmitOrder:
    return SubmitOrder.new(
        timestamp_ns=ts, client_order_id=coid, user_id=user, symbol_id=symbol,
        side=side, tif=tif, price_ticks=price, qty=qty,
    )


def test_a_resting_order_is_accepted_and_does_not_trade():
    m = NaiveMatcher()
    out = m.apply(submit(coid=1, user=10, side=BUY, price=100, qty=5))

    assert len(out) == 1
    assert isinstance(out[0], OrderAccepted)
    assert out[0].order_id == 1
    assert out[0].client_order_id == 1
    # The engine never reads a clock: the outbound record echoes the inbound timestamp.
    assert out[0].timestamp_ns == 1


def test_two_crossing_orders_produce_exactly_one_fill():
    """Criterion 1 of the integration point."""
    m = NaiveMatcher()
    m.apply(submit(coid=1, user=10, side=BUY, price=100, qty=5, ts=1))
    out = m.apply(submit(coid=2, user=20, side=SELL, price=100, qty=5, ts=2))

    accepted, fill = out
    assert isinstance(accepted, OrderAccepted)
    assert isinstance(fill, Fill)
    assert fill.maker_order_id == 1 and fill.taker_order_id == 2
    assert fill.maker_user_id == 10 and fill.taker_user_id == 20
    assert fill.qty == 5
    assert int(fill.aggressor_side) == SELL
    # Both sides fully filled, so nothing is left resting.
    assert m.resting() == []


def test_the_fill_prints_the_resting_price_when_the_seller_aggresses():
    """The frozen schema: `Fill.price_ticks` is *always* the resting (maker) price.

    The seller's lower limit must not replace the higher price of the resting bid.
    """
    m = NaiveMatcher()
    m.apply(submit(coid=1, user=10, side=BUY, price=100, qty=5, ts=1))
    # A sell willing to go down to 90, hitting a bid resting at 100. The maker's price wins.
    _, fill = m.apply(submit(coid=2, user=20, side=SELL, price=90, qty=5, ts=2))

    assert fill.price_ticks == 100


def test_price_time_priority_across_two_makers_at_one_price():
    m = NaiveMatcher()
    m.apply(submit(coid=1, user=10, side=BUY, price=100, qty=5, ts=1))
    m.apply(submit(coid=2, user=11, side=BUY, price=100, qty=7, ts=2))
    out = m.apply(submit(coid=3, user=20, side=SELL, price=100, qty=8, ts=3))

    fills = [r for r in out if isinstance(r, Fill)]
    assert [f.maker_order_id for f in fills] == [1, 2]
    assert [f.qty for f in fills] == [5, 3]
    # Order 2 keeps its place in the queue with the untraded remainder.
    assert m.resting() == [(2, 1, BUY, 100, 4)]


def test_two_symbols_never_cross():
    """The naive model's book crosses on price alone, so a book is held per symbol."""
    m = NaiveMatcher()
    m.apply(submit(coid=1, user=10, side=BUY, price=100, qty=5, symbol=3))
    out = m.apply(submit(coid=2, user=20, side=SELL, price=100, qty=5, symbol=7))

    assert not [r for r in out if isinstance(r, Fill)]
    assert m.resting() == [(1, 3, BUY, 100, 5), (2, 7, SELL, 100, 5)]


def test_an_ioc_remainder_is_cancelled_and_never_rests():
    m = NaiveMatcher()
    m.apply(submit(coid=1, user=10, side=BUY, price=100, qty=3, ts=1))
    out = m.apply(submit(coid=2, user=20, side=SELL, price=100, qty=10, tif=IOC, ts=2))

    accepted, fill, cancelled = out
    assert isinstance(accepted, OrderAccepted)
    assert fill.qty == 3
    assert isinstance(cancelled, OrderCancelled)
    assert cancelled.remaining_qty == 7
    assert int(cancelled.reason) == int(CancelReason.IOC_EXPIRED)
    assert m.resting() == []


def test_cancel_removes_a_resting_order():
    """Criterion 4 of the integration point."""
    m = NaiveMatcher()
    m.apply(submit(coid=1, user=10, side=BUY, price=100, qty=5))
    out = m.apply(
        CancelOrder.new(
            timestamp_ns=9, client_order_id=2, user_id=10, target_client_order_id=1
        )
    )

    assert len(out) == 1
    cancelled = out[0]
    assert isinstance(cancelled, OrderCancelled)
    assert cancelled.order_id == 1
    assert cancelled.remaining_qty == 5
    assert int(cancelled.reason) == int(CancelReason.USER_REQUESTED)
    assert m.resting() == []


def test_cancelling_another_users_order_is_unknown_not_a_leak():
    m = NaiveMatcher()
    m.apply(submit(coid=1, user=10, side=BUY, price=100, qty=5))
    out = m.apply(
        CancelOrder.new(
            timestamp_ns=9, client_order_id=1, user_id=99, target_client_order_id=1
        )
    )

    assert isinstance(out[0], OrderRejected)
    assert int(out[0].reason) == int(RejectReason.UNKNOWN_ORDER)
    # And the real owner's order is untouched.
    assert m.resting() == [(1, 1, BUY, 100, 5)]


def test_a_cancel_after_a_full_fill_is_unknown():
    """The cancel lost the race. Nothing is left to remove, and nothing is invented."""
    m = NaiveMatcher()
    m.apply(submit(coid=1, user=10, side=BUY, price=100, qty=5, ts=1))
    m.apply(submit(coid=2, user=20, side=SELL, price=100, qty=5, ts=2))
    out = m.apply(
        CancelOrder.new(
            timestamp_ns=3, client_order_id=3, user_id=10, target_client_order_id=1
        )
    )

    assert isinstance(out[0], OrderRejected)
    assert int(out[0].reason) == int(RejectReason.UNKNOWN_ORDER)


def test_bad_bounds_are_rejected_and_consume_no_order_id():
    m = NaiveMatcher()
    bad = [
        (submit(coid=1, user=10, side=9, price=100, qty=5), RejectReason.INVALID_SIDE),
        (submit(coid=2, user=10, side=BUY, price=100, qty=5, tif=9), RejectReason.INVALID_TIF),
        (submit(coid=3, user=10, side=BUY, price=0, qty=5), RejectReason.INVALID_PRICE),
        (submit(coid=4, user=10, side=BUY, price=100, qty=0), RejectReason.INVALID_QUANTITY),
    ]
    for record, expected in bad:
        out = m.apply(record)
        assert isinstance(out[0], OrderRejected), record
        assert int(out[0].reason) == int(expected)

    # A rejected order never reached the book, so it was never assigned an id.
    assert m.next_order_id == 1


def test_order_ids_are_monotonic_and_engine_assigned():
    m = NaiveMatcher()
    ids = [
        m.apply(submit(coid=i, user=10, side=BUY, price=100 - i, qty=1))[0].order_id
        for i in range(1, 6)
    ]
    assert ids == [1, 2, 3, 4, 5]


def test_the_same_inbound_sequence_produces_identical_output_twice():
    """Determinism — the property the whole replay story rests on."""
    sequence = [
        submit(coid=1, user=10, side=BUY, price=100, qty=5, ts=1),
        submit(coid=2, user=11, side=BUY, price=101, qty=3, ts=2),
        submit(coid=3, user=20, side=SELL, price=99, qty=6, ts=3),
        CancelOrder.new(timestamp_ns=4, client_order_id=4, user_id=10, target_client_order_id=1),
    ]
    a, b = NaiveMatcher(), NaiveMatcher()
    first = [o for r in sequence for o in a.apply(r)]
    second = [o for r in sequence for o in b.apply(r)]

    assert first == second
    assert a.resting() == b.resting()


def test_money_records_cross_the_engine_untouched_and_come_out_sequenced():
    """`CreateAccount` is *"forwarded by the engine untouched — the engine is money-blind"*.

    Forwarding is not bookkeeping. What the pass adds is a position in the total order, which
    only the engine can supply; the grant itself comes from the shared configuration, since
    `CreateAccount` carries no amount.
    """
    from contracts.v1.generated.contracts import (
        AccountCreated,
        CashCredited,
        CreateAccount,
        CreditCash,
    )

    m = NaiveMatcher(initial_cash_ticks=1_000_000)

    created = m.apply(
        CreateAccount.new(timestamp_ns=5, client_order_id=1, user_id=77)
    )
    assert len(created) == 1
    assert isinstance(created[0], AccountCreated)
    assert created[0].user_id == 77
    assert created[0].initial_cash_ticks == 1_000_000
    assert created[0].timestamp_ns == 5  # echoed, never re-read from a clock

    credited = m.apply(
        CreditCash.new(timestamp_ns=6, client_order_id=2, user_id=77, amount_ticks=250)
    )
    assert isinstance(credited[0], CashCredited)
    assert credited[0].amount_ticks == 250

    # And no order id was consumed: neither record is an order.
    assert m.next_order_id == 1
