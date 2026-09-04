"""OHLCV bars, and the trade tape.

Two streams with opposite requirements, which is why they sit together here and are handled
apart (Open Issue 006 §5):

- **The tape is never conflated.** Every print is delivered. A dropped trade is a *wrong* tape,
  not a stale one — it leaves a hole in history that nothing backfills.
- **Bars are conflated by definition.** A bar is an aggregate; it is published when it closes.

Bars are aggregated server-side rather than in the browser for a reason beyond bandwidth: the
backtester in Task 7.1 consumes them. Computing them twice — once for display and once for
research — is two implementations that will eventually disagree about what a candle is.

## Which clock

Buckets are cut on `Fill.timestamp_ns`, the timestamp the gateway stamped and every consumer
replays identically, with the width taken from `market_data.bar_bucket_seconds`.

**This is the thing to revisit at Task 5.1.** The replay clock maps one real second to one
simulated minute (Open Issue 005 §5g), so "a one-minute bar" is about to become ambiguous: one
real minute, or one simulated minute — which is one real second. At present rates the first
holds thousands of fills and the second holds a handful, and 7.1's backtester consumes whichever
this turns out to mean. The width lives in configuration precisely so 5.1 can answer that by
changing a line rather than this file.
"""

from __future__ import annotations

from dataclasses import dataclass, field

NS_PER_SECOND = 1_000_000_000


@dataclass(frozen=True)
class Trade:
    """One print, as the tape carries it."""

    symbol_id: int
    price_ticks: int
    qty: int
    aggressor_side: int
    timestamp_ns: int
    seq: str


@dataclass
class Bar:
    """One OHLCV candle. Field names are the frozen contract's (§3.3)."""

    symbol_id: int
    bucket_seconds: int
    bar_open_ns: int
    open_ticks: int
    high_ticks: int
    low_ticks: int
    close_ticks: int
    volume: int

    def extend(self, price_ticks: int, qty: int) -> None:
        self.high_ticks = max(self.high_ticks, price_ticks)
        self.low_ticks = min(self.low_ticks, price_ticks)
        self.close_ticks = price_ticks
        self.volume += qty


class Tape:
    """Trades since the last drain, per symbol.

    Held rather than published immediately because the tape shares a connection with the
    conflated channels: 5.2b sends everything accumulated since the last tick, all of it, on the
    same tick the book snapshot goes out. Batched delivery is not conflation — nothing is
    dropped or superseded, it is only carried together.
    """

    def __init__(self) -> None:
        self._pending: dict[int, list[Trade]] = {}
        self.total = 0

    def record(self, trade: Trade) -> None:
        self._pending.setdefault(trade.symbol_id, []).append(trade)
        self.total += 1

    def pending(self, symbol_id: int) -> list[Trade]:
        return list(self._pending.get(symbol_id, ()))

    def drain(self, symbol_id: int) -> list[Trade]:
        return self._pending.pop(symbol_id, [])


class BarBuilder:
    """Bars for one symbol at one width.

    A bar closes when a trade arrives in a later bucket, so the close is driven by the data and
    not by a timer. That makes the aggregation a pure function of the stream: a replay produces
    identical bars, which a wall-clock timer could never guarantee.

    The consequence is worth stating: a bar with no trade after it stays open until one arrives.
    `flush()` exists for the end of a replay, where there is no later trade coming and the final
    partial bar is still the truth about what happened.
    """

    def __init__(self, symbol_id: int, bucket_seconds: int) -> None:
        if bucket_seconds < 1:
            raise ValueError("a bar width must be at least one second")
        self.symbol_id = symbol_id
        self.bucket_seconds = bucket_seconds
        self._width_ns = bucket_seconds * NS_PER_SECOND
        self.current: Bar | None = None
        self.closed: list[Bar] = []

    def bucket_start(self, timestamp_ns: int) -> int:
        return (timestamp_ns // self._width_ns) * self._width_ns

    def add(self, *, price_ticks: int, qty: int, timestamp_ns: int) -> Bar | None:
        """Fold one trade in. Returns the bar this trade *closed*, if any."""
        start = self.bucket_start(timestamp_ns)

        if self.current is not None and start == self.current.bar_open_ns:
            self.current.extend(price_ticks, qty)
            return None

        finished = self.current
        if finished is not None:
            self.closed.append(finished)

        self.current = Bar(
            symbol_id=self.symbol_id,
            bucket_seconds=self.bucket_seconds,
            bar_open_ns=start,
            open_ticks=price_ticks,
            high_ticks=price_ticks,
            low_ticks=price_ticks,
            close_ticks=price_ticks,
            volume=qty,
        )
        return finished

    def flush(self) -> Bar | None:
        """Close the open bar. For the end of a replay, where no later trade is coming."""
        finished, self.current = self.current, None
        if finished is not None:
            self.closed.append(finished)
        return finished


@dataclass
class BarSet:
    """Every configured width, for one symbol."""

    symbol_id: int
    widths: tuple[int, ...]
    builders: dict[int, BarBuilder] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.builders = {w: BarBuilder(self.symbol_id, w) for w in self.widths}

    def add(self, *, price_ticks: int, qty: int, timestamp_ns: int) -> list[Bar]:
        """Fold a trade into every width. Returns whichever bars that closed."""
        return [
            closed
            for builder in self.builders.values()
            if (closed := builder.add(
                price_ticks=price_ticks, qty=qty, timestamp_ns=timestamp_ns
            )) is not None
        ]
