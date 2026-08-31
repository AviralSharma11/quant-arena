import { BrowserRouter, Link, Navigate, Route, Routes, useLocation } from "react-router-dom";

import { ROUTES } from "./routes";
import { Placeholder } from "./screens/Placeholder";

function Nav() {
  const { pathname } = useLocation();
  return (
    <nav>
      {ROUTES.map((route) => (
        <Link
          key={route.id}
          to={route.path}
          aria-current={pathname === route.path ? "page" : undefined}
        >
          {route.title}
        </Link>
      ))}
    </nav>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <Nav />
      <main>
        <Routes>
          {ROUTES.map((route) => (
            <Route
              key={route.id}
              path={route.path}
              element={<Placeholder route={route} />}
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
