/**
 * The symbol table, from `GET /symbols` and from nowhere else.
 *
 * `contracts/v1/rest_and_ws.md` §2.3 makes that endpoint the **only** source of symbol names,
 * tick sizes and the enum tables, and Task 5.1 is why it now matters: the ten listed symbols
 * carry four different tick sizes, so a client that assumed a scale would misprice eight of
 * them. Until this file existed the frontend held a hard-coded `["QAA", "QAB"]` — correct when
 * two provisional symbols were all there were, and silently wrong the moment 5.1 landed.
 *
 * This is also the one REST call the trading screen is *supposed* to make. Task 6.1's
 * Boundaries forbid polling the REST endpoints, because they exist for re-synchronisation after
 * a gap — but the symbol table is fetched once at startup and never again, which is a different
 * thing entirely. It does not change while a session is open; a symbol listing is a restart.
 */

/** One instrument, exactly as §2.3 serves it. */
export interface Symbol {
  symbol_id: number;
  name: string;
  /**
   * The divisor the presentation layer applies for display, and never on the way back.
   *
   * A price that reaches the gateway is always integer ticks (Open Issue 016). Dividing here
   * and multiplying before submission is the whole of the contract's position on floats: they
   * are allowed in the presentation layer and are a bug anywhere below it.
   */
  tick_size_ticks: number;
  lot_size: number;
}

export interface SymbolTable {
  symbols: Symbol[];
  byName: Map<string, Symbol>;
}

/** Fetch the table once. Throws rather than falling back — see `formatTicks`. */
export async function fetchSymbols(
  fetchImpl: typeof fetch = fetch,
): Promise<SymbolTable> {
  const response = await fetchImpl("/symbols");
  if (!response.ok) {
    throw new Error(`GET /symbols returned ${response.status}`);
  }
  const body = (await response.json()) as { symbols: Symbol[] };
  const symbols = [...body.symbols].sort((a, b) => a.symbol_id - b.symbol_id);
  return {
    symbols,
    byName: new Map(symbols.map((symbol) => [symbol.name, symbol])),
  };
}

/**
 * Integer ticks to a display string, at the symbol's own scale.
 *
 * **There is deliberately no default tick size.** A missing symbol throws rather than falling
 * back to 1, because falling back would render a 7,983,040-tick price as "7983040" beside a
 * correctly formatted one and look like a real number rather than a bug. Open Issue 014 §14e's
 * rule — that a trading interface must never quietly show something wrong — applies to a
 * misplaced decimal point exactly as it applies to a stale price.
 */
export function formatTicks(priceTicks: number, symbol: Symbol | undefined): string {
  if (symbol === undefined) {
    throw new Error("cannot format a price without its symbol's tick size");
  }
  const decimals = Math.max(0, Math.round(Math.log10(symbol.tick_size_ticks)));
  return (priceTicks / symbol.tick_size_ticks).toFixed(decimals);
}

/** Display units back to integer ticks, for the order ticket in 6.1b. Rounds, never floors. */
export function toTicks(displayPrice: number, symbol: Symbol): number {
  return Math.round(displayPrice * symbol.tick_size_ticks);
}

/**
 * The bar width the trading screen charts.
 *
 * Named in **simulated** time, like every bar channel: at the replay clock's one real second to
 * one simulated minute, `1m` is the one-second bucket and is what the market experiences as a
 * minute. `services/fanout/messages.py` `width_label` is the other half of this agreement, and
 * `bars:*:1h` is the coarser series the backtester (7.1) will read.
 *
 * A constant rather than a literal in two files, because the subscription and the chart that
 * reads the buffer have to name the same width or the chart silently draws nothing.
 */
export const CHART_BAR_WIDTH = "1m";

/** Every channel this client subscribes to, for the whole symbol table (§3.1). */
export function channelsFor(
  symbols: readonly Symbol[],
  barWidth: string = CHART_BAR_WIDTH,
): string[] {
  return symbols.flatMap((symbol) => [
    `book:${symbol.name}:l2`,
    `tape:${symbol.name}`,
    `bars:${symbol.name}:${barWidth}`,
  ]);
}
