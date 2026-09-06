"""Task 5.1 — crypto fair value, the replay clock, and the ten-symbol table.

One test per success criterion, plus the properties the criteria rest on. The symbol table's
own criteria live in `tests/config/test_settings.py`, next to the rest of the configuration.

The criteria, and where each is proven:

1. Ten symbols show distinct, realistically moving prices → `test_settings.py` for the table,
   `test_the_ten_symbols_follow_distinct_price_paths` here for the prices.
2. The replay ratio appears in configuration and in the stream → the configuration half is
   `test_the_replay_ratio_is_configured`. **The stream half is not yet implemented**: it needs
   Amendment 2 and six lines in Dev A's engine (`HANDOFF.md` section 3), and is marked xfail
   rather than quietly omitted, so it fails loudly the day it lands and stops being a promise.
3. The system runs fully offline from pinned data → `test_no_network_is_reachable_from_the_loader`.
4. The fallback is deterministic with no data file → `test_the_fallback_is_deterministic_...`.
5. The README states the provenance → `test_the_readme_states_the_data_provenance`.
"""

from __future__ import annotations

import random
from pathlib import Path

import pytest

from config.settings import Settings
from services.bots.fairvalue import (
    DATA_PATH,
    ReplayClock,
    ReplayFairValue,
    SyntheticFairValue,
    build_fair_value,
    load_price_history,
    load_replay_history,
    verify_checksum,
)

ROOT = Path(__file__).resolve().parents[1]

pinned_data = pytest.mark.skipif(
    not DATA_PATH.is_file(),
    reason="no pinned dataset; run `python scripts/fetch_market_history.py`",
)


@pytest.fixture(scope="module")
def settings() -> Settings:
    return Settings.load()


@pytest.fixture(scope="module")
def history(settings: Settings) -> dict[str, tuple[int, ...]]:
    if not DATA_PATH.is_file():
        pytest.skip("no pinned dataset")
    return load_price_history(DATA_PATH)


# --- Criterion 1: ten symbols, distinct and realistic ----------------------------------------


@pinned_data
def test_the_ten_symbols_follow_distinct_price_paths(history, settings: Settings):
    """Ten *different* series, not one series offset ten ways.

    The cheap way to fake this criterion is one price path plus a per-symbol constant, which
    looks distinct in a screenshot and is worthless as a backtest universe — every strategy
    would see the same signal on all ten books. Comparing normalised shapes catches that; a
    constant offset or a scale factor would leave the shapes identical.
    """
    assert len(history) == 10
    shapes = set()
    for symbol, prices in history.items():
        window = prices[:500]
        first = window[0]
        # Relative moves, rounded, so scale and offset cancel and only the *shape* remains.
        shapes.add(tuple(round((p - first) * 10_000 / first) for p in window))
    assert len(shapes) == 10, "two symbols share a normalised price path"


@pinned_data
def test_every_replayed_price_is_a_positive_integer_number_of_ticks(history):
    """Integer ticks all the way down (Open Issue 016).

    A float here is the one place a float could reach a price the gateway receives, and a
    non-positive price is rejected as INVALID_PRICE — a slow, confusing failure mode rather
    than a bounded one.
    """
    for symbol, prices in history.items():
        assert all(isinstance(p, int) for p in prices), symbol
        assert min(prices) > 0, symbol


@pinned_data
def test_the_prices_actually_move(history):
    """"Realistically moving" starts with "moving". A flat series would satisfy every other
    assertion here and make the whole exercise pointless.

    Measured in **basis points, not distinct tick values**, and that distinction was found by
    this test failing. QAJ's tick is the real 0.001 increment of the instrument behind it, so a
    ~$0.85 price spans only about 180 possible tick values in total and shows 51 distinct ones
    over a thousand minutes. That is a correct coarse tick size, not a dead market — counting
    distinct integers would have punished the symbols whose tick sizes are most realistic, and
    the fix was the assertion rather than the data.
    """
    for symbol, prices in history.items():
        window = prices[:1_000]
        assert len(set(window)) > 20, f"{symbol} barely moves"
        # Real one-minute crypto moves; a series ranging by less than a tenth of a percent over
        # a thousand minutes would be a bug in the loader, not a quiet market.
        assert (max(window) - min(window)) * 10_000 / min(window) > 10, symbol


@pinned_data
def test_each_configured_symbol_has_a_price_series(history, settings: Settings):
    """The two lists are edited in different places and must not drift.

    A symbol in the configuration with no data silently falls back to the seeded walk, so this
    would otherwise show up as one book quoting a random walk in the middle of a real market.
    """
    for symbol in settings.symbols:
        assert symbol.name in history, f"{symbol.name} has no pinned price series"


# --- Criterion 2: the replay ratio, in configuration and in the stream ------------------------


