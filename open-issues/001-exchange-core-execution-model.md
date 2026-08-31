# Open Issue 001 — Exchange Core: Execution & Consistency Model

**Status:** closed by user
**Opened:** 2026-08-27
**Contested:** the durability mechanism (sub-decision 2c) and, by extension, whether the
order book lives in a custom in-memory engine at all. See Open Issue 003.
**Owner:** _unassigned_

---

## 1. The problem

Multiple participants — robot traders and humans — submit orders and cancels for the
same symbol concurrently. Three properties must hold:

1. **A single definite sequence exists.** Two requests must be applied in *some* agreed
   order, and every part of the system must agree what that order was. Without this,
   price-time priority is unenforceable and orders can be silently skipped.
2. **The order book stays correct.** No crossed book; no quantity created or destroyed;
   no order filled twice; a cancelled order never trades.
3. **The same input sequence reproduces the same output.** This single property is
   load-bearing for Goal 1 (correct exchange core), Goal 3 (reproducible backtests),
   and Problem 9 (recovery by replay).

The mechanism that provides these guarantees determines nearly every downstream design
choice, so this is the first architectural decision.

---

## 2. Approaches considered

### Approach A — Database as the arbiter

Resting orders are SQL rows. Each incoming order opens a transaction, locks the symbol,
queries for matching orders, writes fills, and commits.

**Advantages**
- Familiar to most developers; low conceptual overhead.
- Durability is automatic; a crash mid-match rolls back cleanly.
- No custom recovery code required.

**Disadvantages**
- Every match is several round-trips under a lock. Realistic ceiling is a few hundred
  orders/sec, degrading further under contention — three orders of magnitude below the
  agreed target of 5–20k orders/sec end-to-end.
- **Not deterministic.** Lock acquisition order and commit interleaving vary between
  runs, so replay is impossible and reproducibility is lost.
- The order book becomes an emergent property of SQL queries rather than an explicit,
  unit-testable data structure — removing the project's central engineering artifact.
- Makes a C++ engine pointless, discarding the strongest placement-relevant artifact.

**Verdict:** rejected. Fails the agreed scale targets and eliminates determinism.

---

### Approach B — Single-writer in-memory engine over a sequenced input log

One component owns the order book in memory and processes requests one at a time in a
fixed order. Every request is assigned a monotonic sequence number on arrival and
appended to a durable log. The engine consumes that log and emits output events
(`Accepted`, `Rejected`, `Filled`, `Cancelled`, `BookChanged`). All other state —
balances, portfolios, market data, charts, backtest history — is derived by consuming
that output event stream.

```
  HTTP / WS  ──►  Gateway  ──►  [seq 1,2,3,4…]  ──►  Engine  ──►  output events
 (concurrent)   (validate,       ordered,          (single             │
                 risk, dedupe)   durable log)       writer)            ├─► balances / portfolio
                                                                      ├─► market data → clients
                                                                      └─► history → backtester
```

This is the LMAX Disruptor pattern and is how production exchanges are built.

**Advantages**
- No locks in the hot path — there is no shared mutable state to contend over.
- **Deterministic by construction.** Replaying the log rebuilds an identical book.
  One property satisfies Goal 1, Goal 3's reproducibility, and Problem 9 simultaneously.
- **Trivially testable.** The engine is a function from a list of requests to a list of
  events: no database, no network, no mocks. This is what makes property-based and
  differential testing feasible.
- **Solves Problem 6 for free.** Analytics, logging, and notifications subscribe to the
  output stream and are structurally incapable of slowing the trading path.
- Fast enough that the engine ceases to be the bottleneck, pushing optimisation work
  onto market-data fan-out — a more transferable performance-engineering story.

**Disadvantages**
- The recovery path (snapshot + log replay) must be written by hand and is easy to get
  subtly wrong.
- Durability becomes an explicit design decision rather than a database guarantee.
  See sub-decision 2c below.
- Requires discipline inside the engine: no wall-clock reads, no randomness, no I/O, no
  iteration over unordered containers. Violating any of these silently destroys
  determinism, and the failure is not noticed until a replay disagrees.

---

### Approach C — Actor per symbol

Each symbol is an independent concurrent actor with its own mailbox; a framework supplies
supervision and message ordering.

**Advantages**
- Clean scale-out path to hundreds or thousands of symbols.
- Framework handles mailbox and supervision plumbing.

**Disadvantages**
- Functionally identical to Approach B at the agreed scale of 8–12 symbols, but adds a
  framework's worth of concepts (supervision trees, mailbox semantics, backpressure).
- Least idiomatic in both Python and C++; most natural in Erlang/Elixir or Akka.
- Solves a scaling problem the project has explicitly deprioritised.

**Verdict:** deferred. Reconsider only in Phase 3 if the symbol count grows past ~100.

---

## 3. Recommendation

**Adopt Approach B.** It is the only option of the three that provides determinism, and
determinism is a prerequisite for three of the project's six stated goals.

