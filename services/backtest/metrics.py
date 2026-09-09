"""Metrics for a completed run, including the buy-and-hold comparison.

## Why buy-and-hold is not optional

Task 7.1's Purpose says it plainly: a strategy returning 8% in a market that returned 20% has
lost money in the only sense that matters. Reporting a strategy's return without the market's
is the most flattering thing a backtest can do, so the comparison is computed here rather than
left to the reader.

The comparison is held to the **same rules as the strategy**: it buys at the first bar a
strategy could have filled at — the second bar's open, since a decision on bar 0 fills on bar 1
— and it pays the taker fee. A buy-and-hold that entered for free at bar 0's open would be
beaten by nothing, and would flatter every strategy compared against it.

## Money is integer ticks; only ratios are floats

Open Issue 016 keeps money in integers everywhere, and that rule does not relax because a
number is a *result*. `pnl_ticks`, `final_equity_ticks` and every fee are exact. Return, Sharpe
and volatility are ratios of those integers and are floats by nature — rounded on the way out,
so two runs of the same manifest serialise byte-identically (Success Criterion 2).

## Sharpe here is per bar, and deliberately not annualised

Annualising needs a periods-per-year, and the dataset's clock is *simulated* minutes at one
real second each (Open Issue 005 §10.5g). There is no honest number of simulated minutes in a
year, so annualising would mean inventing the most load-bearing constant in the figure. The
field is named `sharpe_per_bar` so it cannot be mistaken for the annualised statistic a reader
would otherwise assume.
"""

from __future__ import annotations

import statistics
from dataclasses import asdict, dataclass

from services.backtest.bars import Bar
from services.ledger.ledger import calculate_fees

#: Ratios are rounded before serialisation. Two runs of one manifest execute the same
#: operations in the same order and so already agree bit for bit; rounding is for the reader,
#: and it also removes any chance that a platform's last-place float digit becomes the reason
#: Success Criterion 2 fails.
RATIO_DIGITS = 6


@dataclass(frozen=True)
class Trade:
    """One filled order. `fee_ticks` is always the taker fee — see `runner.py`."""

    bar_index: int
    side: int
    qty: int
    price_ticks: int
    fee_ticks: int
    realised_pnl_ticks: int


@dataclass(frozen=True)
class Metrics:
    initial_cash_ticks: int
    final_equity_ticks: int
    pnl_ticks: int
    return_pct: float
    trade_count: int
    win_count: int
    win_rate_pct: float
    max_drawdown_pct: float
    volatility_per_bar_pct: float
    sharpe_per_bar: float
    fees_paid_ticks: int
    buy_and_hold_return_pct: float
    excess_return_pct: float

    def as_dict(self) -> dict:
        return asdict(self)


def _round(value: float) -> float:
    return round(value, RATIO_DIGITS)


def max_drawdown_pct(equity_curve: list[int]) -> float:
    """The deepest peak-to-trough fall, as a percentage of the peak.

    Measured on the mark-to-market curve rather than on realised P&L, because a strategy that
    never closes a losing position has a drawdown of zero by the realised measure — which is
    the opposite of the truth the number is asked for.
    """
    if not equity_curve:
        return 0.0
    peak = equity_curve[0]
    worst = 0.0
    for equity in equity_curve:
        peak = max(peak, equity)
        if peak > 0:
            worst = max(worst, (peak - equity) / peak)
    return _round(worst * 100)


def buy_and_hold_return_pct(bars: tuple[Bar, ...], initial_cash_ticks: int) -> float:
    """Buy as much as the cash allows at bar 1's open, pay the taker fee, mark at the last close.

    Bar 1, not bar 0: a strategy's earliest possible fill is the open after the first bar it was
    shown, and a benchmark entering a bar earlier is being handed a head start rather than being
    compared.
    """
    if len(bars) < 2:
        return 0.0
    entry = bars[1].open_ticks
    if entry <= 0:
        return 0.0

    # Largest quantity whose notional *and* taker fee fit the starting cash. Solved by trying
    # the unfee'd quantity and stepping down, rather than algebraically: the fee is an integer
    # floor division, so the closed form is off by one exactly where it matters.
    qty = initial_cash_ticks // entry
    while qty > 0 and entry * qty + calculate_fees(entry, qty).taker_fee_ticks > initial_cash_ticks:
        qty -= 1
    if qty <= 0:
        return 0.0

    fee = calculate_fees(entry, qty).taker_fee_ticks
    cash = initial_cash_ticks - entry * qty - fee
    final_equity = cash + qty * bars[-1].close_ticks
    return _round((final_equity - initial_cash_ticks) / initial_cash_ticks * 100)


def compute(
    *,
    bars: tuple[Bar, ...],
    trades: list[Trade],
    equity_curve: list[int],
    initial_cash_ticks: int,
) -> Metrics:
    final_equity = equity_curve[-1] if equity_curve else initial_cash_ticks
    pnl = final_equity - initial_cash_ticks

    # A win is a *closing* trade that realised a profit. Opening trades realise nothing, so
    # counting them would drive the win rate toward 50% for any strategy that trades in pairs,
    # whatever it earned.
    closing = [t for t in trades if t.realised_pnl_ticks != 0]
    wins = [t for t in closing if t.realised_pnl_ticks > 0]

    returns = [
        (equity_curve[i] - equity_curve[i - 1]) / equity_curve[i - 1]
        for i in range(1, len(equity_curve))
        if equity_curve[i - 1] > 0
    ]
    volatility = statistics.pstdev(returns) if len(returns) > 1 else 0.0
    mean_return = statistics.fmean(returns) if returns else 0.0
    sharpe = mean_return / volatility if volatility > 0 else 0.0

    benchmark = buy_and_hold_return_pct(bars, initial_cash_ticks)
    strategy_return = _round(pnl / initial_cash_ticks * 100) if initial_cash_ticks else 0.0

    return Metrics(
        initial_cash_ticks=initial_cash_ticks,
        final_equity_ticks=final_equity,
        pnl_ticks=pnl,
        return_pct=strategy_return,
        trade_count=len(trades),
        win_count=len(wins),
        win_rate_pct=_round(len(wins) / len(closing) * 100) if closing else 0.0,
        max_drawdown_pct=max_drawdown_pct(equity_curve),
        volatility_per_bar_pct=_round(volatility * 100),
        sharpe_per_bar=_round(sharpe),
        fees_paid_ticks=sum(t.fee_ticks for t in trades),
        buy_and_hold_return_pct=benchmark,
        excess_return_pct=_round(strategy_return - benchmark),
    )
