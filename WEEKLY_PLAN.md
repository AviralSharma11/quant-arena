# Project Weekly Execution Plan

**Quant Arena — Phase 1**
**Window:** 28 August – 15 October 2026 (7 weeks) · **Team:** 2 developers, full-time
**Estimated work:** ~345 hours against ~430 effective hours

---

## How to read this document

Open the current week. Each task tells you what to build, why it exists, which technologies to
use, what is explicitly out of scope, how to verify completion, and what to hand over.

### Source of truth

This plan encodes the decisions recorded in `open-issues/001`–`019`. **Where `README.md` and
those issues differ, the issues win** — they are the result of working the design through.
The differences that matter most:

| `README.md` implies | Actually decided | Issue |
|---|---|---|
| A durable event log, mechanism unspecified | **Redis Streams**, locked | 003 |
| Snapshot + replay recovery | **No snapshots in Phase 1** — full replay from the retained stream | 018 §13.1 |
| Backtesting against market data | **Bar-open fill simulation** in Phase 1; engine-based fills are Phase 2 | 018 §3.2 |
| Distributed tracing | **The event stream is the trace** — a `trace <client_order_id>` tool, not OpenTelemetry | 012 §5 |
| Monitoring and metrics | **Structured logs plus offline plots** — no Prometheus or Grafana | 012 §12e |
| A set of simulated assets | **Ten symbols**, fair value from replayed real crypto history | 005, 018 §11.1 |

### Ownership — engineered so neither developer is ever blocked

The split is **not** by subsystem alone. It is arranged so that **no task ever waits on a task
assigned to the other developer in the same week.** Appendix D contains the full analysis, the
three mechanisms that make it work, and the week-by-week assignment.

- **Dev A — the exchange core:** schema and codegen, naive model, C++ engine, engine process,
  the testing programme, deployment and CI, frontend, backtester.
- **Dev B — the platform:** gateway, streams, ledger, risk, idempotency, bots, fan-out, archiver,
  load generation, benchmarks, hardening.

Three rules make this parallel-safe:

1. **A joint contract session on day one.** Both developers write the event schema, the REST
   surface and the WebSocket message shapes together. It takes half a day and it is the only
   time they must work in lockstep.
2. **Stub first, integrate later.** Any component whose upstream is not finished is built against
   a stub agreed in that session. There are exactly three: a stub engine, a mock WebSocket
   server, and hand-made sample Parquet files.
3. **A short integration point at the end of a week** swaps each stub for the real thing.

### Critical path

Arrows crossing between developers always point to a **previous** week, never the current one.

```
WEEK 1   [JOINT] 1.1 contracts
             │
    A ───────┼──► 1.2 naive model ──► 2.3 scenarios
    B ───────┴──► 1.3 gateway (stub engine) ──► 1.4 docker stack

WEEK 2   A ──► 2.4 C++ engine core
         B ──► 2.1 Redis streams ──► 2.2 ledger
         [INTEGRATE] stub engine → naive model over the real stream

WEEK 3   A ──► 3.4 engine + nanobind        A ──► 3.3 deploy + CI
         B ──► 3.1 risk ──► 3.2 idempotency

WEEK 4   A ──► 4.1 differential + property tests
         B ──► 4.4 bots            B ──► 5.2 fan-out (starts)

WEEK 5   A ──► 4.2 engine process ──► 4.3 benchmark   A ──► 5.4 frontend (mock WS)
         B ──► 5.2 fan-out (finishes)                 B ──► 5.1 crypto prices, 10 symbols
         [INTEGRATE] mock WS → real fan-out;  naive model → C++ engine process

WEEK 6   A ──► 6.1 trading screen
         B ──► 6.2 archiver · 6.3 load generator · 6.4 duplicates · 5.3 recovery script

WEEK 7   A ──► 7.1 backtester ──► 7.2 backtest screen ──► 7.3 integration tests
         B ──► 7.4 benchmarks + trace + report ──► 7.5 hardening + definition of done
```

### Assumptions

1. Deadline 15 October 2026; two developers full-time; ~345 h of work against ~430 h capacity.
2. Architecture per `open-issues/001`–`019`, superseding `README.md` where they differ.
3. All ten technology choices in Open Issue 019 adopted as recommended. Deployment **target**
   (single VM versus managed platform) remains deferred to week 3.
4. Effort figures are indicative, derived from issue estimates. They are not commitments.
5. Weeks 1 and 7 carry deliberate slack; weeks 3–6 sit near capacity. The ~85 hours of slack
   absorbs the 30–50% overrun that unfamiliar work historically incurs.

---
---

# Week 1 — Foundations and First Vertical Slice

## Weekly Objective

Establish the contract every component is written against, build the deliberately naive engine
that serves as both week-1 implementation and permanent test oracle, and stand up a gateway and
local stack so that an order can travel end to end.

## Expected Outcome

`docker compose up` brings up the whole stack. A registered user submits an order over HTTP, the
naive engine matches it, and the resulting event can be observed. The system is demonstrable
from week 1 and stays that way.

---

### Task 1.1: Event Schema and Code Generation

#### Tech Stack
Python, C++20, custom code generator, `struct`

#### Purpose
The event schema is two contracts at once: between every component in the system, and between
the two developers. Everything else is written against it, which is why it comes first.

It also carries a constraint an ordinary API does not — **these records are replayed**, so the
layout cannot drift silently. A layout mismatch between the C++ and Python sides does not raise
an error; it misreads fields, in the money path. Generating both from one definition removes
that entire failure class.

#### What Needs To Be Done
- Define the four inbound record types — `SubmitOrder`, `CancelOrder`, `CreateAccount`,
  `CreditCash` — and the outbound types: `OrderAccepted`, `OrderRejected`, `Fill`,
  `OrderCancelled`, `BookChanged`, `AccountCreated`, `CashCredited`.
- Give every record `seq`, `timestamp_ns`, `schema_version`, `record_type`.
- Include `aggressor_side` on `Fill` (required for maker/taker fees) and `tif` on `SubmitOrder`
  (`GTC` and `IOC` only).
- Write the generator producing a C++ packed-struct header and a Python module of `struct`
  format strings plus named tuples.
- Fix the conventions: `int64` price ticks, `int64` quantities, `uint64` order ids, `int64`
  nanosecond timestamps, `int16` symbol ids, enums as small integers, `snake_case` field names
  carrying their units (`price_ticks`, `timestamp_ns`).

#### Success Criteria
- One definition file generates both the C++ header and the Python module.
- A record round-trips Python → bytes → Python with every field identical.
- `sizeof` of each C++ struct equals `struct.calcsize` of its Python counterpart.
- Every record carries `schema_version`.
- No field anywhere is a float, a string, or variable-width.

#### Boundaries / Constraints
- Do not use Protobuf, FlatBuffers or Cap'n Proto — variable-width formats defeat fixed-size POD.
- No strings, no nesting, no optional fields, no dynamic keys.
- No floating point below the presentation layer.
- Do not add fields speculatively beyond `tif`, which is included precisely to avoid a later
  schema change.

#### Dependencies
None.

#### Deliverables
Schema definition file · generator script · generated C++ header · generated Python module ·
round-trip and size-parity tests.

---

### Task 1.2: Naive Python Model Engine

#### Tech Stack
Python, pytest

#### Purpose
A deliberately unoptimised matching engine that does three jobs for the price of one: it is the
**week-1 engine** so the system is demonstrable immediately; it becomes the **permanent test
oracle** the C++ engine is measured against; and it is the **interface specification** that lets
Dev B work without waiting for C++.

The naivety is the point. It encodes the matching *rules*, not the data structures, so it
changes only when the rules change — which after week 1 is almost never.

#### What Needs To Be Done
- Hold all resting orders in a flat list. Re-sort on every operation. Do not optimise.
- Implement limit orders, cancellation, partial and full execution, and price-time priority.
- Emit outbound events conforming to the Task 1.1 schema.
- Keep it under roughly 250 lines so it stays reviewable by eye.

#### Success Criteria
- A crossing order matches a resting order and emits a `Fill`.
- A partial fill leaves the correct remainder resting.
- Cancellation removes an order, and a cancelled order never subsequently fills.
- With equal prices, the earlier order fills first.
- All emitted events validate against the schema.
- The implementation is under 250 lines.