---

## 4. Sub-decisions inside Approach B

### 2a — Single writer granularity: one thread total, or one per symbol?

At a target of 20k orders/sec against an engine capable of ~500k orders/sec, a single
thread services all 12 symbols at roughly 4% utilisation. Per-symbol threads buy no
throughput at this scale and cost a clean global sequence number, which complicates the
event log, replay, and debugging.

**Recommended:** one engine thread total, with book state partitioned by symbol
*internally* so that sharding across threads later is a contained change with a
documented before/after benchmark attached.

---

### 2b — Where is the sequence number assigned?

A single point must decide "this request is #1041, this one is #1042."

| Option | Notes |
|---|---|
| (i) The gateway process itself | Zero new infrastructure; requires exactly one gateway |
| (ii) A dedicated sequencer component | Supports multiple gateways; more moving parts |
| (iii) A message broker (Kafka, Redis Streams) | Offsets provide ordering; significant operational weight |

**Recommended:** option (i) for Phase 1. A single gateway process running an async event
loop — the loop's own ordering *is* the sequence. When WebSocket fan-out saturates that
process (expected in Phase 2), promote to (ii) or (iii); that migration is itself a
strong piece of engineering writing.

**Counter-argument to anticipate:** "this does not scale." True, and deliberately so.
Introducing a broker for a single producer at 20k msg/sec before any measurement has been
taken is precisely the resume-driven architecture that `README.md` §5 rejects.

---

### 2c — Durability ordering: log before applying, or after? **(the sharp edge)**

If a fill is applied in memory and acknowledged to the user, and the process then crashes
before the log write lands, the trade is lost but the user saw it — money invented from
nothing.

Log-before-apply is the correct ordering, but an `fsync` per order costs roughly 1 ms on
SSD, capping throughput near 1000 orders/sec and breaking the p99 latency target.

**Standard solution — group commit:** accumulate requests for a short window (~200 µs, or
N requests), `fsync` the batch once, then apply and acknowledge the whole batch. This is
what PostgreSQL, Kafka, and production exchanges do. It costs a small fixed latency and
buys real durability.

**Recommended:** log-before-apply with group commit; acknowledge only after `fsync`
returns. This is a small amount of logic and it is the difference between a trading system
and a trading demo. It also provides a genuinely interesting benchmark axis — batch size
versus latency versus throughput — which serves Goal 5 directly.

**Alternative if simplicity is preferred:** acknowledge first, write the log
asynchronously. This is defensible for a play-money system, but it must be a documented,
deliberate choice with the failure mode written down — not an accident.

---

## 5. What is deliberately NOT decided here

- The language the engine is written in (Python vs C++) — Open Issue 002.
- Whether the engine runs inside the gateway process or its own — Open Issue 002.
- Where the authoritative record of cash and positions lives — Open Issue 003.
- The log's physical format and storage medium — Open Issue on persistence.

Approach B holds regardless of any of these.

---

## 6. Questions to resolve before marking DECIDED

1. Any objection to Approach B, or to rejecting A and C outright?
2. **Sub-decision 2c is the one most worth pushing on.** Accept ~200 µs of batching
   latency in exchange for real durability, or argue that play money does not warrant
   `fsync` at all? Both are defensible; the decision must be recorded either way.
3. Does 2b feel too simple? If the instinct is "surely this needs Kafka," it is better
   argued now than re-litigated in week 3.

---

## 7. Decision log

| Date | Status | Note |
|---|---|---|
| 2026-08-27 | PROPOSED | Approach B with sub-decisions 2a (single thread), 2b (gateway sequences), 2c (group commit) |

---

## 8. Provisional resolution — REOPENED, NOT FINAL

**Approach B is adopted.** Approaches A and C are rejected for the reasons recorded above.
A and C may be revisited only if the symbol count grows beyond ~100 (Phase 3).

Sub-decisions, all confirmed:

| ID | Decision |
|---|---|
| 2a | One engine thread total. Book state partitioned by symbol internally so that sharding across threads later is a contained change with a published before/after benchmark. |
| 2b | The single gateway process assigns sequence numbers. No message broker in Phase 1. Promote to a dedicated sequencer or broker only when WebSocket fan-out measurably saturates the gateway process. |
| 2c | **Log before apply, with group commit.** Accumulate requests for ~200 µs or N records, flush once, publish the write cursor, and only then acknowledge the client. Acknowledged means durable; unacknowledged means it never happened. There is no window in which a user observes a fill that is not on disk. |

The concrete mechanism implementing 2c — a memory-mapped append-only log — is specified in
Open Issue 002. That mechanism also supplies inter-process transport and replay-based
recovery, so durability, IPC, and recovery are one component rather than three.

Questions in §6 are now closed. The "do we need Kafka" question was raised and answered:
no, because there is a single producer on a single machine. Open Issue 002 §7 records the
reasoning for future reference.

---

