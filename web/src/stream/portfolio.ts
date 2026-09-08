/**
 * Private state — cash, positions and open orders — folded from the private stream.
 *
 * The mirror image of `buffer.ts`, and deliberately the opposite in every respect that matters:
 *
 * | | market data (`buffer.ts`) | private data (this file) |
 * |---|---|---|
 * | frequency | 20 Hz | a handful of messages per order |
 * | droppable | yes — the next snapshot is complete | **never** — nothing supersedes a fill |
 * | lives in | a mutable buffer outside React | React state |
 * | painted by | the `requestAnimationFrame` loop | an ordinary re-render |
 *
 * That is not an inconsistency. Open Issue 014 §14a keeps high-frequency data out of framework
 * state because twenty re-renders a second drop frames; it says nothing against React state for
 * data that arrives when a human places an order. `buffer.ts`'s rule is about *frequency*, and
 * this is the file that says so.
 *
 * Pure, like `Ledger.apply`, `RiskState.apply` and `MarketState.apply` on the server — no fetch,
 * no clock, no React import. `reduce()` takes one action and returns the next state, so the whole
 * of it is testable without a browser and a re-synchronisation is just another action.
 */

import type { PrivateMessage } from "./types.ts";

/**
 * Maker and taker fees, in basis points.
 *
 * **This is a deliberate second copy of `services/ledger/ledger.py`, and the only one.** The
 * private stream carries no balance — `schema.toml` has no record that does, because the engine
 * is money-blind — so a client that wants to show cash moving on a fill without a REST round
 * trip has to apply the same arithmetic the ledger does. §3.4 anticipates exactly this: `role`
 * is on the wire "so the client does not have to work out whether it paid the maker or the taker
 * fee", which is only useful to a client that is going to charge itself one of them.
 *
 * The duplication is guarded rather than trusted: `tests/web/test_trading_screen.py` reads these
 * two constants out of this file and asserts they equal `MAKER_FEE_BPS` and `TAKER_FEE_BPS` in
 * `services/ledger/ledger.py`, so a change on the server side fails the suite here.
 *
 * Cash derived this way is still an estimate in one respect: it is correct per fill, but a
 * client that missed a fill has missed its cash too. That is what the sequence gap and the
 * `GET /portfolio` re-synchronisation are for, and why `resync` replaces rather than adjusts.
 */
export const MAKER_FEE_BPS = 2;
export const TAKER_FEE_BPS = 10;
export const BPS_DENOMINATOR = 10_000;

/** Integer arithmetic, floored, exactly as `calculate_fees` does it. Ticks are never floats. */
export function feeTicks(notionalTicks: number, bps: number): number {
  return Math.floor((notionalTicks * bps) / BPS_DENOMINATOR);
}

export interface OpenOrderView {
  orderId: number;
  clientOrderId: number;
  /** The symbol *name*. `GET /symbols` is the only thing that maps an id to one (§2.3). */
  symbol: string;
  side: number;
  priceTicks: number;
  qty: number;
  remainingQty: number;
  tif: number;
}

/**
 * The last thing that happened to an order this session submitted, for the ticket to report.
 *
 * **Structured, not a pre-formatted string.** A price only means something beside its symbol's
 * `tick_size_ticks`, and this module has no symbol table — it is fed by the private stream,
 * which carries names rather than scales. Formatting here would mean either rendering raw ticks
 * (8418076 where the book says 84180.76) or smuggling the table into a pure reducer. So the
 * numbers travel intact and `OrderTicket` formats them through `formatTicks`, which throws
 * without a tick size rather than guessing — Open Issue 014 §14e covers a misplaced decimal
 * point exactly as it covers a stale price.
 */
export interface Notice {
  kind: "accepted" | "rejected" | "filled" | "cancelled" | "resynced";
  clientOrderId: number | null;
  symbol: string | null;
  /** A short verb phrase with no numbers in it. The panel supplies the numbers. */
  detail: string;
  priceTicks: number | null;
  qty: number | null;
  /** Monotonic within a session, so a repeated identical notice still re-renders. */
  at: number;
}

export interface PortfolioState {
  /**
   * `null` until something says otherwise — the grant arrives as an `AccountCreated` on the
   * private stream, or as `GET /portfolio` on a resync.
   *
   * Null rather than zero on purpose. A balance of zero and a balance not yet known are
   * different facts, and rendering the second as the first shows a user a bankrupt account
   * while the grant is still in flight (Open Issue 014 §14e).
   */
  cashTicks: number | null;
  /** Keyed by symbol name, and only non-zero entries are kept. */
  positions: Record<string, number>;
  /** Keyed by `order_id`, which is the only identifier every private message about a resting
   *  order carries — a `Fill` does not name the `client_order_id`. */
  openOrders: Record<number, OpenOrderView>;
  notices: Notice[];
  /** Counters, for the tests and for a log line. */
  applied: number;
  resyncs: number;
}

