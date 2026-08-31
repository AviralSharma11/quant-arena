# Open Issue 003 — Persistence and State Store

**Status:** **DECIDED — LOCKED (2026-08-28)**
**CURRENT STATE:** Layer A = custom single-writer C++ engine. **Layer B = S2, Redis Streams.**
Layer C = deferred. Sections 8 and 11 below record the decision history; §12 is authoritative.
**Opened:** 2026-08-27
**Blocks:** Open Issue 001 (sub-decision 2c), Open Issue 002 (transport), Open Issue 004
**Owner:** _unassigned_

---

## 1. Why this issue exists

Open Issue 001 proposed a custom single-writer in-memory engine, and Open Issue 002
proposed memory-mapped append-only logs as its transport and durable record. Before either
is finalised, alternatives raised for evaluation are:

- **Redis** as hot state and/or the ordered durable record, with asynchronous write-behind
  to a relational database.
- **SQLAlchemy / SQLModel** as the data-access layer instead of hand-written SQL.

**These alternatives do not compete with the same decision.** They sit at three distinct
layers that are commonly conflated. Separating them is the first job of this issue,
because a good answer at one layer says almost nothing about the others.

| Layer | Question | Genuinely contested? |
|---|---|---|
| **A** | Who owns and mutates the order book? | **Yes** |
| **B** | What is the durable, ordered record of truth? | **Yes** |
| **C** | How does application code talk to the relational database? | Low stakes, decide late |

---

## 2. Layer A — Who owns and mutates the order book?

### A1 — Custom in-memory engine (Open Issue 001, Approach B)

Order book is a data structure in a process the team writes, C++ or Python.

**For:** full control over data structures and therefore over throughput. Trivially
unit-testable as a pure function. Deterministic if written with discipline. Enables the
C++ engine artifact. Property-based and differential testing are straightforward.

**Against:** the team writes and maintains it, including snapshotting. Custom code is
custom risk.

### A2 — Redis as the order book

Price levels as sorted sets; matching implemented in a Lua script executed atomically
server-side.

**For:** Redis is single-threaded, so serialisation of concurrent requests is provided
rather than built — the same single-writer property as A1, for free. Persistence (AOF/RDB),
pub/sub fan-out, and expiry come built in. Substantially less code than A1. Redis is a
widely valued operational skill.

**Against:** the matching rules end up in Lua, which is hard to unit test, hard to debug,
and effectively impossible to property-test against a reference implementation — which
removes the strongest correctness argument available to this project. A network round trip
per order, ~50–100 µs even on loopback. A realistic match loop performing several
`ZRANGEBYSCORE` / `ZREM` operations lands around 5–20k orders/sec, at the bottom of the
agreed target range rather than comfortably above it. Eliminates the C++ engine artifact.

### A3 — Relational database as the order book

Covered as Approach A in Open Issue 001. Rejected there on throughput (~hundreds of
orders/sec) and on loss of determinism. Recorded here only for completeness.

---

## 3. Layer B — What is the durable, ordered record?

This is the layer where the alternatives are strongest, and where the original proposal
was most likely over-engineered.

### B1 — Memory-mapped append-only log (Open Issue 002)

**For:** sub-microsecond append and read. Doubles as inter-process transport, so IPC,
durability, and replay collapse into one component. Group commit gives a durability window
of roughly 200 µs. Multiple independent consumers, each holding its own offset, with no
coordination.

**Against:** the team writes it — cursor publication protocol, `msync` handling, segment
rotation, retention, checkpointing, and reader logic across segment boundaries. Estimated
30–40 hours. Platform-specific `msync` behaviour. Producer and consumer must share a
machine. Custom code in the one place where a bug silently invents or destroys money.

### B2 — Redis Streams

`XADD` appends with monotonic IDs; `XRANGE` replays; consumer groups track offsets.

**For:** **this is structurally the same design as B1 — an append-only log with per-consumer
offsets — implemented, tested, and operated by someone else.** It preserves every
correctness property that motivated B1: total ordering, replay from an arbitrary offset,
deterministic reconstruction, independent consumers. Saves an estimated 30–40 hours of the
most dangerous custom code in the project. Redis is easy to run locally and in the cloud,
and comes with mature operational tooling. At 5–20k orders/sec it is comfortably within
Redis's capability.