#### Boundaries / Constraints
- **Do not optimise.** No heaps, no price-level maps, no intrusive lists. Clever code here
  destroys the model's value as a reviewable specification.
- No money, no balances, no fees — the engine is money-blind.
- No market orders yet; they arrive in week 3 as banded limits.
- No I/O of any kind.

#### Dependencies
Task 1.1.

#### Deliverables
Model engine module · unit tests.

---

### Task 1.3: Gateway Skeleton — FastAPI, Sessions, Accounts

#### Tech Stack
FastAPI, uvicorn, Redis (sessions), argon2-cffi, PostgreSQL, SQLModel

#### Purpose
The gateway is the system's entry point and, once streams arrive in week 2, its **single
producer** — the component whose single-threadedness makes ordering and reservation state
race-free. Building it now with sessions and accounts means real users exist before there is
anything for them to do.

#### What Needs To Be Done
- Stand up the FastAPI application on uvicorn with the async event loop that later becomes the
  sequencing point.
- Implement register, login and logout using Argon2id and **Redis-backed session cookies**
  (`httpOnly`, `Secure`, `SameSite`).
- Create accounts with a virtual cash grant.
- Add `POST /orders` and `DELETE /orders/{client_order_id}`, validating input and calling the
  naive model in-process for now.
- Define the read-model schema for accounts in PostgreSQL via SQLModel.

#### Success Criteria
- A user registers, logs in, and receives virtual capital.
- The session survives a restart of the application process, because it lives in Redis.
- `POST /orders` returns `202` with an order identifier; a malformed order returns `400`.
- Passwords are stored as Argon2id hashes; no plaintext or general-purpose hash appears anywhere.

#### Boundaries / Constraints
- **No JWT.** Session cookies are the decision (Open Issue 015 §2).
- No risk checks or reservations yet — week 3.
- No idempotency yet — week 3.
- The engine is called in-process for this week only; Redis Streams arrive in week 2. Keep the
  call site behind a thin interface so the substitution is contained.
- No email verification — deferred to Phase 2.

#### Dependencies
Tasks 1.1, 1.2.

#### Deliverables
Gateway application · auth endpoints · order endpoints · account read-model schema.

---

### Task 1.4: Local Docker Stack and Shared Configuration

#### Tech Stack
Docker, docker-compose, Redis, PostgreSQL

#### Purpose
One command brings the whole system up, locally and later in production. Deployment problems
discovered in week 1 cost hours; the same problems discovered in week 7 cost the deadline.

The shared configuration file also lands here, because several later decisions depend on every
process reading identical parameters — symbols, tick sizes, replay clock, bot seeds, conflation
rate, book depth, rate limits.

#### What Needs To Be Done
- Write Dockerfiles for the gateway.
- Compose Redis, PostgreSQL and the gateway with health checks and restart policies.
- Create the single version-controlled configuration file and its loader.
- **Stamp the configuration's content hash into the log at startup**, so every session is
  self-describing.

#### Success Criteria
- `docker compose up` brings the stack up clean from a fresh checkout.
- The gateway reaches Redis and PostgreSQL; health checks pass.
- The configuration hash appears in the startup log of every process.
- A README section documents the run procedure.

#### Boundaries / Constraints
- No Kubernetes.
- No public deployment this week — that is Task 3.3.
- No Prometheus or Grafana; observability is structured logs plus offline analysis.
- Do not spread configuration across environment variables and code defaults; one file is the
  point.

#### Dependencies
Task 1.3.

#### Deliverables
Dockerfiles · `docker-compose.yml` · configuration file and loader · hash stamping · README run
instructions.

---
---

# Week 2 — Durable Event Flow and Money

## Weekly Objective

Replace the in-process engine call with a durable, ordered Redis stream, derive balances from
that stream rather than writing them directly, and begin the C++ engine.

## Expected Outcome

An order travels: gateway → durable Redis stream → engine → outbound stream → ledger → balance
change. Killing and restarting the ledger reproduces identical balances by replay. The C++
engine matches its first orders under Catch2.

---

### Task 2.1: Redis Streams Event Flow, Durability, and Halt State

#### Tech Stack
Redis Streams, redis-py, FastAPI

#### Purpose
This is the spine of the system. A single ordered stream is what makes price-time priority
enforceable, replay possible, and determinism achievable — three of the six goals rest on it.

**The Redis stream ID is the sequence number.** The gateway's role is single *producer*, not
sequencer; ordering comes from there being exactly one writer to one stream.

#### What Needs To Be Done
- Add the inbound stream: the gateway `XADD`s validated requests instead of calling the engine.
- Add the outbound stream for engine output.
- Write a reusable consumer helper over `XREAD COUNT n BLOCK`.
- Configure durability: `appendfsync always` with pipelined `XADD`. **Measure it.** If the
  throughput is unacceptable, fall back to `everysec` and document the one-second window
  explicitly rather than leaving it implicit.
- Set `MAXLEN ~` trimming at approximately 2 million entries.
- Implement the **halt state**: when Redis is unreachable, reject orders with a clear reason and
  surface the state, rather than timing out silently or accepting what cannot be recorded.

#### Success Criteria
- An order is `XADD`ed and read back with a monotonically increasing stream ID.
- Sustained `XADD` throughput is measured and recorded alongside the chosen `appendfsync`
  setting and the reasoning.
- `XREAD COUNT n` returns genuine batches under load, with per-record cost recorded at batch
  sizes 1, 10, 100.
- Stopping Redis puts the gateway into a visible halt state that rejects orders with a reason.
- Restarting Redis clears the halt state without a gateway restart.

#### Boundaries / Constraints
- **No memory-mapped log.** S1 was assessed and rejected (Open Issue 009 §8); it is Phase 3.
- **No Kafka.** One producer, one machine — the broker solves nothing here.
- **No snapshots or checkpointing.** Consumers replay the retained stream from its start.
- No separate sequencer process; the single gateway is the single producer.

#### Dependencies
Tasks 1.1, 1.3.

#### Deliverables
Stream client module · durability configuration with recorded measurements · halt-state handling
· tuning record for the benchmark report.

---

### Task 2.2: Ledger Writer and Replay-Based Rebuild

#### Tech Stack
Python, Redis Streams, PostgreSQL, SQLModel

#### Purpose
The ledger derives cash and positions **from the event stream**, which establishes the property
the whole money design rests on: the stream is authoritative and the relational database is a
derived read model, never a source of truth.

It is also the first component to prove that replay works, which is the mechanism recovery,
determinism and reproducibility all reuse.

#### What Needs To Be Done
- Build a consumer tailing the outbound stream.
- Apply `Fill` to both sides' cash and positions, charging **maker/taker fees in basis points**
  into a house fee account.
- Apply `AccountCreated` and `CashCredited`.
- Write into the PostgreSQL read model in batches.
- On startup, rebuild all state by replaying the retained stream from its start.

#### Success Criteria
- A fill moves both counterparties' cash and positions correctly.
- Fees land in the house account and the extended cash-conservation invariant holds: user cash
  plus reservations plus the fee account is constant except at deposits.
- Killing the ledger and restarting reproduces byte-identical balances by replay.
- No balance is ever written to PostgreSQL from any source other than the stream.

#### Boundaries / Constraints
- **No snapshots** — rebuild by full replay. Checkpointing is a Phase 2 optimisation with a
  before/after measurement attached.
- No ORM on the write path; use bulk writes into the read model.
- The ledger must never accept a write from the gateway or anywhere else — only the stream.
- Do not make PostgreSQL authoritative for anything.

#### Dependencies
Task 2.1.

#### Deliverables
Ledger consumer · read-model schema · maker/taker fee logic · replay rebuild · cash-conservation
test.

---

### Task 2.3: Hand-Written Matching Scenarios (T1)

#### Tech Stack
pytest

#### Purpose
These encode the matching rules as executable examples, and they run against the **naive model,
not the C++ engine**. That ordering matters: the model is the oracle everything else is measured
against, so it has to be verified first. If the model is wrong, the differential harness in
week 4 proves only that both implementations are wrong in the same way.

#### What Needs To Be Done
Write scenarios covering: full fill · partial fill · multiple sequential partials · no match, so
the order rests · price improvement (a buy at 102 filling at 101) · price-time priority across
equal prices · cancel before any fill · cancel after a partial fill · cancel racing a fill ·
self-trade attempt · an order crossing several price levels · an order exhausting one side
entirely · zero and negative quantities rejected · a price off the tick grid rejected.

