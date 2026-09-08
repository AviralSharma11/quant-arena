/**
 * The order ticket — buy/sell, limit and market, quantity and price.
 *
 * React state throughout, and that is correct rather than a compromise. `buffer.ts` keeps market
 * data out of React because twenty messages a second would be twenty re-renders; a form that
 * changes when a human types is the low-frequency chrome Open Issue 014 §14a explicitly assigns
 * to framework state.
 *
 * ## The ticket never reports an outcome
 *
 * `POST /orders` returns an acknowledgement: the request was durably recorded, not that it
 * traded or that the order exists (Open Issue 008 §9h). So the button's answer is "sent", and
 * everything that actually happened — accepted, rejected, filled — appears in the notices below,
 * which come from the **private stream**. Showing "order placed" off the 202 would be showing an
 * outcome the gateway had not yet observed, which §14e is precisely about not doing.
 *
 * The one exception is a synchronous refusal: a `409` from a risk check, a `429`, a `503`. Those
 * never reach the stream at all, because the order never reached the engine, so if the ticket
 * did not report them nothing would.
 */

import { useState } from "react";

import { cancelOrder, submitOrder, type OrderSide, type OrderType } from "../stream/orders";
import type { Notice } from "../stream/portfolio";
import { formatTicks, type Symbol } from "../stream/symbols";

export interface OrderTicketProps {
  symbol: Symbol;
  /** Best bid and best ask in ticks, read once per submission to prefill — never polled. */
  topOfBook: () => { bid: number | null; ask: number | null };
  notices: Notice[];
  /** Needed to render a notice at the scale of *its own* symbol, which is not necessarily the
   *  selected one — a fill can arrive on any symbol this session has an order on. */
  symbolsByName: Map<string, Symbol>;
  /** Injectable so the ticket can be driven in a test without a network. */
  submit?: typeof submitOrder;
}

/**
 * One notice line: what happened, to which symbol, at what price.
 *
 * The price is formatted at its own symbol's scale rather than the selected one. Ten listed
 * symbols carry four different tick sizes, so a fill on QAJ rendered against QAA's divisor is
 * wrong by four orders of magnitude — and `formatTicks` throws rather than defaulting, so a
 * symbol the table does not know shows no number at all instead of a plausible wrong one.
 */
function NoticeLine({ notice, symbolsByName }: { notice: Notice; symbolsByName: Map<string, Symbol> }) {
  const symbol = notice.symbol === null ? undefined : symbolsByName.get(notice.symbol);
  const price =
    notice.priceTicks !== null && symbol !== undefined
      ? formatTicks(notice.priceTicks, symbol)
      : null;

  return (
    <li className={`notice ${notice.kind}`}>
      <span className="notice-kind">{notice.kind}</span>{" "}
      {notice.symbol !== null && <><span className="notice-symbol">{notice.symbol}</span>{" "}</>}
      <span className="notice-detail">{notice.detail}</span>
      {notice.qty !== null && <> {notice.qty}</>}
      {price !== null && <> @ {price}</>}
    </li>
  );
}

const BUY: OrderSide = 1;
const SELL: OrderSide = 2;