def test_the_replay_ratio_is_configured(settings: Settings):
    """Open Issue 005 sub-decision 5g: one real second to one simulated minute."""
    assert settings.replay_real_seconds_per_simulated_minute == 1


@pytest.mark.xfail(
    reason=(
        "Criterion 2's stream half needs Amendment 2 — a ConfigureReplay/ReplayConfigured pair "
        "and six lines in engine/cpp/stream_engine.cpp. Agreed in principle, awaiting Dev A. "
        "See HANDOFF.md section 3."
    ),
    strict=True,
)
def test_the_replay_ratio_reaches_the_stream():
    """Deliberately failing, not skipped and not deleted.

    `schema.toml` has no record type that can carry a configuration value, so there is nowhere
    for the ratio to live on the stream until the amendment lands. Marked `strict` so that the
    day Dev A's six lines arrive, this test starts XPASSing and the suite fails until the
    criterion is actually asserted — an unmet criterion that goes quiet is how 3.1 and 3.2 came
    to be marked done with four criteria failing.
    """
    from contracts.v1.generated import contracts

    assert hasattr(contracts, "ConfigureReplay")


# --- Criterion 3: fully offline from pinned data ---------------------------------------------


def test_no_network_is_reachable_from_the_loader(monkeypatch, settings: Settings):
    """Task 5.1's Boundaries: never fetch live at runtime — it destroys reproducibility.

    Proven by breaking the socket layer outright and then loading anyway. A test that merely
    asserted "we do not call the API" would pass on a module that imported httpx and used it
    somewhere else.
    """
    import socket

    def refuse(*args, **kwargs):
        raise AssertionError("the fair-value loader must never open a socket")

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)

    if DATA_PATH.is_file():
        loaded = load_replay_history(settings)
        assert loaded and len(loaded) == 10
    else:
        assert load_replay_history(settings) is None


@pinned_data
def test_the_pinned_data_matches_the_recorded_checksum(settings: Settings):
    """The checksum is the whole point of pinning: a silently different file means a benchmark
    or a recorded session describes prices nobody can reproduce."""
    verify_checksum(DATA_PATH, settings.replay_data_sha256)


@pinned_data
def test_a_corrupted_data_file_is_refused(tmp_path: Path, settings: Settings):
    """Absent means "you are offline" and falls back. *Wrong* means "you are about to generate
    a session nobody can reproduce", and that is not something to shrug off."""
    corrupt = tmp_path / "market_history.parquet"
    corrupt.write_bytes(DATA_PATH.read_bytes() + b"\x00")
    with pytest.raises(ValueError, match="data_sha256"):
        verify_checksum(corrupt, settings.replay_data_sha256)


def test_the_dataset_carries_no_instrument_name_or_timestamp(history):
    """"Anonymised" has to mean something checkable.

    The file holds a fictional symbol, a 0-based minute index and a price. No instrument name
    and no wall clock, so a row cannot be joined back to a moment in the real market without
    re-running the fetch script.
    """
    import pyarrow.parquet as pq

    columns = set(pq.read_schema(DATA_PATH).names)
    assert columns == {"symbol", "minute_index", "close_ticks"}


# --- Criterion 4: the fallback is deterministic with no data file -----------------------------


def test_the_fallback_is_used_when_there_is_no_data_file(settings: Settings, tmp_path: Path):
    assert load_replay_history(settings, data_path=tmp_path / "absent.parquet") is None
    fair_value = build_fair_value(
        symbol_name="QAA", symbol_id=1, settings=settings, rng=random.Random(1), history=None
    )
    assert isinstance(fair_value, SyntheticFairValue)


def test_the_fallback_is_deterministic_with_no_data_file(settings: Settings):
    """Criterion 4, stated exactly: same seed, no data file, identical path.

    Open Issue 005 section 10.2 keeps this generator explicitly for tests and offline
    development, so it is not scaffolding to be removed once the real data arrived.
    """
    def path_of(seed: int) -> list[int]:
        fair_value = build_fair_value(
            symbol_name="QAA",
            symbol_id=1,
            settings=settings,
            rng=random.Random(seed),
            history=None,
        )
        return [fair_value.next_ticks() for _ in range(200)]

    assert path_of(20260904) == path_of(20260904)
    assert path_of(20260904) != path_of(20260905)


@pinned_data
def test_a_symbol_missing_from_the_data_falls_back_rather_than_failing(
    settings: Settings, history
):
    """Adding a symbol to the configuration before re-fetching is a normal intermediate state.

    A market maker quoting a synthetic price on one book is far better than a bot session that
    refuses to start, or a market with no maker on that book at all.
    """
    fair_value = build_fair_value(
        symbol_name="QAZ", symbol_id=99, settings=settings, rng=random.Random(3), history=history
    )
    assert isinstance(fair_value, SyntheticFairValue)


