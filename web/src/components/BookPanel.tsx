/**
 * The L2 order book — ten levels a side, painted outside React.
 *
 * React renders the twenty rows **once**. Everything after that is `textContent` assignment
 * from the frame loop, through refs. That is the whole trick, and it is the same trick the
 * server plays one layer up: the book changes at 20 Hz, and re-rendering a component tree at
 * 20 Hz drops frames on a busy tab (Open Issue 014 §14a).
 *
 * The row count is fixed, which is what makes it work. Ten levels a side is
 * `market_data.book_depth`, so rows are never created or destroyed — only rewritten, or
 * blanked when a level is empty. A variable-length list would put React back in the loop.
 *
 * A level that has gone away is blanked rather than removed, for the same reason: a shrinking
 * table would reflow the panel and shift the price the user was about to click on.
 */

import { useEffect, useRef } from "react";

import type { MarketBuffer } from "../stream/buffer";
import { formatTicks, type Symbol } from "../stream/symbols";

/** Ten a side — `market_data.book_depth`, and the depth §3.2 fixes for the browser wire. */
export const BOOK_DEPTH = 10;

export interface BookPanelProps {
  symbol: Symbol;
  buffer: MarketBuffer;
  /** Registers this panel's paint function with the frame loop owned by the screen. */
  register: (symbolName: string, paint: () => void) => () => void;
}

interface RowRefs {
  price: HTMLTableCellElement | null;
  qty: HTMLTableCellElement | null;
  depth: HTMLDivElement | null;
}

export function BookPanel({ symbol, buffer, register }: BookPanelProps) {
  const bidRows = useRef<RowRefs[]>([]);
  const askRows = useRef<RowRefs[]>([]);
  const spreadRef = useRef<HTMLSpanElement | null>(null);

  useEffect(() => {
    const paint = () => {
      const view = buffer.book(symbol.name);
      const bids = view?.bids ?? [];
      const asks = view?.asks ?? [];

      // The largest resting quantity on screen sets the depth-bar scale. Recomputed per paint
      // rather than kept, because a remembered maximum from a level that has since traded away
      // leaves every bar permanently short.
      let largest = 0;
      for (const [, qty] of bids) largest = Math.max(largest, qty);
      for (const [, qty] of asks) largest = Math.max(largest, qty);

      const fill = (rows: RowRefs[], levels: readonly (readonly [number, number])[]) => {
        for (let index = 0; index < BOOK_DEPTH; index += 1) {
          const row = rows[index];
          if (row === undefined) continue;
          const level = levels[index];
          if (level === undefined) {
            if (row.price) row.price.textContent = "";
            if (row.qty) row.qty.textContent = "";
            if (row.depth) row.depth.style.width = "0%";
            continue;
          }
          const [priceTicks, qty] = level;
          if (row.price) row.price.textContent = formatTicks(priceTicks, symbol);
          if (row.qty) row.qty.textContent = String(qty);
          if (row.depth) {
            row.depth.style.width = largest > 0 ? `${(qty / largest) * 100}%` : "0%";
          }
        }
      };

      // Asks are painted best-first in the array and displayed descending, so index 0 — the
      // best ask — sits nearest the spread. That is the convention every trading screen uses,
      // and getting it upside down makes the book look crossed.
      fill(askRows.current, asks);
      fill(bidRows.current, bids);

      if (spreadRef.current) {
        const bestBid = bids[0]?.[0];
        const bestAsk = asks[0]?.[0];
        spreadRef.current.textContent =
          bestBid !== undefined && bestAsk !== undefined
            ? formatTicks(bestAsk - bestBid, symbol)
            : "—";
      }
    };

    paint();
    return register(symbol.name, paint);
  }, [symbol, buffer, register]);

  const rows = Array.from({ length: BOOK_DEPTH }, (_unused, index) => index);

  return (
    <section className="book-panel" aria-label={`Order book for ${symbol.name}`}>
      <h3>Book</h3>
      <table className="book">
        <tbody>
          {/* Asks, best last so the best ask sits against the spread row below. */}
          {[...rows].reverse().map((index) => (
            <tr key={`ask-${index}`} className="ask">
              <td className="depth-cell">
                <div
                  className="depth ask-depth"
                  ref={(element) => {
                    askRows.current[index] ??= { price: null, qty: null, depth: null };
                    askRows.current[index]!.depth = element;
                  }}
                />
              </td>
              <td
                className="price"
                ref={(element) => {
                  askRows.current[index] ??= { price: null, qty: null, depth: null };
                  askRows.current[index]!.price = element;
                }}
              />
              <td
                className="qty"
                ref={(element) => {
                  askRows.current[index] ??= { price: null, qty: null, depth: null };
                  askRows.current[index]!.qty = element;
                }}
              />
            </tr>
          ))}
          <tr className="spread-row">
            <td colSpan={3}>
              spread <span ref={spreadRef}>—</span>
            </td>
          </tr>
          {rows.map((index) => (
            <tr key={`bid-${index}`} className="bid">
              <td className="depth-cell">
                <div
                  className="depth bid-depth"
                  ref={(element) => {
                    bidRows.current[index] ??= { price: null, qty: null, depth: null };
                    bidRows.current[index]!.depth = element;
                  }}
                />
              </td>
              <td
                className="price"
                ref={(element) => {
                  bidRows.current[index] ??= { price: null, qty: null, depth: null };
                  bidRows.current[index]!.price = element;
                }}
              />
              <td
                className="qty"
                ref={(element) => {
                  bidRows.current[index] ??= { price: null, qty: null, depth: null };
                  bidRows.current[index]!.qty = element;
                }}
              />
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