## 9. Decision log

| Date | Status | Note |
|---|---|---|
| 2026-08-27 | PROPOSED | Approach B with sub-decisions 2a, 2b, 2c |
| 2026-08-27 | DECIDED | Approach B adopted; A rejected on determinism and throughput, C deferred to Phase 3. Mechanism detail delegated to Open Issue 002. |

---

## 10. Reopened 2026-08-27

This issue was marked DECIDED prematurely and has been reverted to PROPOSED. Alternatives
raised for evaluation before anything here is treated as final:

- **Redis** as the hot state store and/or the ordered durable record, with asynchronous
  write-behind to a relational database.
- **SQLAlchemy / SQLModel** as the data-access layer, in place of hand-written SQL.

These do not all compete with the same decision. They are separated by layer and evaluated
in **Open Issue 003 — Persistence and State Store**, which now blocks this issue.

**What remains provisionally agreed and is not contested by those alternatives:**

- 2a — a single writer per symbol, whatever component that writer turns out to be.
- 2b — a single point of sequence assignment, currently the gateway process.
- The requirement that the same input sequence reproduces the same output.

**What is genuinely reopened:**

- Whether the order book is owned by a custom in-memory engine, by Redis, or by a
  relational database (Approach A vs B vs a new Redis-based option).
- Sub-decision 2c — the durability mechanism and its flush window.
- Whether the durable ordered record is a memory-mapped file, a Redis Stream, or a
  database table.

---

## 11. Sub-decision 2c resolved 2026-08-27

Open Issue 003 selected Redis Streams as the durable ordered record. Sub-decision 2c —
log-before-apply with group commit — is therefore implemented by **Redis persistence
configuration** rather than by hand-written batching.

**Proposed configuration:** `appendfsync always` with pipelined `XADD` calls, which is the
same idea as group commit implemented by Redis: one flush per event-loop iteration, amortised
across a batch. Measure it; if throughput proves unacceptable, fall back to `appendfsync
everysec` and document the resulting one-second window explicitly rather than leaving it
implicit. See Open Issue 003 §8.2.

Sub-decisions 2a (one engine thread) and 2b (gateway assigns sequence numbers) are unaffected.
Approach B itself is unaffected — Redis Streams is an implementation of the sequenced durable
input log that Approach B requires, not an alternative to it.

> **Conditional 2026-08-27:** the amendment above follows from the S2 (Redis Streams)
> selection in Open Issue 003, which has since been **placed on hold** pending the validation
> plan in Open Issue 009. Treat this amendment as provisional. If S2 is not confirmed, the
> superseded material above it becomes current again.

---

## 12. Sub-decision 2c re-resolved 2026-08-28

Open Issue 003 selected S1, so §11 is **withdrawn**. Sub-decision 2c reverts to the original
proposal: **hand-written group commit** over the memory-mapped log — accumulate for roughly
200 µs or N records, `msync` once, publish the write cursor, and only then acknowledge.

The durability window returns to approximately 200 µs, against up to one second under Redis
`appendfsync everysec`. This was one of the arguments for S1 and is now realised.

Batch size versus latency versus throughput becomes a benchmark axis, as originally noted, and
is listed in the build order at Open Issue 009 §10.5.

---

## 13. Sub-decision 2c — final, 2026-08-28

Open Issue 003 §12 locked **S2 (Redis Streams)**. Section 12 above is **withdrawn**.

**Final:** sub-decision 2c is implemented by Redis persistence configuration — `appendfsync
always` with pipelined `XADD`, which is group commit implemented by Redis rather than by hand.
If measurement shows the throughput is unacceptable, fall back to `appendfsync everysec` and
**document the one-second window explicitly** rather than leaving it implicit. Measurement is a
2-hour task folded into Redis integration (Open Issue 009 §11.1).

Approach B, sub-decision 2a (one engine thread) and sub-decision 2b (gateway assigns sequence
numbers) were unaffected by every reversal. Redis Streams is an implementation of the sequenced
durable input log that Approach B requires — not an alternative to it.

---

## 14. Sub-decision 2b restated 2026-08-28

Sub-decision 2b was written before the transport was settled and describes the gateway as
*assigning sequence numbers*. Under S2 (Open Issue 003 §12), `XADD` already returns a monotonic
stream ID, so maintaining a separate gateway-assigned sequence would create two ordering
concepts to keep consistent — with no benefit and a real chance of divergence.

**Restated:** the Redis stream ID **is** the sequence number. The gateway's role is more
accurately **single producer** than *sequencer*.

The substance of 2b is unchanged: exactly one producer writing to one stream yields exactly one
ordering, which is what Approach B requires. Every downstream property — determinism, replay,
price-time priority, and the single-writer reservation state in Open Issue 004 — rests on that
and is unaffected.

The promotion path in 2b also still holds: when multiple gateways become necessary, a dedicated
sequencer or a broker-assigned ordering replaces the single-producer guarantee.
