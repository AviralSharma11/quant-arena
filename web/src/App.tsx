import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import { BrowserRouter, Link, Navigate, Route, Routes, useLocation } from "react-router-dom";

import { ConnectionIndicator } from "./components/ConnectionState";
import { ROUTES } from "./routes";
import { Auth, type UserSession } from "./screens/Auth";
import { Placeholder } from "./screens/Placeholder";
import { Trading } from "./screens/Trading";
import {
  EMPTY,
  reduce,
  type OpenOrderItemDto,
  type PortfolioDto,
} from "./stream/portfolio";
import { startStreamSession, type StreamSession } from "./stream/session";
import { fetchSymbols, type Symbol } from "./stream/symbols";
import type { ConnectionState } from "./stream/types";

function Nav({ user, connection }: { user: UserSession | null; connection: ConnectionState }) {
  const { pathname } = useLocation();
  return (
    <nav>
      {ROUTES.map((route) => (
        <Link
          key={route.id}
          to={route.path}
          aria-current={pathname === route.path ? "page" : undefined}
        >
          {route.id === "auth" && user ? `Sign in (${user.username})` : route.title}
        </Link>
      ))}
      {/* On every screen, always. A trading interface that silently shows stale prices is
          worse than one that admits it is disconnected (Open Issue 014 §14e). */}
      <ConnectionIndicator state={connection} />
    </nav>
  );
}

