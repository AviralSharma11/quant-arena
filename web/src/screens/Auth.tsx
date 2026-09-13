import React, { useState } from "react";

import { ConnectionIndicator } from "../components/ConnectionState";
import {
  KeyIcon,
  LockIcon,
  LogInIcon,
  LogOutIcon,
  TerminalIcon,
  UserIcon,
  UserPlusIcon,
} from "../components/Icons";
import type { ConnectionState } from "../stream/types";

export interface UserSession {
  user_id: number;
  username: string;
}

interface AuthProps {
  onLogin: (user: UserSession) => void;
  /** A confirmation carried over from elsewhere — "Signed out." from the header. */
  notice: string | null;
  connection: ConnectionState;
  /** Listed instruments, for the facts panel. Zero while the table has not arrived. */
  symbolCount: number;
}

const USERNAME_MAX = 64;
const PASSWORD_MIN = 8;

/** How long "Account created" stays on screen before the terminal replaces it. */
const REGISTERED_PAUSE_MS = 700;

/** FastAPI sends `detail` as a string, an object, or a list of validation errors. */
function describe(detail: unknown, fallback: string): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail) && typeof detail[0]?.msg === "string") return detail[0].msg;
  if (detail && typeof detail === "object") {
    const { message, reason } = detail as {
      message?: unknown;
      reason?: unknown;
    };
    if (typeof message === "string") return message;
    if (typeof reason === "string") return reason;
  }
  return fallback;
}

/**
 * Sign in and registration — one screen, two modes.
 *
 * Shown only to a visitor without a session: `App` redirects every other route here, and sends a
 * session away from here. Sign-out therefore lives in the header, as `SignOutButton` below.
 */
