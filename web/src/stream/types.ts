/**
 * The browser wire, as `contracts/v1/rest_and_ws.md` §3 fixes it.
 *
 * Mirrored by hand rather than generated. The binary records under `contracts/v1/generated/`
 * are generated because both an engine and a gateway must agree on them to the byte; this is
 * the presentation layer, where strings are allowed and the shapes are stable JSON. A second
 * generator for four message types would cost more than it saves.
 *
 * What is *not* relaxed here: money and quantities stay integer ticks (§1). The frontend
 * divides by the symbol's `tick_size_ticks` for display and never sends a divided value back.
 * A float that reaches the gateway is a bug, not a rounding concern — which is why every
 * numeric field below is an integer and there is no `number` standing in for a price.
 */

/** `[price_ticks, qty]`. Index 0 of a side is always its most aggressive price. */
export type Level = [number, number];

/** Shared by every message: §3.5 requires a sequence on all of them. */
export interface Envelope {
  ch: string;
  seq: string;
}

/**
 * A **complete** top-N snapshot of both sides (§3.2). Not a delta.
 *
 * That completeness is what makes 20 Hz conflation safe: a client that misses a message is
 * fully correct again on the next one, so a dropped frame is free rather than corrupting.
 * It is also why a gap on a book channel is ignored rather than repaired.
 */
export interface BookMessage extends Envelope {
  ts_ns: number;
  bids: Level[];
  asks: Level[];
}

/** One print (§3.3). Never conflated — a dropped trade is a *wrong* tape, not a stale one. */
export interface TapeMessage extends Envelope {
  ts_ns: number;
  price_ticks: number;
  qty: number;
  aggressor_side: number;
}

/** A completed candle, published on close (§3.3). */
export interface BarMessage extends Envelope {
  open_ticks: number;
  high_ticks: number;
  low_ticks: number;
  close_ticks: number;
  volume: number;
  bar_open_ns: number;
}

/**
 * This session's own order and fill events (§3.4).
 *
 * `type` names the record — `OrderAccepted`, `Fill`, `OrderCancelled` and so on — and the
 * remaining fields are that record's, with the same names `schema.toml` uses. `role` is
 * derived by fan-out from `aggressor_side` and which side this user was on, so the client
 * never has to work out whether it paid the maker fee or the taker's.
 */
export interface PrivateMessage extends Envelope {
  ch: "private";
  type: string;
  ts_ns: number;
  order_id?: number;
  client_order_id?: number;
  symbol?: string;
  price_ticks?: number;
  qty?: number;
  aggressor_side?: number;
  role?: "maker" | "taker";
}

/** §3.6. `slow_consumer` precedes a server-initiated close. */
export interface ErrorMessage {
  ch: "error";
  // `resumed` is Task 5.2b's addition. §3.6 enumerates only failures, so a halt had no way
  // to end — see `services/fanout/messages.py`. Pending Dev A's sign-off on the contract.
  code: "unauthenticated" | "unknown_channel" | "slow_consumer" | "halted" | "resumed";
  detail: string;
}

export type StreamMessage =
  | BookMessage
  | TapeMessage
  | BarMessage
  | PrivateMessage
  | ErrorMessage;

/** What the connection indicator shows. A trading UI that silently shows stale prices is
 *  worse than one that admits it is disconnected (Open Issue 014 §14e). */
export type ConnectionState =
  | "connecting"
  | "connected"
  | "reconnecting"
  | "halted"
  | "closed";

export interface ParsedChannel {
  kind: "book" | "tape" | "bars" | "private" | "error" | "unknown";
  symbol: string | null;
  /** `l1` or `l2` for a book, the width suffix for bars, otherwise null. */
  tier: string | null;
}

/**
 * Split `book:QAA:l2` into its parts.
 *
 * Channel names carry the symbol *name*, not its id — `GET /symbols` is the only thing that
 * maps one to the other (§2.3), which is why the client resolves names and never hard-codes
 * an integer the schema owns.
 */
export function parseChannel(ch: string): ParsedChannel {
  const parts = ch.split(":");
  if (parts[0] === "book" && parts.length === 3) {
    return { kind: "book", symbol: parts[1], tier: parts[2] };
  }
  if (parts[0] === "tape" && parts.length === 2) {
    return { kind: "tape", symbol: parts[1], tier: null };
  }
  if (parts[0] === "bars" && parts.length === 3) {
    return { kind: "bars", symbol: parts[1], tier: parts[2] };
  }
  if (ch === "private") return { kind: "private", symbol: null, tier: null };
  if (ch === "error") return { kind: "error", symbol: null, tier: null };
  return { kind: "unknown", symbol: null, tier: null };
}