# --- the replay clock -------------------------------------------------------------------------


def test_the_clock_advances_one_simulated_minute_per_real_second():
    now = {"ns": 0}
    clock = ReplayClock(real_seconds_per_simulated_minute=1, now_ns=lambda: now["ns"])
    assert clock.simulated_minutes() == 0
    now["ns"] = 999_999_999
    assert clock.simulated_minutes() == 0
    now["ns"] = 1_000_000_000
    assert clock.simulated_minutes() == 1
    now["ns"] = 60_000_000_000
    assert clock.simulated_minutes() == 60


def test_the_clock_honours_a_different_ratio():
    now = {"ns": 0}
    clock = ReplayClock(real_seconds_per_simulated_minute=5, now_ns=lambda: now["ns"])
    now["ns"] = 4_000_000_000
    assert clock.simulated_minutes() == 0
    now["ns"] = 5_000_000_000
    assert clock.simulated_minutes() == 1


def test_a_zero_or_negative_ratio_is_refused():
    """Simulated time that does not advance means every bot quotes one price forever — a dead
    market that reports itself healthy."""
    with pytest.raises(ValueError, match="must be positive"):
        ReplayClock(real_seconds_per_simulated_minute=0)


def test_the_clock_does_not_drift_when_a_tick_runs_late():
    """The reason it is a function of elapsed time rather than a counter.

    A counter incremented once per quoting tick loses a minute whenever the loop runs late, and
    two bots keeping their own would silently disagree about what time it is — two symbols
    replaying at different speeds for reasons nobody could reproduce.
    """
    now = {"ns": 0}
    clock = ReplayClock(real_seconds_per_simulated_minute=1, now_ns=lambda: now["ns"])
    # Ten seconds pass with nothing calling the clock at all.
    now["ns"] = 10_000_000_000
    assert clock.simulated_minutes() == 10


def test_replay_is_a_lookup_not_a_step():
    """Calling twice inside one simulated minute returns the same price.

    This falls out of indexing on the clock rather than advancing a cursor, and it is what keeps
    a slow quoting loop from falling behind the market instead of catching up to it.
    """
    now = {"ns": 0}
    clock = ReplayClock(real_seconds_per_simulated_minute=1, now_ns=lambda: now["ns"])
    fair_value = ReplayFairValue(prices_ticks=(10, 20, 30), clock=clock, start_offset=0)
    assert fair_value.next_ticks() == 10
    assert fair_value.next_ticks() == 10
    now["ns"] = 1_000_000_000
    assert fair_value.next_ticks() == 20


def test_replay_wraps_at_the_end_of_the_dataset():
    """Task 5.1: "loop from a randomised offset at the end of the dataset"."""
    now = {"ns": 0}
    clock = ReplayClock(real_seconds_per_simulated_minute=1, now_ns=lambda: now["ns"])
    fair_value = ReplayFairValue(prices_ticks=(10, 20, 30), clock=clock, start_offset=2)
    assert fair_value.next_ticks() == 30
    now["ns"] = 1_000_000_000
    assert fair_value.next_ticks() == 10


def test_an_empty_or_non_positive_series_is_refused():
    clock = ReplayClock(real_seconds_per_simulated_minute=1, now_ns=lambda: 0)
    with pytest.raises(ValueError, match="cannot be empty"):
        ReplayFairValue(prices_ticks=(), clock=clock, start_offset=0)
    with pytest.raises(ValueError, match="above zero"):
        ReplayFairValue(prices_ticks=(10, 0, 30), clock=clock, start_offset=0)


@pinned_data
def test_the_start_offset_is_seeded_and_symbols_do_not_move_in_lockstep(
    settings: Settings, history
):
    """Reproducible from the master seed, and different per symbol.

    Ten symbols all starting at index 0 would move in lockstep through their own histories,
    which reintroduces exactly the correlation the ten separate instruments were chosen to avoid.
    """
    def offsets() -> list[int]:
        out = []
        for symbol in settings.symbols:
            rng = random.Random(settings.bots.seed + symbol.symbol_id)
            fair_value = build_fair_value(
                symbol_name=symbol.name,
                symbol_id=symbol.symbol_id,
                settings=settings,
                rng=rng,
                history=history,
            )
            out.append(fair_value._offset)
        return out

    assert offsets() == offsets(), "the same seed must give the same offsets"
    assert len(set(offsets())) > 1, "every symbol started at the same offset"


# --- Criterion 5: the README says where the prices come from ----------------------------------


def test_the_readme_states_the_data_provenance():
    """Criterion 5, and not a formality: the demonstration shows real price action under
    fictional names, and the project has to say so in the obvious place."""
    readme = (ROOT / "README.md").read_text().lower()
    assert "anonymised" in readme or "anonymized" in readme
    assert "historical" in readme
