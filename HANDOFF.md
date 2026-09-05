# Handoff to Dev A — end of week 4, Dev B running into week 5

**From:** Dev B · **Date:** 2026-09-05 · **Branch:** `task/4.0-week4-prework` (not yet pushed;
the whole of week 4 plus 5.2b goes up as one pull request)

This is the one document to read before your week 5. It covers, in order: **five questions I
need answered**, **one defect in your code**, **what is now unblocked for you**, and **how to
run the things you will be integrating against**.

Nothing here is a request to change a settled decision. Four of the five questions are places
where the frozen contract is genuinely ambiguous or silent, and I have had to choose a reading
to make a success criterion achievable. Each one is isolated to a single function on both sides,
so changing my mind is cheap — but it stops being cheap once your engine ships against it.

---

## 1. Five open questions

### Q1 — On `private`, is `seq` a dense per-user counter or the Redis stream id?

**Status: I have implemented the counter. Confirm or correct.**

`contracts/v1/rest_and_ws.md` §3.4 shows

```jsonc
{ "ch": "private", "type": "Fill", "seq": "1693526400000-9", ... }
```

which is a stream id. But §3.5 requires the client to detect a gap from `seq`, and Open Issue 006
§7c says private messages carry a **per-user sequence number** *precisely so a gap is
detectable*.

Those cannot both be literal. A stream id counts every record on the stream, and the overwhelming
majority of them concern other users — so a stream id is dense for nobody. Read that way, no
private gap is detectable by anyone, and Task 5.2's fourth success criterion cannot be met by any
implementation at all.

So fan-out stamps a dense integer, one per message delivered to that user. It is what
`web/src/stream/gaps.ts` already assumed when I wrote 5.4c.

- Server side: `PrivateRouter.next_seq` in `services/fanout/private.py`
- Client side: `privateSequence()` in `web/src/stream/gaps.ts`
- A test in `tests/fanout/test_private.py` feeds real server frames through the *shipped
  TypeScript tracker*, so if we ever disagree about this, a test fails rather than a user
  silently missing a fill.

**What I need:** a yes, or the other reading and how you would make a gap detectable under it.

### Q2 — `resumed`: a fifth error code, and a deviation from the frozen contract

**Status: I have added it. This one genuinely needs your sign-off.**

§3.6 enumerates four codes — `unauthenticated`, `unknown_channel`, `slow_consumer`, `halted` —
and every one of them is a failure. There is no way to say that a halt has **ended**.

Something has to say it. A halt clears on its own within one watchdog interval (Task 2.1 Success
Criterion 5), so an indicator that could only be reset by reconnecting would show HALTED over a
perfectly working exchange for as long as the tab stayed open. The two alternatives are worse:

- Inferring resumption from the arrival of market data is simply **wrong**. Market data keeps
  flowing throughout a halt — fan-out is still reading a stream the matcher is still draining,
  and it is only the *appending of new orders* that stopped.
- Inferring it from the *absence* of repeated `halted` frames turns the indicator into a timeout.

So: `{"ch": "error", "code": "resumed", "detail": "exchange accepting orders"}`. Additive, so a
client that does not know the code ignores an error it cannot classify — which is what §3.6's
shape already asks of it.

If you agree, this is a `contracts/v1/rest_and_ws.md` §3.6 amendment we should both sign, with no
`schema_version` bump (the binary schema is untouched — this is the browser wire).

### Q3 — Does a `bars:*` message carry `seq`?

§3.5 states the rule that **every** message carries `seq` and the client tracks the last one per
channel. The bar example in §3.3 shows neither `seq` nor `ts_ns`.

I have read that as an abbreviated example rather than a contradiction, and `bar_close()` in
`services/fanout/messages.py` includes `seq`. A channel that omitted it would be the one channel
a client could not gap-check.

Low stakes, but it is a shape we will both code against, so I would rather not assume.

### Q4 — Will the C++ engine emit `BookChanged`?

**This one affects your 4.2, so it matters most.**

`schema.toml` designed `BookChanged` for exactly this purpose — one aggregated price level per
record, fan-out reassembling the book. **Nothing emits it.** Outside the generated contracts and
two schema tests, the record type appears nowhere in the repository.

Fan-out therefore derives the book from `OrderAccepted` + `Fill` + `OrderCancelled`, which
between them carry everything needed. I chose that over making the matcher emit `BookChanged`
for one reason: your C++ engine would have to emit it too at the week-5 integration point, and
that is a same-week cross-developer dependency, which Appendix D.4 forbids.

It also turns out to be the better design independently: **the derivation is engine-agnostic**,
so swapping the naive model for your engine does not touch fan-out at all.

**What I need:** if you are *not* emitting `BookChanged`, nothing changes and integration point 2
is a no-op for fan-out — which is what I am planning for. If you *are*, tell me before you build
it, because fan-out would then be maintaining a book from two sources and I would rather delete
mine than have two that can disagree.

### Q5 — Is `Fill.price_ticks` always the maker's price in your engine too?

`schema.toml` says the field is "always the resting (maker) price — price-time priority", and my
matcher adapter enforces it (see the defect below). Confirming that your C++ engine does the same
is a one-line answer that would save a differential test failing for a reason neither of us
expected.

---

## 2. Maker-price defect — fixed

The matching engines now print the resting (maker) price, including when a seller aggresses into a
higher resting bid. Both the Python reference model and C++ order book track arrival order and have
regression coverage for this case.

The matcher adapter now passes through the corrected model price, so the Python reference, active
matcher, and C++ implementation share the same execution-price semantics.

---

## 3. What is now unblocked for you

All three of the things you were waiting on from me are delivered.

