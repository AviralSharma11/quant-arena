# Handoff to Dev A — round 2, Dev B into week 5

**From:** Dev B · **Date:** 2026-09-06 · **Branch:** `task/5.1-fair-value-replay-clock`
**Supersedes:** the end-of-week-4 handoff of 2026-09-05, every question in which is now closed.

Round 1 asked you five questions and reported one defect. **All five are answered and the defect
is fixed** — the record of that is section 1, kept because the answers are now load-bearing and
the reasoning should not have to be reconstructed.

This round has exactly one ask, and it is section 3: **Amendment 2 to the frozen contract**, and
a six-line change in `engine/cpp/stream_engine.cpp` that only you can make. You have agreed to it
in principle, to be done once your current task is finished. This document is the specification
so that it is a patch rather than a conversation.

---

## 1. Round 1 — closed

| | Question | Answer | Closed by |
|---|---|---|---|
| **Q1** | On `private`, is `seq` a dense per-user counter or the Redis stream id? | **Per-user counter**, as implemented | your reply, 2026-09-05 |
| **Q2** | `resumed` — a fifth WebSocket error code | **Agreed. Now Amendment 1** | your sign-off, 2026-09-05 |
| **Q3** | Does a `bars:*` message carry `seq`? | **Yes**, as implemented | your reply, 2026-09-05 |
| **Q4** | Will the C++ engine emit `BookChanged`? | **No** | your code — see below |
| **Q5** | Is `Fill.price_ticks` always the maker's price? | **Yes** | `e8bc8e9` |

**Amendment 1 is applied.** `contracts/v1/rest_and_ws.md` §3.6 now lists a fifth `code`,
`resumed`, with the reasoning recorded beside it. No `schema_version` bump: the binary records
are untouched and this is the browser wire. The freeze line is intact and the amendment is dated
and attributed beneath it.

**Q4 was answered by reading your code rather than by asking twice.**
`engine/cpp/stream_engine.cpp` enumerates the record types it emits — `OrderAccepted`,
`OrderRejected`, `Fill`, `OrderCancelled`, `AccountCreated`, `CashCredited` — and `BookChanged`
is absent. So fan-out's derived book stands, and integration point 2 was the no-op for fan-out
that it was designed to be. Nothing further needed from you.

**The maker-price defect is fixed, by you, in `e8bc8e9`.** Both books now track arrival order,
so a seller aggressing into a higher resting bid prints the bid. The adapter's override was
removed in the same commit and now passes the model's price straight through; I checked that the
two notions of "maker" cannot disagree, because the taker is always the order just added and so
always holds the highest arrival sequence.

---

## 2. What you completed, as I have recorded it

Reported by you on 2026-09-05 and now on the `STATUS.md` board. Listed so that if any row is
wrong, it is wrong somewhere you can see it.

| Task | Status | What I have as evidence |
|---|---|---|
| 1.2 Naive Python model engine | **done** | maker-price fix `e8bc8e9` |
| 2.3 Hand-written matching scenarios (T1) | **done** | reported |
| 2.4 C++ engine — order book and match loop | **done** | `e4352c1`, arrival-order fix `e8bc8e9` |
| 3.4 C++ engine complete + nanobind | **done** | *(see the note below on nanobind)* |
| 4.1 Differential / property / determinism (T2–T4) | **done** | `2930cb2` — 4 tests, 2 Hypothesis properties at 100 examples each |
| 4.2 Engine process, Redis, replay recovery | **not finished** | `b644130` merged and live in compose |

Three observations, recorded rather than raised as objections — 2.4, 3.4 and 4.2 are yours:

- **No CMake, no Catch2, no nanobind anywhere in the repository.** All three are on `CLAUDE.md`'s
  closed stack list. What exists is a bare `g++` line in the `Dockerfile`, a hand-rolled
  `order_book_test.cpp` with its own `check()`, and a subprocess/stdin-stdout boundary instead of
  a binding. The subprocess boundary may well be the better call — it keeps the engine genuinely
  zero-I/O and money-blind — but it is undocumented, and it lands on me at 7.3 and 7.5.
- **`stream_engine.cpp` hand-codes the wire format.** `contracts/v1/generated/contracts.hpp` is
  generated from `schema.toml` for exactly this purpose, and `stream_engine.cpp` includes only
  `order_book.hpp`, re-typing every offset and record size as a literal (`record_header(kFill,
  79)` and so on). They are correct today and 4.1's differential tests would catch a drift — but
  `generate.py --check` would report everything up to date while the C++ read the wrong bytes.
  **Please drive it from the generated header.** This matters more after section 3 than before it.
- **`e8bc8e9` edited `STATUS.md`**, including replacing a row in the decision log, which is
  append-only. No harm done and the content was right; flagging it so the log's history is not
  quietly wrong. That file is Dev B's — send me the change and I will land it.