#### Success Criteria
- Every scenario passes against the naive model.
- Each test reads as documentation of the rule it covers.
- A failure message names the rule that was violated, not just the assertion.

#### Boundaries / Constraints
- Run against the **model**, not C++ — which does not exist yet.
- No generated or property-based tests here; that is T2 in week 4.
- Do not modify the model to make a scenario pass without first confirming the scenario states
  the rule correctly.

#### Dependencies
Task 1.2.

#### Deliverables
Scenario test suite.

---

### Task 2.4: C++ Engine — Order Book and Match Loop

#### Tech Stack
C++20, CMake, Catch2

#### Purpose
The headline artifact. This is the one component in the entire system with **zero I/O** — no
sockets, no database, no async, no threads — which is exactly what makes it the easiest part to
write in C++ and the part where C++ buys something real.

#### What Needs To Be Done
- Build per-side price-level structures.
- Use intrusive order lists within each level to preserve time priority.
- Maintain an order-id to location map so cancellation is O(1).
- Implement the match loop: crossing, partial fills, resting remainders.
- Emit events as POD records matching the Task 1.1 layout.
- Partition book state by symbol internally, so sharding later is a contained change.

#### Success Criteria
- The Task 2.3 scenarios, ported to Catch2, pass against the C++ engine.
- The book is never crossed after any operation.
- Cancellation is O(1), demonstrated by measurement across book sizes.
- No floating point appears anywhere in the library.
- Builds clean with warnings-as-errors under C++20.

#### Boundaries / Constraints
- **Absolutely no I/O** — no sockets, no Redis, no files, no threads, no clock reads, no
  randomness. Any of these silently destroys determinism.
- No money, balances or fees — the engine stays money-blind.
- No iteration over unordered containers.
- **Do not optimise yet.** The differential harness that makes optimisation safe arrives in
  week 4; optimising before it exists inverts the value.

#### Dependencies
Tasks 1.1, 2.3.

#### Deliverables
C++ engine static library · CMake build · Catch2 unit tests.

---
---

# Week 3 — Risk, Idempotency, and a Complete Engine

## Weekly Objective

Make the money path safe — no double-spend, no duplicate orders — finish the C++ engine and
expose it in-process, and get the system publicly deployed with CI running.

## Expected Outcome

Submitting the same order twice produces exactly one order. An order exceeding available balance
is rejected before it reaches the stream. The C++ engine can be driven from Python. The system
is reachable over HTTPS and every push runs the test suite.

---

### Task 3.1: Risk Checks and In-Memory Reservations

#### Tech Stack
Python, FastAPI

#### Purpose
Solves the defining race of the whole design. The ledger lags by construction, so a risk check
reading a settled balance will pass two orders that together exceed it — inventing money.

The fix is that the **gateway owns risk state in its own memory** and reserves *before*
sequencing. Because the gateway is the single-threaded producer, it is trivially a single writer
over that state, so there is no interleaving in which the race can occur. The safety comes from
ownership, not from checking harder.

#### What Needs To Be Done
- Hold `settled_cash`, `settled_positions` and `reserved` per user in gateway memory.
- Check `settled_cash − reserved ≥ cost`, and on success **increment `reserved` immediately,
  before sequencing**.
- Release reservations **only** on `Filled` or `Cancelled` observed on the outbound stream.
- Implement market orders as **marketable limit orders with a price band** (a market buy becomes
  a limit buy at `best_ask × 1.05`, or a configured per-symbol band).
- Validate order bounds before anything else: quantity in range, price on the tick grid and
  within limits, symbol configured.
- Rebuild all risk state by replaying the outbound stream on startup.

#### Success Criteria
- Two rapid orders that together exceed the balance: the second is rejected.
- A buy filling better than its limit releases the difference — reserve at limit, settle at fill.
- A cancel that loses the race to a fill releases nothing.
- Restarting the gateway rebuilds reservations and balances identically by replay.
- No user's available balance ever goes negative, verified across a bot-driven session.
- A market order into a thin book cannot execute outside its band.

#### Boundaries / Constraints
- Reservations live in **gateway memory, not Redis**. Moving them to Redis reintroduces a
  read-modify-write across a network boundary.
- The engine stays money-blind — it never learns about cash.
- No margin and no retail short selling; retail accounts are cash accounts.
- Designated market-maker accounts may hold negative inventory; retail accounts may not.
- **No snapshots** — risk state rebuilds by full replay.

#### Dependencies
Tasks 2.1, 2.2.

#### Deliverables
Risk module · reservation state and rebuild · banded market orders · input validation ·
conservation tests.

---

### Task 3.2: Idempotency — Two Identifiers and Atomic Claim-and-Append

#### Tech Stack
Redis, Lua, FastAPI

#### Purpose
`README.md` Problem 4. A client cannot distinguish "the request never arrived" from "it
succeeded but the response was lost" — only the server can, and only if designed to.

This matters more here than in ordinary CRUD: a duplicate is not a stray row to clean up later,
it is **a real trade with a real counterparty whose position also moved**. It cannot be undone
without unwinding someone else's fill.

#### What Needs To Be Done
- Require `client_order_id` on every submission; reject `400` if absent.
- Write a **single Lua script** performing the idempotency claim and the `XADD` atomically.
  Two separate operations leave a window in which the key is claimed but no order exists, which
  strands the client on `in_progress` until the TTL expires.
- Implement the ordering **validate → reserve → claim-and-append**, releasing the reservation
  when a duplicate is detected. The alternative ordering leaks a reservation on every retry.
- Record rejections too, so the same `client_order_id` always yields the same answer.
- Set a one-hour TTL from configuration.
- Implement cancel-by-`client_order_id`, so a client can cancel an order whose acknowledgement it
  never received.

#### Success Criteria
- The same `client_order_id` submitted twice produces exactly one order; the second returns the
  stored outcome.
- A retry arriving while the original is still in flight returns `202 in_progress`.
- A retry after a rejection returns the identical rejection and reason.
- A duplicate submission leaves the user's `reserved` total unchanged — no leak.
- A submission without a key is rejected `400` before any other processing.
- Killing the gateway between claim and append is impossible to observe, because they are atomic.

#### Boundaries / Constraints
- Two separate Redis operations are **not acceptable** — it must be one script.
- Do not hold the deduplication store in gateway memory; it must survive a gateway restart.
- TTL comes from the shared configuration file, not a constant.
- Do not treat cancels as requiring deduplication for correctness — a repeat cancel is a no-op —
  but do carry the key for uniformity.

#### Dependencies
Task 3.1.

#### Deliverables
Lua claim-and-append script · idempotency module · cancel-by-client-order-id · retry-path tests
covering all three cases.

---

### Task 3.3: Public Deployment and CI

#### Tech Stack
Docker, GitHub Actions, TLS

#### Purpose
Deployment problems found now cost hours. The same problems found in week 7 cost the deadline.
Getting a public URL and a green pipeline while the system is still small is the cheapest
insurance available.

This is also where the deferred deployment-target decision gets made.

#### What Needs To Be Done
- Decide the deployment target — single VM or managed platform. There is no co-location
  constraint under Redis Streams, so both are viable.
- Publish container images.
- Deploy, with TLS and a domain.
- Set up GitHub Actions running the **per-commit fixed suite**: hand-written scenarios,
  determinism tests, unit tests. Target under two minutes.
- Add a nightly workflow stub, to be filled by the generated tests in week 4.

#### Success Criteria
- The application is reachable over HTTPS from outside the development machines.
- A push runs the fixed suite; a failing suite blocks the merge.
- Redeployment is a single documented command.
- The per-commit suite completes in under two minutes.

#### Boundaries / Constraints
- No Kubernetes.
- No Prometheus or Grafana.
- Do not put generated or property-based tests in the per-commit pipeline — they are slow and
  variable, and belong in the nightly workflow.
- Do not gold-plate the pipeline; it needs to run tests and publish images, nothing more.

#### Dependencies
Tasks 1.4, 2.2.

#### Deliverables
CI workflows · deployment configuration · public URL · deployment runbook.

---

### Task 3.4: C++ Engine Complete and nanobind Adapter

#### Tech Stack
C++20, nanobind, CMake

