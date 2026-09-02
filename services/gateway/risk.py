from __future__ import annotations

import asyncio
from dataclasses import dataclass

from contracts.v1.generated.contracts import (
    AccountCreated,
    CashCredited,
    Fill,
    OrderAccepted,
    OrderCancelled,
    Side,
)
from services.gateway.streams import read_records
from services.ledger.ledger import calculate_fees


@dataclass
class Reservation:
    user_id: int
    order_id: int | None
    client_order_id: int
    symbol_id: int
    side: int
    price_ticks: int
    qty: int


class RiskState:
    """Gateway-owned state for risk checks and reservation accounting."""

    def __init__(self) -> None:
        self.settled_cash: dict[int, int] = {}
        self.reserved: dict[int, int] = {}
        self.pending_by_client: dict[tuple[int, int], Reservation] = {}
        self.open_orders: dict[int, Reservation] = {}
        self.last_seq: str = "0-0"

    @classmethod
    async def from_db(cls, session) -> "RiskState":
        state = cls()
        from services.gateway.models import Account
        from sqlmodel import select

        result = await session.exec(select(Account))
        for account in result.all():
            state.settled_cash[account.user_id] = account.cash_ticks
        return state

    def best_ask(self, symbol_id: int) -> int | None:
        """Lowest resting sell for a symbol, or None when nothing is offered.

        Derived, not stored. `apply(OrderAccepted)` already records every resting order on the
        outbound stream regardless of whose it is, so the top of book is a query over state
        the gateway is keeping anyway — no second book, and nothing new to keep in step.
        """
        prices = [
            o.price_ticks
            for o in self.open_orders.values()
            if o.symbol_id == symbol_id and o.side == int(Side.SELL) and o.qty > 0
        ]
        return min(prices) if prices else None

    def best_bid(self, symbol_id: int) -> int | None:
        """Highest resting buy for a symbol, or None when there is no bid."""
        prices = [
            o.price_ticks
            for o in self.open_orders.values()
            if o.symbol_id == symbol_id and o.side == int(Side.BUY) and o.qty > 0
        ]
        return max(prices) if prices else None

    def banded_market_price(
        self, *, symbol_id: int, side: int, band_bps: int
    ) -> int | None:
        """The limit price a market order becomes, or None if the book cannot support one.

        Task 3.1: market orders are **marketable limit orders with a price band** — a market
        buy becomes a limit buy at `best_ask x 1.05`. The band is what stops a market order
        sweeping a thin book to an absurd price; without it there is nothing to stop it at all.

        Returning None is deliberate and means "there is no opposing side": a market order
        against an empty book has no reference price, and inventing one would be the exact
        failure the band exists to prevent.
        """
        if side == int(Side.BUY):
            reference = self.best_ask(symbol_id)
            if reference is None:
                return None
            # Round up, so the band is never narrower than configured by integer truncation.
            return (reference * (10_000 + band_bps) + 9_999) // 10_000
        reference = self.best_bid(symbol_id)
        if reference is None:
            return None
        # A market sell floors at best_bid x (1 - band); never below one tick.
        return max(1, (reference * (10_000 - band_bps)) // 10_000)

    def available_cash(self, user_id: int) -> int:
        return self.settled_cash.get(user_id, 0) - self.reserved.get(user_id, 0)

    def reserve(self, *, user_id: int, client_order_id: int, symbol_id: int, side: int, price_ticks: int, qty: int) -> None:
        reservation_cost = price_ticks * qty
        if self.available_cash(user_id) < reservation_cost:
            raise ValueError("INSUFFICIENT_CASH")
        self.reserved[user_id] = self.reserved.get(user_id, 0) + reservation_cost
        self.pending_by_client[(user_id, client_order_id)] = Reservation(
            user_id=user_id,
            order_id=None,
            client_order_id=client_order_id,
            symbol_id=symbol_id,
            side=side,
            price_ticks=price_ticks,
            qty=qty,
        )

    def _release_remaining(self, order_id: int) -> None:
        reservation = self.open_orders.get(order_id)
        if reservation is None or reservation.side != int(Side.BUY):
            return
        remaining_ticks = reservation.price_ticks * reservation.qty
        self.reserved[reservation.user_id] = max(
            0,
            self.reserved.get(reservation.user_id, 0) - remaining_ticks,
        )
        self.open_orders.pop(order_id, None)

    def _release_fill(self, order_id: int, *, user_id: int, qty: int, price_ticks: int) -> None:
        """Release what was reserved for the filled quantity — at the limit, not the fill.

        The reservation was taken at `limit x qty`, because that is the most the order could
        ever cost. Releasing `fill_price x qty` instead leaves the price improvement reserved
        forever: an account that keeps getting filled better than its limit quietly loses
        buying power it still owns, with nothing in the log to say why.

        Success Criterion 2 states the rule directly — reserve at the limit, settle at the
        fill. `price_ticks` (the fill price) is therefore deliberately unused for the release;
        settlement at that price is handled by the caller's cash movement.
        """
        reservation = self.open_orders.get(order_id)
        if reservation is None:
            return
        # Only a buy ties up cash, so only a buy releases any. A sell still has to have its
        # remaining quantity reduced and be retired when it is exhausted — otherwise filled
        # sells accumulate in `open_orders` forever and every reader of the resting book,
        # `best_ask` included, sees liquidity that is no longer there.
        if reservation.side == int(Side.BUY):
            release_ticks = reservation.price_ticks * qty
            self.reserved[user_id] = max(0, self.reserved.get(user_id, 0) - release_ticks)
        reservation.qty = max(0, reservation.qty - qty)
        if reservation.qty == 0:
            self.open_orders.pop(order_id, None)

    def apply(self, record, *, stream_id: str | None = None) -> None:
        if stream_id is not None:
            self.last_seq = stream_id

        if isinstance(record, AccountCreated):
            self.settled_cash[record.user_id] = record.initial_cash_ticks
            return

        if isinstance(record, CashCredited):
            self.settled_cash[record.user_id] = self.settled_cash.get(record.user_id, 0) + record.amount_ticks
            return

        if isinstance(record, OrderAccepted):
            pending = self.pending_by_client.pop((record.user_id, record.client_order_id), None)
            if pending is None:
                # No pending reservation means this record is being replayed rather than
                # observed live — a restarted gateway rebuilding from the stream. The cash was
                # reserved by the process that submitted it and that memory is gone, so the
                # reservation has to be re-established here. Without this, a restart forgets
                # every commitment and the account can spend the same ticks twice.
                reservation = Reservation(
                    user_id=record.user_id,
                    order_id=record.order_id,
                    client_order_id=record.client_order_id,
                    symbol_id=record.symbol_id,
                    side=int(record.side),
                    price_ticks=record.price_ticks,
                    qty=record.qty,
                )
                if reservation.side == int(Side.BUY):
                    self.reserved[record.user_id] = self.reserved.get(
                        record.user_id, 0
                    ) + record.price_ticks * record.qty
            else:
                reservation = pending
            reservation.order_id = record.order_id
            self.open_orders[record.order_id] = reservation
            return

        if isinstance(record, Fill):
            fees = calculate_fees(record.price_ticks, record.qty)
            if record.aggressor_side == int(Side.BUY):
                buyer_id = record.taker_user_id
                seller_id = record.maker_user_id
                buyer_delta = -(fees.notional + fees.taker_fee_ticks)
                seller_delta = fees.notional - fees.maker_fee_ticks
            else:
                buyer_id = record.maker_user_id
                seller_id = record.taker_user_id
                buyer_delta = -(fees.notional + fees.maker_fee_ticks)
                seller_delta = fees.notional - fees.taker_fee_ticks

            self.settled_cash[buyer_id] = self.settled_cash.get(buyer_id, 0) + buyer_delta
            self.settled_cash[seller_id] = self.settled_cash.get(seller_id, 0) + seller_delta

            self._release_fill(record.maker_order_id, user_id=record.maker_user_id, qty=record.qty, price_ticks=record.price_ticks)
            self._release_fill(record.taker_order_id, user_id=record.taker_user_id, qty=record.qty, price_ticks=record.price_ticks)
            return

        if isinstance(record, OrderCancelled):
            order = self.open_orders.get(record.order_id)
            if order is None:
                return
            if order.side == int(Side.BUY):
                self.reserved[order.user_id] = max(
                    0,
                    self.reserved.get(order.user_id, 0) - (order.price_ticks * order.qty),
                )
            self.open_orders.pop(record.order_id, None)
            return

    async def replay_from_stream(self, redis, stream_name: str, *, count: int = 100) -> int:
        last_id = self.last_seq
        replayed = 0
        while True:
            batch = await read_records(redis, stream_name, last_id=last_id, count=count, block_ms=20)
            if not batch:
                break
            for item in batch:
                self.apply(item.record, stream_id=item.stream_id)
            replayed += len(batch)
            last_id = batch[-1].stream_id
        self.last_seq = last_id
        return replayed

    async def watch_stream(self, redis, stream_name: str, *, poll_ms: int, stop: asyncio.Event | None = None) -> None:
        last_id = self.last_seq
        while stop is None or not stop.is_set():
            batch = await read_records(redis, stream_name, last_id=last_id, count=100, block_ms=poll_ms)
            if batch:
                for item in batch:
                    self.apply(item.record, stream_id=item.stream_id)
                last_id = batch[-1].stream_id
                self.last_seq = last_id
            else:
                await asyncio.sleep(max(0.01, poll_ms / 1000))