---

## 3. THE ASK — Amendment 2, and six lines in your engine

### Why a new record type at all

Task 5.1's second success criterion is that **the replay ratio appears in configuration and in
the stream**. Configuration is trivial. The stream half has nowhere to live: `schema.toml`
defines types 1–4 inbound and 10–16 outbound, and **not one of them can carry a configuration
value**. There is no `ConfigStamped` record.

Worth knowing: the decision log's entry of 2026-08-31 says "config hash goes to the log in 1.4,
and to the stream in 2.1". **That second half never happened** — `grep config_hash
services/gateway/streams.py` returns nothing. So this amendment closes that week-1
loose end as well as unblocking 5.1. Both values ride on the same record.

### What I found while designing it, which you need before you touch recovery

`services/matcher/runner.py` states the invariant that crash recovery rests on:

> **Every inbound record produces exactly one anchor.**

Recovery counts anchors on the outbound stream, gets N, replays exactly N inbound records
silently to rebuild the book, and resumes appending at N+1. That count *is* the bookmark; there
is no checkpoint and no snapshot.

Thirty lines away, `services/matcher/adapter.py` says:

```python
# Anything else is a record type this engine has no opinion about. Ignoring it keeps
# the matcher tolerant of a stream that grows types it does not act on.
return []
```

**Those two comments contradict each other.** A record producing zero anchors breaks the
one-to-one mapping the bookmark depends on. The contradiction is dormant only because no such
record type exists yet — and this amendment creates the first one. Concretely:

```
inbound:   [Submit, Submit, ConfigureReplay, Submit]    ← 4 records
outbound:  [Accepted, Accepted, ·········, Accepted]    ← 3 anchors

recover(): anchors = 3 → replay first 3 inbound → stops at ConfigureReplay
           resumes at record 4 — the Submit that was ALREADY matched
           → a duplicate fill, on a position that really moved
```

Your `stream_engine.cpp` has the identical `default: return {};`, and `cpp_runner.py` imports
`is_anchor` straight from `runner.py`, so **both engines carry it** — one duplicate per gateway
restart, accruing silently rather than crashing.

**This is why the new record must be forwarded, not ignored.** Treating it exactly as
`CreateAccount` and `CreditCash` are treated preserves the invariant instead of weakening it, and
needs no change to recovery in either engine.

### Amendment 2 — the specification

Two record types, following the existing imperative-inbound / past-tense-outbound convention
(`SubmitOrder`→`OrderAccepted`, `CreateAccount`→`AccountCreated`):

```toml
[[records]]
name = "ConfigureReplay"
record_type = 5
direction = "inbound"
doc = """The replay clock and the configuration hash, written by the gateway at startup.
Forwarded by the engine untouched, exactly as CreateAccount is — the engine has no opinion about
it, but the forward is what gives it a position in the total order and what keeps the
one-anchor-per-inbound-record invariant true."""
fields = [
  { name = "client_order_id",                   type = "u64", doc = "Idempotency key." },
  { name = "real_seconds_per_simulated_minute", type = "i64" },
  { name = "config_hash_hi",                    type = "u64", doc = "First 8 bytes of the SHA-256, big-endian." },
  { name = "config_hash_lo",                    type = "u64", doc = "Next 8 bytes. 16 bytes is ample to identify a config." },
]

[[records]]
name = "ReplayConfigured"
record_type = 17
direction = "outbound"
doc = "Forwarded record, now sequenced."
fields = [ ...the same four... ]
```

The hash goes as two integers because **the type vocabulary has no string** — which is the
mechanism that keeps every record fixed-size POD, and is not something to work around.

Both records are **60 bytes**: the 28-byte header (`u16` + `u16` + `u64` + `u64` + `i64`) plus
four 8-byte fields. `schema_version` **1 → 2**.

### Your six lines

```cpp
constexpr std::uint16_t kSchemaVersion   = 2;    // line 28, was 1
constexpr std::uint16_t kConfigureReplay = 5;
constexpr std::uint16_t kReplayConfigured = 17;

// in StreamEngine::apply's switch:
case kConfigureReplay:
    return forward_replay_config(record);        // copy the 4 fields, re-header as 17, size 60
