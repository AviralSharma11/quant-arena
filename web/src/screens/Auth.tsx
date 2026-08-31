import React, { useState } from "react";

export interface UserSession {
  user_id: number;
  username: string;
}

interface AuthProps {
  user: UserSession | null;
  onLogin: (user: UserSession) => void;
  onLogout: () => void;
}

export function Auth({ user, onLogin, onLogout }: AuthProps) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setMessage(null);
    setLoading(true);

    try {
      if (mode === "register") {
        const regRes = await fetch("/auth/register", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ username, password }),
        });

        if (!regRes.ok) {
          const errData = await regRes.json().catch(() => ({}));
          throw new Error(errData.detail || `Registration failed (${regRes.status})`);
        }

        setMessage("Account created successfully! Logging you in...");
      }

      // Login (either directly, or auto-login after successful registration)
      const loginRes = await fetch("/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });

      if (!loginRes.ok) {
        const errData = await loginRes.json().catch(() => ({}));
        throw new Error(errData.detail || `Login failed (${loginRes.status})`);
      }

      const userData: UserSession = await loginRes.json();
      onLogin(userData);
      setUsername("");
      setPassword("");
    } catch (err: any) {
      setError(err.message || "An unexpected error occurred");
    } finally {
      setLoading(false);
    }
  }

  async function handleLogoutClick() {
    setError(null);
    setLoading(true);
    try {
      await fetch("/auth/logout", { method: "POST" });
      onLogout();
      setMessage("Logged out successfully.");
    } catch (err: any) {
      setError(err.message || "Logout failed");
    } finally {
      setLoading(false);
    }
  }

  if (user) {
    return (
      <section className="auth-panel">
        <h1>Signed in as {user.username}</h1>
        <p>User ID: <code>{user.user_id}</code></p>
        <p className="status-note">Your session is stored in a secure cookie and survives page reloads.</p>
        <button
          type="button"
          onClick={handleLogoutClick}
          disabled={loading}
          className="btn-logout"
        >
          {loading ? "Signing out..." : "Sign out"}
        </button>
      </section>
    );
  }

  return (
    <section className="auth-panel">
      <h1>{mode === "login" ? "Sign In" : "Create Account"}</h1>
      <p className="auth-subtitle">
        {mode === "login"
          ? "Enter your credentials to access your trading account."
          : "Register to receive starting virtual capital and place orders."}
      </p>

      {error && <div className="alert error" role="alert">{error}</div>}
      {message && <div className="alert success">{message}</div>}

      <form onSubmit={handleSubmit} className="auth-form">
        <div className="form-group">
          <label htmlFor="username">Username</label>
          <input
            id="username"
            type="text"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            required
            minLength={3}
            maxLength={64}
            autoComplete="username"
            placeholder="e.g. trader_one"
            disabled={loading}
          />
        </div>

        <div className="form-group">
          <label htmlFor="password">Password</label>
          <input
            id="password"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            minLength={8}
            maxLength={256}
            autoComplete={mode === "login" ? "current-password" : "new-password"}
            disabled={loading}
          />
        </div>

        <button type="submit" disabled={loading} className="btn-submit">
          {loading
            ? "Processing..."
            : mode === "login"
            ? "Sign In"
            : "Register & Sign In"}
        </button>
      </form>

      <div className="auth-switch">
        {mode === "login" ? (
          <p>
            Don't have an account?{" "}
            <button
              type="button"
              className="btn-link"
              onClick={() => {
                setMode("register");
                setError(null);
                setMessage(null);
              }}
            >
              Create one
            </button>
          </p>
        ) : (
          <p>
            Already have an account?{" "}
            <button
              type="button"
              className="btn-link"
              onClick={() => {
                setMode("login");
                setError(null);
                setMessage(null);
              }}
            >
              Sign in
            </button>
          </p>
        )}
      </div>
    </section>
  );
}
