# Open Issue 019 — Technology Stack

**Status:** RESOLVED (2026-08-28) — all ten open choices adopted as recommended
**Opened:** 2026-08-28
**Sources:** every decision recorded in Open Issues 001–018
**Owner:** _unassigned_

---

## 1. Purpose

A single reference listing every technology Phase 1 depends on, with the issue that decided it.
Anything not traceable to a decision is marked open in §3 rather than assumed.

The governing constraint from `README.md` §5 applies to every line: **each technology must
answer a real problem.** Where something conventional is deliberately absent — Kafka,
OpenTelemetry, Prometheus, Kubernetes, an ORM on the hot path — that absence is a decision, and
§4 records it.

---

## 2. Decided

### 2.1 Matching engine

| Technology | Role | Decided in |
|---|---|---|
| **C++** | Matching engine as a static library with no I/O — roughly 700–1000 lines | OI 002 |
| **CMake** | Build system for the C++ library and both adapters | OI 002 §2 |
| **nanobind** | In-process Python binding for tests and the backtester. Chosen over pybind11 for roughly 10× lower call overhead, which matters when the backtester calls the engine in a tight loop | OI 002 §5 |
| **hiredis** or **redis-plus-plus** | Redis client for the standalone engine process (`XREAD BLOCK`, `XADD`) | OI 003 §8.3 |

### 2.2 Backend platform

| Technology | Role | Decided in |
|---|---|---|
| **Python** | Gateway, ledger, fan-out, bots, archiver, backtester, tooling | OI 002 |
| **Redis** | Durable ordered event streams; sessions; idempotency keys | OI 003 §12, OI 015, OI 008 |
| **redis-py** | Async Redis client for the Python processes | OI 003 |
| **Relational database** | Derived read model — account pages, history, charts, warm start. **Never authoritative** | OI 004 §12 |
| **argon2-cffi** | Password hashing (Argon2id, OWASP parameters) | OI 015 §3 |

### 2.3 Frontend

| Technology | Role | Decided in |
|---|---|---|
| **React** | Three screens: trading, auth, backtest | OI 014 §4 |
| **TradingView Lightweight Charts** | Candlestick chart; ~45 KB, built for streaming financial data | OI 014 §5 |
| **WebSocket** | Market data and the private user stream | OI 006 §3 |

### 2.4 Testing

| Technology | Role | Decided in |
|---|---|---|
| **pytest** | Python test runner for all six layers | OI 010 |
| **Hypothesis** | Property-based generation for T2 and the T3 differential harness | OI 010 §3 |
| **Naive Python model** | Not a library — a ~200-line executable specification kept permanently as the test oracle | OI 002 Option D |

### 2.5 Data

| Technology | Role | Decided in |
|---|---|---|
| **Binance public API** | Historical crypto data driving fair value, and the Phase 1 backtest dataset. Free, no key for public data, no licensing obstacle, 1-minute and tick resolution, no market-hours gaps | OI 005 §10.4 |

### 2.6 Benchmarking and observability

| Technology | Role | Decided in |
|---|---|---|
| **Custom open-loop load generator** | Fixed-schedule request issuance, latency measured from *intended* send time. Avoiding coordinated omission is the reason it is custom rather than off-the-shelf | OI 012 §2 |
| **HDR histogram** | Full latency distributions; p50/p95/p99/p99.9, never averages | OI 012 §2 |
| **Structured JSON logging** | Every process, carrying `client_order_id`, `order_id`, stream sequence | OI 012 §5 |
| **matplotlib** | Offline analysis plots from the archived stream, straight into the benchmark report | OI 012 §12e |
| **Custom `trace` tool** | Reconstructs an order's full path with per-hop timings by querying the stream | OI 012 §5 |

### 2.7 Schema and infrastructure

| Technology | Role | Decided in |
|---|---|---|
| **Custom codegen** | One definition file generating a C++ packed-struct header and a Python module. Roughly 50 lines of generator; chosen over Protobuf because records are fixed-width integers with no strings or nesting | OI 016 §3 |
| **Docker + docker-compose** | All processes; health checks; restart policies. One `up` locally and in production | OI 007 §4 |

---

## 3. Still open — with a recommendation for each

None of these were ever decided. Each is listed with what I would pick and why.

| # | Choice | Recommendation | Reasoning |
|---|---|---|---|
| **19a** | **Python web framework** | **FastAPI** | Native async, first-class WebSocket support, Pydantic validation that maps directly onto the OI 016 schema, and automatic OpenAPI docs. The gateway is an async event loop by design (OI 001, 2b), which rules out Flask and Django |
| **19b** | **ASGI server** | **uvicorn** | The standard pairing with FastAPI |
| **19c** | **Data access layer (Layer C)** | **SQLModel**, or raw `asyncpg` | Explicitly deferred in OI 003 §4 until the schema is written. The single standing constraint is unchanged: **no ORM on any hot path**, and bulk writes into the read model use bulk/`COPY` |
| **19d** | **Relational database** | **PostgreSQL** | Assumed throughout but never stated. Nothing depends on the choice, since it holds only derived state |
| **19e** | **C++ standard** | **C++20** | `std::atomic` with explicit memory ordering, designated initialisers, `<span>`, concepts. C++17 is an acceptable fallback if the toolchain resists |
| **19f** | **C++ test framework** | **Catch2** | Header-only, minimal setup. Most engine testing happens through the Python differential harness, so this covers only unit tests local to the C++ |
| **19g** | **Frontend language and build** | **TypeScript + Vite** | TypeScript pays for itself where the WebSocket message schema is concerned — the OI 016 record types become checked types rather than assumptions. Vite is the low-friction default |
| **19h** | **Archive format** | **Parquet files** | OI 011 §11b left this as "Postgres or Parquet". Parquet is better suited: columnar, compressed, read efficiently by the backtester, and it keeps bulk history out of the read-model database |
| **19i** | **CI** | **GitHub Actions** | Assumed but never stated. Runs the per-commit fixed suite; nightly workflow for generated tests |
| **19j** | **Deployment target** | *Deferred by decision* | OI 007 sub-decision 8b, deferred to late Phase 1. No co-location constraint under S2, so a single VM and a managed platform are both viable |