**Against:** a network hop on the hot path (~50–100 µs on loopback) where B1 costs
sub-microsecond. Durability is weaker by default: `appendfsync everysec` leaves a window of
up to one second, against roughly 200 µs for B1's group commit. `appendfsync always` closes
that gap but is slow unless commands are pipelined — pipelining plus `always` is effectively
group commit and is worth benchmarking before assuming otherwise. Streams live in RAM, so
110 GB/day of order flow requires `MAXLEN` trimming, which breaks replay-from-genesis unless
trimmed history is archived elsewhere. Adds an operational dependency the deployment must
carry. A C++ engine needs a Redis client such as hiredis.

### B3 — Relational table as the log

An append-only `events` table with a monotonic sequence column, consumers tracking offsets.

**For:** one datastore instead of two. Real transactional durability. Familiar tooling and
querying; the backtester can simply run SQL over history.

**Against:** insert throughput realistically 5–20k rows/sec with batching, which is at the
target rather than above it. Consumers must poll (or use `LISTEN`/`NOTIFY`), adding latency.
Considerably heavier per record than B1 or B2.

### B4 — Redis as hot state with asynchronous write-behind to a relational database

State is held in Redis for speed; a background worker persists to the database.

**For:** fast reads for portfolio and balance queries; database writes leave the hot path.

**Against:** **this must be examined carefully, because it has a durability hole that the
other options do not.** If Redis acknowledges a write and the process dies before the
database write lands, that state is lost while the user has already seen it — the exact
failure mode Open Issue 001 sub-decision 2c exists to prevent. Closing it requires Redis's
own durability (AOF), at which point Redis is functioning as the durable record and the
relational database is a derived read model — which is B2, described differently.

**Important framing:** "write-behind to a database" is not an alternative to the event-log
design; it is *what the event-log design already does*. The durable ordered record is the
write-ahead record, and the relational store is the asynchronously-updated read model. The
real question is only which technology plays the role of the durable ordered record — B1,
B2, or B3.

---

## 4. Layer C — Data-access layer for the relational read model

Genuinely low stakes, and deliberately deferred. Whatever the answer at Layers A and B,
this layer only ever serves the derived read model: account pages, trade history, charts,
and warm-start on restart. It is never on the matching path.

| Option | Notes |
|---|---|
| Raw SQL (`asyncpg` / `psycopg`) | Fastest, most explicit, most boilerplate |
| SQLAlchemy Core | Composable query building without ORM object overhead |
| SQLAlchemy ORM | Familiar, productive; identity map and lazy loading are traps under load |
| SQLModel | SQLAlchemy plus Pydantic; least boilerplate, pairs naturally with FastAPI |

The only constraint worth fixing now: **no ORM on any hot path**. Bulk writes into the read
model should use bulk/`COPY` paths regardless of which option is chosen. Beyond that, this
can be decided when the schema is written and is cheap to change.

---

## 5. Coherent stacks

Options only make sense in combination. Rough estimates; throughput figures are end-to-end
unless labelled otherwise.

| Stack | Layer A | Layer B | Est. hours | Throughput | Artifact strength | Main risk |
|---|---|---|---|---|---|---|
| **S1** | C++ engine | mmap log | ~90 | 500k engine / 20k+ e2e | Highest | Most custom code in the riskiest place |
| **S2** | C++ engine | Redis Streams | ~60 | 500k engine / 15–30k e2e | High | Network hop; weaker default durability |
| **S3** | Python engine | Redis Streams | ~35 | 50k engine / 10k e2e | Medium | No C++ artifact |
| **S4** | Redis + Lua | Redis AOF | ~30 | 5–15k e2e | Low–Medium | Matching logic is hard to test |
| **S5** | Relational DB | Same DB | ~25 | 0.3–1k e2e | Low | Fails the agreed scale targets |

Every stack pairs with a relational read model at Layer C.

**Note that S2 preserves the entire engine artifact and every correctness property of S1**,
trading roughly 30 hours of custom log code for a network hop and an operational dependency.
That trade deserves a genuine decision rather than an assumption in either direction.

### Budget context

With the Phase 1 deadline at 15 October, the effective budget is roughly 430 hours against
an estimated 365 hours of work. **S1 is affordable.** The relevant caution is that the ~30–40
hours S1 adds are concentrated in hand-written durability code — the single place in the
system where a defect silently invents or destroys money, and therefore the work most likely
to overrun its estimate.

### Deliberately no recommendation

Unlike Open Issues 001, 002 and 004, this issue records **no recommended option**. S1 and S2
are both defensible, and the choice turns on two judgements that belong to the team rather
than to any technical argument:

