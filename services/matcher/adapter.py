"""Contract records in, contract records out — with Dev A's naive model in the middle.

Task 1.2's `engine.naive_model` is the executable specification and the differential-test
oracle (Open Issue 001, 010). It is **permanent**, and it is Dev A's file. So it is wrapped,
never edited: everything that reconciles it with frozen contracts v1 lives here, on Dev B's
side of the line.

Three reconciliations are needed, and each is a real difference rather than a naming quibble:

1. **Vocabulary.** The model speaks `symbol: str`, `price`, `quantity`; the contract speaks
   `symbol_id: i16`, `price_ticks`, `qty`. Translation is mechanical.

2. **One book per symbol.** `OrderBook.match()` crosses on price alone — `best_bid` is
   `max(self.bids)` across every order in the book, and the symbol is only ever stamped onto
   the resulting `Fill`. A single book would therefore trade symbol 3 against symbol 7 at a
   matching price. So a book is held per `symbol_id`.

3. **The maker price.** The model and the frozen schema both use the resting order's price.
   The book tracks arrival order so a seller aggressing into a resting bid prints the bid price,
   rather than the seller's lower limit.

Two behaviours are preserved deliberately, because a consumer could come to depend on them and
then break when the C++ engine arrives at the week-5 integration point:

- **No clock is ever read.** Outbound records echo the inbound `timestamp_ns`. The engine is
  deterministic (Open Issue 001), so a fresh timestamp here would be a lie that consumers
  would quietly build on. The week-1 stub engine did the same, for the same reason.
- **`order_id` is engine-assigned, monotonic `uint64`** (Open Issue 008 sub-decision 9a). The
  counter lives here and is re-derived by replay, never persisted.
"""

from __future__ import annotations

from dataclasses import dataclass

from contracts.v1.generated.contracts import (
    AccountCreated,
    CancelOrder,
    CancelReason,
    CashCredited,
    ConfigureReplay,
    CreateAccount,
    CreditCash,
    Fill,
    OrderAccepted,
    OrderCancelled,
    OrderRejected,
    ReplayConfigured,
    RejectReason,
    Side,
    SubmitOrder,
    Tif,
)
from engine.naive_model import Order as ModelOrder
from engine.naive_model import OrderBook as ModelBook
from engine.naive_model import Side as ModelSide
from services.matcher.snapshot import EngineSnapshot, SnapshotOrder

#: Outbound records are `NamedTuple`s with no common base, so the union is spelled out.
Outbound = (
    OrderAccepted
    | OrderRejected
    | Fill
    | OrderCancelled
    | AccountCreated
    | CashCredited
    | ReplayConfigured
)

_SIDE_TO_MODEL = {int(Side.BUY): ModelSide.BUY, int(Side.SELL): ModelSide.SELL}


@dataclass
class _Live:
    """What the matcher knows about an order the model is holding.

    The model's `Order` is removed from the book the moment it fully fills, but a fill still
    needs the maker's `user_id`, `client_order_id` and price. Keeping a parallel record is
    cheaper and clearer than reaching back into a book that may no longer contain the order.
    """

    order_id: int
    client_order_id: int
    user_id: int
    symbol_id: int
    side: int
    price_ticks: int
    qty: int
    model_order: ModelOrder

    @property
    def remaining_qty(self) -> int:
        return self.model_order.remaining_quantity


