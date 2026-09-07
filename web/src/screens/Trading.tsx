/**
 * The trading screen — Task 6.1a, the market half.
 *
 * `WEEKLY_PLAN.md` is blunt about what this is: **"This screen is the demonstration"**. Beats 1
 * to 4 of the five-minute walkthrough happen here, and it is the only place "real-time" is
 * actually experienced — everything upstream exists to make it feel immediate.
 *
 * ## What is here, and what is 6.1b's
 *
 * 6.1a is **read-only**: the book, the tape and the chart, one symbol at a time, all painted
 * from the mutable buffer on the frame loop. 6.1b adds the order ticket, open orders with
 * cancellation, and portfolio — which are driven by the private stream.
 *
 * The seam is not arbitrary. This half is market data: high-frequency, droppable, and kept out
 * of React entirely. The other half is private data: low-frequency, never dropped, and React
 * state is exactly right for it. The rule in `buffer.ts` is about *frequency*, not about
 * principle, and 6.1b will say so where it puts a fill into `useState`.
 *
 * ## One frame loop, not three
 *
 * The screen owns the loop and the panels register paint callbacks with it. Three loops would
 * mean three `requestAnimationFrame` chains painting the same symbol's change in the same
 * frame, and `takeChanged()` **clears** the changed set — so the first loop to run would
 * consume the change and the other two would paint nothing. That is not a performance
 * preference; it is the reason a single loop is the only correct arrangement.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { BookPanel } from "../components/BookPanel";
import { Chart } from "../components/Chart";
import { TapePanel } from "../components/TapePanel";
import type { MarketBuffer } from "../stream/buffer";
import { startFrameLoop } from "../stream/frameLoop";
import { formatTicks, type Symbol } from "../stream/symbols";

export interface TradingProps {
  buffer: MarketBuffer | null;
  symbols: readonly Symbol[];
}

type PaintRegistry = Map<string, Set<() => void>>;

export function Trading({ buffer, symbols }: TradingProps) {
  // The selected symbol IS React state — it changes when a human clicks, which is about as
  // low-frequency as an event gets. The prices behind it are not.
  const [selectedName, setSelectedName] = useState<string | null>(null);
  const registry = useRef<PaintRegistry>(new Map());

  const selected = useMemo(
    () => symbols.find((symbol) => symbol.name === selectedName) ?? symbols[0],
    [symbols, selectedName],
  );

  /**
   * A panel's paint callback, keyed by symbol. Returns its own unregister.
   *
   * Stable across renders (`useCallback` with no dependencies) because it is a dependency of
   * every panel's effect — a new identity each render would tear down and rebuild all three
   * panels, and the chart would be destroyed and recreated, on every state change.
   */
  const register = useCallback((symbolName: string, paint: () => void) => {
    let painters = registry.current.get(symbolName);
    if (painters === undefined) {
      painters = new Set();
      registry.current.set(symbolName, painters);
    }
    painters.add(paint);
    return () => {
      painters!.delete(paint);
      if (painters!.size === 0) registry.current.delete(symbolName);
    };
  }, []);

  useEffect(() => {
    if (buffer === null) return;
    return startFrameLoop({
      buffer,
      paint: (changed) => {
        for (const symbolName of changed) {
          const painters = registry.current.get(symbolName);
          if (painters === undefined) continue;
          for (const paint of painters) paint();
        }
      },
    });
  }, [buffer]);

  if (buffer === null || selected === undefined) {
    return (
      <section className="trading">
        <h2>Trading</h2>
        <p className="pending">
          {buffer === null
            ? "Connecting to the market data stream…"
            : "No symbols listed. `GET /symbols` returned an empty table."}
        </p>
      </section>
    );
  }

  return (
    <section className="trading">
      <header className="trading-header">
        <h2>Trading</h2>
        <SymbolTabs
          symbols={symbols}
          selected={selected}
          buffer={buffer}
          onSelect={setSelectedName}
          register={register}
        />
      </header>

      <div className="trading-grid">
        <Chart key={`chart-${selected.name}`} symbol={selected} buffer={buffer} register={register} />
        <BookPanel key={`book-${selected.name}`} symbol={selected} buffer={buffer} register={register} />
        <TapePanel key={`tape-${selected.name}`} symbol={selected} buffer={buffer} register={register} />
      </div>

      <p className="hint">
        The order ticket, open orders and portfolio are Task 6.1b. This half is market data only
        &mdash; every number above is painted outside React state, on the animation frame loop.
      </p>
    </section>
  );
}

/**
 * One tab per listed symbol, each showing its own last price.
 *
 * The tabs are React (a fixed list, rendered once) but the prices inside them are not — each
 * subscribes its own painter, so all ten stay live while only one book is on screen. That is
 * deliberate: a demonstration where the nine unselected symbols showed frozen prices would look
 * like nine broken markets.
 */
function SymbolTabs({
  symbols,
  selected,
  buffer,
  onSelect,
  register,
}: {
  symbols: readonly Symbol[];
  selected: Symbol;
  buffer: MarketBuffer;
  onSelect: (name: string) => void;
  register: (symbolName: string, paint: () => void) => () => void;
}) {
  return (
    <div className="symbol-tabs" role="tablist">
      {symbols.map((symbol) => (
        <button
          key={symbol.name}
          role="tab"
          type="button"
          aria-selected={symbol.name === selected.name}
          className={symbol.name === selected.name ? "tab selected" : "tab"}
          onClick={() => onSelect(symbol.name)}
        >
          <span className="tab-name">{symbol.name}</span>
          <TabPrice symbol={symbol} buffer={buffer} register={register} />
        </button>
      ))}
    </div>
  );
}

function TabPrice({
  symbol,
  buffer,
  register,
}: {
  symbol: Symbol;
  buffer: MarketBuffer;
  register: (symbolName: string, paint: () => void) => () => void;
}) {
  const ref = useRef<HTMLSpanElement | null>(null);

  useEffect(() => {
    const paint = () => {
      if (ref.current === null) return;
      const trade = buffer.lastTrade(symbol.name);
      const book = buffer.book(symbol.name);
      // Last traded price, or the mid if nothing has printed yet. A tab reading "—" on a
      // symbol with a live two-sided book would look broken when it is merely quiet.
      if (trade !== undefined) {
        ref.current.textContent = formatTicks(trade.priceTicks, symbol);
      } else if (book?.bids[0] !== undefined && book.asks[0] !== undefined) {
        ref.current.textContent = formatTicks(
          Math.round((book.bids[0][0] + book.asks[0][0]) / 2),
          symbol,
        );
      } else {
        ref.current.textContent = "—";
      }
    };
    paint();
    return register(symbol.name, paint);
  }, [symbol, buffer, register]);

  return <span className="tab-price" ref={ref}>&mdash;</span>;
}
