import { useEffect, useRef, useState } from "react";
import { BrowserRouter, Link, Navigate, Route, Routes, useLocation } from "react-router-dom";

import { ConnectionIndicator } from "./components/ConnectionState";
import { LiveQuote } from "./components/LiveQuote";
import { ROUTES } from "./routes";
import { Auth, type UserSession } from "./screens/Auth";
import { Placeholder } from "./screens/Placeholder";
import type { MarketBuffer } from "./stream/buffer";
import { MOCK_SYMBOLS, startStreamSession, type StreamSession } from "./stream/session";
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

/**
 * The trading route, until Task 6.1 builds it properly.
 *
 * One quote strip per symbol, painted by the `requestAnimationFrame` loop rather than by
 * React — which is what makes Success Criterion 5 checkable at all. Deliberately not a book,
 * not a chart, not an order ticket: those are 6.1's, and building them here would be building
 * past the task.
 */
function Trading({ buffer }: { buffer: MarketBuffer | null }) {
  const route = ROUTES.find((r) => r.id === "trading")!;
  return (
    <>
      <Placeholder route={route} />
      {buffer && (
        <section className="quotes">
          <h2>Live quotes</h2>
          <p className="pending">
            Painted outside React state, on the frame loop. The message and frame counts differ
            because the browser conflates exactly as the server does.
          </p>
          {MOCK_SYMBOLS.map((symbol) => (
            <LiveQuote key={symbol} symbol={symbol} buffer={buffer} />
          ))}
        </section>
      )}
    </>
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
  const resyncing = useRef(false);

  // One stream for the life of the app. Started here rather than per screen so that navigating
  // between the three does not drop and re-establish the connection.
  useEffect(() => {
    const session = startStreamSession({
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
  }, []);

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
                  <Trading buffer={stream?.buffer ?? null} />
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

