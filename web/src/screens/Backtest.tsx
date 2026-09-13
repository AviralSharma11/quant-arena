import { useEffect, useMemo, useState } from "react";

import { BarsIcon, InfoIcon, PlayIcon, SlidersIcon, WarningIcon } from "../components/Icons";
import { formatPercent, formatTicksGrouped, toneOf } from "../format";
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
 *
 * ## Every figure on the page is one the server sent
 *
 * No run identifiers, checksums or loss counts are composed here. The manifest carries the
 * dataset, fill model, fees and config hash, and those are what the page shows about a run.
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

const BAR_WIDTHS = [1, 5, 15, 60] as const;

/** A manifest value as text, or `—` when the server did not send it. */
function manifestText(manifest: Record<string, unknown>, key: string): string {
  const value = manifest[key];
  return value === undefined || value === null ? "—" : String(value);
}

export function Backtest({ symbols }: { symbols: readonly Symbol[] }) {
  const [strategies, setStrategies] = useState<readonly StrategyOption[]>([]);
  const [strategiesFailed, setStrategiesFailed] = useState(false);
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

  // Only a signed-in visitor reaches this screen; `App` sends everyone else to sign in.
  useEffect(() => {
    fetch("/backtests/strategies")
      .then((response) => (response.ok ? response.json() : null))
      .then((body) => {
        if (!body) {
          setStrategiesFailed(true);
          return;
        }
        setStrategies(body.strategies);
        setMaxDay(body.max_day);
        setLastDay(body.max_day);
        if (
          body.strategies.length > 0 &&
          !body.strategies.some((s: StrategyOption) => s.id === strategy)
        ) {
          setStrategy(body.strategies[0].id);
        }
      })
      .catch(() => {
        // Leave the list empty; the form says so rather than offering a strategy the server
        // may not have.
        setStrategiesFailed(true);
      });
    // Runs once: the strategy list does not change while the page is open.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const chosen = useMemo(
    () => strategies.find((option) => option.id === strategy) ?? null,
    [strategies, strategy],
  );

  const rangeInvalid = firstDay < 1 || lastDay > maxDay || firstDay > lastDay;
  const span = rangeInvalid ? 0 : lastDay - firstDay + 1;

  function resetDefaults() {
    setFirstDay(1);
    setLastDay(maxDay);
    setBarMinutes(5);
    if (symbols.length > 0) setSymbol(symbols[0].name);
    if (strategies.length > 0) setStrategy(strategies[0].id);
    setError(null);
  }

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
            : Array.isArray(detail) && typeof detail[0]?.msg === "string"
              ? detail[0].msg
              : (detail?.message ?? detail?.reason ?? `The run failed (${response.status}).`),
        );
        setResult(null);
        return;
      }
      setResult(await response.json());
    } catch {
      setError("Could not reach the exchange.");
      setResult(null);
    } finally {
      setRunning(false);
    }
  }

  // Amounts are scaled by the tick size the result carries, not by a lookup in the symbol
  // table: the form may have moved to another symbol since the run, and the result is the one
  // source that names the scale its own numbers were computed at.
  const money = (ticks: number, sign = false) =>
    formatTicksGrouped(
      ticks,
      result
        ? { symbol_id: 0, name: "", tick_size_ticks: result.tick_size_ticks, lot_size: 1 }
        : undefined,
      { currency: true, sign },
    );

  return (
    <section className="backtest-grid" aria-label="Backtest">
      <div className="bt-left">
        <form className="panel" onSubmit={submit}>
          <div className="panel-head">
            <h3>
              <SlidersIcon size={14} /> Backtest parameters
            </h3>
            <span className="panel-meta">Run inputs</span>
          </div>

          <div className="bt-form">
            <div className="field">
              <div className="field-head">
                <label htmlFor="bt-strategy">Strategy</label>
                <span className="field-meta">
                  {strategies.length > 0 ? `${strategies.length} available` : "—"}
                </span>
              </div>
              <select
                id="bt-strategy"
                className="select"
                value={strategy}
                onChange={(e) => setStrategy(e.target.value)}
                disabled={strategies.length === 0}
              >
                {strategies.map((option) => (
                  <option key={option.id} value={option.id}>
                    {option.name}
                  </option>
                ))}
              </select>
              {chosen && (
                <p className="strategy-note">
                  <InfoIcon size={14} />
                  <span>{chosen.description}</span>
                </p>
              )}
              {strategiesFailed && (
                <p className="field-hint">The strategy list could not be loaded.</p>
              )}
            </div>

            <div className="field">
              <div className="field-head">
                <label htmlFor="bt-symbol">Symbol</label>
                <span className="field-meta">{symbols.length} listed</span>
              </div>
              <select
                id="bt-symbol"
                className="select"
                value={symbol}
                onChange={(e) => setSymbol(e.target.value)}
              >
                {symbols.map((entry) => (
                  <option key={entry.symbol_id} value={entry.name}>
                    {entry.name}
                  </option>
                ))}
              </select>
            </div>

            {/* Simulated days, not dates. The pinned dataset carries a minute index and no
                wall-clock time at all, so a date picker would be showing an invented fact. */}
            <div className="field">
              <div className="field-row">
                <div className="field">
                  <div className="field-head">
                    <label htmlFor="bt-first">From day</label>
                  </div>
                  <div className="input-wrap">
                    <input
                      id="bt-first"
                      type="number"
                      min={1}
                      max={maxDay}
                      value={firstDay}
                      onChange={(e) => setFirstDay(Number(e.target.value))}
                    />
                    <span className="input-affix">Day</span>
                  </div>
                </div>
                <div className="field">
                  <div className="field-head">
                    <label htmlFor="bt-last">To day</label>
                  </div>
                  <div className="input-wrap">
                    <input
                      id="bt-last"
                      type="number"
                      min={1}
                      max={maxDay}
                      value={lastDay}
                      onChange={(e) => setLastDay(Number(e.target.value))}
                    />
                    <span className="input-affix">Day</span>
                  </div>
                </div>
              </div>
              <p className="field-hint">
                {rangeInvalid ? (
                  <>Choose days between 1 and {maxDay}, first no later than last.</>
                ) : (
                  <>
                    Span:{" "}
                    <b>
                      {span} simulated day{span === 1 ? "" : "s"}
                    </b>{" "}
                    of {maxDay} (no calendar dates).
                  </>
                )}
              </p>
            </div>

            <fieldset className="field">
              <legend>Bar width</legend>
              <div className="segmented">
                {BAR_WIDTHS.map((width) => (
                  <button
                    key={width}
                    type="button"
                    aria-pressed={barMinutes === width}
                    onClick={() => setBarMinutes(width)}
                    aria-label={`${width} simulated minute${width === 1 ? "" : "s"}`}
                  >
                    {width}m
                  </button>
                ))}
              </div>
            </fieldset>

            <button
              type="submit"
              className="btn btn-primary btn-block"
              disabled={running || symbol === "" || strategies.length === 0 || rangeInvalid}
            >
              <PlayIcon size={16} />
              {running ? "Running…" : "Run backtest"}
            </button>

            {error && (
              <p className="alert error" role="alert">
                {error}
              </p>
            )}

            <div className="field-head">
              <span />
              <button type="button" className="link-button" onClick={resetDefaults}>
                Reset defaults
              </button>
            </div>
          </div>
        </form>

        {result && (
          <section className="panel" aria-label="Execution model">
            <div className="panel-head">
              <h3>Execution model</h3>
            </div>
            <dl className="kv">
              <div>
                <dt>Fill model</dt>
                <dd>{manifestText(result.manifest, "fill_model")}</dd>
              </div>
              <div>
                <dt>Fees</dt>
                <dd>
                  maker {manifestText(result.manifest, "maker_fee_bps")} bps / taker{" "}
                  {manifestText(result.manifest, "taker_fee_bps")} bps
                </dd>
              </div>
              <div>
                <dt>Dataset</dt>
                <dd>{manifestText(result.manifest, "dataset")}</dd>
              </div>
            </dl>
          </section>
        )}
      </div>

      <div className="bt-right">
        {!result ? (
          <section className="panel bt-empty" aria-label="No results yet">
            <BarsIcon size={28} />
            <h3>{running ? "Running backtest…" : "No run yet"}</h3>
            <p>
              Choose a strategy, a symbol and a day range, then run. Results appear here, with the
              fill model's limitation stated above them.
            </p>
          </section>
        ) : (
          <>
            {/* Above the table, deliberately. A number a reader has already believed cannot be
                un-believed by a paragraph further down the page. */}
            <section className="panel limitation" aria-label="Fill-model limitation">
              <WarningIcon size={20} className="limitation-icon" />
              <div>
                <p className="label-caps limitation-label">Simulation limitation</p>
                <pre>{result.limitation}</pre>
              </div>
            </section>

            <ResultsTable result={result} money={money} />
          </>
        )}
      </div>
    </section>
  );
}

