import { useEffect, useMemo, useState } from "react";

import { formatTicks } from "../stream/symbols";
import type { Symbol } from "../stream/symbols";

/**
 * The backtest screen — the third and final one (Open Issue 014 §11.1).
 *
 * ## Plain React state throughout, and that is not a lapse
 *
 * `buffer.ts` keeps the book and the tape out of React because twenty messages a second would
 * be twenty re-renders. A backtest result is *one object, arriving when a human clicks a
 * button*, which is exactly the low-frequency chrome Open Issue 014 §14a assigns to framework
 * state. The 2026-09-07 decision put it plainly: the rule is about frequency, not principle.
 *
 * ## The limitation text comes from the server
 *
 * Success Criterion 2 asks for it on the page rather than in documentation. It is rendered from
 * `result.limitation`, which the API sends, rather than from a copy kept here — one source, so
 * the page and the CLI report cannot drift into describing different fill models.
 *
 * ## No charts, and no strategy editor
 *
 * 7.2's Boundaries: a metrics table only, no equity-curve or drawdown charts, no strategy
 * editor and no parameter tuning interface. So the form carries symbol, day range and bar
 * width — all properties of the *run* — and never the strategy's own windows.
 */

interface StrategyOption {
  id: string;
  name: string;
  description: string;
}

interface Metrics {
  initial_cash_ticks: number;
  final_equity_ticks: number;
  pnl_ticks: number;
  return_pct: number;
  trade_count: number;
  win_count: number;
  win_rate_pct: number;
  max_drawdown_pct: number;
  volatility_per_bar_pct: number;
  sharpe_per_bar: number;
  fees_paid_ticks: number;
  buy_and_hold_return_pct: number;
  excess_return_pct: number;
}

interface BacktestResult {
  id: string;
  manifest: Record<string, unknown>;
  metrics: Metrics;
  limitation: string;
  tick_size_ticks: number;
  refused_intents: number;
  unfilled_at_end: number;
  first_trade_bar_index: number | null;
}

function pct(value: number): string {
  return `${value >= 0 ? "+" : ""}${value.toFixed(4)}%`;
}