class NaiveMatcher:
    """Single-threaded, in-memory, and zero I/O — like the thing it stands in for.

    `apply()` takes one inbound record and returns the outbound records it produced, in the
    order they must be appended. Nothing here touches Redis; the runner owns all I/O, so this
    class is deterministic and directly testable.
    """

    def __init__(self, *, first_order_id: int = 1, initial_cash_ticks: int = 0) -> None:
        #: Only ever copied onto a forwarded `AccountCreated`. The matcher never reads a
        #: balance and never holds one — see `_forward` for why that keeps it money-blind.
        self._initial_cash_ticks = initial_cash_ticks
        self._books: dict[int, ModelBook] = {}
        self._next_order_id = first_order_id
        #: (user_id, client_order_id) → live order. Cancel arrives by client id, because that
        #: is the only identifier a client is guaranteed to hold (Open Issue 008).
        self._live: dict[tuple[int, int], _Live] = {}
        self._by_order_id: dict[int, _Live] = {}

    # -- introspection, for tests and for the replay proof ------------------------------------

    @property
    def next_order_id(self) -> int:
        return self._next_order_id

    def book(self, symbol_id: int) -> ModelBook:
        book = self._books.get(symbol_id)
        if book is None:
            book = ModelBook()
            self._books[symbol_id] = book
        return book

    def resting(self) -> list[tuple[int, int, int, int, int]]:
        """Every live order as `(order_id, symbol_id, side, price_ticks, remaining_qty)`.

        Sorted by `order_id`, so two matchers built from the same stream compare equal — which
        is how the replay criterion is checked without inventing a snapshot format.
        """
        return sorted(
            (o.order_id, o.symbol_id, o.side, o.price_ticks, o.remaining_qty)
            for o in self._by_order_id.values()
        )

    # -- snapshot (Open Issue 020) -------------------------------------------------------------

    def snapshot(self) -> bytes:
        """This matcher's state in the format the C++ worker also emits (`snapshot.py`)."""
        return EngineSnapshot(
            next_order_id=self._next_order_id,
            orders=tuple(
                SnapshotOrder(
                    order_id=live.order_id,
                    client_order_id=live.client_order_id,
                    user_id=live.user_id,
                    symbol_id=live.symbol_id,
                    side=live.side,
                    indexed=self._live.get((live.user_id, live.client_order_id)) is live,
                    price_ticks=live.price_ticks,
                    remaining_qty=live.remaining_qty,
                    created_at_ns=live.model_order.created_at,
                )
                for live in self._by_order_id.values()
            ),
        ).pack()

    @classmethod
    def restore(cls, data: bytes, *, initial_cash_ticks: int = 0) -> "NaiveMatcher":
        """A matcher that continues exactly where the snapshotted one stopped.

        Orders are re-added in ascending `order_id`, which is arrival order, so every price
        level's queue and every arrival comparison comes back as it was.
        """
        snap = EngineSnapshot.unpack(data)
        matcher = cls(first_order_id=snap.next_order_id, initial_cash_ticks=initial_cash_ticks)
        for o in snap.orders:
            model_order = ModelOrder(
                order_id=o.order_id,
                user_id=o.user_id,
                symbol=str(o.symbol_id),
                side=_SIDE_TO_MODEL[o.side],
                price=o.price_ticks,
                quantity=o.remaining_qty,
                created_at=o.created_at_ns,
            )
            live = _Live(
                order_id=o.order_id,
                client_order_id=o.client_order_id,
                user_id=o.user_id,
                symbol_id=o.symbol_id,
                side=o.side,
                price_ticks=o.price_ticks,
                qty=o.remaining_qty,
                model_order=model_order,
            )
            matcher.book(o.symbol_id).add_order(model_order)
            matcher._by_order_id[o.order_id] = live
            if o.indexed:
                matcher._live[(o.user_id, o.client_order_id)] = live
        return matcher

    # -- the one entry point -------------------------------------------------------------------

    def apply(self, record) -> list[Outbound]:
        if isinstance(record, SubmitOrder):
            return self._submit(record)
        if isinstance(record, CancelOrder):
            return self._cancel(record)
        if isinstance(record, (CreateAccount, CreditCash, ConfigureReplay)):
            return self._forward(record)
        # Anything else is a record type this engine has no opinion about. Ignoring it keeps
        # the matcher tolerant of a stream that grows types it does not act on.
        return []

    def _forward(self, record: CreateAccount | CreditCash | ConfigureReplay) -> list[Outbound]:
        """Money records cross the engine untouched, and come out sequenced.

        The schema is explicit about this: `CreateAccount` is *"forwarded by the engine
        untouched — the engine is money-blind"*, and `AccountCreated` is *"forwarded record,
        now sequenced"*. Forwarding is not bookkeeping — nothing here reads or holds a balance,
        which is exactly what money-blind means (Open Issue 001, 002). What the pass gives the
        stream is a **position in the total order**, which is the one thing only the engine can
        supply and the one thing every downstream consumer needs.

        `initial_cash_ticks` is not on `CreateAccount`, so it comes from the shared
        configuration — the same file, hashed into both processes' startup lines, so a gateway
        and a matcher that disagreed about the grant would announce it rather than diverge.
        """
        if isinstance(record, CreateAccount):
            return [
                AccountCreated.new(
                    timestamp_ns=record.timestamp_ns,
                    client_order_id=record.client_order_id,
                    user_id=record.user_id,
                    initial_cash_ticks=self._initial_cash_ticks,
                )
            ]
        if isinstance(record, CreditCash):
            return [
                CashCredited.new(
                    timestamp_ns=record.timestamp_ns,
                    client_order_id=record.client_order_id,
                    user_id=record.user_id,
                    amount_ticks=record.amount_ticks,
                )
            ]
        return [
            ReplayConfigured.new(
                timestamp_ns=record.timestamp_ns,
                client_order_id=record.client_order_id,
                real_seconds_per_simulated_minute=record.real_seconds_per_simulated_minute,
                config_hash_hi=record.config_hash_hi,
                config_hash_lo=record.config_hash_lo,
            )
        ]

    # -- submit --------------------------------------------------------------------------------

    def _submit(self, order: SubmitOrder) -> list[Outbound]:
        reason = self._reject_reason(order)
        if reason is not None:
            return [
                OrderRejected.new(
                    timestamp_ns=order.timestamp_ns,
                    client_order_id=order.client_order_id,
                    user_id=order.user_id,
                    symbol_id=order.symbol_id,
                    reason=int(reason),
                )
            ]

        order_id = self._next_order_id
        self._next_order_id += 1

        model_order = ModelOrder(
            order_id=order_id,
            user_id=order.user_id,
            # The model insists on a string and only ever stamps it onto its own Fill, which
            # is discarded below. The real symbol is `symbol_id`, and the book is chosen by it.
            symbol=str(order.symbol_id),
            side=_SIDE_TO_MODEL[int(order.side)],
            price=order.price_ticks,
            quantity=order.qty,
            created_at=order.timestamp_ns,
        )
        live = _Live(
            order_id=order_id,
            client_order_id=order.client_order_id,
            user_id=order.user_id,
            symbol_id=order.symbol_id,
            side=int(order.side),
            price_ticks=order.price_ticks,
            qty=order.qty,
            model_order=model_order,
        )
        self._live[(order.user_id, order.client_order_id)] = live
        self._by_order_id[order_id] = live

        # Accepted is emitted before any fill. The ledger opens the order on OrderAccepted and
        # reduces it on each Fill (`services/ledger/ledger.py`), so the reverse order would
        # apply a fill to an order the read model had never heard of.
        out: list[Outbound] = [
            OrderAccepted.new(
                timestamp_ns=order.timestamp_ns,
                order_id=order_id,
                client_order_id=order.client_order_id,
                user_id=order.user_id,
                price_ticks=order.price_ticks,
                qty=order.qty,
                symbol_id=order.symbol_id,
                side=int(order.side),
                tif=int(order.tif),
            )
        ]

        book = self.book(order.symbol_id)
        for model_fill in book.match_order(model_order):
            out.append(self._to_contract_fill(order, model_fill, taker_order_id=order_id))

        # IOC: whatever did not trade on arrival never rests.
        if int(order.tif) == int(Tif.IOC) and model_order.remaining_quantity > 0:
            remaining = model_order.remaining_quantity
            book.remove_order(order_id)
            self._forget(live)
            out.append(
                OrderCancelled.new(
                    timestamp_ns=order.timestamp_ns,
                    order_id=order_id,
                    client_order_id=order.client_order_id,
                    user_id=order.user_id,
                    remaining_qty=remaining,
                    symbol_id=order.symbol_id,
                    reason=int(CancelReason.IOC_EXPIRED),
                )
            )
        elif model_order.remaining_quantity == 0:
            self._forget(live)

        return out

    def _to_contract_fill(
        self, taker: SubmitOrder, model_fill, *, taker_order_id: int
    ) -> Fill:
        """Translate one model fill into the frozen contract.

        The model reports `buy_order_id` / `sell_order_id`; the contract needs maker and taker,
        which is why `aggressor_side` exists at all (it "cannot be derived after the fact",
        `schema.toml`). Here the aggressor is known for certain: it is the order just added.
        """
        if model_fill.buy_order_id == taker_order_id:
            maker_order_id = model_fill.sell_order_id
        else:
            maker_order_id = model_fill.buy_order_id

        maker = self._by_order_id[maker_order_id]
        filled = self._by_order_id[taker_order_id]

        if maker.remaining_qty == 0:
            self._forget(maker)

        return Fill.new(
            timestamp_ns=taker.timestamp_ns,
            maker_order_id=maker_order_id,
            taker_order_id=taker_order_id,
            maker_user_id=maker.user_id,
            taker_user_id=filled.user_id,
            price_ticks=model_fill.price,
            qty=model_fill.quantity,
            symbol_id=maker.symbol_id,
            aggressor_side=int(taker.side),
        )

    @staticmethod
    def _reject_reason(order: SubmitOrder) -> RejectReason | None:
        """Bounds only. Cash and symbol existence are deliberately not checked here.

        Risk and reservations are Task 3.1 and live in the gateway, because the engine is
        **money-blind** (Open Issue 001, 002) — it must never learn about cash. The symbol
        registry arrives with Task 5.1; `UNKNOWN_SYMBOL` already exists in the contract for it.
        """
        if int(order.side) not in _SIDE_TO_MODEL:
            return RejectReason.INVALID_SIDE
        if int(order.tif) not in (int(Tif.GTC), int(Tif.IOC)):
            return RejectReason.INVALID_TIF
        if order.price_ticks <= 0:
            return RejectReason.INVALID_PRICE
        if order.qty <= 0:
            return RejectReason.INVALID_QUANTITY
        return None

    # -- cancel --------------------------------------------------------------------------------

    def _cancel(self, cancel: CancelOrder) -> list[Outbound]:
        live = self._live.get((cancel.user_id, cancel.target_client_order_id))
        if live is None:
            # UNKNOWN_ORDER for both "never existed" and "belongs to someone else": the lookup
            # is keyed by user, so another user's order is simply absent, and answering the
            # same way leaks nothing about their order ids. The week-1 stub engine reasoned identically.
            return [
                OrderRejected.new(
                    timestamp_ns=cancel.timestamp_ns,
                    client_order_id=cancel.client_order_id,
                    user_id=cancel.user_id,
                    symbol_id=0,
                    reason=int(RejectReason.UNKNOWN_ORDER),
                )
            ]

        remaining = live.remaining_qty
        self.book(live.symbol_id).remove_order(live.order_id)
        self._forget(live)
        return [
            OrderCancelled.new(
                timestamp_ns=cancel.timestamp_ns,
                order_id=live.order_id,
                client_order_id=live.client_order_id,
                user_id=cancel.user_id,
                remaining_qty=remaining,
                symbol_id=live.symbol_id,
                reason=int(CancelReason.USER_REQUESTED),
            )
        ]

    def _forget(self, live: _Live) -> None:
        self._live.pop((live.user_id, live.client_order_id), None)
        self._by_order_id.pop(live.order_id, None)
