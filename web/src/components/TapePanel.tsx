/**
 * The trade tape — every print, newest first.
 *
 * Painted outside React on the frame loop, like the book, but the data underneath behaves
 * differently and the panel has to respect that. A book message is a complete snapshot, so
 * losing one is free. **A dropped print is a *wrong* tape, not a stale one** (§3.3), which is
 * why the tape is never conflated on the server and why `MarketBuffer` accumulates prints
 * instead of overwriting them.
 *
 * What conflation still buys here is rendering: fifty prints between two frames are painted
 * once, in one pass, rather than fifty times. Nothing is dropped — the buffer holds all of
 * them — only the intermediate *paints* are skipped.
 *
 * A fixed number of rows again, for the reason `BookPanel` gives: rows are rewritten, never
 * created, so React is out of the loop after the first render.
 */

import { useEffect, useRef } from "react";

import type { MarketBuffer } from "../stream/buffer";
import { formatTicks, type Symbol } from "../stream/symbols";

/** Rows on screen. The buffer keeps `TAPE_LIMIT`; this is the window onto it. */
export const TAPE_ROWS = 30;

/** §3.3's `aggressor_side`, and `schema.toml`'s `Side` enum: 1 buy, 2 sell. */
const BUY = 1;

export interface TapePanelProps {
  symbol: Symbol;
  buffer: MarketBuffer;
  register: (symbolName: string, paint: () => void) => () => void;
}

interface RowRefs {
  row: HTMLTableRowElement | null;
  price: HTMLTableCellElement | null;
  qty: HTMLTableCellElement | null;
  time: HTMLTableCellElement | null;
}

/**
 * Nanoseconds since the epoch to a wall-clock time.
 *
 * `ts_ns` is the gateway's stamp, which is real time — **not** the replay clock. Task 5.1's
 * one-real-second-to-one-simulated-minute ratio governs the price *process*, not when a trade
 * happened, and a tape showing simulated time would say a hundred prints occurred in the same
 * simulated minute while the user watched them arrive seconds apart.
 */
function formatTime(tsNs: number): string {
  const date = new Date(tsNs / 1_000_000);
  return date.toLocaleTimeString(undefined, { hour12: false });
}

export function TapePanel({ symbol, buffer, register }: TapePanelProps) {
  const rows = useRef<RowRefs[]>([]);

  useEffect(() => {
    const paint = () => {
      const tape = buffer.tape(symbol.name);
      for (let index = 0; index < TAPE_ROWS; index += 1) {
        const refs = rows.current[index];
        if (refs === undefined) continue;
        // The buffer keeps oldest-first; the tape reads newest-first.
        const trade = tape[tape.length - 1 - index];
        if (trade === undefined) {
          if (refs.price) refs.price.textContent = "";
          if (refs.qty) refs.qty.textContent = "";
          if (refs.time) refs.time.textContent = "";
          if (refs.row) refs.row.className = "";
          continue;
        }
        if (refs.price) refs.price.textContent = formatTicks(trade.priceTicks, symbol);
        if (refs.qty) refs.qty.textContent = String(trade.qty);
        if (refs.time) refs.time.textContent = formatTime(trade.tsNs);
        // Colour by which side crossed the spread, which is what `aggressor_side` is for —
        // `schema.toml` says it "cannot be derived after the fact", so it travels on the Fill.
        if (refs.row) refs.row.className = trade.aggressorSide === BUY ? "buy" : "sell";
      }
    };

    paint();
    return register(symbol.name, paint);
  }, [symbol, buffer, register]);

  return (
    <section className="tape-panel" aria-label={`Trade tape for ${symbol.name}`}>
      <h3>Tape</h3>
      <table className="tape">
        <tbody>
          {Array.from({ length: TAPE_ROWS }, (_unused, index) => (
            <tr
              key={index}
              ref={(element) => {
                rows.current[index] ??= { row: null, price: null, qty: null, time: null };
                rows.current[index]!.row = element;
              }}
            >
              <td
                className="price"
                ref={(element) => {
                  rows.current[index] ??= { row: null, price: null, qty: null, time: null };
                  rows.current[index]!.price = element;
                }}
              />
              <td
                className="qty"
                ref={(element) => {
                  rows.current[index] ??= { row: null, price: null, qty: null, time: null };
                  rows.current[index]!.qty = element;
                }}
              />
              <td
                className="time"
                ref={(element) => {
                  rows.current[index] ??= { row: null, price: null, qty: null, time: null };
                  rows.current[index]!.time = element;
                }}
              />
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
