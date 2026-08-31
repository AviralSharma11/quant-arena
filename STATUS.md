# Status — Quant Arena, Dev B

**Last updated:** 2026-08-31 · **HEAD** `a37b49b` · **Week 1** (28 Aug – 3 Sep)
**State:** Contracts v1 frozen. On branch `task/1.1-contracts`, **not yet merged to `main`**.
**Contracts (1.1):** **FROZEN 2026-08-31**, agreed by both developers. `contracts/v1/`.

> This file is **Dev B's**. Dev A's rows are reported by me, never inferred from the repo.
> Update protocol is in `CLAUDE.md` — propose a diff, wait for confirmation, never write unasked.

---

## NOW

**Task 1.3 · Gateway skeleton — FastAPI, sessions, accounts, plus the stub engine** — Dev B, week 1

- **Step:** stand up FastAPI on uvicorn. Register/login/logout with Argon2id and Redis-backed
  session cookies. Accounts with a virtual cash grant. `POST /orders` and
  `DELETE /orders/{client_order_id}` validating against frozen `contracts/v1`. Build the stub
  engine behind a thin interface.
- **Files:** `services/gateway/`, `services/engine_stub/` — neither exists yet.
- **Done when:** a user registers, logs in and receives virtual capital · the session survives an
  app restart · `POST /orders` returns `202` with an order id, malformed returns `400` ·
  passwords are Argon2id. (`WEEKLY_PLAN.md` task 1.3, Success Criteria 1–4.)
- **Not in this step:** no risk checks or reservations (week 3) · no idempotency (week 3) ·
  no Redis Streams (week 2) · no JWT · no email verification.
- **Keep the stub engine behind a thin interface** — it is swapped at the end of week 2.

*If NOW is empty or stale, ask. Do not pick a task yourself.*

## Then next

1. **1.4** Local Docker stack and shared configuration (Dev B, wk 1)
2. **5.4a** Frontend scaffold — Vite + TypeScript + React (Dev B, wk 1)
3. **2.1** Redis Streams, durability, halt state (Dev B, wk 2) — **unblocks Dev A 4.2**

## Blocked / waiting

- **On Dev A:** nothing.
- **On a decision from me:** nothing.
- **On something external:** nothing.

*Empty is the normal state. Dev B has almost no cross-developer dependencies. A long list here
means something is wrong with the plan, not with the week.*

---

## Task board

`Wk` is the **scheduled** week from Appendix D.3 — not the chapter a task is documented under.

| Task | Own | Wk | Status | Evidence / note |
|---|---|---|---|---|
| 1.1  Contracts — schema, REST, WS shapes | AB | 1 | done | `1ced47a` built, `a37b49b` frozen · 233 tests pass |
| 1.2  Naive Python model engine | A | 1 | A:unknown | not yet reported |
| 2.3  Hand-written matching scenarios (T1) | A | 1 | A:unknown | not yet reported |
| 1.3  Gateway skeleton + stub engine | B | 1 | wip | — |
| 1.4  Local Docker stack + shared config | B | 1 | todo | — |
| 5.4a Frontend scaffold (Vite/TS/React) | B | 1 | todo | — |
| 2.4  C++ engine — order book and match loop | A | 2 | A:unknown | — |
| 2.1  Redis Streams, durability, halt state | B | 2 | todo | **unblocks Dev A 4.2** |
| 2.2  Ledger writer + replay rebuild | B | 2 | todo | — |
| 5.4b Auth screens | B | 2 | todo | — |
| 3.4  C++ engine complete + nanobind | A | 3 | A:unknown | — |
| 3.1  Risk checks + in-memory reservations | B | 3 | todo | — |
| 3.2  Idempotency — atomic claim-and-append | B | 3 | todo | **unblocks Dev A 6.4** |
| 3.3  Public deployment and CI | B | 3 | todo | deployment target decided here |
| 4.1  Differential/property/determinism (T2–T4) | A | 4 | A:unknown | — |
| 4.4  Bots — market maker and noise traders | B | 4 | todo | — |
| 5.2a Fan-out process begins | B | 4 | todo | depends 2.1, **not** 4.2 |
| 5.4c WS client, gap detection, rAF loop | B | 4 | todo | mock WS server until 5.2b |
| 4.2  Engine process, Redis, replay recovery | A | 5 | A:unknown | — |
| 4.3  Native engine benchmark (B1) | A | 5 | A:unknown | — |
| 5.3  Kill-the-engine recovery script (T6) | A | 5 | A:unknown | — |
| 5.2b Fan-out completes — conflation, WS server | B | 5 | todo | **unblocks Dev A 6.3** |
| 5.1  Crypto fair value, replay clock, 10 symbols | B | 5 | todo | — |
| 6.1a Trading screen begins | B | 5 | todo | — |
| 6.3  Open-loop load generator | A | 6 | A:unknown | — |
| 6.4  Duplicate injection in load harness | A | 6 | A:unknown | — |
| 6.1b Trading screen completes | B | 6 | todo | — |
| 6.2  Archiver | B | 6 | todo | — |
| 7.4  Benchmarks, trace tool, report | A | 7 | A:unknown | — |
| 7.1  Backtester | B | 7 | todo | — |
| 7.2  Backtest screen | B | 7 | todo | — |
| 7.3  Integration tests (T5, thinned) | B | 7 | todo | — |
| 7.5  Definition-of-done walk | AB | 7 | todo | — |

