"""The runner: the fill model, the fees, the refusals, and the no-lookahead guarantee.

Most of these run against a hand-written bar series rather than the pinned dataset. That is
deliberate: a fill price assertion against real prices proves the arithmetic on one accidental
number, while a series where the open of bar 1 is unmistakably 200 proves the *rule*.
"""

from __future__ import annotations

import pytest

from contracts.v1.generated.contracts import Side
from services.backtest.bars import Bar
from services.backtest.manifest import Manifest
from services.backtest.runner import run
from services.backtest.strategy import OrderIntent, PortfolioView
from services.ledger.ledger import calculate_fees


def bar(index: int, open_ticks: int, close_ticks: int | None = None) -> Bar:
    close = open_ticks if close_ticks is None else close_ticks
    return Bar(
        index=index, first_minute=index,
        open_ticks=open_ticks, high_ticks=max(open_ticks, close),
        low_ticks=min(open_ticks, close), close_ticks=close,
    )


def manifest(**overrides) -> Manifest:
    base = dict(
        strategy="scripted", parameters={}, symbol="QAA", dataset="test",
        dataset_sha256="0" * 64, bar_minutes=1, first_bar_index=0, last_bar_index=1,
        initial_cash_ticks=1_000_000, config_hash="test",
    )
    return Manifest(**{**base, **overrides})


class Scripted:
    """Emits a fixed list of intents on a given bar index, and records what it was handed."""

    def __init__(self, script: dict[int, list[OrderIntent]]) -> None:
        self.name = "scripted"
        self.script = script
        self.seen: list[tuple[Bar, PortfolioView]] = []

    def parameters(self) -> dict[str, int]:
        return {}

    def on_bar(self, bar_, portfolio):
        self.seen.append((bar_, portfolio))
        return list(self.script.get(bar_.index, []))


# -- the fill model -----------------------------------------------------------------------

def test_an_intent_fills_at_the_next_bars_open_never_this_bars_close():
    """The rule the whole backtest rests on: a signal cannot be acted on at the price that
    produced it. Bar 0 closes at 999 and bar 1 opens at 200 — the fill must be 200."""
    bars = (bar(0, 100, 999), bar(1, 200, 300))
    strategy = Scripted({0: [OrderIntent(side=int(Side.BUY), qty=1)]})

    result = run(strategy=strategy, bars=bars, manifest=manifest(), initial_cash_ticks=1_000_000)

    assert len(result.trades) == 1
    assert result.trades[0].price_ticks == 200
    assert result.trades[0].bar_index == 1


def test_every_fill_pays_the_takers_fee_from_the_ledger_not_a_local_copy():
    """A bar-open fill crosses whatever is there, so it is always the aggressor. The rate comes
    from `services/ledger/ledger.py`, so the exchange and the backtester cannot disagree."""
    bars = (bar(0, 100), bar(1, 10_000))
    strategy = Scripted({0: [OrderIntent(side=int(Side.BUY), qty=3)]})

    result = run(strategy=strategy, bars=bars, manifest=manifest(), initial_cash_ticks=1_000_000)

    expected = calculate_fees(10_000, 3)
    assert result.trades[0].fee_ticks == expected.taker_fee_ticks
    assert result.trades[0].fee_ticks != expected.maker_fee_ticks


def test_an_intent_on_the_final_bar_is_reported_rather_than_dropped():
    """There is no next open to fill at. A strategy that appears to trade less than it did is
    a strategy being measured wrongly."""
    bars = (bar(0, 100), bar(1, 200))
    strategy = Scripted({1: [OrderIntent(side=int(Side.BUY), qty=1)]})

    result = run(strategy=strategy, bars=bars, manifest=manifest(), initial_cash_ticks=1_000_000)

    assert result.trades == []
    assert result.unfilled_at_end == 1


def test_a_run_needs_at_least_two_bars():
    with pytest.raises(ValueError, match="one to decide on and one to fill at"):
        run(strategy=Scripted({}), bars=(bar(0, 100),), manifest=manifest(),
            initial_cash_ticks=1_000)


# -- lookahead, structurally --------------------------------------------------------------