export function Backtest({
  symbols,
  signedIn,
}: {
  symbols: readonly Symbol[];
  signedIn: boolean;
}) {
  const [strategies, setStrategies] = useState<readonly StrategyOption[]>([]);
  const [maxDay, setMaxDay] = useState(7);
  const [strategy, setStrategy] = useState("sma_crossover");
  const [symbol, setSymbol] = useState("");
  const [barMinutes, setBarMinutes] = useState(5);
  const [firstDay, setFirstDay] = useState(1);
  const [lastDay, setLastDay] = useState(7);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<BacktestResult | null>(null);

  // The symbol table is the only source of names and scales (§2.3). Defaulting to the first
  // listed symbol rather than to a literal "QAA" for the reason the 2026-09-07 decision deleted
  // STREAM_SYMBOLS: a hard-coded name has the same defect one listing later.
  useEffect(() => {
    if (symbol === "" && symbols.length > 0) setSymbol(symbols[0].name);
  }, [symbols, symbol]);

  useEffect(() => {
    if (!signedIn) return;
    fetch("/backtests/strategies")
      .then((response) => (response.ok ? response.json() : null))
      .then((body) => {
        if (!body) return;
        setStrategies(body.strategies);
        setMaxDay(body.max_day);
        setLastDay(body.max_day);
      })
      .catch(() => {
        // Leave the list empty; the form says so rather than offering a strategy the server
        // may not have.
      });
  }, [signedIn]);

  const chosen = useMemo(
    () => strategies.find((option) => option.id === strategy) ?? null,
    [strategies, strategy],
  );

  const symbolForResult = useMemo(
    () => symbols.find((entry) => entry.name === symbol),
    [symbols, symbol],
  );

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setRunning(true);
    setError(null);
    try {
      const response = await fetch("/backtests", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          symbol,
          strategy,
          bar_minutes: barMinutes,
          first_day: firstDay,
          last_day: lastDay,
        }),
      });
      if (!response.ok) {
        const body = await response.json().catch(() => null);
        const detail = body?.detail;
        setError(
          typeof detail === "string"
            ? detail
            : (detail?.message ?? detail?.reason ?? `the run failed (${response.status})`),
        );
        setResult(null);
        return;
      }
      setResult(await response.json());
    } catch {
      setError("could not reach the exchange");
      setResult(null);
    } finally {
      setRunning(false);
    }
  }

  if (!signedIn) {
    return (
      <section>
        <h1>Backtest</h1>
        <p className="pending">Sign in to run a backtest.</p>
      </section>
    );
  }

  return (
    <section className="backtest">
      <h1>Backtest</h1>

      <form onSubmit={submit}>
        <label>
          Strategy
          <select value={strategy} onChange={(e) => setStrategy(e.target.value)}>
            {strategies.map((option) => (
              <option key={option.id} value={option.id}>
                {option.name}
              </option>
            ))}
          </select>
        </label>

        <label>
          Symbol
          <select value={symbol} onChange={(e) => setSymbol(e.target.value)}>
            {symbols.map((entry) => (
              <option key={entry.symbol_id} value={entry.name}>
                {entry.name}
              </option>
            ))}
          </select>
        </label>

        {/* Simulated days, not dates. The pinned dataset carries a minute index and no
            wall-clock time at all, so a date picker would be showing an invented fact. */}
        <label>
          From day
          <input
            type="number"
            min={1}
            max={maxDay}
            value={firstDay}
            onChange={(e) => setFirstDay(Number(e.target.value))}
          />
        </label>
        <label>
          To day
          <input
            type="number"
            min={1}
            max={maxDay}
            value={lastDay}
            onChange={(e) => setLastDay(Number(e.target.value))}
          />
        </label>

        <label>
          Bar width
          <select value={barMinutes} onChange={(e) => setBarMinutes(Number(e.target.value))}>
            {[1, 5, 15, 60].map((width) => (
              <option key={width} value={width}>
                {width} simulated minute{width === 1 ? "" : "s"}
              </option>
            ))}
          </select>
        </label>

        <button type="submit" disabled={running || symbol === ""}>
          {running ? "Running…" : "Run backtest"}
        </button>
      </form>

      {chosen && <p className="strategy-note">{chosen.description}</p>}
      {error && <p role="alert">{error}</p>}

      {result && (
        <>
          {/* Above the table, deliberately. A number a reader has already believed cannot be
              un-believed by a paragraph further down the page. */}
          <pre className="limitation">{result.limitation}</pre>

          <table>
            <caption>
              Run {result.id} — {result.metrics.trade_count} trades
              {result.first_trade_bar_index !== null &&
                `, first at bar ${result.first_trade_bar_index}`}
            </caption>
            <tbody>
              <Row label="Starting cash">
                {formatTicks(result.metrics.initial_cash_ticks, symbolForResult)}
              </Row>
              <Row label="Final equity">
                {formatTicks(result.metrics.final_equity_ticks, symbolForResult)}
              </Row>
              <Row label="P&L">{formatTicks(result.metrics.pnl_ticks, symbolForResult)}</Row>
              <Row label="Return">{pct(result.metrics.return_pct)}</Row>
              <Row label="Buy and hold">{pct(result.metrics.buy_and_hold_return_pct)}</Row>
              <Row label="Excess return" emphasis>
                {pct(result.metrics.excess_return_pct)}
              </Row>
              <Row label="Trades">{String(result.metrics.trade_count)}</Row>
              <Row label="Win rate">
                {`${result.metrics.win_rate_pct.toFixed(2)}% (${result.metrics.win_count} closing trades in profit)`}
              </Row>
              <Row label="Fees paid">
                {formatTicks(result.metrics.fees_paid_ticks, symbolForResult)}
              </Row>
              <Row label="Max drawdown">{`${result.metrics.max_drawdown_pct.toFixed(4)}%`}</Row>
              <Row label="Volatility per bar">
                {`${result.metrics.volatility_per_bar_pct.toFixed(4)}%`}
              </Row>
              {/* Named per bar, because the clock is simulated minutes and there is no honest
                  number of them in a year. */}
              <Row label="Sharpe per bar">{result.metrics.sharpe_per_bar.toFixed(4)}</Row>
              {result.refused_intents > 0 && (
                <Row label="Refused intents">
                  {`${result.refused_intents} — no margin and no short selling in Phase 1`}
                </Row>
              )}
            </tbody>
          </table>
        </>
      )}
    </section>
  );
}

function Row({
  label,
  children,
  emphasis,
}: {
  label: string;
  children: React.ReactNode;
  emphasis?: boolean;
}) {
  return (
    <tr className={emphasis ? "emphasis" : undefined}>
      <th scope="row">{label}</th>
      <td>{children}</td>
    </tr>
  );
}