```

**`kSchemaVersion` is the line that matters most, and it is the one that will be forgotten**,
because nothing will complain. I checked: `schema_version` is carried on every record but
**never validated at runtime**. `unpack()` reads the field; no consumer compares it to
`SCHEMA_VERSION`. The "consumers accept the current version only" rule lives in a doc comment
and one schema test, nowhere else. So an engine still stamping `1` after the bump is not
rejected — it is simply wrong, forever, unnoticed. This is the strongest argument for including
the generated header rather than hand-coding the constant.

### What I am doing on my side

Everything except your six lines: the `schema.toml` edit and regeneration, the gateway writing
the record at startup, `adapter.py` forwarding it, `is_anchor` accepting `ReplayConfigured`, and
the tests. Your patch lands against a finished, tested design.

### What it costs both of us

A `schema_version` bump means **truncate and rebuild** — `docker compose down -v`. That is the
standing rule from 2026-08-31 ("a breaking change during development means truncate and
rebuild"), and it is cheap now and expensive later, which is an argument for doing it this week.

**Contracts v1 is frozen, so this needs your signature**, as `resumed` did. Sign it in
`contracts/v1/schema.toml` and I will land the rest.

---

## 4. Both integration points are passed

Integration point 2 (Appendix D.2) landed **early**, in `b644130`. `docker-compose.yml` now runs
`python -m services.matcher.cpp_runner`, the C++ binary is built in its own `engine-builder`
stage, and Redis durability, sequence stamping and recovery all stayed in Python — which is the
division I was hoping for.

It touched fan-out, the ledger and the gateway not at all. All three consume `qa.outbound` and
none of them knows what produced it, which was the point of deriving the book rather than reading
`BookChanged`.

The naive model is **stopped in the running stack and kept in the tests**, which was my
assumption in round 1 and is what your compose change implements. Open Issues 001 and 010 keep it
permanent as an executable specification and as 4.1's differential oracle — that is about the
test suite, not the stack.

---

## 5. Still yours, and still useful

Nothing you were waiting on from me is outstanding. `2.1 streams` → your 4.2, `3.2 idempotency`
→ your 6.4, and `5.2 fan-out` → your 6.3 were all delivered in weeks 2, 3 and 5 respectively.

The notes from round 1 that are still worth having, unchanged:

**For 6.3 — the open-loop load generator.** Fan-out is its own service on **port 8001**,
`ws://…/stream`, authenticated by the session cookie. `GET :8001/health` gives you
`stream_position`, `records_applied`, `ticks`, `subscribers`, `serialisations` and
`max_tick_seconds`; the last two are what proved 5.2's Success Criterion 5 and are yours for
stream lag. `benchmarks/bench_fanout.py` already opens 200 authenticated WebSockets — it is
**closed-loop** and so not what 6.3 is for, but the connection setup, cookie handling and
fresh-account trick are worth stealing rather than rewriting. Two things that cost me an
afternoon: `POST /auth/register` does **not** set a session cookie, so log in afterwards; and a
resting buy reserves cash at its limit price for as long as it rests, so a reused account runs
out after a few thousand orders and every later order is a `409` that changes nothing. Use a
fresh account per run. Rate limiting is **1000 orders/sec per account**, one tier, and runs
*ahead of* the idempotency claim deliberately.

**For 6.4 — duplicate injection.** `client_order_id` is a client-assigned `uint64`, mandatory.
`IdempotencyStore.claim()` reports **which caller won the key**, which is the whole of Success
Criterion 3.2.2. One trap from my own bots: a counter restarting at 1 re-sends keys the store has
already answered — every order acknowledged, none appended, the market silent while every
participant reports success. Base your ids on a millisecond timestamp.

---

## 6. Running what you integrate against

```bash
docker compose up --build          # redis, postgres, gateway, matcher (C++), ledger, fanout
curl localhost:8000/health         # gateway
curl localhost:8001/health         # fan-out: stream position, ticks, serialisations
docker compose logs gateway | grep config_hash

QA_BOT_PASSWORD=... docker compose --profile bots up -d   # a live market to load-test against

cd web && npm install && npm run dev    # localhost:5173, proxied to both services
```

- The **bots profile** is off by default. A stack that always has a live market is what you want
  for a demo and the last thing you want under a test suite.
- If `--profile bots up` fails with "`dmm_qaa` exists with different credentials", the accounts
  are left from a run under another `QA_BOT_PASSWORD`. `docker compose down -v`, or reuse that
  password. A data condition, not a code one.
- **After Amendment 2 lands, `docker compose down -v` is mandatory, not optional.**

---

## 7. Where the reasoning lives

| Question | File |
|---|---|
| Why the book is derived, and the four decisions behind fan-out | `services/fanout/README.md` |
| Why 200 clients cost the order path 150 µs | `benchmarks/results/5.2-fanout-conflation.md` |
| Why `appendfsync always` is affordable | `benchmarks/results/2.1-stream-durability.md` |
| Every decision made during the build, one line each | `STATUS.md`, decision log |
| What deviated from the plan and why | `STATUS.md`, deviations |
| The four WebSocket codes, and now the fifth | `contracts/v1/rest_and_ws.md` §3.6 |

**One thing needs you: section 3.** Everything else here is a record, not a request.