- whether a durability window of up to one second is acceptable for a simulated exchange,
  given that it must be documented either way; and
- whether hand-writing the log — `mmap`, memory ordering, group commit, segment rotation —
  is itself a **learning objective**. If it is, that is a wholly legitimate reason to choose
  S1. It simply has nothing to do with performance, and should be recorded as the actual
  reason rather than dressed up as one.

### Note on the "Redis then write asynchronously to the database" framing

This is worth restating because it caused the original confusion. A durable ordered record
with an asynchronously-updated relational read model **is** the event-log architecture. The
naive version — acknowledge from Redis, persist later, no Redis durability — reopens exactly
the failure that Open Issue 001 sub-decision 2c exists to close: a user observes a fill that
does not survive a crash. Closing that hole means enabling Redis durability, at which point
Redis is the durable record and the database is derived. That is option B2, arrived at from
a different direction.


---

## 6. Questions to resolve

1. **Layer B is the real decision. Is Redis Streams (B2) preferable to a hand-written mmap
   log (B1)?** The saved ~30–40 hours are the most dangerous hours in the project. The cost
   is a network hop and a weaker default durability window.
2. **Does the durability window matter for play money?** B1 gives ~200 µs; B2 with
   `appendfsync everysec` gives up to 1 s; B2 with pipelining plus `appendfsync always` may
   land close to B1 and should be measured rather than assumed.
3. **Is writing the log by hand a learning goal in itself?** If understanding `mmap`,
   memory ordering, and group commit is a stated objective, that is a legitimate reason to
   choose B1 that has nothing to do with performance — but it should be recorded as the
   actual reason.
4. **Is Redis acceptable as an operational dependency** in the Phase 1 deployment?
5. **Does anything at Layer A change?** Redis-as-order-book (A2) is a real option but costs
   testability and the C++ artifact.

---

## 7. Decision log

| Date | Status | Note |
|---|---|---|
| 2026-08-27 | OPEN | Issue created after alternatives were raised. Layers separated; no option adopted. |

---

## 8. Decision 2026-08-27 — S2 (Redis Streams)

**Layer B is resolved: the durable ordered record is a Redis Stream.** Layer A remains a
custom single-writer C++ engine (A1). Layer C stays deliberately open.

Reasoning, recorded so it does not have to be reconstructed later: Redis Streams is
structurally the same design as a hand-written memory-mapped log — an append-only sequence
with monotonic IDs and independent per-consumer offsets — and preserves every correctness
property that motivated it: total ordering, replay from an arbitrary offset, deterministic
reconstruction, and independent consumers. It removes roughly 30–40 hours of hand-written
code from the one part of the system where a defect silently invents or destroys money. The
price is a network hop of ~50–100 µs and one operational dependency.

### 8.1 Consequences that must be carried into other issues

| # | Consequence | Affects |
|---|---|---|
| 1 | Durability is now a Redis persistence configuration rather than hand-written group commit | OI 001 sub-decision 2c |
| 2 | The mmap transport design is superseded; the engine needs a Redis client | OI 002 §3, §4 |
| 3 | The co-location constraint disappears | OI 007 sub-decision 8b |
| 4 | Redis is on the critical path — a new failure mode | New work item |
| 5 | Streams live in RAM, so retention and archival are now required | **New component** |
| 6 | Fan-out consumes the outbound stream via consumer groups, natively | OI 006 sub-decision 7b |
| 7 | Gateway reservations *could* live in Redis; not taken | OI 004 |

### 8.2 Consequence 1 — durability configuration (open sub-question)

| Setting | Durability window | Cost |
|---|---|---|
| `appendfsync everysec` (default) | Up to **1 second** of acknowledged orders lost on power failure | Fast |
| `appendfsync always` | Per-write flush — no window | Slow unless commands are pipelined |
| **`appendfsync always` + pipelining** | Effectively group commit: one flush per event-loop iteration across a batch | **Proposed** |

Pipelining plus `always` is the same idea as the group commit proposed in Open Issue 001
sub-decision 2c, implemented by Redis rather than by hand. **Proposed:** configure
`appendfsync always`, pipeline `XADD` calls, and measure. If the measured throughput proves
unacceptable, fall back to `everysec` and document the one-second window explicitly rather
than leaving it implicit.

### 8.3 Consequence 2 — engine transport

The C++ engine now reads the inbound stream with `XREAD BLOCK` through a client such as
hiredis or redis-plus-plus, and writes results with `XADD`.

