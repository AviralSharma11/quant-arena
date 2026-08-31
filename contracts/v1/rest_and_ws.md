# Contracts v1 — REST surface and WebSocket messages

**Status: PROPOSED by Dev B — awaiting Dev A sign-off.** Task 1.1 is joint (`WEEKLY_PLAN.md`
Appendix D.2). The binary record layout is in `schema.toml`; this file is the other half of the
same contract — the shapes that cross the network to a browser.

Sources: Open Issue 016 §5 (REST surface, confirmed complete), Open Issue 008 §9h (response
codes), Open Issue 006 (fan-out, conflation, tiering), Open Issue 014 (frontend, gap detection),
Open Issue 015 (sessions).

---

## 1. Conventions above the wire

The binary records forbid floats and strings because they are replayed. JSON on the browser wire
is the **presentation layer**, so strings are allowed here — symbol *names* appear, and enums are
sent as their integer values with the names available from `GET /symbols`.

**Money and quantities stay integer ticks even in JSON.** The frontend divides by the symbol's
`tick_size` for display and never sends a divided value back. A float that reaches the gateway is
a bug, not a rounding concern.

`seq` on the wire is the Redis stream id rendered as its usual `"<ms>-<ord>"` string, so a client
can compare and report a gap without needing 128-bit arithmetic.

---

## 2. REST surface

Deliberately small. Streaming carries everything continuous; REST carries actions and
re-synchronisation. **Open Issue 016 §10 records this surface as complete — an addition during
the build is a scope change to be raised, not absorbed.**

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/auth/register` | Create a user; grants virtual capital |
| `POST` | `/auth/login` | Start a session |
| `POST` | `/auth/logout` | End a session |
| `POST` | `/orders` | Submit — returns an **acknowledgement, not a result** |
| `DELETE` | `/orders/{client_order_id}` | Cancel by client order id |
| `GET` | `/orders/open` | Re-synchronisation after a stream gap |
| `GET` | `/portfolio` | Re-synchronisation after a stream gap |
| `GET` | `/symbols` | Static configuration |
| `POST` | `/backtests` | Run |
| `GET` | `/backtests/{id}` | Retrieve |
| `WS` | `/stream` | Market data and the private user stream |

`GET /orders/open` and `GET /portfolio` exist **solely** for re-synchronisation after a detected
sequence gap (Open Issue 014 sub-decision 14e). The UI must not poll them.

### 2.1 Sessions

Redis-backed session cookies — `httpOnly`, `Secure`, `SameSite`. **No JWT.** Passwords are
Argon2id. The cookie carries an opaque session id; all state lives in Redis, so a gateway
restart does not log anyone out.

### 2.2 `POST /orders`

```jsonc
// request
{ "client_order_id": 1042, "symbol": "BTC", "side": 1, "price_ticks": 6412500, "qty": 3, "tif": 1 }
```

`client_order_id` is a **`uint64`, mandatory**, unique per user. Absent or malformed → `400`
before any other processing (Open Issue 008 §9d/9g).

```jsonc
// 202
{ "client_order_id": 1042, "order_id": 88117, "seq": "1693526400000-4", "status": "accepted" }
```

Order submission returns an acknowledgement. Fills, partial fills and the final state arrive on
the **private stream**, not in this response (Open Issue 008 §9h). The UI is built around the
stream from the beginning; that is a cheap decision now and an expensive retrofit later.

| Code | `status` | Meaning |
|---|---|---|
| `202` | `accepted` | Sequenced and durable. The outcome will arrive on the private stream |
| `202` | `in_progress` | Duplicate of a request still being processed. Await the private stream |
| `200` | `replay` | Duplicate of a completed request; the original outcome is returned verbatim |
| `400` | — | Malformed, or missing `client_order_id` |
| `409` | `rejected` | Failed validation or a risk check. Never reached the engine; carries a `reason` from `RejectReason` |
| `503` | `halted` | The exchange cannot durably record orders |

**The same `client_order_id` always yields the same answer** (Open Issue 008 §13). `200 replay`
returns the stored original outcome, including its rejection reason if it was rejected.

`DELETE /orders/{client_order_id}` returns the same shape; `404` is not used — an unknown order
is `409` with `reason = UNKNOWN_ORDER`, so that a cancel is idempotent in the same way a submit is.

### 2.3 `GET /symbols`

Static per deployment. The only place symbol *names* and `tick_size` are defined, and therefore
the only thing that maps `symbol_id` on the wire to something a human reads.

```jsonc
{ "symbols": [ { "symbol_id": 1, "name": "BTC", "tick_size_ticks": 1, "lot_size": 1 } ],
  "enums": { "side": {"BUY": 1, "SELL": 2}, "tif": {"GTC": 1, "IOC": 2},
             "reject_reason": { "UNKNOWN_SYMBOL": 1 } },
  "schema_version": 1 }
