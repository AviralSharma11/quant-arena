"""Bar construction over the pinned dataset.

The dataset holds one close per simulated minute and no OHLC, so a bar is built rather than
read. These tests pin the construction, because every number the backtester reports is
downstream of it.
"""

from __future__ import annotations

import pytest

from services.backtest.bars import Bar, build_bars, load_bars
from services.bots.fairvalue import DATA_PATH
from config.settings import Settings


def test_a_bar_is_open_high_low_close_over_its_bucket():
    bars = build_bars([10, 40, 20, 90, 30, 50], bar_minutes=3)
    assert bars == (
        Bar(index=0, first_minute=0, open_ticks=10, high_ticks=40, low_ticks=10, close_ticks=20),
        Bar(index=1, first_minute=3, open_ticks=90, high_ticks=90, low_ticks=30, close_ticks=50),
    )


def test_a_trailing_partial_bucket_is_dropped_not_emitted_short():
    """A final bar built from two minutes when every other holds three is a different
    measurement, and a strategy's last signal would be taken against it."""
    bars = build_bars([1, 2, 3, 4, 5], bar_minutes=3)
    assert len(bars) == 1
    assert bars[0].close_ticks == 3


def test_one_minute_bars_collapse_to_a_point_and_that_is_stated():
    """At width 1 the open and close of a bar are the same number. Not a defect — but it is
    why the default is wider, and why the docstring says so."""
    bars = build_bars([7, 8, 9], bar_minutes=1)
    assert len(bars) == 3
    for bar in bars:
        assert bar.open_ticks == bar.high_ticks == bar.low_ticks == bar.close_ticks


def test_a_bar_width_below_one_is_refused():
    with pytest.raises(ValueError, match="at least 1"):
        build_bars([1, 2, 3], bar_minutes=0)


def test_the_real_dataset_loads_and_covers_the_whole_series():
    """10,080 simulated minutes per symbol, so 2,016 five-minute bars with nothing left over."""
    bars = load_bars("QAA", bar_minutes=5, expected_sha256=Settings.load().replay_data_sha256)
    assert len(bars) == 2016
    assert bars[0].first_minute == 0
    assert bars[-1].first_minute == 10075
    assert all(b.low_ticks <= b.open_ticks <= b.high_ticks for b in bars)
    assert all(b.low_ticks <= b.close_ticks <= b.high_ticks for b in bars)


def test_an_unlisted_symbol_names_what_is_available():
    with pytest.raises(KeyError, match="QAA"):
        load_bars("NOPE", bar_minutes=1)


def test_a_dataset_that_fails_its_checksum_raises(tmp_path):
    """A silently different file means a backtest nobody can reproduce — which is the one
    thing the run manifest exists to make impossible."""
    fake = tmp_path / "market_history.parquet"
    fake.write_bytes(DATA_PATH.read_bytes())
    with pytest.raises(ValueError, match="does not match replay.data_sha256"):
        load_bars("QAA", bar_minutes=1, data_path=fake, expected_sha256="0" * 64)
