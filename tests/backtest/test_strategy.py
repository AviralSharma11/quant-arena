"""The SMA crossover, and the interface it is an instance of."""

from __future__ import annotations

import pytest

from contracts.v1.generated.contracts import Side
from services.backtest.bars import Bar
from services.backtest.strategy import OrderIntent, PortfolioView, SmaCrossover


def view(position: int = 0, cash: int = 10**9, index: int = 0) -> PortfolioView:
    return PortfolioView(cash_ticks=cash, position=position, bar_index=index)


def feed(strategy: SmaCrossover, closes: list[int], position: int = 0) -> list[OrderIntent]:
    """Run a price path through the strategy, tracking position as the runner would."""
    intents: list[OrderIntent] = []
    for i, close in enumerate(closes):
        for intent in strategy.on_bar(
            Bar(index=i, first_minute=i, open_ticks=close, high_ticks=close,
                low_ticks=close, close_ticks=close),
            view(position=position, index=i),
        ):
            intents.append(intent)
            position += intent.qty if intent.side == int(Side.BUY) else -intent.qty
    return intents


def test_it_is_silent_until_the_slow_window_is_full():
    """A mean over a short window is a different statistic wearing the same name. Refusing to
    trade on a number that does not exist yet is not inactivity."""
    strategy = SmaCrossover(fast=2, slow=5)
    assert feed(strategy, [10, 20, 30, 40]) == []


def test_it_trades_on_the_crossing_and_not_on_every_bar_the_condition_holds():
    """A strategy re-sending its intent every bar would pay the taker fee repeatedly to hold a
    position it already had — measuring the fee schedule rather than the signal."""
    rising = list(range(100, 160))
    intents = feed(SmaCrossover(fast=2, slow=5), rising)
    assert len(intents) <= 1, "a monotone rise contains at most one crossing"


def test_a_rise_then_a_fall_buys_then_sells():
    path = list(range(100, 140)) + list(range(140, 100, -1))
    intents = feed(SmaCrossover(fast=3, slow=10), path)
    sides = [i.side for i in intents]
    assert int(Side.BUY) in sides and int(Side.SELL) in sides
    assert sides.index(int(Side.BUY)) < sides.index(int(Side.SELL))


def test_it_never_sells_what_it_does_not_hold():
    """Phase 1 has no short selling. An unconditional sell would be an intent the runner must
    refuse, which is noise in the trade count rather than a decision."""
    falling = list(range(160, 100, -1))
    assert feed(SmaCrossover(fast=2, slow=5), falling, position=0) == []


def test_parameters_are_reported_for_the_manifest():
    assert SmaCrossover(fast=4, slow=9, qty=3).parameters() == {"fast": 4, "slow": 9, "qty": 3}


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"fast": 10, "slow": 10}, "shorter"),
        ({"fast": 30, "slow": 10}, "shorter"),
        ({"fast": 0, "slow": 10}, "at least one bar"),
        ({"fast": 2, "slow": 5, "qty": 0}, "at least one unit"),
    ],
)
def test_nonsense_parameters_are_refused_at_construction(kwargs, message):
    """A fast window longer than the slow one inverts the signal silently — the backtest still
    runs and still reports numbers, which is the worst available failure."""
    with pytest.raises(ValueError, match=message):
        SmaCrossover(**kwargs)