export default function App() {
  const [user, setUser] = useState<UserSession | null>(() => {
    try {
      const saved = localStorage.getItem("qa_user");
      return saved ? JSON.parse(saved) : null;
    } catch {
      return null;
    }
  });

  // Low-frequency chrome, so React state is exactly right for it. The book is not here.
  const [connection, setConnection] = useState<ConnectionState>("closed");
  const [stream, setStream] = useState<StreamSession | null>(null);
  // The symbol table, fetched once from `GET /symbols` — §2.3's only source of names and tick
  // sizes. React state because it arrives asynchronously and the tree genuinely depends on it,
  // and because it is written exactly once per session.
  const [symbols, setSymbols] = useState<readonly Symbol[]>([]);
  const resyncing = useRef(false);

  /**
   * Private state — cash, positions, open orders — folded from the private stream (Task 6.1b).
   *
   * React state, and deliberately so. `buffer.ts` keeps the book out of React because twenty
   * messages a second would be twenty re-renders; private data arrives when a human places an
   * order, which is exactly the low-frequency chrome Open Issue 014 §14a assigns to framework
   * state. The rule in `buffer.ts` is about frequency, not principle.
   */
  const [portfolio, dispatch] = useReducer(reduce, EMPTY);

  /** `symbol_id` → name. The REST re-synchronisation endpoints speak ids; the private stream
   *  speaks names, and `GET /symbols` (§2.3) is the only thing that maps one to the other. */
  const nameById = useMemo(() => {
    const table: Record<number, string> = {};
    for (const symbol of symbols) table[symbol.symbol_id] = symbol.name;
    return table;
  }, [symbols]);
  /**
   * Re-synchronise after a detected private-stream gap.
   *
   * Keyed on `nameById`, which changes only when the symbol table does — and the symbol table
   * is fetched exactly once per session. So this identity is stable in practice, which matters:
   * it is handed to the stream session in an effect, and a new identity every render would tear
   * the socket down and rebuild it on every state change, which is a reconnect per fill. The
   * session effect already depends on `symbols`, so the two change together or not at all.
   *
   * Both responses are **parsed and applied**. Until 6.1b this fetched and discarded them,
   * which made the whole gap path a no-op costing two HTTP round trips: the calls exist solely
   * to repair state a lost message corrupted (Open Issue 014 §14e), so throwing the answer away
   * left the corruption in place.
   */
  const resync = useCallback(async () => {
    if (resyncing.current) return;
    resyncing.current = true;
    try {
      const [ordersResponse, portfolioResponse] = await Promise.all([
        fetch("/orders/open").catch(() => null),
        fetch("/portfolio").catch(() => null),
      ]);
      if (!ordersResponse?.ok || !portfolioResponse?.ok) return;
      const openOrders = (await ordersResponse.json()) as OpenOrderItemDto[];
      const portfolioBody = (await portfolioResponse.json()) as PortfolioDto;
      dispatch({
        type: "resync",
        openOrders,
        portfolio: portfolioBody,
        nameById,
        at: Date.now(),
      });
    } catch {
      // A resync that fails leaves the previous state, which is wrong in a known direction and
      // will be repaired by the next gap. Clearing it instead would show an empty portfolio to
      // a user who holds positions — worse than stale.
    } finally {
      resyncing.current = false;
    }
  }, [nameById]);

  // One stream for the life of the app, but it cannot start until the symbol table is known:
  // the channels to subscribe to are derived from it. Two effects, ordered by that dependency.
  useEffect(() => {
    let cancelled = false;
    fetchSymbols()
      .then((table) => {
        if (!cancelled) setSymbols(table.symbols);
      })
      .catch(() => {
        // Leave the table empty. The trading screen says so rather than inventing symbols —
        // a client that guessed would show prices at the wrong scale (Open Issue 014 §14e).
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (symbols.length === 0) return;
    const session = startStreamSession({
      symbols,
      onState: (state) => {
        setConnection(state);
        // **Every time the socket comes up, not only the first.**
        //
        // `StreamClient.onopen` resets the sequence tracker, because the server's position is
        // unknown across a reconnect and the first message back would otherwise look like a gap
        // on every channel at once. That reset is also an admission: whatever this client missed
        // while it was disconnected is gone, and no gap will ever be detected for it.
        //
        // So a connect is a gap, and it is repaired the same way §3.5 repairs one. The case that
        // exposed this: the session starts at mount while the visitor is signed *out*, so its
        // one resync got a 401 and the socket reconnect-looped; signing in made the next retry
        // succeed, and cash sat on "awaiting the grant…" because nothing re-ran the fetch. The
        // `AccountCreated` carrying the grant had been and gone while nobody was listening —
        // `PrivateRouter.route` only delivers to connected users.
        if (state === "connected") void resync();
      },
      // A private-stream gap means this client missed an order event, so its view of open
      // orders and balances is wrong (Open Issue 014 §14e). These two endpoints exist *solely*
      // for this — the UI must never poll them.
      onResync: resync,
      // Every private message this session is party to. Low-frequency and never dropped, so it
      // goes into React state rather than the frame-loop buffer.
      onPrivate: (message) => dispatch({ type: "private", message, at: Date.now() }),
    });
    setStream(session);
    return () => session.stop();
  }, [symbols, resync]);

  // Verify session on page mount
  useEffect(() => {
    fetch("/portfolio")
      .then((res) => {
        if (res.ok) {
          return res.json();
        } else if (res.status === 401) {
          // Session was revoked in Redis or expired
          setUser(null);
          localStorage.removeItem("qa_user");
        }
      })
      .then((portfolio) => {
        if (!portfolio) return;
        // Functional form, so this does not close over `user` and cannot act on a stale copy
        // of it. It also says the rule directly: a session rehydrated from the server never
        // overwrites one already restored from localStorage, which is the one that knows the
        // real username — `/portfolio` does not carry it, and inventing `user_<id>` would put
        // a fabricated name in the nav.
        setUser((current) =>
          current ?? { user_id: portfolio.user_id, username: `#${portfolio.user_id}` },
        );
      })
      .catch(() => {
        // Network error or gateway offline
      });
  }, []);

  function handleLogin(session: UserSession) {
    setUser(session);
    try {
      localStorage.setItem("qa_user", JSON.stringify(session));
    } catch {
      // Storage unavailable
    }
  }

  function handleLogout() {
    setUser(null);
    try {
      localStorage.removeItem("qa_user");
    } catch {
      // Storage unavailable
    }
  }

  return (
    <BrowserRouter>
      <Nav user={user} connection={connection} />
      <main>
        <Routes>
          {ROUTES.map((route) => (
            <Route
              key={route.id}
              path={route.path}
              element={
                route.id === "auth" ? (
                  <Auth user={user} onLogin={handleLogin} onLogout={handleLogout} />
                ) : route.id === "trading" ? (
                  <Trading
                    buffer={stream?.buffer ?? null}
                    symbols={symbols}
                    portfolio={portfolio}
                    signedIn={user !== null}
                  />
                ) : (
                  <Placeholder route={route} />
                )
              }
            />
          ))}
          {/* Three screens only. Anything else goes to the trading screen rather than
              growing a fourth page (Open Issue 014 §11.1). */}
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </BrowserRouter>
  );
}