export function OrderTicket({
  symbol,
  topOfBook,
  notices,
  symbolsByName,
  submit = submitOrder,
}: OrderTicketProps) {
  const [side, setSide] = useState<OrderSide>(BUY);
  const [orderType, setOrderType] = useState<OrderType>("limit");
  const [qty, setQty] = useState("1");
  const [price, setPrice] = useState("");
  const [pending, setPending] = useState(false);
  const [ack, setAck] = useState<string | null>(null);

  /** Prefill from the touch of the book this side would trade against. A convenience, and it
   *  reads the buffer once on click rather than subscribing to it — the ticket must not
   *  re-render at the book's rate. */
  function prefill() {
    const { bid, ask } = topOfBook();
    const reference = side === BUY ? ask : bid;
    if (reference !== null) setPrice(formatTicks(reference, symbol));
  }

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    setAck(null);

    const quantity = Number(qty);
    if (!Number.isInteger(quantity) || quantity <= 0) {
      setAck("Quantity must be a whole number above zero.");
      return;
    }
    const displayPrice = Number(price);
    if (orderType === "limit" && (!Number.isFinite(displayPrice) || displayPrice <= 0)) {
      setAck("A limit order needs a price above zero.");
      return;
    }

    setPending(true);
    try {
      const result = await submit({
        symbol,
        side,
        orderType,
        qty: quantity,
        displayPrice: orderType === "limit" ? displayPrice : undefined,
      });
      // Deliberately not "placed". 202 means recorded; the private stream says what happened.
      if (result.status === "accepted") {
        setAck(`Sent — ${result.clientOrderId}. Watching the private stream for the outcome.`);
      } else if (result.status === "in_progress") {
        setAck("A request with this id is still in flight. Awaiting the private stream.");
      } else if (result.status === "rate_limited") {
        setAck("Rate limited — not processed. Try again in a moment.");
      } else if (result.status === "halted") {
        setAck(`Exchange halted (${result.reason ?? "unknown"}). Nothing was recorded.`);
      } else {
        // A 409 from a risk check. This never reaches the private stream, because the order
        // never reached the engine — so if the ticket did not say so, nothing would.
        setAck(`Rejected: ${result.reason ?? "unknown"}`);
      }
    } catch {
      setAck("The gateway could not be reached. Nothing was recorded.");
    } finally {
      setPending(false);
    }
  }

  return (
    <section className="ticket-panel" aria-label={`Order ticket for ${symbol.name}`}>
      <h3>Ticket</h3>
      <form className="ticket" onSubmit={onSubmit}>
        <div className="ticket-sides" role="group" aria-label="Side">
          <button
            type="button"
            className={side === BUY ? "side buy selected" : "side buy"}
            aria-pressed={side === BUY}
            onClick={() => setSide(BUY)}
          >
            Buy
          </button>
          <button
            type="button"
            className={side === SELL ? "side sell selected" : "side sell"}
            aria-pressed={side === SELL}
            onClick={() => setSide(SELL)}
          >
            Sell
          </button>
        </div>

        <label>
          Type
          <select
            value={orderType}
            onChange={(event) => setOrderType(event.target.value as OrderType)}
          >
            <option value="limit">Limit</option>
            <option value="market">Market</option>
          </select>
        </label>

        <label>
          Quantity
          <input
            type="number"
            min={1}
            step={1}
            value={qty}
            onChange={(event) => setQty(event.target.value)}
          />
        </label>

        <label>
          Price
          <input
            type="number"
            step="any"
            value={orderType === "market" ? "" : price}
            placeholder={orderType === "market" ? "at the band" : "0.00"}
            /* A market order carries no price: the gateway derives a banded limit off the
               opposing touch, and sending one is a 400. Disabled rather than ignored, so the
               reason is visible rather than a silently dropped field. */
            disabled={orderType === "market"}
            onChange={(event) => setPrice(event.target.value)}
          />
        </label>

        <button type="button" className="prefill" onClick={prefill} disabled={orderType === "market"}>
          Touch
        </button>

        <button type="submit" className="submit" disabled={pending}>
          {pending ? "Sending…" : `${side === BUY ? "Buy" : "Sell"} ${symbol.name}`}
        </button>
      </form>

      {ack !== null && (
        <p className="ticket-ack" role="status">
          {ack}
        </p>
      )}

      <ul className="notices" aria-label="Recent order events">
        {notices.map((notice, index) => (
          <NoticeLine key={`${notice.at}-${index}`} notice={notice} symbolsByName={symbolsByName} />
        ))}
      </ul>
    </section>
  );
}

export interface OpenOrdersProps {
  orders: import("../stream/portfolio").OpenOrderView[];
  symbolsByName: Map<string, Symbol>;
  /** Injectable so a test can drive cancellation without a network. */
  cancel?: typeof cancelOrder;
}

/**
 * Open orders, with cancellation.
 *
 * Driven entirely by the private stream: an order appears on `OrderAccepted`, shrinks on `Fill`,
 * and leaves on `OrderCancelled`. Nothing here polls `GET /orders/open` — that endpoint is read
 * once, on a detected gap, from `App.tsx`.
 *
 * **A cancel that loses the race to a fill** (6.1's third success criterion) needs no special
 * case here, and that is the design working rather than an omission. The button sends a request
 * the gateway records; if the order filled first the engine answers `UNKNOWN_ORDER` and the row
 * is already gone, removed by the `Fill` that filled it. The row disappearing is the correct UI
 * outcome either way, and it disappears because of what the stream said, not because the button
 * was pressed.
 */
export function OpenOrders({ orders, symbolsByName, cancel = cancelOrder }: OpenOrdersProps) {
  const [cancelling, setCancelling] = useState<Record<number, boolean>>({});

  async function onCancel(clientOrderId: number, orderId: number) {
    setCancelling((current) => ({ ...current, [orderId]: true }));
    try {
      await cancel(clientOrderId);
    } catch {
      // The row stays; the stream is what removes it. A failed cancel that hid the order would
      // show a user an order they still have.
    } finally {
      setCancelling((current) => {
        const next = { ...current };
        delete next[orderId];
        return next;
      });
    }
  }

  return (
    <section className="orders-panel" aria-label="Open orders">
      <h3>Open orders</h3>
      {orders.length === 0 ? (
        <p className="empty">Nothing resting.</p>
      ) : (
        <table className="open-orders">
          <thead>
            <tr>
              <th>Symbol</th>
              <th>Side</th>
              <th>Price</th>
              <th>Left</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {orders.map((order) => {
              const symbol = symbolsByName.get(order.symbol);
              return (
                <tr key={order.orderId} className={order.side === BUY ? "buy" : "sell"}>
                  <td>{order.symbol}</td>
                  <td>{order.side === BUY ? "Buy" : "Sell"}</td>
                  {/* `formatTicks` throws without a tick size rather than defaulting to 1: a
                      7,983,040-tick price rendered as "7983040" beside a correct one looks like
                      a number, not a bug. A symbol missing from the table is shown as such. */}
                  <td>{symbol ? formatTicks(order.priceTicks, symbol) : "—"}</td>
                  <td>
                    {order.remainingQty}
                    {order.remainingQty !== order.qty && <span className="of"> of {order.qty}</span>}
                  </td>
                  <td>
                    <button
                      type="button"
                      className="cancel"
                      disabled={cancelling[order.orderId] === true}
                      onClick={() => onCancel(order.clientOrderId, order.orderId)}
                    >
                      {cancelling[order.orderId] === true ? "…" : "Cancel"}
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </section>
  );
}