#### Purpose
Completes the engine and exposes it **in-process** to Python. That adapter is what makes the
differential test harness practical in week 4 — driving a subprocess from Hypothesis is
miserable — and it is the reason nanobind was chosen over pybind11: roughly ten times lower call
overhead, which matters when a harness calls the engine millions of times in a tight loop.

The same library will also be wrapped by a standalone binary in week 4. One core, two thin
adapters.

#### What Needs To Be Done
- Complete matching semantics: banded market orders, `IOC` time-in-force, self-trade prevention.
- Build the nanobind module over the same static library.
- Provide a **batch submit API** so per-call binding overhead is amortised.
- Wire the build so the module imports cleanly in the test environment and in CI.

#### Success Criteria
- Python drives the C++ engine and receives events matching the schema.
- The Catch2 scenario suite passes identically through the binding.
- Batch submission measurably amortises per-call overhead against single-call submission.
- The module builds in CI on Linux.

#### Boundaries / Constraints
- The engine library still has no I/O; the adapter adds none.
- The adapter contains **no logic** — no validation, no transformation beyond marshalling.
- Still do not optimise the engine; the harness that makes that safe lands next week.

#### Dependencies
Task 2.4.

#### Deliverables
Completed C++ engine · nanobind module with batch API · build and CI integration.

---
---

# Week 4 — Proof, and the Engine in Production Shape

## Weekly Objective

Prove the C++ engine correct against the naive model, move it into its own process reading the
stream, measure its native throughput, and bring the market to life with bots.

## Expected Outcome

The two engine implementations emit byte-identical event streams across thousands of generated
order sequences. The engine runs as a separate process and recovers from a kill by replay. The
native throughput number exists. Bots quote and trade.

---

### Task 4.1: Differential, Property and Determinism Tests (T2–T4)

#### Tech Stack
pytest, Hypothesis

#### Purpose
The strongest correctness artifact this project can produce, and the thing that makes optimising
the C++ engine safe rather than reckless.

Two independently written implementations agreeing across millions of generated operations is a
far stronger statement than any coverage percentage, and it is what database and consensus teams
actually do.

#### What Needs To Be Done
- Write Hypothesis strategies generating arbitrary sequences of submissions and cancellations.
- Assert invariants **after every step**, not only at the end:
  - **I1** book never crossed · **I2** quantity conserved · **I3** fills never exceed remaining ·
    **I4** cancelled orders never fill · **I5** price-time priority never violated ·
    **I6** resting quantities strictly positive · **I7** identical input yields identical output.
- Build the **I5 checker**: an independent view of the book asserting that for every fill, no
  order existed with a strictly better price, or an equal price and an earlier sequence, that
  could have filled instead.
- Build the differential harness: run each generated sequence through both implementations and
  assert the event streams are identical field by field, in order.
- Add the determinism test (same input twice, byte-identical output) and the replay test.
- Commit a **regression corpus**: every generated sequence that finds a failure is saved and
  replayed forever after, on every commit.

#### Success Criteria
- Ten thousand or more generated sequences produce byte-identical output from both engines.
- Invariants I1–I7 hold at every step of every sequence.
- The I5 checker detects a deliberately injected priority violation.
- Running the same sequence twice yields identical output.
- A discovered failure lands in the corpus and thereafter runs per-commit in under two minutes.

#### Boundaries / Constraints
- **Generated tests run nightly; fixed tests and the regression corpus run per commit.** The
  corpus belongs on the per-commit side despite its origin — once captured, it is a fixed, fast,
  deterministic test for a bug that has already happened once.
- Do not chase a coverage percentage. Chase invariants.
- No mutation testing — Phase 2.
- Do not weaken an invariant to make a test pass.

#### Dependencies
Task 3.4.

#### Deliverables
Property suite · I5 priority checker · differential harness · determinism and replay tests ·
regression corpus · CI wiring for both cadences.

---

### Task 4.2: Standalone Engine Process, Redis Integration, Replay Recovery

#### Tech Stack
C++20, hiredis or redis-plus-plus, Redis Streams, Docker

#### Purpose
Turns the engine into its own operating-system process reading the inbound stream and writing the
outbound one. This is the pipeline architecture actually operating: **one project, separate
processes, one coherent event flow**.

Recovery is the same code path as normal operation — the main loop always reads records forward
from an offset — so there is no separate recovery logic that can drift out of sync.

#### What Needs To Be Done
- Build the standalone binary tailing the inbound stream with `XREAD COUNT n BLOCK` and writing
  results with `XADD`.
- Implement **pass-through** for `CreateAccount` and `CreditCash`: stamp and forward untouched,
  so there is exactly one global ordering for all state changes.
- Process records in batches to amortise the network hop.
- On startup, rebuild the book by replaying the retained stream from its start.
- Add the engine as a compose service with a restart policy.

#### Success Criteria
- The engine process consumes live orders and emits events end to end.
- Killing and restarting it rebuilds the book by replay with **no lost or duplicated fills**.
- Batching is confirmed: per-record cost falls substantially at batch size 100.
- Pass-through records appear on the outbound stream in correct order relative to fills.
- Recovery time is measured and recorded.

#### Boundaries / Constraints
- **No snapshots or checkpointing.** Full replay only. Checkpointing is Phase 2, and the slow
  recovery number measured here becomes its before/after baseline.
- No memory-mapped log.
- The engine remains money-blind; pass-through means forwarding, not interpreting.
- Do not add a second entry path into the stream.

#### Dependencies
Tasks 4.1, 2.1.

#### Deliverables
Standalone engine binary · Redis adapter · pass-through handling · replay recovery · compose
service · recorded recovery time.

---

### Task 4.3: Native Engine Benchmark (B1)

#### Tech Stack
C++20, custom harness

#### Purpose
The headline throughput number, measured with **no Python anywhere in the path**. Benchmarking
the engine through the binding would measure nanobind's call overhead, not the engine — and a
sharp reader will catch that.

#### What Needs To Be Done
- Write a pure C++ harness feeding synthetic order flow directly to the library.
- Measure sustained orders per second and per-order cost.
- Record hardware, compiler version, build flags and the order-flow mix used.

#### Success Criteria
- B1 is recorded with full methodology, hardware and build configuration.
- Sustained throughput reaches at least 500,000 orders/sec on a single core.
- The result is labelled distinctly from any end-to-end figure.

#### Boundaries / Constraints
- No Python in this harness.
- **Never present B1 as system throughput.** B1 and B2 are separate numbers and conflating them
  is how credibility is lost.
- Do not tune the synthetic flow to flatter the number; use a realistic mix of matches, rests
  and cancels.

#### Dependencies
Task 4.2.

#### Deliverables
Native benchmark harness · recorded B1 results with methodology.

---

### Task 4.4: Bots — Designated Market Maker and Noise Traders

#### Tech Stack
Python, httpx, websockets

#### Purpose
Without synthetic order flow the book is empty, nothing trades, and there is no demonstration.
But the bots are more than a prop: they are the **load generator** for the benchmarks and the
**data source** for the archive.

The market maker in particular is what makes the market continuously tradeable, and inventory
skewing is what keeps it stable over hours rather than minutes.

#### What Needs To Be Done
- Build a bot runner with **seeded** random number generators, seeds recorded in configuration.
- Implement the designated market maker: two-sided quotes around fair value, **skewed by
  inventory**, with configurable obligations — maximum spread, minimum size per side, minimum
  two-sided uptime.
- Implement noise traders with Poisson arrival.
- Run bots as **real API clients**, authenticating and submitting like any other participant.
- Instrument obligation compliance so breaches are detectable events.

#### Success Criteria
- With bots running, the book is never empty and trades print continuously.
- The market maker meets its configured obligations across a session; breaches are recorded.
- Market-maker inventory stays bounded rather than drifting one-sided.
- Seeded runs reproduce identical bot behaviour.
- Bots authenticate and are rate-limited exactly like any user.

#### Boundaries / Constraints
- Bots go through the **real API**, not in-process. In-process bots exercise none of the code
  they would be testing and are worthless as a load test.
- No momentum or value traders — Phase 2. Real price data supplies the structure they would add.
- Designated market-maker accounts may hold negative inventory; retail accounts may not.
- Do not special-case bots anywhere in the gateway.

#### Dependencies
Task 3.2.

#### Deliverables
Bot runner · market maker with inventory skew and obligations · noise trader · obligation
instrumentation · bot configuration.

