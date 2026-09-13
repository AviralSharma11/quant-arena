/**
 * Display formatting for amounts, percentages and times.
 *
 * **Built on `formatTicks`, never beside it.** `symbols.ts` owns the one rule that turns integer
 * ticks into a decimal string at a symbol's own scale, and throws without a tick size rather than
 * guessing. Everything here takes that string and only decorates it — digit grouping, a currency
 * mark, a sign — so a second copy of the scale arithmetic cannot drift from the first.
 *
 * The rupee mark and Indian digit grouping (1,00,00,000.00) are presentation only. Nothing
 * formatted here is ever parsed back or sent to the gateway; the order ticket reads its own input
 * field and converts with `toTicks`.
 */

import { formatTicks, type Symbol } from "./stream/symbols";

const GROUPING = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });

export interface AmountOptions {
  /** Prefix the rupee mark. */
  currency?: boolean;
  /** Prefix `+` on a positive amount. A negative amount always carries its `-`. */
  sign?: boolean;
}

export function formatTicksGrouped(
  ticks: number,
  symbol: Symbol | undefined,
  { currency = false, sign = false }: AmountOptions = {},
): string {
  const [whole, fraction] = formatTicks(Math.abs(ticks), symbol).split(".");
  let text = GROUPING.format(Number(whole));
  if (fraction !== undefined) text += `.${fraction}`;
  if (currency) text = `₹${text}`;
  if (ticks < 0) return `-${text}`;
  return sign && ticks > 0 ? `+${text}` : text;
}

/** A percentage with its sign always shown, so a loss is unmistakable at a glance. */
export function formatPercent(value: number, digits = 2): string {
  return `${value > 0 ? "+" : ""}${value.toFixed(digits)}%`;
}

export type Tone = "up" | "down" | "flat";

export function toneOf(value: number): Tone {
  return value > 0 ? "up" : value < 0 ? "down" : "flat";
}

/** Milliseconds since the epoch to `HH:MM:SS`, 24-hour. */
export function formatClock(ms: number): string {
  return new Date(ms).toLocaleTimeString(undefined, { hour12: false });
}
