"""A stand-in for the matching engine, for week 1 only.

Dev A is building the naive model and the C++ engine in parallel, so the gateway is built
against this instead and swapped at the end-of-week-2 integration point (Appendix D.2, and the
Dependencies line of Task 1.3).

It accepts orders and assigns order ids. It does **not** match, and emits no fills — there is
nowhere to put a fill until the stream exists in week 2 and the private feed in week 5.

Two behaviours are copied from the real engine on purpose, because they are the ones the
gateway could accidentally come to depend on:

- **It never reads a clock.** Outbound records echo the inbound `timestamp_ns`. The real engine
  is deterministic and clock-free (Open Issue 001), so a gateway that assumed outbound records
  carried a fresh timestamp would break on substitution.
- **Order ids are monotonic uint64, engine-assigned** (Open Issue 008 sub-decision 9a).
"""

from __future__ import annotations

from dataclasses import dataclass

from contracts.v1.generated.contracts import (
    CancelOrder,
    CancelReason,
    OrderAccepted,
    OrderCancelled,
    OrderRejected,
    RejectReason,
    SubmitOrder,
)


@dataclass
class _LiveOrder:
    order_id: int
    symbol_id: int
    qty: int


class StubEngine:
    """Single-threaded and in-memory, like the thing it stands in for."""

    def __init__(self) -> None:
        self._next_order_id = 1
        self._live: dict[tuple[int, int], _LiveOrder] = {}

    def submit(self, order: SubmitOrder) -> OrderAccepted | OrderRejected:
        order_id = self._next_order_id
        self._next_order_id += 1
        self._live[(order.user_id, order.client_order_id)] = _LiveOrder(
            order_id=order_id, symbol_id=order.symbol_id, qty=order.qty
        )
        return OrderAccepted.new(
            timestamp_ns=order.timestamp_ns,
            order_id=order_id,
            client_order_id=order.client_order_id,
            user_id=order.user_id,
            price_ticks=order.price_ticks,
            qty=order.qty,
            symbol_id=order.symbol_id,
            side=order.side,
            tif=order.tif,
        )

    def cancel(self, cancel: CancelOrder) -> OrderCancelled | OrderRejected:
        key = (cancel.user_id, cancel.target_client_order_id)
        live = self._live.pop(key, None)
        if live is None:
            # An order this user does not have. NOT_ORDER_OWNER is deliberately not used here:
            # the stub cannot distinguish "never existed" from "belongs to someone else", and
            # saying UNKNOWN_ORDER for both leaks nothing about other users' order ids.
            return OrderRejected.new(
                timestamp_ns=cancel.timestamp_ns,
                client_order_id=cancel.client_order_id,
                user_id=cancel.user_id,
                symbol_id=0,
                reason=RejectReason.UNKNOWN_ORDER,
            )
        return OrderCancelled.new(
            timestamp_ns=cancel.timestamp_ns,
            order_id=live.order_id,
            client_order_id=cancel.target_client_order_id,
            user_id=cancel.user_id,
            remaining_qty=live.qty,
            symbol_id=live.symbol_id,
            reason=CancelReason.USER_REQUESTED,
        )