**Vocabulary.**

- Dev B and joint rows: `todo` · `wip` · `blocked` · `done`. **`done` requires a commit SHA or a
  named passing test in Evidence. No evidence, no `done`.**
- Dev A rows: `A:todo` · `A:wip` · `A:done` · `A:unknown`, always with `reported YYYY-MM-DD`.
  **Reported by me, never inferred.** Do not read the repo and conclude a Dev A task is finished
  — Dev A may push work in progress, or finish without pushing. `A:unknown` is expected.
- **Exactly one row may be `wip`**, and it must match NOW.

---

## This week — week 1 (28 Aug – 3 Sep)

Dev B's tasks with their Success Criteria as a live checklist. Deleted when the week closes.

**1.1 Contracts** *(joint)*
- [x] One definition file generates both outputs — `test_generated_is_current.py`
- [x] Round-trips with every field identical — `test_roundtrip.py`, Hypothesis, 11 records
- [x] C++ `sizeof` == Python `struct.calcsize` — `test_sizes.py`, compiles and runs the header;
      field offsets compared too, so layouts are identical and not merely the same length
- [x] Every record carries `schema_version` — `test_conventions.py`
- [x] No float, string or variable-width field — `test_conventions.py`

**1.3 Gateway skeleton**
- [ ] A user registers, logs in, and receives virtual capital
- [ ] The session survives a restart of the app process (it lives in Redis)
- [ ] `POST /orders` returns `202` with an order id; a malformed order returns `400`
- [ ] Passwords stored as Argon2id hashes — no plaintext, no general-purpose hash

**1.4 Docker stack**
- [ ] `docker compose up` comes up clean from a fresh checkout
- [ ] The gateway reaches Redis and PostgreSQL; health checks pass
- [ ] The config content hash appears in every process's startup log
- [ ] A README section documents the run procedure

**5.4a Frontend scaffold**
- [ ] Vite + TypeScript + React builds and serves
- [ ] Routing in place for the three planned screens

---

## Decisions made during the build

Append-only, one line each. **May not reverse anything in `CLAUDE.md`** — a reversal is a
`CLAUDE.md` edit plus an `open-issues/` amendment, proposed separately.

| Date | Decision | Why | Amends |
|---|---|---|---|
| 2026-08-31 | Repo layout: `contracts/` `services/` `engine/` `web/` `config/` | Every session was going to guess paths otherwise | closes a STATUS open question; belongs in `CLAUDE.md` |
| 2026-08-31 | `client_order_id` is `uint64` | OI 008 says "opaque, client-assigned"; Criterion 5 forbids strings and variable-width. An integer satisfies both | — |
| 2026-08-31 | `schema.toml` is TOML via stdlib `tomllib` | Declarative, and no dependency outside the closed stack list | — |
| 2026-08-31 | `seq` is derived from the Redis stream id on read, never authored | A producer cannot know its own id before `XADD` returns; `with_seq()` applies it and every replay re-derives it | implements OI 003, no parallel counter |
| 2026-08-31 | Consumers accept the **current** `schema_version` only | OI 016 §2 justified compatibility as "one snapshot interval", but OI 018 §13.1 removed snapshots. A breaking change during development means truncate and rebuild | corrects OI 016 §2 |
| 2026-08-31 | **Contracts v1 frozen**, both developers signed off | Everything from here is written against it; a change now needs both developers, a `schema_version` bump and a stream truncation | — |
| 2026-08-31 | `open-issues/` un-gitignored and committed (`e6f2370`) | `CLAUDE.md` cites 001–019 as the top precedence tier; untracked files cannot be that | — |

## Deviations from the plan

Task moved weeks · scope cut · estimate blown · criterion waived — with the reason. Distinct from
decisions: a schedule deviation is not a design change.

- **1.1's joint session (Appendix D.2) happened as async review, not a half-day together.**
  Dev B wrote the full contract as a proposal; Dev A reviewed and signed off the same day.
  Outcome as specified, mechanism not. No schedule impact.

## How to run it right now

```bash
python3 -m venv .venv && .venv/bin/pip install pytest hypothesis   # first time only
python contracts/v1/generate.py --check    # exit 1 if generated/ is stale
.venv/bin/python -m pytest contracts/v1/tests -q
```
`test_sizes.py` needs a C++20 compiler and **skips loudly** without one — a green run carrying
that skip has not verified Success Criterion 3. Superseded by the Docker stack in 1.4.

## Open questions

*(none)*

## Archive

*(one line per closed week — everything else from that week is deleted; git history is the record)*