**The engine library itself is unaffected**, because it only ever knew "records in, records
out" (Open Issue 002 §5). Only the standalone adapter changes. That the design absorbed a
complete transport substitution without touching the core is evidence the boundary was drawn
in the right place.

**Latency has a floor now, but throughput does not.** `XREAD COUNT n` returns a batch, so the
network cost is amortised across the batch — at a batch size of 100, the per-record cost is
roughly 1 µs. The native engine benchmark (Open Issue 002 sub-decision 3d) remains honest
and is still reported separately from the end-to-end number.

### 8.4 Consequence 5 — retention and archival (a new component)

This is the one genuinely new problem created by choosing S2, and it must not be discovered
in week 5.

**Redis Streams are held in RAM.** A hand-written log lived on disk, so replay-from-genesis
was free. That is no longer true.

Volumes: steady-state bot flow of roughly 500 orders/sec at ~100 bytes is about 4.3 GB/day —
too much to retain in memory indefinitely, though comfortable over a few days. Benchmark
bursts at 20k orders/sec produce far more.

**Proposed:**

- Trim the streams with `MAXLEN ~` at roughly 5–10 million entries (on the order of 1 GB).
- Add an **archiver process** that tails the outbound stream and writes older events to
  durable storage — Postgres, or Parquet files.
- **Recovery** uses the engine snapshot plus replay from Redis, which is well inside the
  retained window.
- **Backtesting and long-range history** read from the archive, not from Redis.

Cost: approximately **8 hours**, plus the archive schema. This is a new Phase 1 work item
and belongs in the history and backtester issue.

### 8.5 Consequence 4 — Redis on the critical path

If Redis is unreachable, no orders can be accepted. The requirement is that this **fails
loudly**: the gateway enters an explicit *exchange halted* state, rejects new orders with a
clear reason, and surfaces the state in the UI — rather than timing out silently or, worse,
accepting orders it cannot durably record. Roughly 4 hours, and a genuine halt state is
itself realistic exchange behaviour.

### 8.6 Net effect on the budget

| Change | Hours |
|---|---|
| mmap log implementation removed | −35 |
| Redis integration (client, consumer groups, checkpointing) | +10 |
| Archiver process | +8 |
| Redis failure handling and halt state | +4 |
| **Net** | **−13** |

### 8.7 Layer C remains open

The data-access layer for the relational read model (raw SQL, SQLAlchemy Core, SQLAlchemy
ORM, SQLModel) is unaffected by this decision and stays deferred until the schema is written.
The single standing constraint is unchanged: **no ORM on any hot path.**

## 9. Decision log

| Date | Status | Note |
|---|---|---|
| 2026-08-27 | OPEN | Layers separated; no option adopted |
| 2026-08-27 | DECIDED | **S2 — Redis Streams as the durable ordered record.** Layer A remains a custom C++ engine; Layer C still open. Consequences recorded in §8.1. |

---

## 10. Placed on hold 2026-08-27

The S2 decision recorded in §8 was marked DECIDED prematurely and has been **reverted to
provisional**. S2 remains the leading option and §8's reasoning stands, but it is not
committed.

**Open Issue 009 — S2 Validation Plan** now holds the open discussion, structured as a set of
timeboxed measurements with pass/fail criteria rather than as an argument. Nothing in §8
should be treated as settled until Open Issue 009 reports.

Everything recorded in §8.1 as a *consequence* of S2 — the Redis durability configuration,
the superseded mmap transport, the lifted co-location constraint, the archiver component, and
the halt-state requirement — is likewise **conditional**. Each is annotated as such in the
issue it affects.

Layer A (custom single-writer C++ engine) and Layer C (data-access layer, still deferred) are
unaffected by this hold.

---

## 11. S1 selected, 2026-08-28

**Layer B is resolved as S1: a custom memory-mapped append-only log.** This is chosen with the
complexity assessment in Open Issue 009 §8 on the record, including the recommendation against
it. The team elected S1 after that assessment; the decision is deliberate, not uninformed.

The S2 material in §8 is retained as recorded reasoning but is **no longer the plan**. The
mmap design in Open Issue 002 §3–§4, previously superseded, is **current again**.

### 11.1 What reverts

| Item | Now |
|---|---|
| Transport | Memory-mapped append-only log (OI 002 §3–§4) |
| Durability (OI 001, 2c) | Hand-written group commit: batch, `msync`, publish cursor, acknowledge |
| Co-location (OI 007, 8b) | **Constraint returns** — gateway, engine, fan-out and ledger share one machine |
| Validation spike (OI 009) | No longer required; 16 hours returned |