export function Auth({ onLogin, notice, connection, symbolCount }: AuthProps) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  function switchMode(next: "login" | "register") {
    setMode(next);
    setError(null);
    setMessage(null);
  }

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
          throw new Error(describe(errData.detail, `Registration failed (${regRes.status})`));
        }

        setMessage("Account created. Signing you in…");
      }

      // Login (either directly, or auto-login after successful registration)
      const loginRes = await fetch("/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });

      if (!loginRes.ok) {
        const errData = await loginRes.json().catch(() => ({}));
        throw new Error(describe(errData.detail, `Login failed (${loginRes.status})`));
      }

      const userData: UserSession = await loginRes.json();
      // Let a new account's confirmation be read before the terminal replaces this screen.
      if (mode === "register") {
        await new Promise((resolve) => setTimeout(resolve, REGISTERED_PAUSE_MS));
      }
      onLogin(userData);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "An unexpected error occurred");
      setLoading(false);
    }
  }

  const success = message ?? notice;
  const passwordProgress = Math.min(password.length / PASSWORD_MIN, 1);

  return (
    <div className="auth-page">
      <div className="auth-grid">
        <aside className="auth-aside">
          <section className="panel auth-intro" aria-labelledby="auth-brand">
            <div className="auth-intro-top">
              <span className="label-caps accent">Simulated exchange</span>
              <span className="label-caps muted">No real money</span>
            </div>
            <h1 className="auth-brand" id="auth-brand">
              Quant Arena
            </h1>
            <p className="auth-lede">
              A live, continuously matching order book over ten simulated instruments, and a
              backtester over recorded market history.
            </p>
            <dl className="fact-list">
              <div>
                <dt>Instruments</dt>
                <dd>{symbolCount > 0 ? `${symbolCount} listed` : "—"}</dd>
              </div>
              <div>
                <dt>Order book</dt>
                <dd>L2 · 10 levels a side · 20 Hz</dd>
              </div>
              <div>
                <dt>Matching</dt>
                <dd>Price-time priority</dd>
              </div>
              <div>
                <dt>Starting capital</dt>
                <dd className="up">Granted on registration</dd>
              </div>
            </dl>
          </section>

          <section className="panel auth-note" aria-labelledby="auth-note-title">
            <h2 className="label-caps" id="auth-note-title">
              <TerminalIcon size={16} /> How an order is answered
            </h2>
            <p>
              Sending an order returns an acknowledgement, not a fill. Whether it was accepted,
              filled or cancelled arrives moments later on your private stream.
            </p>
          </section>
        </aside>

        <section className="panel auth-card" aria-labelledby="auth-title">
          <div className="auth-tabs" role="tablist" aria-label="Sign in or create an account">
            <button
              type="button"
              role="tab"
              aria-selected={mode === "login"}
              onClick={() => switchMode("login")}
            >
              <LockIcon size={16} /> Sign In
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={mode === "register"}
              onClick={() => switchMode("register")}
            >
              <UserPlusIcon size={16} /> Create Account
            </button>
          </div>

          <h2 className="auth-title" id="auth-title">
            {mode === "login" ? "Sign in to Quant Arena" : "Create your account"}
          </h2>
          <p className="auth-subtitle">
            {mode === "login"
              ? "Access your virtual trading account and stream simulated market data."
              : "Register to receive starting virtual capital and place orders."}
          </p>

          {error && (
            <div className="alert error" role="alert">
              {error}
            </div>
          )}
          {success && (
            <div className="alert success" role="status">
              {success}
            </div>
          )}

          <form onSubmit={handleSubmit} className="auth-form">
            <div className="field">
              <div className="field-head">
                <label htmlFor="username">Username</label>
                {mode === "register" && (
                  <span className="field-meta">
                    {username.length} / {USERNAME_MAX} chars
                  </span>
                )}
              </div>
              <div className="input-wrap">
                <UserIcon size={16} className="input-icon" />
                <input
                  id="username"
                  type="text"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  required
                  minLength={mode === "register" ? 3 : undefined}
                  maxLength={USERNAME_MAX}
                  autoComplete="username"
                  placeholder="e.g. trader_one"
                  disabled={loading}
                />
              </div>
              {mode === "register" && <p className="field-hint">3 to 64 characters.</p>}
            </div>

            <div className="field">
              <div className="field-head">
                <label htmlFor="password">Password</label>
                {mode === "register" && <span className="field-meta">8–256 characters</span>}
              </div>
              <div className="input-wrap">
                <KeyIcon size={16} className="input-icon" />
                <input
                  id="password"
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                  minLength={mode === "register" ? PASSWORD_MIN : undefined}
                  maxLength={256}
                  autoComplete={mode === "login" ? "current-password" : "new-password"}
                  placeholder="Enter password"
                  disabled={loading}
                />
              </div>
              {/* Registration only — signing in checks an existing password, so a length rule has
                  nothing to say there. Length only: it measures the one rule the server enforces
                  and claims nothing about strength. */}
              {mode === "register" && (
                <div className="length-meter" aria-hidden="true">
                  <div className="length-meter-track">
                    <div
                      className={
                        passwordProgress === 1 ? "length-meter-fill met" : "length-meter-fill"
                      }
                      style={{ width: `${passwordProgress * 100}%` }}
                    />
                  </div>
                  <span className="field-meta">Min. {PASSWORD_MIN} chars</span>
                </div>
              )}
            </div>

            <button type="submit" disabled={loading} className="btn btn-primary btn-block btn-auth">
              <LogInIcon size={18} />
              {loading
                ? mode === "login"
                  ? "Signing in…"
                  : "Creating account…"
                : mode === "login"
                  ? "Sign In"
                  : "Create Account & Sign In"}
            </button>
          </form>

          {/* The indicator is on every screen. Here it will read Reconnecting: `/stream` needs
              the session cookie, so market data starts flowing only after sign-in. */}
          <div className="auth-foot">
            <ConnectionIndicator state={connection} />
            <span>Market data streams once you are signed in.</span>
          </div>
        </section>
      </div>
    </div>
  );
}

/**
 * The header's sign-out control.
 *
 * The session is ended on the server before it is forgotten here. If the request fails the user
 * stays signed in and can retry: clearing local state over a cookie that is still valid would show
 * a sign-in form to someone the gateway still recognises.
 */
export function SignOutButton({ onSignedOut }: { onSignedOut: () => void }) {
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);

  async function handleLogoutClick() {
    setLoading(true);
    setFailed(false);
    try {
      await fetch("/auth/logout", { method: "POST" });
    } catch {
      setFailed(true);
      setLoading(false);
      return;
    }
    setLoading(false);
    onSignedOut();
  }

  return (
    <button
      type="button"
      className="btn btn-compact"
      onClick={handleLogoutClick}
      disabled={loading}
      title={failed ? "The gateway could not be reached. You are still signed in." : undefined}
    >
      <LogOutIcon size={14} />
      {loading ? "Signing out…" : failed ? "Retry sign out" : "Sign out"}
    </button>
  );
}
