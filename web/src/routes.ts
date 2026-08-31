/**
 * The three screens, as data.
 *
 * Open Issue 014 sub-decision 14d fixes the list at exactly three and §11.1 records why: user
 * interface work has no natural stopping point, so the limit is a scope commitment made in
 * advance rather than a judgement made later under pressure. A fourth screen is a scope change
 * to be raised, not an addition to this array.
 *
 * Kept as a plain exported array so the router is built from one source, and so the count can
 * be asserted from outside the browser — see tests/web/test_scaffold.py.
 */

export type ScreenId = "trading" | "auth" | "backtest";

export interface RouteDefinition {
  id: ScreenId;
  path: string;
  title: string;
  /** What the screen will hold, and which task builds it. */
  summary: string;
  builtBy: string;
}

export const ROUTES: readonly RouteDefinition[] = [
  {
    id: "trading",
    path: "/",
    title: "Trading",
    summary:
      "Candlestick chart, L2 book at ten levels, trade tape, order ticket, open orders, portfolio.",
    builtBy: "Task 6.1",
  },
  {
    id: "auth",
    path: "/login",
    title: "Sign in",
    summary: "Login and registration. Deliberately unremarkable.",
    builtBy: "Task 5.4b",
  },
  {
    id: "backtest",
    path: "/backtest",
    title: "Backtest",
    summary:
      "Strategy picker, symbol, date range, run. Equity curve, drawdown, metrics, buy-and-hold comparison.",
    builtBy: "Task 7.2",
  },
] as const;