---
---

# Week 5 — A Market That Is Alive

## Weekly Objective

Drive the market from replayed real crypto prices across ten symbols, build the fan-out process
that pushes it to browsers, prove recovery under load, and lay the frontend foundation.

## Expected Outcome

Ten symbols with prices that move realistically. A WebSocket client receives book, tape and bar
updates at 20 Hz. Killing the engine mid-load recovers with no lost fills, from one command. The
frontend connects, authenticates and renders without re-rendering on every tick.

---

### Task 5.1: Bots Complete — Crypto Fair Value, Replay Clock, Ten Symbols

#### Tech Stack
Python, Binance public API, Parquet

#### Purpose
Real historical prices bring volatility clustering, regime changes and genuine trends for free —
statistical structure that a synthetic model would take far longer to approximate and would
approximate worse.

This matters beyond realism: the bots are the data-generating process for the research half of
the project, and a price series with no structure makes every backtest meaningless.

#### What Needs To Be Done
- Fetch one-minute crypto history once from the Binance public API. **Pin it** — commit it, or
  fetch at setup from a pinned URL with a recorded checksum.
- Map real price paths onto **fictional symbols**, so nobody believes they are trading the real
  instrument.
- Implement the replay clock at **1 real second : 1 simulated minute**, configurable, with the
  ratio **stamped into the event stream** because it determines the timestamps the backtester
  consumes.
- Loop from a randomised offset at the end of the dataset.
- Build a synthetic fallback generator for tests and offline development.
- Scale to ten symbols.

#### Success Criteria
- Ten symbols show distinct, realistically moving prices.
- The replay ratio appears in configuration and in the stream.
- The system runs fully offline from pinned data.
- The fallback generator produces a deterministic price path with no data file present.
- The README states that price paths derive from anonymised historical data.

#### Boundaries / Constraints
- **Never fetch live at runtime** — it destroys reproducibility, which is a stated project goal.
- Do not name symbols after real instruments.
- No momentum or value bots.
- Do not tune the price process to make any particular strategy profitable — parameters are fixed
  before strategies are written.

#### Dependencies
Task 4.4.

#### Deliverables
Data loader · pinned dataset with checksum · replay clock · symbol mapping · synthetic fallback
generator.

---

### Task 5.2: Fan-Out Process, WebSocket, and Conflation

#### Tech Stack
Python, FastAPI/websockets, Redis Streams

#### Purpose
This is where the system's real bottleneck lives and therefore where performance work actually
pays. With a C++ engine matching at 500k orders/sec the engine is idle; fan-out saturates first.

The arithmetic drives the design: 20,000 events/sec × 500 clients is 10 million messages/sec,
which is not achievable in any language on one machine. So the design problem is not *how to
broadcast quickly* — it is **what to decline to send.**

#### What Needs To Be Done
- Build fan-out as a **separate process** tailing the outbound stream.
- Implement subscription filtering, so a client receives only the symbols it watches.
- Implement the **20 Hz conflation tick**: coalesce updates and send only current state.
- **Serialise once per symbol per tick** and write identical bytes to every subscriber.
- Publish tiered feeds: **L1** (best bid/offer plus last trade) and **L2** (complete top-N
  snapshots, N = 10, configurable).
- Send the **trade tape un-conflated** — every print.
- Aggregate and publish one-second bars.
- Build the private per-user stream with **per-user sequence numbers** for gap detection.
- Implement the slow-client policy: if a client's send buffer is non-empty when the next tick
  fires, **skip that client for that tick**. Private data instead uses a bounded buffer, then
  disconnection.

#### Success Criteria
- Two hundred or more concurrent clients receive updates without gateway acknowledgement latency
  degrading measurably.
- A deliberately slowed client receives a lower frame rate and demonstrably affects no other
  client.
- A client that disconnects and reconnects recovers the full book from the next snapshot, with no
  special handling.
- A gap in the private stream is detectable from its sequence numbers.
- Serialisation happens once per symbol per tick, verified by instrumentation.

#### Boundaries / Constraints
- **No delta encoding.** Complete snapshots make dropped frames self-correcting; deltas are
  Phase 2 with a before/after measurement.
- **No binary protocol yet** — JSON, but with a schema that is binary-ready: fixed field order,
  integer types, no dynamic keys. Conflation is worth ~85×; encoding is worth ~5×.
- **Fan-out must not run inside the gateway.** Coupling it to the sequencer degrades order
  acknowledgement latency under connection load.
- Private data must never be dropped.

#### Dependencies
Task 4.2.

#### Deliverables
Fan-out process · WebSocket server · conflation loop · L1/L2/tape/bar stream builders · private
stream with sequence numbers · backpressure policy.

---

### Task 5.3: Kill-the-Engine Recovery Script (T6)

#### Tech Stack
Python, Docker, pytest

#### Purpose
Five hours of work carrying three separate obligations: it is **beat 6 of the demonstration** —
the moment that separates this from a trading-themed web application — it is the Reliability
evidence in the definition of done, and it produces the recovery-time measurement that
`README.md` Goal 5 names explicitly.

#### What Needs To Be Done
- Write a single command that: starts the stack, drives sustained order flow, kills the engine
  container mid-flight, waits for recovery, and asserts correctness.
- Assert **no lost and no duplicated fills**, and that cash conservation holds across the kill.
- Print the measured recovery time.
- Capture a GIF of a successful run for the README.

#### Success Criteria
- Runs end to end from one command and passes reliably across repeated runs.
- Prints a recovery time.
- A deliberately broken replay path makes the script fail — proving it actually checks something.
- The GIF exists and shows the full sequence.

#### Boundaries / Constraints
- A local scripted test, not CI — CI integration is Phase 1.5 if week 7 allows.
- **Do not add snapshots to make recovery faster.** The slow number measured here is the
  deliberate Phase 2 baseline.
- Do not weaken the assertions to make the script pass.

#### Dependencies
Tasks 4.2, 4.4.

#### Deliverables
Recovery script · correctness assertions · recorded recovery time · README GIF.

---

### Task 5.4: Frontend Foundation

#### Tech Stack
TypeScript, React, Vite, WebSocket

#### Purpose
The frontend must be **streaming-first from the first commit**. Order submission returns an
acknowledgement, not a result — fills arrive later on the private stream — so a UI built around
request/response would have to be rewritten.

This task also solves the rendering problem, which is the same problem the server solved one
layer up: the book updates at 20 Hz, and a conventional framework re-rendering on every update
drops frames. **The answer is the same: conflate at the boundary.**

#### What Needs To Be Done
- Scaffold Vite + TypeScript + React.
- Build the auth screens.
- Build the WebSocket client: subscription management, reconnection, **sequence-gap detection**
  on the private stream, and REST re-synchronisation when a gap is found.
- Build the **`requestAnimationFrame` render loop**: high-frequency data lands in a plain mutable
  buffer **outside React state**; the frame loop reads the buffer and paints what is current.
- Add a visible connection-state indicator: connected, reconnecting, or halted.

#### Success Criteria
- Login works and the session survives a page reload.
- The WebSocket reconnects and re-subscribes after a forced drop.
- A private-stream sequence gap triggers REST re-synchronisation of open orders and portfolio.
- Connection state is visible at all times.
- **No React re-render occurs on book updates**, verifiable in the React profiler.

#### Boundaries / Constraints
- High-frequency data must **not** live in React state.
- Three screens only. No settings, admin, profile, design system, theming, or responsive work
  beyond not breaking.
- Build around the private stream, not synchronous request/response.
- Do not poll the REST endpoints; they exist solely for re-synchronisation after a gap.

#### Dependencies
Task 5.2.

#### Deliverables
Frontend project · auth screens · WebSocket client with gap detection · rAF render loop ·
connection indicator.

---
---

# Week 6 — The Product

## Weekly Objective

Build the trading screen — the thing users actually see and the substance of the demonstration —
and stand up the archiver and the load generator.

## Expected Outcome

Open the site: the market is moving, the book updates, the tape scrolls, the chart streams. Place
an order and watch the portfolio change without refreshing. The archive is filling with data the
backtester will use next week, and the load generator can drive the system honestly.

---

### Task 6.1: Trading Screen

#### Tech Stack
TypeScript, React, TradingView Lightweight Charts

