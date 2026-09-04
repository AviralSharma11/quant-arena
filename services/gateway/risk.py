"""Risk checks and reservation accounting, in gateway process memory.

A buy and a sell consume **different resources**, and the check has to match:

|  | Buy | Sell |
|---|---|---|
| Consumes | cash | inventory |
| Reserved at submit | `limit x qty` ticks | `qty` units |
| Released on fill | at the **limit**, not the fill price | `qty` units |
| Released on cancel | the remaining `limit x qty` | the remaining `qty` |
| Refused with | `INSUFFICIENT_CASH` | `INSUFFICIENT_POSITION` |

Until week 4 only the left column existed and it was applied to both sides, so a sell froze
cash it did not need and never released it — two sells of half the grant each exhausted an
account that had spent nothing — while nothing anywhere checked whether the seller owned what
it was selling. `RejectReason.INSUFFICIENT_POSITION` had been in the frozen contract since
Task 1.1 and was raised by nothing.

## Designated market makers

Open Issue 004 section 5, invariant 2 is `position >= 0` for every user and symbol: no short
selling in Phase 1, because shorts need margin and margin needs a real risk engine.

That collides with the market maker, and Open Issue 005 section 5e is where the collision was
recorded: a quoter that cannot sell what it does not hold runs out of inventory, its ask side
disappears, and the book goes one-sided — which is the exact failure the market maker exists to
prevent. Section 10.6 confirms the resolution: **retail accounts are cash accounts and may
never go negative; designated market makers may, in exchange for quoting obligations.** Real
exchanges are built this way, so it is the real structure rather than a shortcut.

The privilege is a set of user ids resolved from `bots.designated_market_maker_accounts` in the
shared configuration. Exactly one branch reads it — `reject_reason_for` skips the inventory
check — and that single branch is the whole of the exemption.

The invariant therefore restates rather than relaxes: **`position >= 0` for every *retail*
account and symbol.**
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from contracts.v1.generated.contracts import (
    AccountCreated,
    CashCredited,
    Fill,
    OrderAccepted,
    OrderCancelled,
    RejectReason,
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
        #: Cash committed to resting buys, by user. Buys only — a sell costs no cash.
        self.reserved: dict[int, int] = {}
        #: Settled inventory, by (user_id, symbol_id). Negative only for a market maker.
        self.positions: dict[tuple[int, int], int] = {}
        #: Units committed to resting sells, by (user_id, symbol_id). The inventory mirror of
        #: `reserved`: without it an account could offer the same ten units on ten orders.
        self.reserved_qty: dict[tuple[int, int], int] = {}
        self.pending_by_client: dict[tuple[int, int], Reservation] = {}
        self.open_orders: dict[int, Reservation] = {}
        #: User ids permitted to hold negative inventory. Resolved from configuration, never
        #: from a request — see the module docstring.
        self.market_makers: set[int] = set()
        self.last_seq: str = "0-0"

    @classmethod
    async def from_db(cls, session, settings=None) -> "RiskState":
        state = cls()
        from services.gateway.models import Account, User
        from sqlmodel import select

        result = await session.exec(select(Account))
        for account in result.all():
            state.settled_cash[account.user_id] = account.cash_ticks

        if settings is not None and settings.designated_market_maker_accounts:
            # Names to ids, once at startup. Resolving per order would put a database read on
            # the hot path for a fact that only changes when someone registers.
            users = await session.exec(
                select(User).where(
                    User.username.in_(settings.designated_market_maker_accounts)
                )
            )
            state.market_makers = {user.id for user in users.all()}
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

    def position(self, user_id: int, symbol_id: int) -> int:
        """Settled inventory, ignoring what is already committed to resting sells."""
        return self.positions.get((user_id, symbol_id), 0)

    def available_position(self, user_id: int, symbol_id: int) -> int:
        """What is left to sell. The inventory analogue of `available_cash`."""
        key = (user_id, symbol_id)
        return self.positions.get(key, 0) - self.reserved_qty.get(key, 0)

    def is_market_maker(self, user_id: int) -> bool:
        return user_id in self.market_makers

    def reject_reason_for(
        self, *, user_id: int, symbol_id: int, side: int, price_ticks: int, qty: int
    ) -> RejectReason | None:
        """The one place the buy/sell asymmetry is decided. None means the order may proceed.

        Kept here rather than in the route so that the rule and the accounting that implements
        it cannot drift apart — a check in the route against state this class owns is a second
        opinion waiting to disagree.
        """
        if side == int(Side.BUY):
            if self.available_cash(user_id) < price_ticks * qty:
                return RejectReason.INSUFFICIENT_CASH
            return None

        # A sell costs no cash. It costs inventory — and a designated market maker is the one
        # kind of account allowed to run out of it (Open Issue 005 section 10.6).
        if self.is_market_maker(user_id):
            return None
        if self.available_position(user_id, symbol_id) < qty:
            return RejectReason.INSUFFICIENT_POSITION
        return None

    def reserve(self, *, user_id: int, client_order_id: int, symbol_id: int, side: int, price_ticks: int, qty: int) -> None:
        reason = self.reject_reason_for(
            user_id=user_id, symbol_id=symbol_id, side=side,
            price_ticks=price_ticks, qty=qty,
        )
        if reason is not None:
            raise ValueError(reason.name)

        if side == int(Side.BUY):
            self.reserved[user_id] = (
                self.reserved.get(user_id, 0) + price_ticks * qty
            )
        else:
            # Reserved for a market maker too, even though nothing checks it. The release
            # paths stay symmetric, and `available_position` keeps reporting a figure that
            # means the same thing for every account.
            key = (user_id, symbol_id)
            self.reserved_qty[key] = self.reserved_qty.get(key, 0) + qty

        self.pending_by_client[(user_id, client_order_id)] = Reservation(
            user_id=user_id,
            order_id=None,
            client_order_id=client_order_id,
            symbol_id=symbol_id,
            side=side,
            price_ticks=price_ticks,
            qty=qty,
        )

    def release(self, reservation: Reservation, qty: int) -> None:
        """Give back what `qty` of an order had committed. Buys hold cash, sells hold units."""
        if reservation.side == int(Side.BUY):
            self.reserved[reservation.user_id] = max(
                0,
                self.reserved.get(reservation.user_id, 0)
                # At the limit price the reservation was taken at, never the fill price.
                # Releasing at the fill strands the price improvement for the life of the
                # process (Task 3.1, Success Criterion 2).
                - reservation.price_ticks * qty,
            )
            return
        key = (reservation.user_id, reservation.symbol_id)
        self.reserved_qty[key] = max(0, self.reserved_qty.get(key, 0) - qty)

    def _release_remaining(self, order_id: int) -> None:
        """Retire an order and give back everything it still had committed."""
        reservation = self.open_orders.pop(order_id, None)
        if reservation is None:
            return
        self.release(reservation, reservation.qty)

    def _release_fill(self, order_id: int, *, qty: int) -> None:
        """Give back what the filled quantity had committed, and retire an exhausted order.

        Both sides matter here. A buy releases cash; a sell releases units. And either way the
        remaining quantity has to come down and the order be retired when it is exhausted —
        otherwise filled orders accumulate in `open_orders` forever and every reader of the
        resting book, `best_ask` and `best_bid` included, sees liquidity that is no longer
        there.
        """
        reservation = self.open_orders.get(order_id)
        if reservation is None:
            return
        self.release(reservation, qty)
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
                # Re-establish the commitment, in whichever resource the side consumes.
                # Without this a restart forgets every one of them and the account can
                # promise the same ticks — or the same units — twice.
                if reservation.side == int(Side.BUY):
                    self.reserved[record.user_id] = self.reserved.get(
                        record.user_id, 0
                    ) + record.price_ticks * record.qty
                else:
                    key = (record.user_id, record.symbol_id)
                    self.reserved_qty[key] = (
                        self.reserved_qty.get(key, 0) + record.qty
                    )
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

            # Inventory moves with the cash. The gateway has to keep its own copy: the
            # ledger's positions live in another process behind PostgreSQL, and the next
            # order's check cannot wait on a read model to catch up.
            buyer_key = (buyer_id, record.symbol_id)
            seller_key = (seller_id, record.symbol_id)
            self.positions[buyer_key] = self.positions.get(buyer_key, 0) + record.qty
            self.positions[seller_key] = self.positions.get(seller_key, 0) - record.qty

            self._release_fill(record.maker_order_id, qty=record.qty)
            self._release_fill(record.taker_order_id, qty=record.qty)
            return

        if isinstance(record, OrderCancelled):
            # Whatever is left goes back — cash for a buy, units for a sell. A cancelled sell
            # returned nothing before week 4, so an account that quoted and cancelled all day
            # slowly lost the ability to quote at all.
            self._release_remaining(record.order_id)
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
        """Tail the outbound stream forever, and **survive Redis going away**.

        Without the guard below this loop was the one long-running loop in the system with no
        exception handling — `StreamProducer._run`, `watch_health`, `Matcher.run` and
        `LedgerConsumer.run` all have one. When Redis stopped, `read_records` raised, the
        exception left the coroutine, and the task ended for good.

        Nothing announced it. `/health` recovered, because the halt watchdog is a different
        task and it survived; the gateway went on answering requests; and the risk state was
        permanently frozen at the moment Redis died — no grants, no fills, no reservation
        released — until someone restarted the process. Task 2.1's fifth criterion is that a
        Redis restart needs no gateway restart, and the halt cleared exactly as promised while
        the gateway was left blind behind it.

        A `stop` event still ends the loop, and cancellation still propagates. Only the
        transient failure is absorbed.
        """
        last_id = self.last_seq
        while stop is None or not stop.is_set():
            try:
                batch = await read_records(
                    redis, stream_name, last_id=last_id, count=100, block_ms=poll_ms
                )
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — a dead watcher is worse than a slow one
                # `last_id` is deliberately not advanced, so whatever was missed while Redis
                # was away is read when it returns. The halt state is `watch_health`'s to
                # report; this loop's only job is to still be here afterwards.
                await asyncio.sleep(max(0.01, poll_ms / 1000))
                continue

            if batch:
                for item in batch:
                    self.apply(item.record, stream_id=item.stream_id)
                last_id = batch[-1].stream_id
                self.last_seq = last_id
            else:
                await asyncio.sleep(max(0.01, poll_ms / 1000))