function ResultsTable({
  result,
  money,
}: {
  result: BacktestResult;
  money: (ticks: number, sign?: boolean) => string;
}) {
  const m = result.metrics;
  const manifest = result.manifest;
  return (
    <section className="panel bt-results" aria-label="Results">
      <div className="panel-head">
        <h3>
          <BarsIcon size={14} /> Results
        </h3>
        <span className="panel-meta">
          {manifestText(manifest, "strategy")} on {manifestText(manifest, "symbol")}
        </span>
      </div>

      <table>
        <caption>
          <div className="run-line">
            <span className="run-id">Run {result.id}</span>
            <span className="run-span">
              {manifestText(manifest, "bar_minutes")}-minute bars
              {result.first_trade_bar_index !== null &&
                ` · first trade at bar ${result.first_trade_bar_index}`}
            </span>
          </div>
        </caption>
        <tbody>
          <Row label="Starting cash">{money(m.initial_cash_ticks)}</Row>
          <Row label="Final equity">{money(m.final_equity_ticks)}</Row>
          <Row label="Profit and loss" note="Net of fees" className="pnl">
            <span className={`chip ${toneOf(m.pnl_ticks)}`}>
              <span className={toneOf(m.pnl_ticks)}>{money(m.pnl_ticks, true)}</span>
            </span>
          </Row>
          <Row label="Strategy return">
            <span className={toneOf(m.return_pct)}>{formatPercent(m.return_pct)}</span>
          </Row>
          <Row label="Buy-and-hold return">
            <span className="muted">{formatPercent(m.buy_and_hold_return_pct)}</span>
          </Row>
          <tr className="headline">
            <th scope="row">
              <span className="label-caps accent">Headline comparison</span>
              <span className="headline-title">Excess return over buy-and-hold</span>
            </th>
            <td>
              <span className={`headline-value ${toneOf(m.excess_return_pct)}`}>
                {formatPercent(m.excess_return_pct)}
              </span>
            </td>
          </tr>
          <Row label="Trades">
            {m.trade_count}
            <span className="row-note">{m.win_count} closing trades in profit</span>
          </Row>
          <Row label="Win rate">
            <span className="meter" aria-hidden="true">
              <span
                style={{
                  width: `${Math.max(0, Math.min(100, m.win_rate_pct))}%`,
                }}
              />
            </span>
            {m.win_rate_pct.toFixed(2)}%
          </Row>
          <Row label="Fees paid">{money(m.fees_paid_ticks)}</Row>
          <Row label="Max drawdown">
            <span className={m.max_drawdown_pct > 0 ? "down" : "flat"}>
              {m.max_drawdown_pct.toFixed(4)}%
            </span>
          </Row>
          <Row label="Volatility per bar">{m.volatility_per_bar_pct.toFixed(4)}%</Row>
          {/* Named per bar, because the clock is simulated minutes and there is no honest
              number of them in a year. */}
          <Row label="Sharpe per bar">
            <span className={toneOf(m.sharpe_per_bar)}>{m.sharpe_per_bar.toFixed(4)}</span>
          </Row>
          <Row label="Refused intents">
            {result.refused_intents}
            <span className="row-note">No margin and no short selling in Phase 1</span>
          </Row>
          {result.unfilled_at_end > 0 && (
            <Row label="Unfilled at end">{result.unfilled_at_end}</Row>
          )}
        </tbody>
      </table>

      <p className="bt-foot">
        <span>config_hash</span>
        <code>{manifestText(manifest, "config_hash")}</code>
        <span>· metrics only, no equity curve (Task 7.2)</span>
      </p>
    </section>
  );
}

function Row({
  label,
  note,
  className,
  children,
}: {
  label: string;
  note?: string;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <tr className={className}>
      <th scope="row">
        {label}
        {note && <span className="row-note">{note}</span>}
      </th>
      <td>{children}</td>
    </tr>
  );
}