def test_the_strategy_is_handed_one_bar_at_a_time_and_never_a_series():
    """Success Criterion 3, and the reason it is met by the type signature: what `on_bar`
    receives is a single frozen `Bar` and a frozen `PortfolioView`. There is no sequence, no
    index into one, and no callback that could fetch another — so no expression a strategy can
    write reaches bar N+1."""
    bars = tuple(bar(i, 100 + i) for i in range(5))
    strategy = Scripted({})

    run(strategy=strategy, bars=bars, manifest=manifest(), initial_cash_ticks=1_000_000)

    assert [b.index for b, _ in strategy.seen] == [0, 1, 2, 3, 4]
    for seen_bar, view in strategy.seen:
        assert isinstance(seen_bar, Bar)
        assert isinstance(view, PortfolioView)
        # Frozen: a strategy cannot reach back into the runner's accounting through what it
        # was handed. Under Phase 2's sandbox this object will be a copy whether or not anyone
        # remembered to make one, so it behaves that way here.
        with pytest.raises(Exception):
            seen_bar.close_ticks = 1  # type: ignore[misc]
        with pytest.raises(Exception):
            view.cash_ticks = 1  # type: ignore[misc]


def test_the_portfolio_view_a_strategy_sees_excludes_the_fill_it_has_not_had_yet():
    """On bar 0 the strategy asks to buy; on bar 1 it is shown the portfolio *after* that fill.
    Any other ordering would let a strategy see a position before the price that created it."""
    bars = (bar(0, 100), bar(1, 200), bar(2, 300))
    strategy = Scripted({0: [OrderIntent(side=int(Side.BUY), qty=2)]})

    run(strategy=strategy, bars=bars, manifest=manifest(), initial_cash_ticks=1_000_000)

    positions = [view.position for _, view in strategy.seen]
    assert positions == [0, 2, 2]


# -- refusals -----------------------------------------------------------------------------

def test_a_buy_the_cash_cannot_cover_is_refused_and_counted():
    """No margin in Phase 1, so this is an ordinary outcome rather than an error. Raising
    would end a run at its most interesting moment."""
    bars = (bar(0, 100), bar(1, 1_000_000))
    strategy = Scripted({0: [OrderIntent(side=int(Side.BUY), qty=50)]})

    result = run(strategy=strategy, bars=bars, manifest=manifest(), initial_cash_ticks=1_000)

    assert result.trades == []
    assert result.refused_intents == 1


def test_the_fee_is_part_of_what_the_cash_must_cover():
    """Notional alone fits; notional plus the taker fee does not. Off by exactly the fee, which
    is the boundary a naive check would get wrong."""
    price, qty = 10_000, 10
    notional = price * qty
    fee = calculate_fees(price, qty).taker_fee_ticks
    assert fee > 0

    bars = (bar(0, 1), bar(1, price))
    strategy = Scripted({0: [OrderIntent(side=int(Side.BUY), qty=qty)]})

    exact = run(strategy=strategy, bars=bars, manifest=manifest(),
                initial_cash_ticks=notional + fee)
    assert len(exact.trades) == 1

    short = run(strategy=Scripted({0: [OrderIntent(side=int(Side.BUY), qty=qty)]}),
                bars=bars, manifest=manifest(), initial_cash_ticks=notional + fee - 1)
    assert short.refused_intents == 1


def test_a_sell_of_stock_not_held_is_refused_no_short_selling_in_phase_1():
    bars = (bar(0, 100), bar(1, 200))
    strategy = Scripted({0: [OrderIntent(side=int(Side.SELL), qty=1)]})

    result = run(strategy=strategy, bars=bars, manifest=manifest(), initial_cash_ticks=1_000_000)

    assert result.trades == []
    assert result.refused_intents == 1


# -- accounting ---------------------------------------------------------------------------

def test_a_round_trip_realises_profit_net_of_both_fees():
    """Buy at 1,000 and sell at 2,000: the realised figure must be net, because a "win" that
    was gross would count a trade the fee schedule actually lost."""
    bars = (bar(0, 1), bar(1, 1_000), bar(2, 2_000), bar(3, 2_000))
    strategy = Scripted({
        0: [OrderIntent(side=int(Side.BUY), qty=1)],
        1: [OrderIntent(side=int(Side.SELL), qty=1)],
    })

    result = run(strategy=strategy, bars=bars, manifest=manifest(), initial_cash_ticks=1_000_000)

    buy, sell = result.trades
    assert (buy.price_ticks, sell.price_ticks) == (1_000, 2_000)
    assert sell.realised_pnl_ticks == 2_000 - 1_000 - sell.fee_ticks
    assert result.metrics.win_count == 1

    expected_cash = 1_000_000 - (1_000 + buy.fee_ticks) + (2_000 - sell.fee_ticks)
    assert result.metrics.final_equity_ticks == expected_cash
    assert result.metrics.fees_paid_ticks == buy.fee_ticks + sell.fee_ticks
