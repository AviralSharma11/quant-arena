/**
 * Cash and positions, driven by the private stream.
 *
 * The balance moves on a `Fill`, not on a poll. `services/fanout/private.py` sends this user
 * every record they are party to, and `stream/portfolio.ts` applies the same maker/taker
 * arithmetic the ledger does — so the number here changes at the moment the trade prints, which
 * is what 6.1's first success criterion means by "without refreshing".
 *
 * Cash is `null` until something says otherwise, and is rendered as such rather than as zero. A
 * balance of zero and a balance not yet known are different facts; showing the second as the
 * first tells a freshly registered user they are bankrupt while their grant is still in flight.
 * The grant is an event (`routes_auth.py`), so there genuinely is a moment when it is unknown.
 */

import { positionList, type PortfolioState } from "../stream/portfolio";
import { formatTicks, type Symbol } from "../stream/symbols";

export interface PortfolioPanelProps {
  state: PortfolioState;
  symbolsByName: Map<string, Symbol>;
  /** The symbol whose scale cash is shown in. Ticks are one currency across the venue, so this
   *  is a display choice, not a conversion. */
  cashSymbol: Symbol;
}

export function PortfolioPanel({ state, symbolsByName, cashSymbol }: PortfolioPanelProps) {
  const positions = positionList(state);

  return (
    <section className="portfolio-panel" aria-label="Portfolio">
      <h3>Portfolio</h3>

      <dl className="balance">
        <dt>Cash</dt>
        <dd className="cash">
          {state.cashTicks === null ? (
            <span className="pending">awaiting the grant…</span>
          ) : (
            formatTicks(state.cashTicks, cashSymbol)
          )}
        </dd>
      </dl>

      {positions.length === 0 ? (
        <p className="empty">No positions.</p>
      ) : (
        <table className="positions">
          <thead>
            <tr>
              <th>Symbol</th>
              <th>Quantity</th>
            </tr>
          </thead>
          <tbody>
            {positions.map(({ symbol, qty }) => (
              <tr key={symbol} className={qty < 0 ? "short" : "long"}>
                <td>{symbol}</td>
                <td>{qty}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {/* Only non-zero positions are kept, so an emptied one disappears rather than showing a
          zero row — `withoutZero` in the reducer. `symbolsByName` is threaded through for the
          same reason the book uses it: a name the symbol table does not know must not be
          rendered at a guessed scale. */}
      {positions.some(({ symbol }) => !symbolsByName.has(symbol)) && (
        <p className="warning">
          A position references a symbol not in <code>GET /symbols</code>.
        </p>
      )}
    </section>
  );
}
