/**
 * The trading screen — Task 6.1.
 *
 * `WEEKLY_PLAN.md` is blunt about what this is: **"This screen is the demonstration"**. Beats 1
 * to 4 of the five-minute walkthrough happen here, and it is the only place "real-time" is
 * actually experienced — everything upstream exists to make it feel immediate.
 *
 * ## Two halves, one screen
 *
 * The market half — symbol strip, chart, book, tape — is high-frequency, droppable, and kept out
 * of React entirely: each panel is rendered once and then painted through refs. The private half
 * — ticket, open orders, portfolio — is low-frequency, never dropped, and React state is exactly
 * right for it. The rule in `buffer.ts` is about *frequency*, not about principle.
 *
 * Only a signed-in visitor reaches this screen; `App` sends everyone else to sign in.
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
import { OpenOrders, OrderTicket } from "../components/OrderTicket";
import { PortfolioPanel } from "../components/PortfolioPanel";
import { TapePanel } from "../components/TapePanel";
import { formatTicksGrouped } from "../format";
import type { MarketBuffer } from "../stream/buffer";
import { startFrameLoop } from "../stream/frameLoop";
import { openOrderList, type PortfolioState } from "../stream/portfolio";
import type { Symbol } from "../stream/symbols";

export interface TradingProps {
  buffer: MarketBuffer | null;
  symbols: readonly Symbol[];
  /** Cash, positions and open orders, folded from the private stream by `App`. */
  portfolio: PortfolioState;
}

type PaintRegistry = Map<string, Set<() => void>>;

export function Trading({ buffer, symbols, portfolio }: TradingProps) {
  // The selected symbol IS React state — it changes when a human clicks, which is about as
  // low-frequency as an event gets. The prices behind it are not.
  const [selectedName, setSelectedName] = useState<string | null>(null);
  const registry = useRef<PaintRegistry>(new Map());

  const selected = useMemo(
    () => symbols.find((symbol) => symbol.name === selectedName) ?? symbols[0],
    [symbols, selectedName],
  );

  const symbolsByName = useMemo(
    () => new Map(symbols.map((symbol) => [symbol.name, symbol])),
    [symbols],
  );
  const openOrders = useMemo(() => openOrderList(portfolio), [portfolio]);

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

  /**
   * The top of book, read on demand.
   *
   * A function rather than a value on purpose: the ticket prefills from the touch when a human
   * clicks, and passing the price itself would mean this component re-rendering at the book's
   * rate to keep it current — which is exactly what the frame loop exists to avoid.
   */
  const topOfBook = useCallback(() => {
    if (buffer === null || selected === undefined) return { bid: null, ask: null };
    const view = buffer.book(selected.name);
    return {
      bid: view?.bids[0]?.[0] ?? null,
      ask: view?.asks[0]?.[0] ?? null,
    };
  }, [buffer, selected]);

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
      <section className="panel trading-pending" aria-label="Trading">
        <h2 className="panel-title">Trading</h2>
        <p className="pending">
          {buffer === null
            ? "Connecting to the market data stream…"
            : "No symbols listed. `GET /symbols` returned an empty table."}
        </p>
      </section>
    );
  }

  return (
    <section className="trading" aria-label="Trading">
      <SymbolStrip
        symbols={symbols}
        selected={selected}
        buffer={buffer}
        onSelect={setSelectedName}
        register={register}
      />

      <div className="trading-grid">
        <div className="col-main">
          <Chart key={`chart-${selected.name}`} symbol={selected} buffer={buffer} register={register} />
          {/* The private half (6.1b). These re-render on a fill; the market panels are painted
              through refs and never re-render at all. Nothing in the private half subscribes
              to the buffer — `topOfBook` reads it once, when a human clicks. */}
          <div className="private-row">
            <OpenOrders orders={openOrders} symbolsByName={symbolsByName} />
            <PortfolioPanel state={portfolio} symbolsByName={symbolsByName} cashSymbol={selected} />
          </div>
        </div>

        <div className="col-book">
          <BookPanel key={`book-${selected.name}`} symbol={selected} buffer={buffer} register={register} />
          <TapePanel key={`tape-${selected.name}`} symbol={selected} buffer={buffer} register={register} />
        </div>

        <div className="col-ticket">
          <OrderTicket
            symbol={selected}
            topOfBook={topOfBook}
            notices={portfolio.notices}
            symbolsByName={symbolsByName}
          />
        </div>
      </div>
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
function SymbolStrip({
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
    <div className="panel symbol-strip">
      <span className="label-caps symbol-strip-label">Instruments</span>
      <div className="symbol-strip-tabs" role="tablist" aria-label="Instruments" style={{ display: "contents" }}>
        {symbols.map((symbol) => (
          <button
            key={symbol.name}
            role="tab"
            type="button"
            aria-selected={symbol.name === selected.name}
            className="tab"
            onClick={() => onSelect(symbol.name)}
          >
            <span className="tab-name">{symbol.name}</span>
            <TabPrice symbol={symbol} buffer={buffer} register={register} />
          </button>
        ))}
      </div>
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
        ref.current.textContent = formatTicksGrouped(trade.priceTicks, symbol, { currency: true });
      } else if (book?.bids[0] !== undefined && book.asks[0] !== undefined) {
        ref.current.textContent = formatTicksGrouped(
          Math.round((book.bids[0][0] + book.asks[0][0]) / 2),
          symbol,
          { currency: true },
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
