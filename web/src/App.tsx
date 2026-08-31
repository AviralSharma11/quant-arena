import { useEffect, useState } from "react";
import { BrowserRouter, Link, Navigate, Route, Routes, useLocation } from "react-router-dom";

import { ROUTES } from "./routes";
import { Auth, type UserSession } from "./screens/Auth";
import { Placeholder } from "./screens/Placeholder";

function Nav({ user }: { user: UserSession | null }) {
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
      <Nav user={user} />
      <main>
        <Routes>
          {ROUTES.map((route) => (
            <Route
              key={route.id}
              path={route.path}
              element={
                route.id === "auth" ? (
                  <Auth user={user} onLogin={handleLogin} onLogout={handleLogout} />
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