#### Purpose
**This screen is the demonstration** — beats 1 through 4 of the five-minute walkthrough all
happen here. It is also the only place where "real-time" is actually experienced; everything
upstream exists to make this screen feel immediate.

#### What Needs To Be Done
- Build the **order book panel** (L2, ten levels each side) rendered from the buffer on the frame
  loop.
- Build the **trade tape**, showing every print.
- Integrate **Lightweight Charts** for candlesticks, fed from the bar stream.
- Build the **order ticket**: buy/sell, limit and market, quantity and price.
- Build **open orders** with cancellation.
- Build **portfolio and balance**, driven by the private stream.

#### Success Criteria
- A user places an order and sees the fill and the portfolio change **without refreshing**.
- Book and tape update smoothly under full bot load, with no visible frame drops.
- Cancellation works, and a cancel that loses the race to a fill is handled correctly in the UI.
- The chart streams new bars as they complete.
- A user completes the full workflow — log in, view market, place order, see fill, cancel
  remainder — without touching anything outside this screen.

#### Boundaries / Constraints
- No settings, admin or profile screens. The three-screen limit is a scope commitment made in
  advance, and a fourth screen is a scope change to be raised, not absorbed.
- Do not poll the REST endpoints — they are for re-synchronisation only.
- Do not put book or tape updates into React state.
- No design system, no theming, no mobile-responsive work beyond not breaking.

#### Dependencies
Tasks 5.4, 5.1.

#### Deliverables
Trading screen with all six panels · order submission and cancellation flows.

---

### Task 6.2: Archiver

#### Tech Stack
Python, pyarrow (Parquet), Redis Streams

#### Purpose
Produces the derivatives the backtester consumes — trades, bars and periodic book snapshots — and
preserves history beyond the retained stream window.

Note the scope carefully: because Phase 1 backtests run against **real crypto history**, not
own-market history, and because `MAXLEN` is sized so replay never needs the archive, **the
archiver is not correctness-critical in Phase 1**. It becomes so in Phase 2.

#### What Needs To Be Done
- Build a consumer tailing the outbound stream.
- Write **trades**, **1-second and 1-minute OHLCV bars**, and **1 Hz L2 snapshots** to Parquet.
- Partition by symbol and day.
- Checkpoint the consumer offset so restarts resume cleanly.

#### Success Criteria
- A session produces readable Parquet files.
- Bars reconcile exactly against the trade stream they were built from.
- Restarting the archiver resumes with neither a gap nor a duplicate.
- Volume is in line with the ~345 MB/day estimate for ten symbols.

#### Boundaries / Constraints
- Do not treat the archive as authoritative in Phase 1 — `MAXLEN` is sized so it never needs to be.
- Do not write bulk history into PostgreSQL; that is the read model, not a data warehouse.
- Do not build a query API over the archive; the backtester reads the files directly.
- Bar aggregation is shared with the live chart pipeline — build it once.

#### Dependencies
Task 5.2.

#### Deliverables
Archiver process · Parquet schemas and partitioning · bar aggregation · offset checkpointing.

---

### Task 6.3: Open-Loop Load Generator

#### Tech Stack
Python, HDR histogram

#### Purpose
**The methodology is the claim.** A naive load generator sends a request, waits for the response,
then sends the next — so when the system stalls, the generator stalls too, never issuing the
requests that would have arrived during the stall and never recording their latency. The stall is
erased from the measurement. This is coordinated omission, and avoiding it is what makes the
resulting numbers defensible.

#### What Needs To Be Done
- Issue requests on a **fixed schedule, independent of completions**.
- Measure latency from **intended** send time, not actual send time.
- Record full distributions with HDR histograms.
- Print a per-second summary line: offered rate, achieved rate, p50, p99, stream lag.
- Discard a warm-up interval and record which interval was discarded.
- Support a configurable rate ramp.

#### Success Criteria
- The generator sustains its target rate regardless of server latency.
- Against a deliberately stalled server, the stall appears in p99 rather than being hidden.
- The per-second summary prints throughout a run.
- The warm-up interval is recorded alongside results.

#### Boundaries / Constraints
- **Must not wait for a response before issuing the next request.** This is the entire point.
- **Never report averages.** Distributions only — p50, p95, p99, p99.9 and maximum.
- Run against the **scratch deployment**, never the demo instance: a 20k/sec run consumes the
  retained stream window in about 100 seconds.
- Do not use an off-the-shelf generator without confirming it is open-loop.

#### Dependencies
Task 5.2.

#### Deliverables
Load generator · HDR histogram reporting · live per-second summary · rate configuration.

---

### Task 6.4: Duplicate Injection in the Load Harness

#### Tech Stack
Python

#### Purpose
Converts idempotency from an untested defensive mechanism into a **measured claim**. "We
implemented idempotency" is not a claim; *"duplicates injected at 1% of order flow, zero
duplicate fills across N million orders"* is.

The highest interview value per hour of anything remaining in Phase 1.

#### What Needs To Be Done
- Add a configurable duplicate-submission rate to the load harness.
- Verify after each run that duplicates produced no additional fills and no leaked reservations.

#### Success Criteria
- At 1% injection over a sustained run: zero duplicate fills and zero leaked reservations.
- The resulting figure is recorded in a form quotable in the benchmark report.

#### Boundaries / Constraints
- Do not disable deduplication "to see what happens" against the demo instance.
- Run on the scratch deployment.

#### Dependencies
Tasks 6.3, 3.2.

#### Deliverables
Duplicate injection mode · verification check · recorded result.

---
---

# Week 7 — Research and Evidence

## Weekly Objective

Ship the backtester, produce the benchmark report, harden the deployment, and walk the definition
of done literally.

## Expected Outcome

A user selects a strategy and a date range, runs a backtest, and gets a report with a buy-and-hold
comparison and a stated fill-model limitation. The benchmark report is published with its
methodology and its limits. Every checklist item is marked pass or fail with evidence.

---

### Task 7.1: Backtester

#### Tech Stack
Python, pyarrow (Parquet), numpy

#### Purpose
The research half of the project, and what makes the "Quant" in Quant Arena honest by 15 October.

Two design points carry more weight than they appear to. **Bars are fed one at a time**, so
lookahead bias is prevented *structurally* rather than by documentation — lookahead is the most
common way a backtest becomes silently wrong. And the **buy-and-hold comparison is mandatory**,
because a strategy returning 8% in a market that returned 20% has lost money in the only sense
that matters.

#### What Needs To Be Done
- Build the bar loader over the pinned crypto dataset.
- Build the runner, feeding bars **one at a time** to the strategy.
- Implement the **controlled Python strategy interface**: plain data in (current bar, own
  portfolio), plain data out (zero or more order records), no I/O in the contract, and **no
  reference to the engine, the adapter or the dataset**.
- Implement fill simulation at the **next bar's open**.
- Compute metrics: P&L, return %, trade count, win rate, maximum drawdown, volatility, Sharpe,
  and the buy-and-hold comparison.
- Implement one built-in strategy: SMA crossover.
- Record a **run manifest**: strategy and parameters, dataset and date range, configuration hash,
  engine version, seed.
- Add the reproducibility test — run twice, assert byte-identical — to the per-commit suite.

#### Success Criteria
- A backtest runs and produces every metric plus the buy-and-hold comparison.
- Two runs with the same manifest produce byte-identical results.
- The strategy provably cannot see future bars, enforced by the interface rather than convention.
- The fill-model limitation is printed in the report output, not buried.
- Maker/taker fees are applied, so frequently-trading strategies are penalised realistically.

#### Boundaries / Constraints
- **No engine-based fill simulation.** Phase 1 fills at the next bar's open; reconstructing books
  and matching through the real engine is Phase 2, where it arrives as a before/after comparison.
- **One** built-in strategy, not three.
- A metrics table, not equity-curve or drawdown charts.
- No user-submitted code and no sandbox.
- The strategy receives plain data only — passing it a live portfolio object or an engine handle
  would convert the Phase 2 sandbox from serialisation work into a redesign.

#### Dependencies
Task 6.2.

#### Deliverables
Backtest runner · controlled strategy interface · SMA crossover strategy · metrics module · run
manifest · reproducibility test.

---

### Task 7.2: Backtest Screen

#### Tech Stack
TypeScript, React

#### Purpose
Makes the backtester usable and demonstrable — beat 7 of the walkthrough.