export const EMPTY: PortfolioState = {
  cashTicks: null,
  positions: {},
  openOrders: {},
  notices: [],
  applied: 0,
  resyncs: 0,
};

/** How many notices are kept. The panel shows a few; this is not a log. */
export const NOTICE_LIMIT = 8;

/** `GET /orders/open`, as `routes_orders.py` `OpenOrderItem` serves it. */
export interface OpenOrderItemDto {
  order_id: number;
  client_order_id: number;
  user_id: number;
  symbol_id: number;
  side: number;
  price_ticks: number;
  qty: number;
  remaining_qty: number;
  tif: number;
  created_at_ns: number;
}

/** `GET /portfolio`, as `routes_orders.py` `PortfolioResponse` serves it. */
export interface PortfolioDto {
  user_id: number;
  cash_ticks: number;
  positions: { symbol_id: number; qty: number }[];
}

export type PortfolioAction =
  | { type: "private"; message: PrivateMessage; at?: number }
  | {
      type: "resync";
      openOrders: OpenOrderItemDto[];
      portfolio: PortfolioDto;
      /** `symbol_id` → name. The REST endpoints speak ids; the private stream speaks names. */
      nameById: Record<number, string>;
      at?: number;
    }
  | { type: "reset" };

function withNotice(state: PortfolioState, notice: Notice): Notice[] {
  const notices = [notice, ...state.notices];
  return notices.length > NOTICE_LIMIT ? notices.slice(0, NOTICE_LIMIT) : notices;
}

function withoutZero(positions: Record<string, number>): Record<string, number> {
  const kept: Record<string, number> = {};
  for (const [symbol, qty] of Object.entries(positions)) {
    if (qty !== 0) kept[symbol] = qty;
  }
  return kept;
}

/**
 * Fold one action in. Never mutates — every branch returns a new object, because React state
 * that is mutated in place does not re-render.
 */