### 11.2 What S1 improves, and it is not nothing

**Redis leaves the critical order path.** Under S2, an unreachable Redis meant no orders could
be accepted at all. Under S1 the order path depends only on the local filesystem.

Redis is still required for sessions (Open Issue 015) and currently for idempotency
deduplication (Open Issue 008 sub-decision 9c). Neither is as critical, and the deduplication
store could move into gateway memory, rebuilt from the log on restart exactly as risk state is
— which would remove Redis from the submit path entirely. **Open question, not decided here.**

**Replay from genesis becomes free again.** The log is on disk rather than in RAM, so the
archiver's purpose changes: it is no longer rescuing events from a RAM-bounded stream, but
building queryable derivatives (bars, L2 snapshots) for the backtester. Same cost, different
reason. Retention and rotation are still required, but the retained window can be far longer.

### 11.3 Budget

| | Hours |
|---|---|
| Running total under S2 | 423 |
| S1 in place of S2 (67 vs 22) | +45 |
| Validation spike no longer needed | −16 |
| **Revised total** | **452** |
| Effective budget | ~430 |

**Approximately 22 hours over**, where the position under S2 was about 7 hours under. Stated
once for the record. The cut list in Open Issue 012 §7 remains available if it is wanted; the
schedule risk has already been accepted deliberately (Open Issue 012 §10).

## 12. Decision log

| Date | Status | Note |
|---|---|---|
| 2026-08-27 | OPEN | Layers separated; no option adopted |
| 2026-08-27 | DECIDED then REVERTED | S2 marked prematurely, returned to provisional |
| 2026-08-28 | **S1 SELECTED** | Custom memory-mapped log chosen after the complexity assessment in OI 009 §8 recommended against it. Mitigation plan in OI 009 §10. Not marked DECIDED pending explicit confirmation. |

---

## 12. AUTHORITATIVE — S2 locked, 2026-08-28

**Layer B is Redis Streams. This decision is locked.** Section 11 (the S1 selection) is
**withdrawn**; sections 8 and 11 are retained only as decision history.

This issue reversed three times — S2 provisional, then on hold, then S1, then S2 locked. The
history is preserved deliberately rather than tidied away, because the reasoning at each step is
what makes the final choice defensible. **This section is the one to read; the others explain
how it was reached.**

### 12.1 Current state

| Layer | Decision |
|---|---|
| **A** — order book ownership | Custom single-writer C++ engine (unchanged throughout) |
| **B** — durable ordered record | **Redis Streams** |
| **C** — data access for the read model | Deferred until the schema is written; no ORM on any hot path |

### 12.2 Consequences, restated as current

| Item | Current |
|---|---|
| Transport | `XADD` to the inbound stream; engine reads with `XREAD COUNT n BLOCK` via hiredis or redis-plus-plus |
| Durability (OI 001, 2c) | Redis persistence — `appendfsync always` with pipelined `XADD`, measured; fall back to `everysec` with the 1-second window documented explicitly |
| Co-location (OI 007, 8b) | **No constraint.** Processes need not share a machine |
| Retention | Streams are RAM-resident; `MAXLEN ~` trimming at ~5–10 M entries, with the archiver writing older events to durable storage |
| Critical path | **Redis is on it.** An unreachable Redis means orders cannot be accepted, so the halt state in §8.5 is required, not optional |
| Validation spike (OI 009) | Not run. See OI 009 §11 for which experiments survive as configuration work |

### 12.3 Budget

| | Hours |
|---|---|
| Under the S1 selection | 452 |
| S2 in place of S1 (22 vs 67) | −45 |
| Residual Redis tuning folded into integration (OI 009 §11) | +5 |
| **Total** | **412** |
| Effective budget | ~430 |

Approximately **18 hours of slack**, the most comfortable position the project has held since
the testing and benchmarking increases were accepted.

## 13. Decision log

| Date | Status | Note |
|---|---|---|
| 2026-08-27 | OPEN | Layers separated; no option adopted |
| 2026-08-27 | DECIDED then REVERTED | S2 marked prematurely, returned to provisional |
| 2026-08-28 | S1 SELECTED | Chosen after the complexity assessment recommended against it |
| 2026-08-28 | **DECIDED — LOCKED** | **S2, Redis Streams.** §12 is authoritative; §8 and §11 are history. |