#### What Needs To Be Done
- Build the form: strategy dropdown, symbol, date range, run button.
- Render results as a metrics table including the buy-and-hold comparison.
- Display the fill-model limitation on the results page.

#### Success Criteria
- A user runs a backtest from the interface and sees results.
- The limitation text is visible on the page, not hidden in documentation.

#### Boundaries / Constraints
- Metrics table only — no equity-curve or drawdown charts.
- No strategy editor and no parameter tuning interface.
- This is the third and final screen.

#### Dependencies
Tasks 7.1, 6.1.

#### Deliverables
Backtest screen · results view.

---

### Task 7.3: Integration Tests (T5, thinned)

#### Tech Stack
pytest, httpx

#### Purpose
Catches the wiring, serialisation and authentication faults that unit and property layers
structurally cannot see, because those layers deliberately run without a network or a database.

Deliberately thinned to the critical path — T1 through T4 carry the correctness argument.

#### What Needs To Be Done
- Test the critical path through the real API: register → receive capital → submit order → fill →
  portfolio reflects it.
- Test cancellation through the API.
- Test a duplicate submission through the API.
- Test a risk rejection through the API.

#### Success Criteria
- The critical path passes against a running stack.
- A failure points clearly at the layer responsible.

#### Boundaries / Constraints
- Critical path only — this layer is deliberately thin.
- Do not duplicate what T1–T4 already cover.
- No browser-driven end-to-end tests.

#### Dependencies
Task 6.1.

#### Deliverables
Integration test suite.

---

### Task 7.4: Benchmark Runs, Trace Tool, and Report

#### Tech Stack
Python, matplotlib, structured logs

#### Purpose
Goal 5's deliverable. For readers of the repository, **the benchmark report is read more often
than the code**, so it is a first-class artifact rather than documentation of one.

The `trace` tool is worth noting as a decision in its own right: `README.md` asks for
distributed tracing, and the conventional answer is OpenTelemetry with a collector and a backend.
That is unnecessary here, because **the event stream already is the trace** — every request
carries a `client_order_id` and a sequence number, so reconstructing an order's path is a query.

#### What Needs To Be Done
- Run **B2** end-to-end benchmarks on the **scratch deployment**.
- Measure connection scaling — clients supported before update delay degrades.
- Measure **market quality under load**: spread, depth, and market-maker obligation compliance as
  throughput rises.
- Build `trace <client_order_id>`, reconstructing an order's full path with per-hop timings.
- Generate plots from the archived stream and structured logs into PNG.
- Write the report: methodology including how coordinated omission was avoided, hardware,
  software versions, configuration hash, results as distributions, the measured latency budget,
  bottleneck analysis, at least one before/after optimisation, and **limitations**.

#### Success Criteria
- B1 and B2 are reported separately, clearly labelled, with full distributions.
- The measured latency budget is published and shows the two counter-intuitive findings: matching
  is a negligible fraction of end-to-end latency, and the 20 Hz conflation window dominates it.
- `trace <client_order_id>` prints a full path with per-hop timings.
- At least one optimisation is documented with numbers on both sides.
- The report states what was **not** tested and what the numbers do not support.

#### Boundaries / Constraints
- **Never conflate B1 and B2.**
- Never report averages.
- No Prometheus or Grafana — logs plus offline plots.
- Run on the scratch deployment, not the demo instance.
- Do not claim anything that was not measured.

#### Dependencies
Tasks 6.3, 6.4, 5.3.

#### Deliverables
B2 and connection-scaling results · market-quality measurements · `trace` tool · plots ·
benchmark report.

---

### Task 7.5: Deployment Hardening and Definition-of-Done Pass

#### Tech Stack
Docker, GitHub Actions

#### Purpose
Converts "it works on the demo instance" into something shippable, and forces the honest final
assessment. The definition of done is worth having only if it is applied **literally** — an
unchecked item on 15 October is a miss to state plainly, not to reinterpret.

#### What Needs To Be Done
- Verify restart policies and health checks under induced failure.
- Set up backups for the read model.
- Write the operations runbook.
- Walk the definition-of-done checklist item by item, recording pass or fail **with evidence**.
- Write the README: architecture, the **one project + separate processes + one coherent event
  flow** framing, the "why not" decisions, the demo GIF, and a link to the benchmark report.

#### Success Criteria
- Every checklist item is marked pass or fail with evidence attached.
- Misses are stated plainly in the README rather than reinterpreted.
- The README explains what each component solves and why the architecture is shaped as it is.
- A reader can go from the README to a running local stack in under fifteen minutes.

#### Boundaries / Constraints
- **Do not reinterpret a failed checklist item as passed.**
- No new features in week 7.
- Do not begin Phase 2 work, however tempting the slack looks.

#### Dependencies
Tasks 7.4, 7.2, 7.3.

#### Deliverables
Hardened deployment · backups · runbook · completed definition-of-done checklist · README.

---
---

# Appendix A — Phase 2 and Phase 3

Phase 1 is committed. These are not scheduled week by week, because their scope and timing are
not yet fixed — but every remaining component of `README.md` appears here, so nothing is
unrepresented.

## Phase 2 — Depth (November–December)

> **Headline: we measured, then improved — and here are the numbers on both sides.**

Ordered by value:

| # | Work | Why it is Phase 2 |
|---|---|---|
| 1 | **Engine-based backtest fills**, published as a before/after against Phase 1 | Arrives as a *comparison* rather than a claim, and is cheaper once the archive, adapter and test suite exist |
| 2 | **User strategies** — restricted DSL first, then sandboxed Python on OS limits | The whole of `README.md` Goal 4. Phase 1's controlled interface makes this serialisation work, not redesign |
| 3 | **Competitions**, ranked by risk-adjusted return | Depends on (2); this is also where email verification becomes meaningful |
| 4 | Additional strategies; equity-curve and drawdown charts | Depth on a working foundation |
| 5 | Momentum and value bots | Microstructure realism; real price data already supplies macro structure |
| 6 | **Analytics as a separate service** | The one legitimate microservice boundary — read-only, stream-consuming, its own database |
| 7 | Margin accounts and retail short selling | Requires a real risk engine; the natural sequel to Phase 1's cash accounts |
| 8 | Delta encoding and a binary protocol | Each justified by a bottleneck actually observed in the Phase 1 report |
| 9 | **Checkpointing and snapshots**, with a recovery-time before/after | The slow recovery number from Task 4.2 is the baseline |
| 10 | The archive restored to an authoritative role | Once own-market history is needed for backtesting |

## Phase 3 — Last Improvements

Genuinely optional; each self-contained with a publishable result.

| # | Work |
|---|---|
| 1 | **Memory-mapped log replacing Redis Streams**, with before/after latency numbers — analysed and costed in Open Issue 009 §8 |
| 2 | Engine sharding by symbol across threads or processes |
| 3 | Multiple gateways behind a dedicated sequencer |
| 4 | L3 order-by-order market data feed |
| 5 | Market-impact modelling in backtests |
| 6 | Multi-region and high availability |

## Never

Real money. Integration with a real exchange.

---

# Appendix B — Coverage Map

Every problem and goal in `README.md`, traced to where it is addressed.

## The nine problems (`README.md` §2)

| # | Problem | Addressed by |
|---|---|---|
| 1 | Matching buyers and sellers | 1.2, 2.4, 3.4 |
| 2 | Maintaining a correct order book | 2.4, 4.1 (invariants I1–I6) |
| 3 | Processing events in the correct order | 2.1 (stream ID as sequence), 4.2 |
| 4 | Preventing duplicate processing | 3.2, 6.4 |
| 5 | Updating users in real time | 5.2, 5.4, 6.1 |
| 6 | Separating critical from non-critical work | 5.2, 6.2 — fan-out and archiver consume the stream and cannot slow the trading path |
| 7 | Quantitative strategy testing | 7.1, 7.2 |
| 8 | Measuring risk | 3.1 (pre-trade), 7.1 (drawdown, volatility, Sharpe) |
| 9 | Recovering from failures | 2.2, 4.2, 5.3 |

## The six goals (`README.md` §4)

