"""In-memory ledger and fee accounting.

The ledger derives cash and positions from the event stream, establishing the central property
the money design rests on: **the stream is authoritative and the relational database is a
derived read model, never a source of truth** (Open Issue 004).

Maker/taker fees in basis points (Open Issue 011 §11.2):
- Taker: 10 bps (0.10%) for removing liquidity.
- Maker: 2 bps (0.02%) for providing resting liquidity.
- Fees accumulate in the house fee account.

Invariant I10 (extended cash conservation):
`sum(user_cash) + house_fees == sum(initial_grants + credits)`
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from contracts.v1.generated.contracts import (
    AccountCreated,
    CashCredited,
    Fill,
    OrderAccepted,
    OrderCancelled,
    Side,
)

MAKER_FEE_BPS: int = 2
TAKER_FEE_BPS: int = 10
BPS_DENOMINATOR: int = 10_000


@dataclass
class OpenOrderRecord:
    order_id: int
    client_order_id: int
    user_id: int
    symbol_id: int
    side: int
    price_ticks: int
    qty: int
    remaining_qty: int
    tif: int
    created_at_ns: int


@dataclass(frozen=True)
class FeeBreakdown:
    notional: int
    maker_fee_ticks: int
    taker_fee_ticks: int
    total_fee_ticks: int


def calculate_fees(price_ticks: int, qty: int) -> FeeBreakdown:
    notional = price_ticks * qty
    maker_fee = (notional * MAKER_FEE_BPS) // BPS_DENOMINATOR
    taker_fee = (notional * TAKER_FEE_BPS) // BPS_DENOMINATOR
    return FeeBreakdown(
        notional=notional,
        maker_fee_ticks=maker_fee,
        taker_fee_ticks=taker_fee,
        total_fee_ticks=maker_fee + taker_fee,
    )


class Ledger:
    """Deterministic, in-memory ledger state derived solely from stream events."""

    def __init__(self) -> None:
        self.cash_balances: dict[int, int] = {}
        self.positions: dict[tuple[int, int], int] = {}  # (user_id, symbol_id) -> qty
        self.open_orders: dict[int, OpenOrderRecord] = {}  # order_id -> OpenOrderRecord
        self.house_fee_ticks: int = 0
        self.total_deposits_ticks: int = 0
        self.last_seq: str = "0-0"
        self.events_processed: int = 0
        # What `apply` touched since the consumer last took them. The read model is written
        # incrementally from these: rewriting every row after every batch was a full copy of
        # the open-order table per hundred records, and with a quarter of a million orders
        # resting the ledger fell hours behind the stream it projects.
        self.dirty_accounts: set[int] = set()
        self.dirty_positions: set[tuple[int, int]] = set()
        self.dirty_orders: set[int] = set()

    def take_dirty(self) -> tuple[set[int], set[tuple[int, int]], set[int]]:
        """Hand over and clear the keys changed since the previous call."""
        dirty = (self.dirty_accounts, self.dirty_positions, self.dirty_orders)
        self.dirty_accounts, self.dirty_positions, self.dirty_orders = set(), set(), set()
        return dirty

    def reset(self) -> None:
        self.cash_balances.clear()
        self.positions.clear()
        self.open_orders.clear()
        self.house_fee_ticks = 0
        self.total_deposits_ticks = 0
        self.last_seq = "0-0"
        self.events_processed = 0
        self.take_dirty()

    def apply(self, record: Any, stream_id: str | None = None) -> None:
        """Apply an event record to update balances, positions, or orders."""
        if stream_id is not None:
            self.last_seq = stream_id
        self.events_processed += 1

        if isinstance(record, AccountCreated):
            self.cash_balances[record.user_id] = record.initial_cash_ticks
            self.dirty_accounts.add(record.user_id)
            self.total_deposits_ticks += record.initial_cash_ticks

        elif isinstance(record, CashCredited):
            current = self.cash_balances.get(record.user_id, 0)
            self.cash_balances[record.user_id] = current + record.amount_ticks
            self.dirty_accounts.add(record.user_id)
            self.total_deposits_ticks += record.amount_ticks

        elif isinstance(record, OrderAccepted):
            self.dirty_orders.add(record.order_id)
            self.open_orders[record.order_id] = OpenOrderRecord(
                order_id=record.order_id,
                client_order_id=record.client_order_id,
                user_id=record.user_id,
                symbol_id=record.symbol_id,
                side=int(record.side),
                price_ticks=record.price_ticks,
                qty=record.qty,
                remaining_qty=record.qty,
                tif=int(record.tif),
                created_at_ns=record.timestamp_ns,
            )

        elif isinstance(record, OrderCancelled):
            self.dirty_orders.add(record.order_id)
            if record.order_id in self.open_orders:
                order = self.open_orders[record.order_id]
                order.remaining_qty -= record.remaining_qty
                if order.remaining_qty <= 0:
                    del self.open_orders[record.order_id]

        elif isinstance(record, Fill):
            fees = calculate_fees(record.price_ticks, record.qty)
            self.house_fee_ticks += fees.total_fee_ticks

            # aggressor_side indicates which side aggressed (is taker)
            if record.aggressor_side == Side.BUY or int(record.aggressor_side) == 1:
                # Buyer is taker, Seller is maker
                buyer_id = record.taker_user_id
                seller_id = record.maker_user_id
                buyer_cash_delta = -(fees.notional + fees.taker_fee_ticks)
                seller_cash_delta = fees.notional - fees.maker_fee_ticks
            else:
                # Seller is taker, Buyer is maker
                buyer_id = record.maker_user_id
                seller_id = record.taker_user_id
                buyer_cash_delta = -(fees.notional + fees.maker_fee_ticks)
                seller_cash_delta = fees.notional - fees.taker_fee_ticks

            # Apply cash changes
            self.cash_balances[buyer_id] = self.cash_balances.get(buyer_id, 0) + buyer_cash_delta
            self.cash_balances[seller_id] = self.cash_balances.get(seller_id, 0) + seller_cash_delta

            self.dirty_accounts.update((buyer_id, seller_id))
            self.dirty_orders.update((record.maker_order_id, record.taker_order_id))

            # Apply position changes
            buyer_key = (buyer_id, record.symbol_id)
            seller_key = (seller_id, record.symbol_id)
            self.positions[buyer_key] = self.positions.get(buyer_key, 0) + record.qty
            self.positions[seller_key] = self.positions.get(seller_key, 0) - record.qty
            self.dirty_positions.update((buyer_key, seller_key))

            # Update resting maker order quantity
            if record.maker_order_id in self.open_orders:
                maker_order = self.open_orders[record.maker_order_id]
                maker_order.remaining_qty -= record.qty
                if maker_order.remaining_qty <= 0:
                    del self.open_orders[record.maker_order_id]

            # Update taker order if it was tracked in open orders
            if record.taker_order_id in self.open_orders:
                taker_order = self.open_orders[record.taker_order_id]
                taker_order.remaining_qty -= record.qty
                if taker_order.remaining_qty <= 0:
                    del self.open_orders[record.taker_order_id]

    def get_balance(self, user_id: int) -> int:
        return self.cash_balances.get(user_id, 0)

    def get_position(self, user_id: int, symbol_id: int) -> int:
        return self.positions.get((user_id, symbol_id), 0)

    def get_user_positions(self, user_id: int) -> dict[int, int]:
        return {
            sym: qty
            for (uid, sym), qty in self.positions.items()
            if uid == user_id and qty != 0
        }

    def get_open_orders(self, user_id: int) -> list[OpenOrderRecord]:
        return [
            order for order in self.open_orders.values() if order.user_id == user_id
        ]

    def total_user_cash(self) -> int:
        return sum(self.cash_balances.values())

    def total_system_cash(self) -> int:
        return self.total_user_cash() + self.house_fee_ticks
