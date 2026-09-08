/**
 * Submitting and cancelling — the only two REST calls the trading screen makes on purpose.
 *
 * `POST /orders` and `DELETE /orders/{id}` are *actions*. `GET /orders/open` and `GET /portfolio`
 * are re-synchronisation and are called from one place only, on a detected sequence gap
 * (Open Issue 014 §14e); 6.1's Boundaries forbid polling them, and nothing here does.
 *
 * ## The API is acknowledgement-shaped
 *
 * A `202` means the request was **durably recorded**, not that it traded or that the order
 * exists. `order_id` comes back null, because the engine assigns it and the gateway does not yet
 * know it (Open Issue 008 §9h). Everything that actually happened — accepted, rejected, filled,
 * cancelled — arrives on the private stream. So these functions deliberately return the
 * acknowledgement and nothing more: a caller tempted to render an outcome from this response is
 * rendering a guess.
 *
 * ## Three places the implementation and `rest_and_ws.md` §2.2 differ
 *
 * Found while writing this, and specified back to Dev A as the second half of Amendment 2
 * (`HANDOFF.md` §3). The code below follows the **gateway**, which is right in two of the three:
 *
 * 1. The request carries `symbol_id`, not §2.2's `"symbol": "BTC"`. §2.3 makes `GET /symbols`
 *    the only name↔id mapping and `schema.toml` types the field as an `i16`, so resolving a
 *    name at the REST edge would be a second mapping to keep in step.
 * 2. A duplicate of a completed request comes back `202 accepted` / `409 rejected` rather than
 *    §2.2's `200 replay`. Either way the same `client_order_id` yields the same answer, which
 *    is the property Open Issue 008 §13 actually requires.
 * 3. `DELETE` carries a **body** — the cancel's own `client_order_id`. §2.2 shows none, but
 *    Open Issue 008 makes a cancel a request in its own right: one id names the order being
 *    cancelled, the other makes the cancel itself retry-safe.
 */

import { toTicks, type Symbol } from "./symbols.ts";

export type OrderSide = 1 | 2;
export type OrderType = "limit" | "market";

/**
 * `client_order_id` generation — a millisecond timestamp, never a counter from 1.
 *
 * This is the trap `HANDOFF.md` §5 and the decision log both record from Task 4.4's bots: a
 * counter that restarts at 1 re-sends keys the idempotency store has already answered, so every
 * order comes back acknowledged, none is appended, and the market goes silent while every
 * participant reports success. A page reload restarts a counter exactly the way a bot restart
 * does, so the browser has the same problem.
 *
 * The low bit of a millisecond clock is not unique enough on its own — a user can click twice
 * inside a millisecond, and React can fire an effect twice in development — so a per-session
 * counter is folded into the low digits. Monotonic within a session, distinct across reloads.
 */
let counter = 0;
export function nextClientOrderId(now: () => number = Date.now): number {
  counter = (counter + 1) % 1_000;
  return now() * 1_000 + counter;
}

export interface Acknowledgement {
  ok: boolean;
  status: "accepted" | "in_progress" | "rejected" | "halted" | "rate_limited" | "error";
  clientOrderId: number;
  /** The Redis stream id, which *is* the sequence number (Open Issue 003). */
  seq: string | null;
  /** A `RejectReason` name on a 409, or a halt reason on a 503. */
  reason: string | null;
  httpStatus: number;
}

async function interpret(
  response: Response,
  clientOrderId: number,
): Promise<Acknowledgement> {
  let body: Record<string, unknown> = {};
  try {
    body = (await response.json()) as Record<string, unknown>;
  } catch {
    // A 204, or a proxy error page. The status code still says what happened.
  }
  const detail = (body.detail ?? {}) as Record<string, unknown>;
  const reason = typeof detail.reason === "string" ? detail.reason : null;

  if (response.ok) {
    // 202 carries `status`, which is "accepted" or "in_progress" — a retry that arrived while
    // the original was still in flight must not be shown as a placed order.
    const status = body.status === "in_progress" ? "in_progress" : "accepted";
    return {
      ok: true,
      status,
      clientOrderId,
      seq: typeof body.seq === "string" && body.seq !== "" ? body.seq : null,
      reason: null,
      httpStatus: response.status,
    };
  }
  if (response.status === 429) {
    // Not a rejection. "Not processed, ask again" — nothing was recorded against the key,
    // because rate limiting runs ahead of the idempotency claim by design.
    return { ok: false, status: "rate_limited", clientOrderId, seq: null, reason, httpStatus: 429 };
  }
  if (response.status === 503) {
    return { ok: false, status: "halted", clientOrderId, seq: null, reason, httpStatus: 503 };
  }
  if (response.status === 409) {
    return { ok: false, status: "rejected", clientOrderId, seq: null, reason, httpStatus: 409 };
  }
  return {
    ok: false,
    status: "error",
    clientOrderId,
    seq: null,
    reason: reason ?? `HTTP ${response.status}`,
    httpStatus: response.status,
  };
}

export interface SubmitRequest {
  symbol: Symbol;
  side: OrderSide;
  orderType: OrderType;
  /** Quantity in units, as the contract counts them. */
  qty: number;
  /** In *display* units — converted here, once. Omitted for a market order. */
  displayPrice?: number;
  tif?: 1 | 2;
}

/**
 * Submit one order.
 *
 * The price is multiplied back into integer ticks here and nowhere else. Open Issue 016 permits
 * a float in the presentation layer and calls one below it a bug, so this function is the
 * boundary: a float goes in, integer ticks go out, and nothing downstream sees the float.
 */
export async function submitOrder(
  request: SubmitRequest,
  fetchImpl: typeof fetch = fetch,
  now: () => number = Date.now,
): Promise<Acknowledgement> {
  const clientOrderId = nextClientOrderId(now);
  const body: Record<string, unknown> = {
    client_order_id: clientOrderId,
    symbol_id: request.symbol.symbol_id,
    side: request.side,
    // A market order is IOC by the gateway's own doing — it rewrites the tif — but sending GTC
    // for a limit order is this client's to choose, and GTC is what a resting quote means.
    tif: request.tif ?? 1,
    qty: request.qty,
    order_type: request.orderType,
  };
  if (request.orderType === "limit") {
    // Required for a limit order, and the model validator rejects it outright on a market one:
    // the band sets that price, and sending both is a 400 before anything else happens.
    body.price_ticks = toTicks(request.displayPrice ?? 0, request.symbol);
  }

  const response = await fetchImpl("/orders", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return interpret(response, clientOrderId);
}

/**
 * Cancel by the *target's* client order id, with a fresh key for the cancel itself.
 *
 * Two identifiers (Open Issue 008). The path names the order being withdrawn; the body carries
 * this cancel's own idempotency key, so a cancel whose response was lost can be retried without
 * risking a second one. It is also why a client can cancel an order whose acknowledgement it
 * never received.
 */
export async function cancelOrder(
  targetClientOrderId: number,
  fetchImpl: typeof fetch = fetch,
  now: () => number = Date.now,
): Promise<Acknowledgement> {
  const clientOrderId = nextClientOrderId(now);
  const response = await fetchImpl(`/orders/${targetClientOrderId}`, {
    method: "DELETE",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ client_order_id: clientOrderId }),
  });
  return interpret(response, clientOrderId);
}
