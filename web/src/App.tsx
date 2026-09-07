import { useEffect, useRef, useState } from "react";
import { BrowserRouter, Link, Navigate, Route, Routes, useLocation } from "react-router-dom";

import { ConnectionIndicator } from "./components/ConnectionState";
import { ROUTES } from "./routes";
import { Auth, type UserSession } from "./screens/Auth";
import { Placeholder } from "./screens/Placeholder";
import { Trading } from "./screens/Trading";
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
      onState: setConnection,
      onResync: async () => {
        // A private-stream gap means this client missed an order event, so its view of open
        // orders and balances is wrong (Open Issue 014 §14e). These two endpoints exist
        // *solely* for this — the UI must never poll them.
        if (resyncing.current) return;
        resyncing.current = true;
        try {
          await Promise.all([
            fetch("/orders/open").catch(() => null),
            fetch("/portfolio").catch(() => null),
          ]);
        } finally {
          resyncing.current = false;
        }
      },
    });
    setStream(session);
    return () => session.stop();
  }, [symbols]);

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
        if (portfolio && !user) {
          setUser({ user_id: portfolio.user_id, username: `user_${portfolio.user_id}` });
        }
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
                  <Trading buffer={stream?.buffer ?? null} symbols={symbols} />
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

