/**
 * High-frequency market data, held **outside React state**.
 *
 * This file deliberately imports nothing from React, and there is a test that asserts it. That
 * is not stylistic: the moment this data reaches framework state, every one of the 20 messages
 * a second becomes a re-render of the tree, and Open Issue 014 §14a is that dropped frames in a
 * trading UI read as "the site is slow" and undermine the entire real-time claim.
 *
 * The buffer has **no subscribe, no listener, no callback** — nothing here can notify anybody.
 * A component cannot accidentally re-render on a write, because there is no mechanism by which
 * it could learn one happened. Rendering is *pull-based*: `frameLoop.ts` asks once per animation
 * frame what changed, and paints that.
 *
 * ## Why overwriting is safe
 *
 * A burst of book messages simply overwrites, and the next frame paints the latest. That is the
 * browser-side equivalent of the server's 20 Hz conflation, and it is correct for exactly the
 * same reason: **every book message is a complete snapshot**, so an intermediate one that is
 * never painted has been lost harmlessly.
 *
 * The tape is the exception and is handled as one. A dropped print is a *wrong* tape, not a
 * stale one, so prints accumulate rather than overwrite — bounded, because an unbounded array
 * in a buffer that lives for the length of a session is a memory leak measured in hours.
 */

import type { BarMessage, BookMessage, Level, TapeMessage } from "./types.ts";

/**
 * How many prints the tape keeps. The trading screen shows a window, not a ledger — the
 * archive (Task 6.2) is the record of what traded, and §3.5 says plainly that a hole in the
 * tape is not backfilled because the tape is a display.
 */
export const TAPE_LIMIT = 200;

/**
 * How many closed candles the chart keeps in memory.
 *
 * At one real second to one simulated minute (Task 5.1), 600 bars is ten minutes of watching
 * and ten simulated hours of market. Bounded for the same reason the tape is: this buffer lives
 * for the length of a session, and an unbounded array in it is a memory leak measured in hours.
 */
export const BAR_LIMIT = 600;

export interface BookView {
  bids: Level[];
  asks: Level[];
  tsNs: number;
  seq: string;
}

export interface TradeView {
  priceTicks: number;
  qty: number;
  aggressorSide: number;
  tsNs: number;
  seq: string;
}

export interface BarView {
  openTicks: number;
  highTicks: number;
  lowTicks: number;
  closeTicks: number;
  volume: number;
  barOpenNs: number;
  seq: string;
}

export class MarketBuffer {
  private readonly books = new Map<string, BookView>();
  private readonly tapes = new Map<string, TradeView[]>();
  private readonly lastTrades = new Map<string, TradeView>();
  private readonly bars = new Map<string, BarView[]>();
  private readonly changed = new Set<string>();

  /** Messages written since construction. For the render-conflation test, and for a log line. */
  writes = 0;

  writeBook(symbol: string, message: BookMessage): void {
    this.books.set(symbol, {
      bids: message.bids,
      asks: message.asks,
      tsNs: message.ts_ns,
      seq: message.seq,
    });
    this.changed.add(symbol);
    this.writes += 1;
  }

  writeTrade(symbol: string, message: TapeMessage): void {
    const trade: TradeView = {
      priceTicks: message.price_ticks,
      qty: message.qty,
      aggressorSide: message.aggressor_side,
      tsNs: message.ts_ns,
      seq: message.seq,
    };
    let tape = this.tapes.get(symbol);
    if (tape === undefined) {
      tape = [];
      this.tapes.set(symbol, tape);
    }
    tape.push(trade);
    // Trimmed from the front, so the newest prints survive. Splice rather than a fresh array:
    // this runs on the receive path, and allocating a 200-element array per print at 20 Hz
    // across ten symbols is garbage the frame loop would rather not be competing with.
    if (tape.length > TAPE_LIMIT) tape.splice(0, tape.length - TAPE_LIMIT);

    this.lastTrades.set(symbol, trade);
    this.changed.add(symbol);
    this.writes += 1;
  }

  /**
   * A closed candle (§3.3). The third kind of market data, and it behaves like neither of the
   * other two.
   *
   * A book message is a complete snapshot, so it **overwrites**. A print is unique, so it
   * **accumulates**. A bar is published on close and is therefore also unique — but a repeat of
   * the same `bar_open_ns` replaces rather than appends, because a re-delivered bar after a
   * reconnect is the same candle, not a second one. Appending it would put a duplicate on the
   * chart at the same x position, which is the sort of thing that looks like a data bug in the
   * exchange rather than a bug in the client.
   */
  writeBar(symbol: string, message: BarMessage): void {
    const bar: BarView = {
      openTicks: message.open_ticks,
      highTicks: message.high_ticks,
      lowTicks: message.low_ticks,
      closeTicks: message.close_ticks,
      volume: message.volume,
      barOpenNs: message.bar_open_ns,
      seq: message.seq,
    };
    let series = this.bars.get(symbol);
    if (series === undefined) {
      series = [];
      this.bars.set(symbol, series);
    }
    const last = series[series.length - 1];
    if (last !== undefined && last.barOpenNs === bar.barOpenNs) {
      series[series.length - 1] = bar;
    } else {
      series.push(bar);
      if (series.length > BAR_LIMIT) series.splice(0, series.length - BAR_LIMIT);
    }
    this.changed.add(symbol);
    this.writes += 1;
  }

  /** Oldest first, which is the order a chart library wants. Live, not copied. */
  barSeries(symbol: string): readonly BarView[] {
    return this.bars.get(symbol) ?? [];
  }

  book(symbol: string): BookView | undefined {
    return this.books.get(symbol);
  }

  /** Newest last. Returned live rather than copied — the frame loop reads it and paints. */
  tape(symbol: string): readonly TradeView[] {
    return this.tapes.get(symbol) ?? [];
  }

  lastTrade(symbol: string): TradeView | undefined {
    return this.lastTrades.get(symbol);
  }

  /**
   * Symbols written to since the last call, and clears the set.
   *
   * This is the whole of the conflation. A thousand writes between two frames produce one
   * entry here, so the frame after them paints once — not a thousand times, and not once per
   * message as a state-driven component would.
   */
  takeChanged(): string[] {
    if (this.changed.size === 0) return [];
    const symbols = [...this.changed];
    this.changed.clear();
    return symbols;
  }

  /** For tests and for a shutdown log line. */
  symbols(): string[] {
    return [...this.books.keys()].sort();
  }
}
