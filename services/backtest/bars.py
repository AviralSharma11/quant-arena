"""Bars for the backtester, built from the pinned crypto history.

## Why bars are constructed rather than loaded

`data/market_history.parquet` is `symbol · minute_index · close_ticks` and nothing else — one
close per simulated minute, no OHLC on disk. So a bar is *made* here, by aggregating
`bar_minutes` consecutive minutes:

    open  = the first minute's close in the bucket
    high  = the maximum
    low   = the minimum
    close = the last minute's close

That matters more than it looks. Task 7.1 fills at **the next bar's open**, and the whole point
of that rule is that a signal cannot be acted on at the price that generated it. With one
minute per bar the open and the close of a bar are the same number, and the rule still holds —
the fill is on the *next* bar — but it stops being visible. Aggregating makes the open a
genuinely different price from the close the strategy saw, which is the honest demonstration.

`bar_minutes` is a **manifest field, not configuration**. It is a property of one research run,
so two runs at different widths must be comparable without changing the hash of the entire
system; `[replay]` and `[symbols]` describe the exchange, and a backtest's resolution does not.

## Simulated minutes, not real ones

`minute_index` counts *simulated* minutes — Open Issue 005 sub-decision 5g maps one real second
to one simulated minute. So `bar_minutes = 5` is five simulated minutes, which the live market
would traverse in five seconds. This is the same ambiguity the 2026-09-06 decision settled for
the live bar channels, answered the same way: the unit is always simulated time.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from services.bots.fairvalue import DATA_PATH, load_price_history, verify_checksum


@dataclass(frozen=True)
class Bar:
    """One bar of one symbol. Prices are integer ticks — never floats (Open Issue 016).

    `index` is the bar's ordinal in the run, not a timestamp. The pinned dataset carries
    `minute_index` and no wall-clock time at all, so inventing a date here would be inventing
    a fact; `first_minute` is the real coordinate and the manifest records the range in those
    terms.
    """

    index: int
    first_minute: int
    open_ticks: int
    high_ticks: int
    low_ticks: int
    close_ticks: int


def load_bars(
    symbol: str,
    *,
    bar_minutes: int = 1,
    data_path: Path | str | None = None,
    expected_sha256: str | None = None,
) -> tuple[Bar, ...]:
    """Every bar for one symbol, oldest first.

    The checksum is verified when one is supplied, and that is not optional politeness: a
    dataset that does not match `[replay].data_sha256` produces a backtest nobody can
    reproduce, which is precisely what the run manifest exists to make impossible. A silent
    mismatch would let the manifest describe a run that never happened.
    """
    path = Path(data_path) if data_path is not None else DATA_PATH
    if expected_sha256:
        verify_checksum(path, expected_sha256)

    history = load_price_history(path)
    if symbol not in history:
        raise KeyError(
            f"{symbol} is not in {path.name}. It holds: {', '.join(sorted(history))}"
        )
    return build_bars(history[symbol], bar_minutes=bar_minutes)


def build_bars(closes: tuple[int, ...] | list[int], *, bar_minutes: int) -> tuple[Bar, ...]:
    """Aggregate minute closes into bars. A trailing partial bucket is **dropped**.

    Dropped rather than emitted short: a final bar built from two minutes when every other bar
    holds five is not the same measurement, and a strategy's last signal would be taken against
    a bar that means something different from all the ones that trained it. Losing up to
    `bar_minutes - 1` minutes off the end of a 10,080-minute series is the cheaper error, and
    it is the one that cannot skew a metric.
    """
    if bar_minutes < 1:
        raise ValueError(f"bar_minutes must be at least 1, got {bar_minutes}")

    bars: list[Bar] = []
    for index, start in enumerate(range(0, len(closes) - bar_minutes + 1, bar_minutes)):
        bucket = closes[start : start + bar_minutes]
        bars.append(
            Bar(
                index=index,
                first_minute=start,
                open_ticks=bucket[0],
                high_ticks=max(bucket),
                low_ticks=min(bucket),
                close_ticks=bucket[-1],
            )
        )
    return tuple(bars)
