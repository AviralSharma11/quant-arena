"""Everything fan-out knows, derived from the outbound stream and nothing else.

Pure: no clock, no socket, no Redis. `apply()` takes one record and folds it in, exactly as
`Ledger.apply` and `RiskState.apply` do — which is what makes a replay from `0-0` reproduce
identical state, and what lets the whole of this be tested without a store.

`services/fanout/runner.py` owns the I/O.

## Records it acts on, and records it ignores

| Record | What it does here |
|---|---|
| `OrderAccepted` | an order joins the book |
| `Fill` | both sides of the book shrink; a print joins the tape; bars advance |
| `OrderCancelled` | the order leaves the book |
| everything else | ignored |

`AccountCreated`, `CashCredited` and `OrderRejected` are deliberately ignored. Fan-out is
market data — it holds no balances, and a rejected order never reached the book. Ignoring
unknown types rather than raising also keeps this tolerant of a stream that grows records it
has no opinion about, which is how the schema is expected to evolve.
"""

from __future__ import annotations

from contracts.v1.generated.contracts import (
    Fill,
    OrderAccepted,
    OrderCancelled,
    Side,
)
from services.fanout.bars import BarSet, Tape, Trade
from services.fanout.book import Book

BUY, SELL = int(Side.BUY), int(Side.SELL)


class MarketState:
    """Books, tape and bars for every symbol seen on the stream."""

    def __init__(self, *, bar_widths: tuple[int, ...] = (1, 60)) -> None:
        self.bar_widths = tuple(bar_widths)
        self.books: dict[int, Book] = {}
        self.bars: dict[int, BarSet] = {}
        self.tape = Tape()
        self.last_seq: str = "0-0"
        self.records_applied = 0

    # -- accessors, which create on first sight -------------------------------------------------

    def book(self, symbol_id: int) -> Book:
        book = self.books.get(symbol_id)
        if book is None:
            book = Book(symbol_id=symbol_id)
            self.books[symbol_id] = book
        return book

    def bar_set(self, symbol_id: int) -> BarSet:
        bars = self.bars.get(symbol_id)
        if bars is None:
            bars = BarSet(symbol_id=symbol_id, widths=self.bar_widths)
            self.bars[symbol_id] = bars
        return bars

    @property
    def symbols(self) -> list[int]:
        """Symbols that have appeared on the stream, in a stable order."""
        return sorted(self.books)

    # -- the one entry point ---------------------------------------------------------------------

    def apply(self, record, *, stream_id: str | None = None) -> None:
        if stream_id is not None:
            self.last_seq = stream_id
        self.records_applied += 1

        if isinstance(record, OrderAccepted):
            self.book(record.symbol_id).accept(
                order_id=record.order_id,
                side=int(record.side),
                price_ticks=record.price_ticks,
                qty=record.qty,
            )
            return

        if isinstance(record, Fill):
            book = self.book(record.symbol_id)
            # Both sides. The taker was accepted onto the book before it matched, so a fill
            # reduces the resting maker and the aggressor alike — see book.py.
            book.fill(record.maker_order_id, record.qty)
            book.fill(record.taker_order_id, record.qty)

            self.tape.record(Trade(
                symbol_id=record.symbol_id,
                price_ticks=record.price_ticks,
                qty=record.qty,
                aggressor_side=int(record.aggressor_side),
                timestamp_ns=record.timestamp_ns,
                seq=stream_id or self.last_seq,
            ))
            self.bar_set(record.symbol_id).add(
                price_ticks=record.price_ticks,
                qty=record.qty,
                timestamp_ns=record.timestamp_ns,
            )
            return

        if isinstance(record, OrderCancelled):
            # Covers a user cancellation and an expired IOC remainder alike. The distinction
            # matters to the ledger and to the matcher's anchor count; to the book, an order
            # leaving is an order leaving.
            self.book(record.symbol_id).cancel(record.order_id)
            return

    def flush_bars(self) -> None:
        """Close every open bar. For the end of a replay, where no later trade is coming."""
        for bar_set in self.bars.values():
            for builder in bar_set.builders.values():
                builder.flush()

    # -- introspection --------------------------------------------------------------------------

    def snapshot(self) -> dict:
        """A plain summary, for logging and for comparing two replays."""
        return {
            "last_seq": self.last_seq,
            "records_applied": self.records_applied,
            "trades": self.tape.total,
            "books": {
                symbol_id: {
                    "resting": len(book.orders),
                    "bid": book.best(BUY),
                    "ask": book.best(SELL),
                }
                for symbol_id, book in sorted(self.books.items())
            },
        }