```

Serving the enum tables here means the frontend never hard-codes an integer that the schema owns.

---

## 3. WebSocket — `/stream`

One connection. Authenticated by the session cookie; the private stream is whatever that session
owns, never a channel the client asks for by user id.

### 3.1 Subscription

```jsonc
{ "op": "subscribe", "channels": ["book:BTC:l2", "tape:BTC", "bars:BTC:1m"] }
```

Tiering is deliberate (Open Issue 006 §7): most clients want L1 only, which is a fraction of the
bytes of L2. Only a client with the order-book panel open subscribes to `l2`.

| Channel | Contents | Conflation |
|---|---|---|
| `book:{symbol}:l1` | Best bid and ask | 20 Hz |
| `book:{symbol}:l2` | **Complete** snapshot, 10 levels per side | 20 Hz |
| `tape:{symbol}` | Trades | **Never conflated** — every trade is delivered |
| `bars:{symbol}:1m` | OHLCV bars | On close |
| *(implicit)* `private` | This session's own order and fill events | **Never dropped** |

### 3.2 Book — complete snapshots, no delta encoding

```jsonc
{ "ch": "book:BTC:l2", "seq": "1693526400000-4", "ts_ns": 1693526400000000000,
  "bids": [[6412500, 3], [6412400, 11]], "asks": [[6412600, 5]] }
```

Each entry is `[price_ticks, qty]`. **Every message is a complete snapshot** (Open Issue 006) —
delta encoding is Phase 2. A client that misses a message is immediately correct again on the
next one, which is what makes 20 Hz conflation safe to do at all.

### 3.3 Tape and bars

```jsonc
{ "ch": "tape:BTC", "seq": "…", "ts_ns": …, "price_ticks": 6412500, "qty": 2, "aggressor_side": 1 }
{ "ch": "bars:BTC:1m", "open_ticks": …, "high_ticks": …, "low_ticks": …, "close_ticks": …,
  "volume": …, "bar_open_ns": … }
```

The tape is un-conflated because a dropped trade is a wrong tape, not a stale one.

### 3.4 Private stream

Mirrors the outbound records the session's user is party to — `OrderAccepted`, `OrderRejected`,
`Fill`, `OrderCancelled`, `AccountCreated`, `CashCredited` — as JSON with the same field names as
`schema.toml`, plus `type` naming the record.

```jsonc
{ "ch": "private", "type": "Fill", "seq": "1693526400000-9", "ts_ns": …,
  "order_id": 88117, "client_order_id": 1042, "symbol": "BTC",
  "price_ticks": 6412500, "qty": 2, "aggressor_side": 1, "role": "taker" }
```

`role` is derived by fan-out from `aggressor_side` and which side this user was on, so the client
does not have to work out whether it paid the maker or the taker fee.

**Private data is never dropped and never conflated** (Open Issue 006). If it cannot be
delivered, the connection is closed and the client re-synchronises.

### 3.5 Gap detection and re-synchronisation

Every message carries `seq`. The client tracks the last `seq` per channel; a jump means messages
were lost.

Because the book is a complete snapshot, a gap on `book:*` is self-healing — ignore it. A gap on
`private` is not: the client calls `GET /orders/open` and `GET /portfolio` once, then resumes. A
gap on `tape:*` leaves a hole in history that is not backfilled; the tape is a display, not a
record of truth (the archive is).

### 3.6 Errors

```jsonc
{ "ch": "error", "code": "unauthenticated" | "unknown_channel" | "slow_consumer" | "halted",
  "detail": "…" }
```

`slow_consumer` precedes a server-initiated close, so a stalled browser tab cannot apply
back-pressure to the fan-out process.

---

## 4. Open against Dev A

1. Does the private-stream JSON need `maker_order_id` / `taker_order_id` split out, or is the
   `order_id` + `role` projection above sufficient for the trading screen?
2. `GET /symbols` serving the enum tables — confirm the load generator (6.3) reads them from
   there too, rather than importing the Python module, so that both paths are exercised.
