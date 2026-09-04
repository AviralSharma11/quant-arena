import type { ConnectionState } from "../stream/types.ts";

/**
 * Connection state, visible at all times (Task 5.4, Success Criterion 4).
 *
 * Open Issue 014 §14e: "a trading interface that silently shows stale prices is worse than one
 * that admits it is disconnected." So this is chrome that is always on screen, not a toast that
 * appears and fades.
 *
 * React state is the right home for exactly this kind of value — it changes a handful of times
 * a session. The book, which changes twenty times a second, deliberately does not live here;
 * see `stream/buffer.ts`.
 *
 * `halted` is its own state and not a flavour of disconnected. The socket is fine and the
 * prices are current; the *exchange* cannot durably record an order (Open Issue 003 §8.5), so
 * the market is readable and untradeable, and conflating that with a network problem would tell
 * the user to check their wifi.
 */
const LABELS: Record<ConnectionState, string> = {
  connecting: "Connecting",
  connected: "Live",
  reconnecting: "Reconnecting",
  halted: "Exchange halted",
  closed: "Disconnected",
};

export function ConnectionIndicator({ state }: { state: ConnectionState }) {
  return (
    <span className={`conn conn-${state}`} role="status" aria-live="polite">
      <span className="conn-dot" aria-hidden="true" />
      {LABELS[state]}
    </span>
  );
}
