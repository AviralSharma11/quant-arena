"""What the archiver knows, derived from the outbound stream and nothing else.

Pure: no clock, no socket, no disk. `apply()` takes one record and folds it in, exactly as
`MarketState.apply`, `Ledger.apply` and `RiskState.apply` do. `runner.py` owns the I/O.

## Bar aggregation is imported, not reimplemented

Task 6.2's Boundaries say "bar aggregation is shared with the live chart pipeline — build it
once", and it already exists: `services/fanout/state.py` holds the books, the tape and the bars,
all three derived from `OrderAccepted` / `Fill` / `OrderCancelled`. `ArchiveState` **composes** a
`MarketState` rather than copying it or subclassing it.

Composition and not a move to a shared package: fan-out is finished and tested, and editing it
to satisfy a naming preference here buys nothing. The import direction is the honest one anyway —
the archive is downstream of market data, not beside it.

The payoff shows up in the tests. Criterion 2 is "bars reconcile exactly against the trade stream
they were built from", and it is checked by recomputing OHLCV from the **archived trades table**
and comparing against the **archived bars table**. If this module recomputed bars its own way,
that test would only prove two copies of one bug agree.

## One flush unit, and why the checkpoint is a low-water mark

Everything is bucketed into a **flush unit** of `flush_seconds` (60 by default), on
`timestamp_ns` — the stamp the gateway wrote and every consumer replays identically. Never a
clock read: a wall-clock sampler would put a different number of snapshots in a replay than in
the live run, and an archive that cannot be reproduced is not evidence of anything.

A unit *M* is complete the moment the first record of a later unit arrives. At that point:

- every trade in *M* has been seen, because timestamps do not go backwards;
- every bar whose bucket ended inside *M* can be closed, because no later trade can fall into an
  ended bucket — this is why every configured bar width must divide the flush unit, and why a
  width that does not is a `ValueError` at construction rather than a wrong file later;
- every second of *M* has been sampled, because sampling happens as the second advances.

The **checkpoint is a low-water mark**: the last stream ID whose unit is fully written, which is
the ID immediately preceding the oldest still-open unit's first record — and not the last record
applied. It is *exclusive*, matching what `read_records(last_id=…)` means. That single choice is
the whole of Success Criterion 3:

> Every file on disk is complete for everything strictly before the checkpoint, and nothing at or
> after it has been written.

So a restart replays from the checkpoint, re-derives the in-flight units from the stream, and
writes each file exactly once. No overwrite semantics, no dedup pass, no partial file to
reconcile. The price is that the checkpoint lags by one flush unit, so a restart re-reads up to
`flush_seconds` of stream — at 8.32 µs per record that is noise, and it is the reason this is
sound rather than nearly sound.

## Units with no records produce no files

A unit exists here only once a record lands in it. An hour with the stack switched off is an hour
with no files, rather than 3,600 identical snapshots per symbol asserting that a market nobody
was running held its shape. Within a covered unit every second is sampled, which is what 1 Hz
means (Open Issue 011 §11b).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from contracts.v1.generated.contracts import Fill, Side
from services.archiver import schemas
from services.fanout.bars import NS_PER_SECOND, Bar
from services.fanout.state import MarketState

BUY, SELL = int(Side.BUY), int(Side.SELL)

#: The default flush unit, in seconds of stream time. One minute: long enough that the file count
#: stays sane, short enough that the checkpoint lag it implies is not worth thinking about.
DEFAULT_FLUSH_SECONDS = 60


def parse_stream_id(stream_id: str) -> tuple[int, int]:
    """`"1757337600123-4"` → `(1757337600123, 4)`.

    Stream IDs are compared as this pair and never as strings. `"10-0" < "9-0"` is true
    lexicographically and false in every sense that matters — a bug that would surface as a
    checkpoint that walks backwards after a stream has been alive for ten milliseconds.
    """
    ms, _, seq = stream_id.partition("-")
    return int(ms), int(seq or 0)


def min_stream_id(ids: list[str]) -> str:
    return min(ids, key=parse_stream_id)


@dataclass
class Unit:
    """One flush unit's worth of rows, plus the bookmark that makes it resumable."""

    start_ns: int
    #: The stream ID of the first record that landed in this unit.
    first_stream_id: str
    #: The stream ID applied immediately *before* `first_stream_id` — "0-0" if this unit opened on
    #: the very first record. This, not `first_stream_id`, is what the checkpoint reports.
    #:
    #: The reason is an off-by-one that would otherwise be invisible: `read_records(last_id=X)`
    #: returns records strictly *after* X, so a checkpoint naming a record that still has to be
    #: re-read would skip exactly that record on resume — one lost trade per restart, in a file
    #: nobody would think to check. Recording the predecessor makes the persisted checkpoint an
    #: **exclusive** resume point, which is what every reader of it already assumes.
    resume_after: str = "0-0"
    rows: dict[str, dict[str, list]] = field(
        default_factory=lambda: {name: schemas.empty(name) for name in schemas.BY_DATASET}
    )
    #: Seconds already sampled, so a second is never sampled twice and never missed.
    sampled: set[int] = field(default_factory=set)

    def row_count(self, dataset: str) -> int:
        cols = self.rows[dataset]
        return len(next(iter(cols.values()))) if cols else 0

    @property
    def is_empty(self) -> bool:
        return all(self.row_count(name) == 0 for name in self.rows)


