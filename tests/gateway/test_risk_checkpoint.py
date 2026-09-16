"""Open Issue 020: gateway risk state survives a checkpoint exactly, in-flight orders included."""

from __future__ import annotations

from contracts.v1.generated.contracts import (
    AccountCreated,
    CancelReason,
    Fill,
    OrderAccepted,
    OrderCancelled,
    Side,
    Tif,
)
from services.gateway.risk import RiskState

BUY, SELL = int(Side.BUY), int(Side.SELL)


def _accepted(order_id, client_order_id, user_id, side, price, qty):
    return OrderAccepted.new(
        timestamp_ns=order_id, order_id=order_id, client_order_id=client_order_id, user_id=user_id,
        symbol_id=1, side=side, price_ticks=price, qty=qty, tif=int(Tif.GTC),
    )


def _view(state: RiskState):
    return (
        state.settled_cash, {u: v for u, v in state.reserved.items() if v}, state.positions,
        {k: v for k, v in state.reserved_qty.items() if v},
        {i: (r.qty, r.price_ticks, r.side) for i, r in state.open_orders.items()},
    )


def test_checkpoint_with_in_flight_orders_restores_without_double_reserving():
    stream = [
        AccountCreated.new(timestamp_ns=1, client_order_id=1, user_id=1, initial_cash_ticks=1_000_000),
        AccountCreated.new(timestamp_ns=2, client_order_id=2, user_id=2, initial_cash_ticks=1_000_000),
        _accepted(10, 100, 1, BUY, 1_000, 5),
    ]
    tail = [
        _accepted(11, 200, 2, SELL, 1_000, 3),
        Fill.new(timestamp_ns=12, maker_order_id=10, taker_order_id=11, maker_user_id=1,
                 taker_user_id=2, price_ticks=1_000, qty=3, symbol_id=1, aggressor_side=SELL),
        _accepted(12, 101, 1, BUY, 900, 2),
        OrderCancelled.new(timestamp_ns=13, order_id=10, client_order_id=100, user_id=1,
                           symbol_id=1, remaining_qty=2, reason=int(CancelReason.USER_REQUESTED)),
    ]

    live = RiskState()
    live.positions[(2, 1)] = 10
    for i, record in enumerate(stream):
        live.apply(record, stream_id=f"{i + 1}-0")
    # Two orders submitted but not yet on the stream when the checkpoint is taken.
    live.reserve(user_id=2, client_order_id=200, symbol_id=1, side=SELL, price_ticks=1_000, qty=3)
    live.reserve(user_id=1, client_order_id=101, symbol_id=1, side=BUY, price_ticks=900, qty=2)
    saved = live.dump_state()

    for i, record in enumerate(tail):
        live.apply(record, stream_id=f"{len(stream) + i + 1}-0")

    restored = RiskState()
    restored.load_state(saved)
    for i, record in enumerate(tail):
        restored.apply(record, stream_id=f"{len(stream) + i + 1}-0")

    assert _view(restored) == _view(live)
    assert restored.last_seq == live.last_seq
