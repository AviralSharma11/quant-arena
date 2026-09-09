"""The backtest runner: bars in, a result out.

## The loop, and why its order is the whole correctness argument

For each bar *i*:

1. **Fill** whatever the strategy asked for on bar *i−1*, at **bar *i*'s open**.
2. **Show** bar *i* to the strategy and collect what it asks for next.
3. **Mark** the portfolio to bar *i*'s close, appending to the equity curve.

Step 1 before step 2 is not a style choice. It means an intent is always priced by a bar the
strategy had not seen when it formed the intent, so no signal can ever be acted on at the price
that produced it — which is the difference between a backtest and a wish. And because the
strategy is handed one `Bar` and never a series (see `strategy.py`), there is no route to a
future bar at all: lookahead is prevented by what the strategy can *reach*, not by what it is
asked not to do. Task 7.1's third criterion, met structurally.

Intents formed on the **final** bar are never filled — there is no next bar to open. They are
counted as `unfilled_at_end` and reported rather than dropped in silence, because a strategy
that appears to trade less than it did is a strategy being measured wrongly.

## Every fill is a taker

A fill at the next bar's open is an order crossing whatever is there — that is what "at the
open" means. So it pays the **taker** rate, always, and never the maker rate. This is what
makes Success Criterion 5 bite: a strategy that trades often pays 10 bps every time, which is
exactly the penalty Open Issue 011 §11.2 says a fee-free backtest lies about.

The rate is not re-implemented here. `calculate_fees` is imported from the ledger, so the
backtester and the exchange cannot drift into charging different fees for the same trade.

## Refusals are counted, not raised

A buy the cash cannot cover, or a sell of stock not held, is **refused and recorded**. Phase 1
has no margin and no short selling — both are Phase 2 — so these are ordinary outcomes of a
strategy meeting its constraints, not errors. Raising would end a run at its most interesting
moment; ignoring would report a trade count that never happened.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from contracts.v1.generated.contracts import Side

from config.settings import Settings
from services.backtest.bars import Bar, load_bars
from services.backtest.manifest import Manifest
from services.backtest.metrics import Metrics, Trade, compute
from services.backtest.strategy import OrderIntent, PortfolioView, Strategy
from services.bots.fairvalue import DATA_PATH
from services.ledger.ledger import calculate_fees


@dataclass
class Result:
    manifest: Manifest
    metrics: Metrics
    trades: list[Trade]
    refused_intents: int
    unfilled_at_end: int
    first_trade_bar_index: int | None

    def as_dict(self) -> dict:
        """The serialisable result. Ordered by `sort_keys` at write time, so it is stable."""
        return {
            "manifest_id": self.manifest.manifest_id,
            "manifest": self.manifest.as_dict(),
            "metrics": self.metrics.as_dict(),
            "refused_intents": self.refused_intents,
            "unfilled_at_end": self.unfilled_at_end,
            "first_trade_bar_index": self.first_trade_bar_index,
            "trades": [
                {
                    "bar_index": t.bar_index,
                    "side": t.side,
                    "qty": t.qty,
                    "price_ticks": t.price_ticks,
                    "fee_ticks": t.fee_ticks,
                    "realised_pnl_ticks": t.realised_pnl_ticks,
                }
                for t in self.trades
            ],
        }


class _Portfolio:
    """Cash, position, and an average cost basis for realised P&L.

    Average cost rather than FIFO lots: with one symbol, one direction and whole units it gives
    the same total realised P&L, and it does so without a lot ledger whose only consumer is the
    win-rate figure. FIFO becomes worth its weight when Phase 2 adds shorts.
    """

    def __init__(self, cash_ticks: int) -> None:
        self.cash_ticks = cash_ticks
        self.position = 0
        self._cost_basis = 0  # ticks per unit, averaged over the open position

    def buy(self, price_ticks: int, qty: int) -> Trade | None:
        fee = calculate_fees(price_ticks, qty).taker_fee_ticks
        outlay = price_ticks * qty + fee
        if outlay > self.cash_ticks:
            return None
        total_cost = self._cost_basis * self.position + price_ticks * qty
        self.cash_ticks -= outlay
        self.position += qty
        self._cost_basis = total_cost // self.position
        return Trade(
            bar_index=-1, side=int(Side.BUY), qty=qty,
            price_ticks=price_ticks, fee_ticks=fee, realised_pnl_ticks=0,
        )

    def sell(self, price_ticks: int, qty: int) -> Trade | None:
        if qty > self.position:
            return None
        fee = calculate_fees(price_ticks, qty).taker_fee_ticks
        self.cash_ticks += price_ticks * qty - fee
        # Net of the fee, so a "win" means the trade actually made money rather than making it
        # gross and losing it to costs — which is the distinction the fee model exists to draw.
        realised = (price_ticks - self._cost_basis) * qty - fee
        self.position -= qty
        if self.position == 0:
            self._cost_basis = 0
        return Trade(
            bar_index=-1, side=int(Side.SELL), qty=qty,
            price_ticks=price_ticks, fee_ticks=fee, realised_pnl_ticks=realised,
        )

    def equity(self, mark_ticks: int) -> int:
        return self.cash_ticks + self.position * mark_ticks


def run(
    *,
    strategy: Strategy,
    bars: tuple[Bar, ...],
    manifest: Manifest,
    initial_cash_ticks: int,
) -> Result:
    if len(bars) < 2:
        raise ValueError(
            f"a backtest needs at least two bars — one to decide on and one to fill at, "
            f"got {len(bars)}"
        )

    portfolio = _Portfolio(initial_cash_ticks)
    trades: list[Trade] = []
    equity_curve: list[int] = []
    pending: list[OrderIntent] = []
    refused = 0
    first_trade_bar: int | None = None

    for bar in bars:
        # 1. Yesterday's decisions, at today's open. Never at the close they were made on.
        for intent in pending:
            filled = (
                portfolio.buy(bar.open_ticks, intent.qty)
                if intent.side == int(Side.BUY)
                else portfolio.sell(bar.open_ticks, intent.qty)
            )
            if filled is None:
                refused += 1
                continue
            trades.append(
                Trade(
                    bar_index=bar.index, side=filled.side, qty=filled.qty,
                    price_ticks=filled.price_ticks, fee_ticks=filled.fee_ticks,
                    realised_pnl_ticks=filled.realised_pnl_ticks,
                )
            )
            if first_trade_bar is None:
                first_trade_bar = bar.index
        pending = []

        # 2. One bar. Not a series, not an index into one.
        pending = list(
            strategy.on_bar(
                bar,
                PortfolioView(
                    cash_ticks=portfolio.cash_ticks,
                    position=portfolio.position,
                    bar_index=bar.index,
                ),
            )
        )

        # 3. Mark to this bar's close.
        equity_curve.append(portfolio.equity(bar.close_ticks))

    return Result(
        manifest=manifest,
        metrics=compute(
            bars=bars, trades=trades, equity_curve=equity_curve,
            initial_cash_ticks=initial_cash_ticks,
        ),
        trades=trades,
        refused_intents=refused,
        unfilled_at_end=len(pending),
        first_trade_bar_index=first_trade_bar,
    )


#: How much starting cash a run gets when the caller does not say. Ten times the first bar's
#: open, so a one-unit strategy is meaningfully invested rather than rounding to nothing.
DEFAULT_CASH_MULTIPLE = 10


def default_cash_ticks(bars: tuple[Bar, ...]) -> int:
    """Starting cash sized to the instrument, **not** to `exchange.initial_cash_ticks`.

    The exchange grant is 10,000,000,000 ticks, and it is that large for a reason that has
    nothing to do with research: the 2026-09-08 decision sized it at twelve full quotes of the
    dataset's peak so a designated market maker could hold a two-sided book. Handing it to a
    backtest makes a one-unit strategy 0.08% invested, and every metric it produces rounds
    toward zero — a run that appears to say the strategy is flat when what it says is that the
    account was too big to notice it.

    Ten times the opening price instead, so one unit is a tenth of the account. The figure is
    recorded in the manifest like any other input, so a run remains exactly reproducible and a
    caller who wants the exchange grant can simply pass it.
    """
    return bars[0].open_ticks * DEFAULT_CASH_MULTIPLE


def run_from_dataset(
    *,
    strategy: Strategy,
    symbol: str,
    bar_minutes: int = 1,
    first_minute: int = 0,
    last_minute: int | None = None,
    initial_cash_ticks: int | None = None,
    settings: Settings | None = None,
    data_path: Path | str | None = None,
    seed: int = 0,
) -> Result:
    """Load the pinned dataset, build the manifest from it, and run.

    The checksum is always verified here — this is the entry point a report comes out of, and a
    result whose dataset was not the pinned one is a result nobody can reproduce.
    """
    settings = settings or Settings.load()
    path = Path(data_path) if data_path is not None else DATA_PATH

    bars = load_bars(
        symbol,
        bar_minutes=bar_minutes,
        first_minute=first_minute,
        last_minute=last_minute,
        data_path=path,
        expected_sha256=settings.replay_data_sha256,
    )
    cash = initial_cash_ticks if initial_cash_ticks is not None else default_cash_ticks(bars)
    manifest = Manifest(
        strategy=strategy.name,
        parameters=strategy.parameters(),
        symbol=symbol,
        dataset=path.name,
        dataset_sha256=settings.replay_data_sha256,
        bar_minutes=bar_minutes,
        first_minute=first_minute,
        last_minute=first_minute + len(bars) * bar_minutes,
        first_bar_index=bars[0].index,
        last_bar_index=bars[-1].index,
        initial_cash_ticks=cash,
        config_hash=settings.config_hash,
        seed=seed,
    )
    return run(strategy=strategy, bars=bars, manifest=manifest, initial_cash_ticks=cash)