class ArchiveState:
    """Buckets the outbound stream into flush units of Parquet rows."""

    def __init__(
        self,
        *,
        bar_widths: tuple[int, ...] = (1, 60),
        book_depth: int = 10,
        flush_seconds: int = DEFAULT_FLUSH_SECONDS,
    ) -> None:
        if flush_seconds < 1:
            raise ValueError("the flush unit must be at least one second")
        bad = [w for w in bar_widths if flush_seconds % w]
        if bad:
            raise ValueError(
                f"every bar width must divide the flush unit of {flush_seconds}s, so that a "
                f"bucket cannot still be open when its unit is written; these do not: {bad}. "
                "Either change market_data.bar_bucket_seconds or raise flush_seconds to a "
                "common multiple."
            )
        self.market = MarketState(bar_widths=bar_widths)
        self.book_depth = book_depth
        self.flush_seconds = flush_seconds
        self._flush_ns = flush_seconds * NS_PER_SECOND

        self._open: dict[int, Unit] = {}
        #: Sealed and complete, waiting to be written. Ascending by `start_ns`.
        self._ready: list[Unit] = []
        #: The latest unit start that has been sealed. Nothing at or before it may be reopened.
        self._sealed_through: int | None = None
        self.last_stream_id: str = "0-0"
        self.records_applied = 0
        #: Rows whose unit had already been written. Counted rather than misfiled — see `apply`.
        self.late_rows = 0

    # -- geometry -------------------------------------------------------------------------------

    def unit_start(self, timestamp_ns: int) -> int:
        return (timestamp_ns // self._flush_ns) * self._flush_ns

    @staticmethod
    def second_start(timestamp_ns: int) -> int:
        return (timestamp_ns // NS_PER_SECOND) * NS_PER_SECOND

    # -- the one entry point --------------------------------------------------------------------

    def apply(self, record, *, stream_id: str) -> None:
        """Fold one outbound record in.

        The order of the five steps is load-bearing, and the reason is the 60-second bar. It
        closes only when a trade in the *next* minute arrives, so if the unit were sealed before
        the record were applied, that bar would be handed back after its own file had been
        written. Sealing happens last for exactly that reason.
        """
        previous_stream_id = self.last_stream_id
        self.last_stream_id = stream_id
        self.records_applied += 1

        timestamp_ns = getattr(record, "timestamp_ns", None)
        if timestamp_ns is None:
            # `ConfigureReplay`, `ReplayConfigured` and anything else without a stamp: the market
            # state ignores them and they cannot open a unit, but they still advance the ID so a
            # stream of nothing but them does not pin the checkpoint at 0-0 forever.
            self.market.apply(record, stream_id=stream_id)
            return

        second = self.second_start(timestamp_ns)
        unit = self.unit_start(timestamp_ns)

        # 1. Sample every second that this record proves is over, using the book as it stands
        #    *before* the record — which is precisely the book at the end of those seconds.
        self._sample_until(second)

        # 2. Open this record's unit, so anything the record produces has somewhere to go.
        self._ensure_unit(unit, stream_id, previous_stream_id)

        # 3. Fold it into the market state: books, tape, bars.
        self.market.apply(record, stream_id=stream_id)

        # 4. Collect what that produced.
        if isinstance(record, Fill):
            self._collect_trades(record.symbol_id)
        self._collect_bars(self.market.drain_closed_bars())

        # 5. Now that nothing more can arrive for them, seal the earlier units.
        self._seal_before(unit)

    # -- collection -----------------------------------------------------------------------------

    def _ensure_unit(self, start_ns: int, stream_id: str, resume_after: str) -> Unit | None:
        """The open unit for `start_ns`, opening it if this is the first record to land there.

        Returns None for a unit that has already been sealed. A sealed unit is never reopened,
        and that guard is load-bearing rather than defensive: reopening one would build a second,
        partial unit for the same stream minute, which the writer would then write to the same
        deterministic path — replacing a complete file with an incomplete one. Rows that arrive
        for it are counted as late instead (see `_unit_for`).
        """
        if self._sealed_through is not None and start_ns <= self._sealed_through:
            return None
        unit = self._open.get(start_ns)
        if unit is None:
            unit = Unit(
                start_ns=start_ns, first_stream_id=stream_id, resume_after=resume_after
            )
            self._open[start_ns] = unit
        return unit

    def _unit_for(self, timestamp_ns: int) -> Unit | None:
        """The open unit a row belongs in, or None if that unit is already gone.

        None means the stream went backwards across a unit boundary — timestamps are stamped by
        the single producer and are expected non-decreasing, so this is a stream anomaly rather
        than an ordinary case. It is counted and the row dropped, not misfiled into whichever
        unit happens to be open: a trade filed under the wrong minute is a wrong archive, and
        the archive is not correctness-critical enough to justify killing the process over it.
        """
        return self._open.get(self.unit_start(timestamp_ns))

    def _collect_trades(self, symbol_id: int) -> None:
        for trade in self.market.tape.drain(symbol_id):
            unit = self._unit_for(trade.timestamp_ns)
            if unit is None:
                self.late_rows += 1
                continue
            cols = unit.rows["trades"]
            cols["symbol_id"].append(trade.symbol_id)
            cols["price_ticks"].append(trade.price_ticks)
            cols["qty"].append(trade.qty)
            cols["aggressor_side"].append(trade.aggressor_side)
            cols["timestamp_ns"].append(trade.timestamp_ns)
            cols["seq"].append(trade.seq)

    def _collect_bars(self, bars: list[Bar]) -> None:
        for bar in bars:
            # Filed by the unit the bar *opened* in, never the one it closed in. The first is a
            # property of the bar; the second is a property of when the next trade happened.
            unit = self._unit_for(bar.bar_open_ns)
            if unit is None:
                self.late_rows += 1
                continue
            cols = unit.rows["bars"]
            cols["symbol_id"].append(bar.symbol_id)
            cols["bucket_seconds"].append(bar.bucket_seconds)
            cols["bar_open_ns"].append(bar.bar_open_ns)
            cols["open_ticks"].append(bar.open_ticks)
            cols["high_ticks"].append(bar.high_ticks)
            cols["low_ticks"].append(bar.low_ticks)
            cols["close_ticks"].append(bar.close_ticks)
            cols["volume"].append(bar.volume)

    def _sample_until(self, cutoff_second: int) -> None:
        """Sample every not-yet-sampled second of every open unit that ends before `cutoff`.

        Iterating the open units rather than the seconds between the last record and this one is
        deliberate: an idle gap of an hour crosses 3,600 seconds and zero open units, so the work
        here is bounded by how much is in flight and not by how long the market was quiet.
        """
        for start_ns in sorted(self._open):
            unit = self._open[start_ns]
            for second in range(start_ns, start_ns + self._flush_ns, NS_PER_SECOND):
                if second >= cutoff_second:
                    break
                if second in unit.sampled:
                    continue
                unit.sampled.add(second)
                self._write_snapshot(unit, second)

    def _write_snapshot(self, unit: Unit, second_ns: int) -> None:
        cols = unit.rows["snapshots"]
        for symbol_id in self.market.symbols:
            book = self.market.book(symbol_id)
            for side in (BUY, SELL):
                for level, (price_ticks, qty) in enumerate(
                    book.levels(side, self.book_depth)
                ):
                    cols["symbol_id"].append(symbol_id)
                    cols["second_ns"].append(second_ns)
                    cols["side"].append(side)
                    cols["level"].append(level)
                    cols["price_ticks"].append(price_ticks)
                    cols["qty"].append(qty)

    # -- sealing --------------------------------------------------------------------------------

    def _force_close_bars(self, cutoff_ns: int) -> None:
        """Close every bar whose bucket ended at or before `cutoff_ns`.

        A bar normally closes when a later trade arrives. If the record that opened a new unit
        was an `OrderAccepted` rather than a `Fill`, no trade has arrived and the previous unit's
        final bars are still open — so they are closed here instead. Safe because the bucket has
        *ended*: timestamps do not go backwards, so no further trade can fall inside it.
        """
        closed: list[Bar] = []
        for bar_set in self.market.bars.values():
            for width, builder in bar_set.builders.items():
                current = builder.current
                if current is None:
                    continue
                if current.bar_open_ns + width * NS_PER_SECOND <= cutoff_ns:
                    finished = builder.flush()
                    if finished is not None:
                        closed.append(finished)
        self._collect_bars(closed)

    def _seal_before(self, cutoff_unit: int) -> None:
        stale = [start for start in self._open if start < cutoff_unit]
        if not stale:
            return
        self._force_close_bars(cutoff_unit)
        for start in sorted(stale):
            unit = self._open.pop(start)
            self._sealed_through = start if self._sealed_through is None else max(
                self._sealed_through, start
            )
            if not unit.is_empty:
                self._ready.append(unit)

    def finish(self) -> None:
        """Seal everything, for the end of a run.

        The final partial unit is still the truth about what happened — the same reason
        `BarBuilder.flush` exists. Sampling runs one second past the last record seen so that the
        second the last record fell in is represented.
        """
        if not self._open:
            return
        last_unit = max(self._open)
        self._sample_until(last_unit + self._flush_ns)
        self._force_close_bars(last_unit + self._flush_ns)
        for start in sorted(list(self._open)):
            unit = self._open.pop(start)
            self._sealed_through = start if self._sealed_through is None else max(
                self._sealed_through, start
            )
            if not unit.is_empty:
                self._ready.append(unit)

    # -- what the writer consumes ---------------------------------------------------------------

    def drain_ready(self) -> list[Unit]:
        """Sealed units, ascending, removed from the state.

        The caller must write them before asking for `checkpoint()`: the checkpoint answers "what
        is on disk", and between this call and the write, nothing is.
        """
        ready, self._ready = sorted(self._ready, key=lambda u: u.start_ns), []
        return ready

    def checkpoint(self) -> str:
        """The **exclusive** resume point: everything at or before it has been written.

        A reader resumes *after* this ID (`read_records(last_id=…)` and `XREAD` both do), so with
        units still open it is the ID immediately preceding the oldest open unit's first record —
        that record has not been written and must come back on the next read. With nothing open,
        everything seen has been written and the resume point is the last record applied.
        """
        if self._open:
            oldest = min(self._open)
            return self._open[oldest].resume_after
        if self._ready:
            return min_stream_id([u.resume_after for u in self._ready])
        return self.last_stream_id

    def snapshot(self) -> dict:
        """A plain summary, for the startup log and for comparing two replays."""
        return {
            "last_stream_id": self.last_stream_id,
            "checkpoint": self.checkpoint(),
            "records_applied": self.records_applied,
            "units_open": len(self._open),
            "units_ready": len(self._ready),
            "late_rows": self.late_rows,
            "trades_seen": self.market.tape.total,
        }
