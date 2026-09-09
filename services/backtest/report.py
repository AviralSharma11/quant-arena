"""Rendering a `Result` — as text for a person, as JSON for the reproducibility test.

## The limitation is printed, not documented

Task 7.1's fourth success criterion: "the fill-model limitation is printed in the report output,
not buried." So `LIMITATION` is rendered on every text report, above the metrics rather than
below them, and it is part of the JSON too. It is not a footnote and it is not in a README,
because a number a reader has already believed cannot be un-believed by a paragraph further
down the page.

The wording says what the model *does* and what it therefore *cannot* claim. Open Issue 011 §4
is direct about why this matters — how a backtester fills orders determines whether its results
are meaningful, and it is "where most amateur backtesters quietly become worthless."
"""

from __future__ import annotations

import json
import math

from config.settings import Settings
from services.backtest.runner import Result

LIMITATION = (
    "FILL MODEL: every order fills in full at the next bar's open, and pays the taker fee.\n"
    "  It therefore assumes infinite liquidity at that price: no partial fills, no slippage,\n"
    "  and no market impact however large the order. Results overstate what a strategy would\n"
    "  achieve at size, and the overstatement grows with order size and with how thin the book\n"
    "  would really have been. Matching through the real engine against archived L2 snapshots\n"
    "  is Phase 2 (Open Issue 011 sub-decision 11c, reversed for Phase 1 by Open Issue 018\n"
    "  section 3.2), where it arrives as a before-and-after against these numbers."
)


def to_json(result: Result) -> str:
    """Canonical JSON. `sort_keys` is what makes "run twice, byte-identical" a real assertion.

    Without it two runs could differ by dictionary insertion order alone — a difference that
    says nothing about the numbers and would either fail the test spuriously or, worse, be
    "fixed" by comparing something weaker than bytes.
    """
    payload = result.as_dict()
    payload["fill_model_limitation"] = LIMITATION
    return json.dumps(payload, sort_keys=True, indent=2) + "\n"


def _ticks(value: int, tick_size_ticks: int) -> str:
    """Ticks as a decimal figure, by the same rule as the frontend's `formatTicks`.

    There is deliberately **no default tick size**. The 2026-09-07 decision made `formatTicks`
    throw rather than fall back to 1, because a default renders 7,983,040 ticks as "7983040"
    beside a correct price — a plausible number instead of a visible failure. A report is no
    less exposed to that than a chart is, so the size is required here too.
    """
    if tick_size_ticks < 1:
        raise ValueError("cannot format a price without its symbol's tick size")
    decimals = max(0, round(math.log10(tick_size_ticks)))
    sign = "-" if value < 0 else ""
    whole, part = divmod(abs(value), tick_size_ticks)
    return f"{sign}{whole:,}.{part:0{decimals}d}" if decimals else f"{sign}{whole:,}"


def tick_size_for(symbol_name: str, settings: Settings | None = None) -> int:
    """The symbol's tick size, from the one place symbol scales are defined.

    `contracts/v1/rest_and_ws.md` §2.3 makes the symbol table the only source of names and
    scales, and the 2026-09-07 decision deleted a hard-coded list for having the same defect
    one listing later. So this reads the table rather than carrying its own copy.
    """
    settings = settings or Settings.load()
    for symbol in settings.symbols:
        if symbol.name == symbol_name:
            return symbol.tick_size_ticks
    raise KeyError(f"{symbol_name} is not in the symbol table")


def to_text(result: Result, *, tick_size_ticks: int | None = None) -> str:
    m = result.metrics
    tick_size = (
        tick_size_ticks
        if tick_size_ticks is not None
        else tick_size_for(result.manifest.symbol)
    )
    lines = [
        f"Backtest {result.manifest.manifest_id} — {result.manifest.strategy} on "
        f"{result.manifest.symbol}",
        "",
        LIMITATION,
        "",
        f"  dataset            {result.manifest.dataset} "
        f"({result.manifest.dataset_sha256[:12]}…)",
        f"  bars               {result.manifest.last_bar_index + 1} of "
        f"{result.manifest.bar_minutes} simulated minute(s)",
        f"  parameters         {result.manifest.parameters}",
        f"  fees               maker {result.manifest.maker_fee_bps} bps / "
        f"taker {result.manifest.taker_fee_bps} bps (every backtest fill is a taker)",
        f"  config_hash        {result.manifest.config_hash[:16]}…",
        "",
        f"  starting cash      {_ticks(m.initial_cash_ticks, tick_size)}",
        f"  final equity       {_ticks(m.final_equity_ticks, tick_size)}",
        f"  P&L                {_ticks(m.pnl_ticks, tick_size)}",
        f"  return             {m.return_pct:+.4f}%",
        "",
        f"  BUY AND HOLD       {m.buy_and_hold_return_pct:+.4f}%",
        f"  EXCESS RETURN      {m.excess_return_pct:+.4f}%   <- the number that matters",
        "",
        f"  trades             {m.trade_count} "
        f"(first at bar {result.first_trade_bar_index})",
        f"  win rate           {m.win_rate_pct:.2f}% ({m.win_count} closing trades in profit)",
        f"  fees paid          {_ticks(m.fees_paid_ticks, tick_size)} "
        f"({m.fees_paid_ticks / m.initial_cash_ticks * 100:.4f}% of starting cash)",
        f"  max drawdown       {m.max_drawdown_pct:.4f}%",
        f"  volatility/bar     {m.volatility_per_bar_pct:.4f}%",
        f"  Sharpe/bar         {m.sharpe_per_bar:+.4f}  (per bar, NOT annualised — the clock "
        f"is simulated minutes)",
    ]
    if result.refused_intents:
        lines.append(
            f"  refused intents    {result.refused_intents} "
            f"(no margin and no short selling in Phase 1)"
        )
    if result.unfilled_at_end:
        lines.append(
            f"  unfilled at end    {result.unfilled_at_end} "
            f"(decided on the last bar, which has no next open)"
        )
    return "\n".join(lines) + "\n"
