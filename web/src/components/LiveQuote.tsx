import { useEffect, useRef } from "react";

import type { MarketBuffer } from "../stream/buffer.ts";
import { startFrameLoop } from "../stream/frameLoop.ts";

/**
 * Best bid, best offer and last print for one symbol — painted by the frame loop.
 *
 * **This is not the trading screen.** Task 6.1 builds that: chart, ten-level book, tape, order
 * ticket, open orders, portfolio. This is one strip, and it exists because Success Criterion 5
 * ("no React re-render occurs on book updates") needs *something* actually painting book
 * updates before it can be checked at all.
 *
 * How it avoids rendering:
 *
 * - The component renders **once**. There is no state here and no prop that changes.
 * - `useEffect` starts the frame loop, which writes into DOM nodes through refs.
 * - Twenty messages a second reach the buffer and none of them reach React.
 *
 * Writing `textContent` by hand is not how one normally builds a React component, and that is
 * the point: this is the boundary where the framework is deliberately stepped around, and the
 * awkwardness marks it. Open Issue 014 §14a: framework state owns low-frequency chrome, and
 * high-frequency data is painted directly.
 */
export function LiveQuote({ symbol, buffer }: { symbol: string; buffer: MarketBuffer }) {
  const bid = useRef<HTMLSpanElement>(null);
  const ask = useRef<HTMLSpanElement>(null);
  const last = useRef<HTMLSpanElement>(null);
  const frames = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    let painted = 0;
    return startFrameLoop({
      buffer,
      paint: (symbols, current) => {
        if (!symbols.includes(symbol)) return;
        painted += 1;

        const book = current.book(symbol);
        const topBid = book?.bids[0];
        const topAsk = book?.asks[0];
        const trade = current.lastTrade(symbol);

        // Em dash for an empty side. A side offering nothing and a side offering nothing at a
        // price of zero are different facts, and rendering the second is showing a market
        // nobody is making.
        if (bid.current) bid.current.textContent = topBid ? `${topBid[0]} × ${topBid[1]}` : "—";
        if (ask.current) ask.current.textContent = topAsk ? `${topAsk[0]} × ${topAsk[1]}` : "—";
        if (last.current) last.current.textContent = trade ? String(trade.priceTicks) : "—";
        if (frames.current) frames.current.textContent = `${painted} frames · ${current.writes} messages`;
      },
    });
  }, [symbol, buffer]);

  return (
    <div className="quote" data-symbol={symbol}>
      <span className="quote-symbol">{symbol}</span>
      <span className="quote-bid" ref={bid}>—</span>
      <span className="quote-ask" ref={ask}>—</span>
      <span className="quote-last" ref={last}>—</span>
      {/* Frames against messages, side by side: the conflation is the gap between them. */}
      <span className="quote-meter" ref={frames} />
    </div>
  );
}
