"""The fair-value process the market maker quotes around.

Two implementations of one very small interface:

- `ReplayFairValue` — **the real thing**, replaying pinned crypto history at
  1 real second : 1 simulated minute. Real history brings volatility clustering, regime changes
  and genuine trends for free: statistical structure a model would approximate worse and take
  longer to write (Open Issue 005 section 10). It matters beyond realism, because the bots are
  the data-generating process for the research half of the project, and a price series with no
  structure makes every backtest meaningless.
- `SyntheticFairValue` — a seeded random walk, used when no data file is present.

**The synthetic path is not scaffolding.** Open Issue 005 section 10.2 keeps it explicitly "for
tests and for offline development, where a deterministic price path with no data dependency is
more convenient", and Task 5.1's fourth success criterion is that it still produces a
deterministic path with no data file. So 5.1 added the loader *beside* it behind the same
`next_ticks()` call rather than replacing it, and `build_fair_value()` is the one branch
between them.

Integer ticks throughout. A float fair value would be the one place a float could leak into a
price the gateway then receives, and Open Issue 016 is clear that a float below the
presentation layer is a bug rather than a rounding concern. The Parquet is written in ticks by
`scripts/fetch_market_history.py`, so no conversion happens at runtime at all.

**No network code path exists in this module.** Task 5.1's Boundaries forbid fetching live —
it would destroy reproducibility, which is a stated project goal — so the fetch is a setup
script and this is a file read. That is what makes "runs fully offline from pinned data"
provable rather than asserted.
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
import time
from pathlib import Path
from typing import Callable, Protocol


#: The pinned dataset, repo-relative. A path is infrastructure, not a domain parameter, so it
#: is a constant here rather than a line in the hashed configuration — which carries the file's
#: *checksum* instead, under `[replay].data_sha256`. See `config/quant_arena.toml`.
LOGGER_NAME = "quant_arena.bots"
_ROOT = Path(__file__).resolve().parents[2]
DATA_PATH = _ROOT / "data" / "market_history.parquet"


class FairValue(Protocol):
    """What the market maker needs from a price source, and nothing more.

    Deliberately this small: it is the seam Task 5.1 substitutes at. A market maker written
    against a richer interface would have to change when the real data arrives.
    """

    def next_ticks(self) -> int: ...


class SyntheticFairValue:
    """A seeded random walk in integer ticks.

    Gaussian steps rounded to whole ticks. It has no mean reversion and no volatility
    clustering, and that is honest rather than a shortcoming — modelling those was assessed
    (Open Issue 005 sub-decision 5a, option 3) and dropped once real data was adopted, because
    replayed history contains them already. What this generator is for is a price that moves
    reproducibly, so a bot session can be replayed exactly.
    """

    def __init__(
        self, *, start_ticks: int, volatility_ticks: int, rng: random.Random
    ) -> None:
        if start_ticks <= 0:
            raise ValueError("fair value starts above zero — a price of zero is not a price")
        self._value = start_ticks
        self._volatility = volatility_ticks
        self._rng = rng

    @property
    def value_ticks(self) -> int:
        """The current value, without advancing. For assertions and for logging."""
        return self._value

    def next_ticks(self) -> int:
        step = round(self._rng.gauss(0.0, self._volatility))
        # Floored at one tick. A random walk is unbounded below and would otherwise eventually
        # cross zero, at which point every quote derived from it is nonsense and the gateway
        # rejects it as INVALID_PRICE — a slow, confusing failure instead of a bounded one.
        self._value = max(1, self._value + step)
        return self._value


class ReplayClock:
    """Simulated time, as a pure function of elapsed real time.

    The ratio is `real_seconds_per_simulated_minute` — one real second to one simulated minute
    by default (Open Issue 005 sub-decision 5g), so a demonstration shows a trading day rather
    than a quiet minute.

    **Deliberately not a counter incremented once per tick.** A counter drifts the moment a
    quoting loop runs late, and two bots that each kept their own would silently disagree about
    what time it is — which would show up as two symbols replaying at different speeds for
    reasons nobody could reproduce. Elapsed real time cannot drift, and every bot sharing one
    clock reads the same simulated minute by construction.

    A clock read here is not a determinism violation. The engine may never read a clock
    (Open Issue 001), but this is the bot process, and `SyntheticFairValue` already records the
    rule: determinism is a property of **replaying the log**, not of reproducing a live session.
    """

    def __init__(
        self,
        *,
        real_seconds_per_simulated_minute: int,
        now_ns: Callable[[], int] = time.monotonic_ns,
    ) -> None:
        if real_seconds_per_simulated_minute <= 0:
            raise ValueError(
                "real_seconds_per_simulated_minute must be positive — a ratio of zero or less "
                "means simulated time does not advance, and every bot quotes one price forever"
            )
        self._ratio_ns = real_seconds_per_simulated_minute * 1_000_000_000
        self._now_ns = now_ns
        self._start_ns = now_ns()

    def simulated_minutes(self) -> int:
        """Whole simulated minutes since this clock was made. Monotonic, never negative."""
        return max(0, (self._now_ns() - self._start_ns) // self._ratio_ns)


class ReplayFairValue:
    """One symbol's pinned price history, indexed by the replay clock.

    Looping is by modulo from a randomised starting offset, which is Task 5.1's "loop from a
    randomised offset at the end of the dataset". The offset comes from the master seed, so a
    session is reproducible from one number, and it differs per symbol so that ten symbols do
    not all begin at the same point in their own history and move in lockstep.

    `next_ticks()` is a lookup, not a step: calling it twice within the same simulated minute
    returns the same price, and skipping a minute is not an error. That falls out of indexing
    on the clock rather than advancing an internal cursor, and it is what keeps a slow quoting
    loop from falling behind the market instead of catching up to it.
    """

    def __init__(
        self, *, prices_ticks: tuple[int, ...], clock: ReplayClock, start_offset: int
    ) -> None:
        if not prices_ticks:
            raise ValueError("a replayed price series cannot be empty")
        if any(price <= 0 for price in prices_ticks):
            raise ValueError("every replayed price must be above zero — a price of zero is not a price")
        self._prices = prices_ticks
        self._clock = clock
        self._offset = start_offset % len(prices_ticks)

    @property
    def value_ticks(self) -> int:
        """The current value, without advancing. For assertions and for logging."""
        index = (self._offset + self._clock.simulated_minutes()) % len(self._prices)
        return self._prices[index]

    def next_ticks(self) -> int:
        return self.value_ticks


def load_price_history(path: Path | str) -> dict[str, tuple[int, ...]]:
    """Read the pinned Parquet into `{symbol: (close_ticks, ...)}`, oldest minute first.

    `pyarrow` is imported here rather than at module scope so that the synthetic path — which is
    what runs under most of the test suite and in offline development — does not require it.
    """
    import pyarrow.parquet as pq

    table = pq.read_table(path).sort_by([("symbol", "ascending"), ("minute_index", "ascending")])
    series: dict[str, list[int]] = {}
    for symbol, price in zip(
        table.column("symbol").to_pylist(), table.column("close_ticks").to_pylist()
    ):
        series.setdefault(symbol, []).append(int(price))
    return {symbol: tuple(prices) for symbol, prices in series.items()}


def verify_checksum(data_path: Path, expected_sha256: str) -> None:
    """Raise if the pinned file is not the one the configuration records.

    The checksum is the whole point of pinning. A silently different data file means a benchmark
    or a recorded session describes prices nobody can reproduce, which is the failure the
    "never fetch live at runtime" boundary exists to prevent — a corrupted or half-written file
    would otherwise reach it by a different route.
    """
    digest = hashlib.sha256()
    with data_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    actual = digest.hexdigest()
    if expected_sha256 != actual:
        raise ValueError(
            f"{data_path.name} does not match replay.data_sha256.\n"
            f"  expected {expected_sha256}\n  actual   {actual}\n"
            "Re-fetch with `python scripts/fetch_market_history.py`, or restore the "
            "committed file — do not run against unpinned prices."
        )


def build_fair_value(
    *,
    symbol_name: str,
    symbol_id: int,
    settings,
    rng: random.Random,
    data_path: Path | str | None = None,
    expected_sha256: str | None = None,
    history: dict[str, tuple[int, ...]] | None = None,
    clock: ReplayClock | None = None,
) -> FairValue:
    """Replay the pinned history if it is there; fall back to the seeded walk if it is not.

    This single branch is what makes Task 5.1's third and fourth success criteria testable
    against each other: with the file present the system runs entirely offline from it, and with
    the file absent the fallback still produces a deterministic path. Both are exercised by
    pointing this function at a path that does or does not exist.

    A symbol that is missing from an otherwise valid data file also falls back, rather than
    failing the whole bot session: adding a symbol to the configuration before re-fetching is a
    normal intermediate state, and a market maker quoting a synthetic price is far better than a
    market with no maker on that book at all. It says so in the log.
    """
    if history is None and data_path is not None:
        data_path = Path(data_path)
        if data_path.is_file():
            if expected_sha256:
                verify_checksum(data_path, expected_sha256)
            history = load_price_history(data_path)

    prices = (history or {}).get(symbol_name)
    if prices:
        return ReplayFairValue(
            prices_ticks=prices,
            clock=clock
            or ReplayClock(
                real_seconds_per_simulated_minute=(
                    settings.replay_real_seconds_per_simulated_minute
                )
            ),
            # Per symbol, from the master seed: reproducible, and not ten symbols in lockstep.
            start_offset=rng.randrange(len(prices)),
        )

    return SyntheticFairValue(
        start_ticks=settings.bots.fair_value_start_ticks,
        volatility_ticks=settings.bots.fair_value_volatility_ticks,
        rng=rng,
    )


def load_replay_history(settings, data_path: Path | str | None = None) -> dict[str, tuple[int, ...]] | None:
    """The pinned history for a whole session, or `None` if there is no data file.

    One file read for all ten symbols, called once by the bot runner. Returning `None` rather
    than raising is what makes offline development work: with no data file every symbol falls
    back to the seeded walk, which is Task 5.1's fourth success criterion.

    A file that *is* present but does not match `replay.data_sha256` raises, and deliberately so.
    Absent means "you are offline"; wrong means "you are about to generate a session nobody can
    reproduce", and the second is not something to shrug off.
    """
    path = Path(data_path) if data_path is not None else DATA_PATH
    if not path.is_file():
        logging.getLogger(LOGGER_NAME).info(
            json.dumps({
                "event": "fair_value_source",
                "source": "synthetic",
                "detail": f"no pinned data at {path}; using the seeded walk",
            })
        )
        return None
    verify_checksum(path, settings.replay_data_sha256)
    history = load_price_history(path)
    logging.getLogger(LOGGER_NAME).info(
        json.dumps({
            "event": "fair_value_source",
            "source": "replay",
            "symbols": len(history),
            "minutes": len(next(iter(history.values()))) if history else 0,
            "real_seconds_per_simulated_minute": (
                settings.replay_real_seconds_per_simulated_minute
            ),
        })
    )
    return history
