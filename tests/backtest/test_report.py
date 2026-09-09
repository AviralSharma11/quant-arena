"""Success Criteria 1 and 4: every metric plus buy-and-hold, and the limitation printed."""

from __future__ import annotations

import json

import pytest

from services.backtest.bars import Bar
from services.backtest.metrics import buy_and_hold_return_pct, max_drawdown_pct
from services.backtest.report import LIMITATION, to_json, to_text, _ticks
from services.backtest.runner import run_from_dataset
from services.backtest.strategy import SmaCrossover
from services.ledger.ledger import calculate_fees

REQUIRED_METRICS = (
    "pnl_ticks", "return_pct", "trade_count", "win_rate_pct", "max_drawdown_pct",
    "volatility_per_bar_pct", "sharpe_per_bar", "buy_and_hold_return_pct",
)


@pytest.fixture(scope="module")
def result():
    return run_from_dataset(strategy=SmaCrossover(fast=10, slow=30), symbol="QAA", bar_minutes=5)


def test_a_backtest_produces_every_metric_and_the_comparison(result):
    metrics = result.metrics.as_dict()
    for name in REQUIRED_METRICS:
        assert name in metrics, name
    assert result.metrics.trade_count > 0, "a run that never traded measures nothing"


def test_the_fill_model_limitation_is_printed_not_buried(result):
    """Criterion 4. Above the metrics, because a number a reader has already believed cannot be
    un-believed by a paragraph further down."""
    text = to_text(result)
    assert LIMITATION in text
    assert text.index(LIMITATION) < text.index("EXCESS RETURN")
    assert "no partial fills, no slippage" in LIMITATION
    assert json.loads(to_json(result))["fill_model_limitation"] == LIMITATION


def test_the_report_names_the_benchmark_and_the_excess(result):
    text = to_text(result)
    assert "BUY AND HOLD" in text and "EXCESS RETURN" in text
    assert result.metrics.excess_return_pct == round(
        result.metrics.return_pct - result.metrics.buy_and_hold_return_pct, 6
    )


def test_sharpe_is_labelled_per_bar_rather_than_annualised(result):
    """There is no honest number of simulated minutes in a year, so annualising would mean
    inventing the most load-bearing constant in the figure."""
    assert "sharpe_per_bar" in result.metrics.as_dict()
    assert "NOT annualised" in to_text(result)


def test_a_price_cannot_be_formatted_without_its_symbols_tick_size():
    """The 2026-09-07 decision: a default renders 7,983,040 ticks as "7983040" beside a correct
    price — a plausible number instead of a visible failure."""
    with pytest.raises(ValueError, match="tick size"):
        _ticks(7_983_040, 0)
    assert _ticks(7_983_040, 100) == "79,830.40"
    assert _ticks(7_983_040, 10_000) == "798.3040"


# -- the benchmark ------------------------------------------------------------------------

def bars_from(prices: list[int]) -> tuple[Bar, ...]:
    return tuple(
        Bar(index=i, first_minute=i, open_ticks=p, high_ticks=p, low_ticks=p, close_ticks=p)
        for i, p in enumerate(prices)
    )


def test_buy_and_hold_enters_at_the_second_bars_open_and_pays_the_taker_fee():
    """The same rules as the strategy. A benchmark entering a bar earlier, or for free, would
    flatter every strategy compared against it."""
    bars = bars_from([1_000, 100, 100, 200])
    cash = 1_000

    got = buy_and_hold_return_pct(bars, cash)

    qty = 9  # 9 * 100 = 900, plus the taker fee, fits 1,000; 10 would not once the fee lands
    fee = calculate_fees(100, qty).taker_fee_ticks
    expected = ((cash - 100 * qty - fee + qty * 200) - cash) / cash * 100
    assert got == round(expected, 6)


def test_buy_and_hold_is_zero_when_there_is_nothing_to_hold():
    assert buy_and_hold_return_pct(bars_from([100]), 1_000) == 0.0
    assert buy_and_hold_return_pct(bars_from([100, 100]), 1) == 0.0


def test_drawdown_is_measured_on_the_mark_to_market_curve():
    """A strategy that never closes a losing position has a realised drawdown of zero, which is
    the opposite of the truth the number is asked for."""
    assert max_drawdown_pct([100, 200, 150, 400]) == 25.0
    assert max_drawdown_pct([100, 200, 300]) == 0.0
    assert max_drawdown_pct([]) == 0.0