export function reduce(state: PortfolioState, action: PortfolioAction): PortfolioState {
  if (action.type === "reset") return EMPTY;

  if (action.type === "resync") {
    /**
     * Replace, never merge.
     *
     * A resync happens because a private message was *lost* (§3.5), so the local state is known
     * to be wrong in an unknown way — and merging a correct snapshot into unknown-wrong state
     * preserves exactly the error it was fetched to remove. These two endpoints exist solely
     * for this (Open Issue 014 §14e), and they are read from PostgreSQL, which the ledger
     * derives from the same stream the client just missed part of.
     */
    const positions: Record<string, number> = {};
    for (const position of action.portfolio.positions) {
      const name = action.nameById[position.symbol_id];
      if (name !== undefined && position.qty !== 0) positions[name] = position.qty;
    }
    const openOrders: Record<number, OpenOrderView> = {};
    for (const order of action.openOrders) {
      const name = action.nameById[order.symbol_id];
      if (name === undefined) continue;
      openOrders[order.order_id] = {
        orderId: order.order_id,
        clientOrderId: order.client_order_id,
        symbol: name,
        side: order.side,
        priceTicks: order.price_ticks,
        qty: order.qty,
        remainingQty: order.remaining_qty,
        tif: order.tif,
      };
    }
    return {
      ...state,
      cashTicks: action.portfolio.cash_ticks,
      positions,
      openOrders,
      resyncs: state.resyncs + 1,
      notices: withNotice(state, {
        kind: "resynced",
        clientOrderId: null,
        symbol: null,
        detail: "re-synchronised from the exchange",
        priceTicks: null,
        qty: null,
        at: action.at ?? 0,
      }),
    };
  }

  const message = action.message;
  const at = action.at ?? 0;
  const next = { ...state, applied: state.applied + 1 };

  switch (message.type) {
    case "AccountCreated": {
      // The grant. It is an event, not a row (`routes_auth.py`), so this is the first moment a
      // freshly registered client learns its balance.
      const initial = (message as unknown as { initial_cash_ticks?: number }).initial_cash_ticks;
      return { ...next, cashTicks: initial ?? next.cashTicks };
    }

    case "CashCredited": {
      const amount = (message as unknown as { amount_ticks?: number }).amount_ticks ?? 0;
      return { ...next, cashTicks: (next.cashTicks ?? 0) + amount };
    }

    case "OrderAccepted": {
      if (message.order_id === undefined || message.symbol == null) return next;
      const order: OpenOrderView = {
        orderId: message.order_id,
        clientOrderId: message.client_order_id ?? 0,
        symbol: message.symbol,
        side: (message as unknown as { side?: number }).side ?? 0,
        priceTicks: message.price_ticks ?? 0,
        qty: message.qty ?? 0,
        remainingQty: message.qty ?? 0,
        tif: (message as unknown as { tif?: number }).tif ?? 0,
      };
      return {
        ...next,
        openOrders: { ...next.openOrders, [order.orderId]: order },
        notices: withNotice(next, {
          kind: "accepted",
          clientOrderId: order.clientOrderId,
          symbol: order.symbol,
          detail: "resting",
          priceTicks: order.priceTicks,
          qty: order.qty,
          at,
        }),
      };
    }

    case "OrderRejected": {
      // No `order_id`: the order never reached the book and none was assigned. The reason is a
      // `RejectReason` integer, which the ticket resolves through `GET /symbols`' enum table
      // rather than hard-coding — the whole point of that endpoint serving the enums (§2.3).
      const reason = (message as unknown as { reason?: number }).reason;
      return {
        ...next,
        notices: withNotice(next, {
          kind: "rejected",
          clientOrderId: message.client_order_id ?? null,
          symbol: message.symbol ?? null,
          detail: `refused — ${reason ?? "unknown"}`,
          priceTicks: null,
          qty: null,
          at,
        }),
      };
    }

    case "Fill": {
      if (message.symbol == null) return next;
      const qty = message.qty ?? 0;
      const priceTicks = message.price_ticks ?? 0;
      const notional = priceTicks * qty;
      const fee = feeTicks(notional, message.role === "maker" ? MAKER_FEE_BPS : TAKER_FEE_BPS);

      // Which side this user was on. `aggressor_side` says who crossed the spread and `role`
      // says whether this user was that party, so the two together give the direction without
      // the message having to carry it — and `Side.BUY` is 1 (`schema.toml`).
      const aggressorSide = message.aggressor_side ?? 0;
      const userBought =
        message.role === "taker" ? aggressorSide === 1 : aggressorSide !== 1;

      // A buyer pays the notional and the fee; a seller receives the notional less the fee.
      // Identical to `Ledger.apply`, which is the arithmetic this has to agree with.
      const cashDelta = userBought ? -(notional + fee) : notional - fee;
      const positionDelta = userBought ? qty : -qty;

      const positions = withoutZero({
        ...next.positions,
        [message.symbol]: (next.positions[message.symbol] ?? 0) + positionDelta,
      });

      // The resting order shrinks. A fill names this user's own `order_id`, whichever side of
      // the trade it was, so one lookup serves both.
      const openOrders = { ...next.openOrders };
      const resting = message.order_id === undefined ? undefined : openOrders[message.order_id];
      if (resting !== undefined) {
        const remaining = resting.remainingQty - qty;
        if (remaining > 0) {
          openOrders[resting.orderId] = { ...resting, remainingQty: remaining };
        } else {
          delete openOrders[resting.orderId];
        }
      }

      return {
        ...next,
        cashTicks: (next.cashTicks ?? 0) + cashDelta,
        positions,
        openOrders,
        notices: withNotice(next, {
          kind: "filled",
          clientOrderId: resting?.clientOrderId ?? null,
          symbol: message.symbol,
          detail: `${userBought ? "bought" : "sold"} as ${message.role ?? "taker"}`,
          priceTicks,
          qty,
          at,
        }),
      };
    }

    case "OrderCancelled": {
      if (message.order_id === undefined) return next;
      const openOrders = { ...next.openOrders };
      const gone = openOrders[message.order_id];
      delete openOrders[message.order_id];
      return {
        ...next,
        openOrders,
        notices: withNotice(next, {
          kind: "cancelled",
          clientOrderId: gone?.clientOrderId ?? message.client_order_id ?? null,
          symbol: message.symbol ?? gone?.symbol ?? null,
          detail: "withdrawn",
          priceTicks: gone?.priceTicks ?? null,
          qty: (message as unknown as { remaining_qty?: number }).remaining_qty ?? null,
          at,
        }),
      };
    }

    default:
      // A record type this client has no opinion about. Ignoring it keeps the screen tolerant
      // of a stream that grows types, which is how the schema is expected to evolve — the same
      // reasoning `MarketState.apply` and `PrivateRouter.messages_for` both record.
      return next;
  }
}

/** Open orders, newest first, as the panel lists them. */
export function openOrderList(state: PortfolioState): OpenOrderView[] {
  return Object.values(state.openOrders).sort((a, b) => b.orderId - a.orderId);
}

/** Held positions, in a stable order so rows do not jump between renders. */
export function positionList(state: PortfolioState): { symbol: string; qty: number }[] {
  return Object.entries(state.positions)
    .map(([symbol, qty]) => ({ symbol, qty }))
    .sort((a, b) => a.symbol.localeCompare(b.symbol));
}