| You were waiting on | Status | Where |
|---|---|---|
| **2.1 streams** → your 4.2 | done, week 2 | `services/gateway/streams.py`, benchmarked at 72k orders/sec |
| **3.2 idempotency** → your 6.4 | done, week 3 | `services/gateway/idempotency.py`, atomic Lua claim |
| **5.2 fan-out** → your 6.3 | **done, today** | `services/fanout/`, 200 clients measured |

### For 4.2 — the standalone engine process

- The inbound stream is `qa.inbound`, outbound `qa.outbound`, both named in
  `config/quant_arena.toml` under `[streams]`. Never hard-code them.
- **The Redis stream id IS the sequence number.** Records are written with `SEQ_UNASSIGNED` and
  stamped on read by `with_seq()`. Do not add a parallel counter — Open Issue 003.
- Batching: I measured `XREAD COUNT 100` at **8.32 µs per record** against **159.11 µs at a
  count of one**. That is your "batching is confirmed" criterion, already characterised —
  `benchmarks/results/2.1-stream-durability.md`.
- Recovery from `0-0` with no snapshots is what every consumer here does. One warning from
  building three of them: a consumer that *appends* to a stream cannot naively replay, because
  it would duplicate records that moved real positions. My matcher counts **anchors** — exactly
  one outbound record per inbound record — to find its place. Yours will need the same trick or
  its own; `services/matcher/runner.py` is the worked example.
- `Book.fill` in `services/fanout/book.py` is deliberately silent on an unknown order id. The
  stream trims at two million entries, so a replay legitimately begins mid-history and a fill
  can name an order accepted before the window. Raising there makes a trimmed stream unreadable.

### For 6.3 — the open-loop load generator

- Fan-out is its own service on **port 8001**, `ws://…/stream`, authenticated by the session
  cookie. `GET :8001/health` gives you `stream_position`, `records_applied`, `ticks`,
  `subscribers`, `serialisations` and `max_tick_seconds` — the last two are what I used to prove
  Success Criterion 5, and they are yours to use for stream lag in your per-second summary.
- `benchmarks/bench_fanout.py` already opens 200 authenticated WebSockets and samples gateway
  acknowledgement latency. It is **closed-loop** and therefore not what 6.3 is for — but the
  connection setup, the cookie handling and the fresh-account trick are all there and worth
  stealing rather than rewriting.
- **Two things it cost me an afternoon to learn**, both of which will bite a load harness:
  `POST /auth/register` does **not** set a session cookie, so you must log in afterwards; and a
  resting buy reserves cash at its limit price for as long as it rests (Task 3.1), so a reused
  account runs out after a few thousand orders and every later order is a `409` rejection that
  changes nothing. Use a fresh account per run.
- Rate limiting is **1000 orders/sec per account**, one tier, `limits.max_orders_per_second`.
  It runs *ahead of* the idempotency claim, deliberately — a 429 after the claim would answer
  that `client_order_id` "rejected" for the whole TTL.

### For 6.4 — duplicate injection

- `client_order_id` is a client-assigned `uint64`, mandatory. `IdempotencyStore.claim()` reports
  **which caller won the key**, which is the distinction Success Criterion 3.2.2 is about.
- One trap from my own bots: a counter that restarts at 1 re-sends keys the store has already
  answered. Every order gets acknowledged, none is appended, and the market goes quiet while
  every participant reports success. Base your ids on a millisecond timestamp.

---

## 4. Integration point 2 — end of week 5

Appendix D.2: **the C++ engine process replaces the naive model as the stream's consumer.**

What that touches on my side, as far as I can tell: nothing. The matcher is a compose service
with its own command; swapping it is a compose change plus stopping `services/matcher`. Fan-out,
the ledger and the gateway all consume `qa.outbound` and none of them knows or cares what
produced it — which was the point of deriving the book rather than reading `BookChanged`.

What I would like to agree **before** the session rather than during it:

1. Whether the naive model stays running alongside as a differential oracle, or is stopped.
   Open Issue 001 and 010 both say the model is *permanent* as an executable specification, but
   that is about the test suite, not about the running stack. My assumption: stopped in the
   stack, kept in the tests.
2. Q4 above — `BookChanged` or not.
3. Q5 above — maker price.

---

## 5. Running what you will integrate against

```bash
docker compose up --build          # redis, postgres, gateway, matcher, ledger, fanout
curl localhost:8000/health         # gateway
curl localhost:8001/health         # fan-out: stream position, ticks, serialisations
docker compose logs gateway | grep config_hash

QA_BOT_PASSWORD=... docker compose --profile bots up -d   # a live market to load-test against

cd web && npm install && npm run dev    # localhost:5173, proxied to both services
```

Two notes on the stack as it stands:

- The **bots profile** is off by default. A stack that always has a live market is what you want
  for a demo and the last thing you want under a test suite.
- If `docker compose --profile bots up` fails with "`dmm_qaa` exists with different credentials",
  the accounts are left over from a run under another `QA_BOT_PASSWORD`. `docker compose down -v`
  or reuse that password. This is currently true on my machine and is why nine bot tests error
  there; it is a data condition, not a code one.

---

## 6. Where the reasoning lives

I have kept the *why* next to the code rather than in a document that goes stale:

| Question | File |
|---|---|
| Why the book is derived, and the four decisions behind fan-out | `services/fanout/README.md` |
| Why 200 clients cost the order path 150 µs | `benchmarks/results/5.2-fanout-conflation.md` |
| Why `appendfsync always` is affordable | `benchmarks/results/2.1-stream-durability.md` |
| Every decision made during the build, one line each | `STATUS.md`, decision log |
| What deviated from the plan and why | `STATUS.md`, deviations |

**Reply on Q1, Q2 and Q4 first** — those three are the ones that will cost either of us rework if
they turn out differently, and Q2 is the only one that changes a frozen document.