---

## 4. Deliberately absent

Each of these is a decision with a reason, not an omission.

| Not used | Why | Recorded in |
|---|---|---|
| **Kafka** | Its core data structure — a segmented append-only log with per-consumer offsets — is what Redis Streams provides. Kafka adds a broker process, a network hop and operational weight to solve problems this system does not have: one producer, one machine | OI 003 §7 |
| **Protobuf / FlatBuffers / Cap'n Proto** | Variable-width wire formats, so records are not fixed-size POD. The records are already fixed-width integers with no strings or nesting, which is precisely where a framework earns nothing | OI 016 §3 |
| **OpenTelemetry / Jaeger** | The event stream already is the trace. Every request carries a `client_order_id` and a sequence number, so reconstructing an order's path is a query, not a separate system. ~4 hours instead of ~15, and better suited — it traces the actual ordered record rather than sampled spans | OI 012 §5 |
| **Prometheus / Grafana** | Replaced by structured logs plus offline analysis. Audience (a) reads the report, never a live dashboard, and plots generated from the archived stream can be regenerated from any recorded run | OI 012 §12e |
| **Kubernetes** | Excluded by `README.md` §1; weeks of work for two developers; solves no problem this system has | OI 007 §4 |
| **JWT** | Session cookies are correct for a single-backend application. JWT solves stateless verification across independent services, which this system does not have, and `localStorage` storage is readable by any XSS | OI 015 §2 |
| **An ORM on the hot path** | Standing constraint. The read model may use one; nothing on the matching or submit path may | OI 003 §4 |
| **RestrictedPython** | Rejected in principle, not deferred. Object-graph traversal reliably reaches back to builtins, so it looks like protection without being protection | OI 017 §3 |

---

## 5. Questions to resolve

1. **19a–19i** — eight choices, each with a recommendation above. Most are conventional; **19c
   (data access) and 19h (archive format)** are the two worth actual thought.
2. Is there anything in §4 that should be reconsidered? Each absence is defensible, but each is
   also a thing an interviewer may expect to see and ask about.

---

## 6. Decision log

| Date | Status | Note |
|---|---|---|
| 2026-08-28 | OPEN | Stack compiled from OI 001–018. Twenty-two technologies traced to decisions; ten choices identified as never decided, each with a recommendation. |

---

## 7. Resolved 2026-08-28 — all ten recommendations adopted

Every choice listed as open in §3 is adopted as recommended, during the planning of
`WEEKLY_PLAN.md`.

| # | Choice | Adopted |
|---|---|---|
| 19a | Python web framework | **FastAPI** |
| 19b | ASGI server | **uvicorn** |
| 19c | Data access layer | **SQLModel** — no ORM on any hot path; bulk writes into the read model |
| 19d | Relational database | **PostgreSQL** |
| 19e | C++ standard | **C++20** |
| 19f | C++ test framework | **Catch2** |
| 19g | Frontend language and build | **TypeScript + Vite** |
| 19h | Archive format | **Parquet** (pyarrow) |
| 19i | CI | **GitHub Actions** |
| 19j | Deployment target | **Still deferred** — decided in `WEEKLY_PLAN.md` Task 3.3, week 3. No co-location constraint under S2, so a single VM and a managed platform are both viable |

Two of these were flagged in §5 as worth genuine thought rather than convention:

- **19c (data access).** SQLModel is taken because it pairs naturally with FastAPI and Pydantic,
  and because the read model is the only place it is used. The standing constraint is unchanged
  and is the part that matters: **no ORM on any hot path**, and bulk writes into the read model
  use bulk or `COPY` paths regardless.
- **19h (archive format).** Parquet over PostgreSQL because the archive is bulk history read
  sequentially by the backtester, not queried transactionally. Columnar and compressed suits that
  access pattern, and it keeps hundreds of megabytes per day out of the read-model database.

### 7.1 Where the stack is now applied

`WEEKLY_PLAN.md` names concrete technologies per task, traceable to this issue. Nothing in that
plan introduces a technology absent from §2 or §7 here; anything that does is a scope change to
be raised against this issue.

## 8. Decision log

| Date | Status | Note |
|---|---|---|
| 2026-08-28 | OPEN | Stack compiled from OI 001–018; ten choices identified as never decided |
| 2026-08-28 | **RESOLVED** | All ten adopted as recommended. 19j (deployment target) remains deferred by prior decision to week 3 of the execution plan. |
