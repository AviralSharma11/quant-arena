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

import json

from contracts.v1.generated.contracts import (
    Fill,
    OrderAccepted,
    OrderCancelled,
    Side,
)
from services.fanout.bars import Bar, BarSet, Tape, Trade
from services.fanout.book import Book, RestingOrder

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
        #: Symbols whose book moved since the tick last looked. The conflation tick serialises
        #: these and nothing else — a symbol nobody traded does not need re-encoding twenty
        #: times a second just because the clock advanced (Task 5.2 Success Criterion 5).
        self.dirty: set[int] = set()
        #: Bars that closed since the last drain. `BarSet.add` returns them and 5.2a discarded
        #: them; they are published on close (§3.3), so they have to be kept until the tick
        #: that sends them.
        self.closed_bars: list[Bar] = []

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
            self.dirty.add(record.symbol_id)
            self.book(record.symbol_id).accept(
                order_id=record.order_id,
                side=int(record.side),
                price_ticks=record.price_ticks,
                qty=record.qty,
            )
            return

        if isinstance(record, Fill):
            self.dirty.add(record.symbol_id)
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
            self.closed_bars.extend(
                self.bar_set(record.symbol_id).add(
                    price_ticks=record.price_ticks,
                    qty=record.qty,
                    timestamp_ns=record.timestamp_ns,
                )
            )
            return

        if isinstance(record, OrderCancelled):
            self.dirty.add(record.symbol_id)
            # Covers a user cancellation and an expired IOC remainder alike. The distinction
            # matters to the ledger and to the matcher's anchor count; to the book, an order
            # leaving is an order leaving.
            self.book(record.symbol_id).cancel(record.order_id)
            return

    def drain_dirty(self) -> set[int]:
        """Symbols changed since the last call, and clears the set."""
        changed, self.dirty = self.dirty, set()
        return changed

    def drain_closed_bars(self) -> list[Bar]:
        closed, self.closed_bars = self.closed_bars, []
        return closed

    def flush_bars(self) -> None:
        """Close every open bar. For the end of a replay, where no later trade is coming."""
        for bar_set in self.bars.values():
            for builder in bar_set.builders.values():
                builder.flush()

    # -- checkpoint (Open Issue 020) -----------------------------------------------------------

    def dump_state(self) -> bytes:
        """Books and open bars as of `last_seq`. Undelivered tape prints and closed bars are
        left out: they are in-flight output, not state, and the conflation tick owns them."""
        return json.dumps(
            {
                "last_seq": self.last_seq,
                "records_applied": self.records_applied,
                "trades": self.tape.total,
                "books": sorted(
                    [symbol_id, sorted(book.resting())] for symbol_id, book in self.books.items()
                ),
                "bars": sorted(
                    [symbol_id, width, [bar.bar_open_ns, bar.open_ticks, bar.high_ticks,
                                        bar.low_ticks, bar.close_ticks, bar.volume]]
                    for symbol_id, bar_set in self.bars.items()
                    for width, builder in bar_set.builders.items()
                    if (bar := builder.current) is not None
                ),
            },
            separators=(",", ":"),
        ).encode()

    def load_state(self, data: bytes) -> None:
        raw = json.loads(data)
        self.last_seq = raw["last_seq"]
        self.records_applied = raw["records_applied"]
        self.tape.total = raw["trades"]
        self.books = {}
        for symbol_id, resting in raw["books"]:
            book = self.book(symbol_id)
            for order_id, side, price_ticks, remaining_qty in resting:
                book.orders[order_id] = RestingOrder(
                    order_id=order_id, side=side, price_ticks=price_ticks, remaining_qty=remaining_qty
                )
        self.bars = {}
        for symbol_id, width, fields in raw["bars"]:
            builder = self.bar_set(symbol_id).builders.get(width)
            if builder is None:
                continue  # a width no longer configured — config changes also void checkpoints
            open_ns, open_t, high_t, low_t, close_t, volume = fields
            builder.current = Bar(
                symbol_id=symbol_id, bucket_seconds=width, bar_open_ns=open_ns, open_ticks=open_t,
                high_ticks=high_t, low_ticks=low_t, close_ticks=close_t, volume=volume,
            )
        self.dirty = set(self.books)

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