| # | Goal | Addressed by | Success evidence |
|---|---|---|---|
| 1 | Correct exchange core | 2.4, 3.4, 4.1 | Two implementations byte-identical across 10k+ generated sequences; invariants I1–I7 |
| 2 | Real-time user experience | 5.2, 5.4, 6.1 | Place an order, see the result without refreshing |
| 3 | Quantitative research and backtesting | 7.1, 7.2 | Same manifest yields byte-identical results |
| 4 | Safe strategy execution | **Phase 2** — constraint honoured in 7.1 | Phase 1 interface is already sandbox-shaped |
| 5 | Measure and improve performance | 4.3, 6.3, 7.4 | B1 and B2 with distributions, methodology, and stated limits |
| 6 | Evolve toward production | 1.4, 3.3, 7.5 | Deployed, CI green, runbook, checklist walked literally |

## Definition of success (`README.md` §6)

The ten product-success steps map to weeks 1, 3, 5, 6 and 7. The engineering-success criteria —
correctness, real-time behaviour, quantitative capability, performance understanding, reliability,
production engineering — map to tasks 4.1, 5.2, 7.1, 7.4, 5.3 and 7.5 respectively.

**Item 9** ("eventually test a trading strategy") is satisfied in Phase 1 by the built-in
strategy; user-written strategies are Phase 2 by explicit decision.

---

# Appendix C — The Five-Minute Demonstration

Improvised on the day by decision, but the beats are known. Beat 6 is the one worth a dry run,
since it depends on three components behaving together under stress.

| # | Beat | What it proves |
|---|---|---|
| 1 | Open the app — market already moving, book updating, tape scrolling | A live market, not a mock-up |
| 2 | Place a limit order that rests; show it in the book | The book is real and the order is in it |
| 3 | Place a crossing order; show the partial fill and the portfolio updating live | The full path works end to end |
| 4 | Cancel the remainder | Cancellation and race handling |
| 5 | Run the load generator; throughput climbs and the spread visibly widens | Behaviour under load, and market quality degrading measurably |
| 6 | **Kill the engine mid-load; show it recover with no lost fills** | Reliability — the strongest single moment |
| 7 | Run a backtest; show the metrics and the buy-and-hold comparison | The research half is real |

---

# Appendix D — Parallel Work Plan

How the two developers work without ever waiting on each other.

## D.1 The blocking problem in the original split

Tracing every dependency across the original assignment found **four places where one developer
would sit idle waiting for the other**:

| Where | Problem | Severity |
|---|---|---|
| Week 1 | Dev B's gateway (1.3) depended on Dev A's schema **and** naive model | **Hard block on day one** |
| Week 5 | Dev A's frontend (5.4) depended on Dev B's fan-out (5.2) — **same week** | **Hard block** |
| Week 4→5 | Fan-out was listed as depending on the C++ engine process | Serialised two weeks unnecessarily |
| Week 6→7 | Dev A's backtester (7.1) depended on Dev B's archiver (6.2) | Soft — different weeks |

### One of these was a mistake in the plan, not a scheduling problem

Task 5.2 listed its dependency as Task 4.2, the C++ engine process. **That is wrong.** Fan-out
consumes the *outbound stream*; it does not care which engine produced the events. The outbound
stream exists from week 2, with the naive model writing to it.

**Corrected: 5.2 depends on 2.1, not 4.2.** That single correction removes a two-week
serialisation and lets fan-out start in week 4.

## D.2 The three mechanisms

### 1. The joint contract session — day one, half a day, both developers

The only work the two do in lockstep. They agree, together:

- The **event schema** — every record type and field (Task 1.1)
- The **REST surface** — paths, request shapes, response codes
- The **WebSocket message shapes** — book, tape, bars, private messages

Nothing else in the seven weeks requires both people at once. Everything downstream is written
against what is agreed here.

### 2. Stub first, integrate later

Where a component's upstream does not exist yet, it is built against a stub agreed in the
contract session. **There are exactly three**, and each costs about an hour:

| Stub | Built by | Replaces | Swapped at |
|---|---|---|---|
| **Stub engine** — accepts an order, returns canned fill events | Dev B, week 1 | The naive model, then the C++ engine | End of week 2 |
| **Mock WebSocket server** — replays a recorded or synthetic message file at 20 Hz | Dev A, week 5 | The real fan-out process | End of week 5 |
| **Sample Parquet files** — a few hundred hand-made bars in the agreed layout | Dev A, week 7 | The archiver's output | Start of week 7 |

The mock WebSocket server is the highest-value of the three. It removes the worst block in the
plan, and it keeps paying afterwards: frontend work no longer needs the whole stack running, and
edge cases like a slow feed or a sequence gap can be reproduced on demand rather than waited for.

### 3. Integration points

Short joint sessions, roughly two hours, where a stub is swapped for the real thing:

| When | What is connected |
|---|---|
| End of week 2 | Gateway stops calling the stub engine and writes to the real stream; naive model consumes it |
| End of week 5 | Frontend points at the real fan-out; the C++ engine process replaces the naive model |
| Start of week 7 | Backtester reads real archived Parquet instead of samples |

## D.3 Week-by-week assignment

Every cross-developer arrow points to a **previous** week. Hours are indicative.

| Wk | Dev A — exchange core | h | Dev B — platform | h |
|---|---|---|---|---|
| **1** | **[JOINT] 1.1 contracts** · 1.2 naive model · 2.3 scenarios | 18 | **[JOINT] 1.1 contracts** · 1.3 gateway on a stub engine · 1.4 docker stack | 29 |
| **2** | 2.4 C++ engine — book and match loop | 24 | 2.1 Redis streams · 2.2 ledger | 30 |
| **3** | 3.4 engine complete + nanobind · 3.3 deploy and CI | 25 | 3.1 risk and reservations · 3.2 idempotency | 27 |
| **4** | 4.1 differential, property and determinism tests | 24 | 4.4 bots · 5.2 fan-out (starts) | 30 |
| **5** | 4.2 engine process · 4.3 native benchmark · 5.4 frontend on mock WS | 26 | 5.2 fan-out (finishes) · 5.1 crypto prices and ten symbols | 26 |
| **6** | 6.1 trading screen | 30 | 6.2 archiver · 6.3 load generator · 6.4 duplicates · 5.3 recovery script | 27 |
| **7** | 7.1 backtester · 7.2 backtest screen · 7.3 integration tests | 25 | 7.4 benchmarks, trace tool, report · 7.5 hardening and definition of done | 27 |
| | **Total** | **172** | **Total** | **196** |

### What moved, and why

| Change | Reason |
|---|---|
| **1.1 becomes a joint task** | It is the contract between the two developers. Writing it together costs half a day and removes the week-1 block entirely |
| **2.3 scenarios pulled into week 1** | Dev A has slack in week 1, and the scenarios verify the model — which must be right before anything is measured against it |
| **3.3 deployment moves to Dev A** | Dev B is at capacity in week 3 with risk and idempotency; Dev A has room. Its dependencies (1.4, 2.2) are both from earlier weeks |
| **5.2 fan-out moves to week 4** | Follows from the corrected dependency in D.1 — it needs the stream, not the C++ engine |
| **5.4 frontend moves to week 5 on a mock server** | Removes the week-5 block, and flattens Dev A's peak, which previously put 54 hours into a single week |
| **5.3 recovery script moves to Dev B, week 6** | Its dependencies (4.2, 4.4) are both complete by then, and Dev A is fully occupied with the trading screen |

### Balance

Dev A carries 172 hours against Dev B's 196. The gap is deliberate: Dev A owns the C++ engine
and the differential testing programme, which carry the steepest learning curve and the widest
estimate uncertainty in the project.

## D.4 The rule to check against

> **No task may depend on a task assigned to the other developer in the same week.**

This is checkable. Before moving a task between weeks or developers, verify it still holds. If a
change would violate it, the fix is almost always a stub and an integration point, not a
reordering.

## D.5 What to do when someone finishes early

Idle time is more likely than blocking under this plan, so it is worth saying what to do with it.

**Do not start next week's task early** if it has an unmet cross-developer dependency — that
recreates the coupling this appendix exists to remove. Instead, in order of value:

1. **Extend the regression corpus.** Every generated failing case saved is a permanent test.
2. **Write the README section** for what you just built, while the reasoning is fresh.
3. **Improve a stub.** A mock WebSocket server that can simulate a slow client or a sequence gap
   makes week 6 much easier.
4. **Pair on the other developer's task.** This is the only situation where working on the other
   track is a good idea — as a second pair of eyes, not as a parallel owner.
