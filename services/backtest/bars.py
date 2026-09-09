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
from functools import lru_cache
from pathlib import Path

from services.bots.fairvalue import DATA_PATH, load_price_history, verify_checksum

#: One simulated day. The replay clock maps one real second to one simulated minute (Open Issue
#: 005 sub-decision 5g), so a simulated day is 1,440 of them and the pinned dataset's 10,080
#: minutes are exactly seven. This is how a "date range" is expressed on a dataset that carries
#: no dates: `minute_index` is the only time coordinate there is, and a day is the largest unit
#: of it that reads like one.
MINUTES_PER_SIMULATED_DAY = 1440


@lru_cache(maxsize=4)
def _cached_history(path_str: str, expected_sha256: str | None) -> dict[str, tuple[int, ...]]:
    """The dataset, read once per process.

    Cached because the gateway serves backtests per request and re-reading 100,800 Parquet rows
    on each one puts a fifth of a second of avoidable work on a process that must stay
    answerable to HTTP (Open Issue 007). The file is pinned and checksum-verified, so it cannot
    change under the cache without the checksum having already refused it. Values are tuples,
    so a caller cannot corrupt the cache for everyone else.
    """
    path = Path(path_str)
    if expected_sha256:
        verify_checksum(path, expected_sha256)
    return load_price_history(path)


def day_range_to_minutes(first_day: int, last_day: int) -> tuple[int, int]:
    """Days 1..N to a half-open minute range. Day 1 is minutes 0..1439.

    One-based because it is a label a person picks in a form, and a "day 0" in a dropdown reads
    as a bug even when it is not.
    """
    if first_day < 1 or last_day < first_day:
        raise ValueError(
            f"day range must be 1 <= first <= last, got first={first_day} last={last_day}"
        )
    return (first_day - 1) * MINUTES_PER_SIMULATED_DAY, last_day * MINUTES_PER_SIMULATED_DAY


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
    first_minute: int = 0,
    last_minute: int | None = None,
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
    history = _cached_history(str(path), expected_sha256)
    if symbol not in history:
        raise KeyError(
            f"{symbol} is not in {path.name}. It holds: {', '.join(sorted(history))}"
        )

    closes = history[symbol]
    end = len(closes) if last_minute is None else min(last_minute, len(closes))
    if first_minute < 0 or first_minute >= end:
        raise ValueError(
            f"the requested range is empty: minutes {first_minute}..{end} of "
            f"{len(closes)} available"
        )
    # Sliced *before* the bars are built, never after. Slicing built bars would leave the first
    # bar of a range straddling the boundary — open from outside the range, close from inside —
    # so two runs over adjacent ranges would disagree about a bar they both think they own.
    return build_bars(closes[first_minute:end], bar_minutes=bar_minutes)


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
