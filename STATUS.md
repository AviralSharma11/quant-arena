# Status — Quant Arena, Dev B

**Last updated:** 2026-08-31 · **HEAD** `44035c8` · **Week 3** (11–17 Sep) — Task 3.1 complete
**State:** 325 standalone tests pass (all suites green). 3.1 risk checks verified and merged.
**Contracts (1.1):** **FROZEN 2026-08-31**, agreed by both developers. `contracts/v1/`.

> This file is **Dev B's**. Dev A's rows are reported by me, never inferred from the repo.
> Update protocol is in `CLAUDE.md` — propose a diff, wait for confirmation, never write unasked.

---

## NOW

**Task 3.2 · Idempotency — atomic claim-and-append** — Dev B, week 3

- **Step:** require `client_order_id` on every submission · write Lua script for atomic
  claim-and-append · implement validate → reserve → claim-and-append ordering.
- **Files:** `services/gateway/routes_orders.py`, `services/gateway/idempotency.py` (new).
- **Done when:** same `client_order_id` twice produces one order; second returns stored outcome ·
  no reservation leak on retry · duplicate submission with rejection returns identical answer.
  (`WEEKLY_PLAN.md` task 3.2, Success Criteria 1–4.)

*If NOW is empty or stale, ask. Do not pick a task yourself.*

## Then next

1. **3.2** Idempotency — atomic claim-and-append (Dev B, wk 3)
2. **3.3** Public deployment and CI (Dev B, wk 3)

## Blocked / waiting

- **On Dev A:** nothing.
  *(End of week 2 is the first integration point: the stub engine is swapped for the real
  stream and Dev A's naive model consumes it. Appendix D.2.)*
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
| 1.3  Gateway skeleton + stub engine | B | 1 | done | `4c09dfa` · 52 gateway tests pass · real Redis + Postgres |
| 1.4  Local Docker stack + shared config | B | 1 | done | `84c270e` · verified from a fresh clone, 34s to all-healthy |
| 5.4a Frontend scaffold (Vite/TS/React) | B | 1 | done | `94fbe04` · 10 tests · builds, serves, 3 routes |
| 2.4  C++ engine — order book and match loop | A | 2 | A:unknown | — |
| 2.1  Redis Streams, durability, halt state | B | 2 | done | `8fd72bc` · 359 tests pass · durability 72k/s measured |
| 2.2  Ledger writer + replay rebuild | B | 2 | done | `2948867` · 11 ledger tests pass · replay rebuild and maker/taker fees verified |
| 5.4b Auth screens | B | 2 | done | `bd321b3` · login, registration, and session rehydration verified · 302 tests pass |
| 3.4  C++ engine complete + nanobind | A | 3 | A:unknown | — |
| 3.1  Risk checks + in-memory reservations | B | 3 | done | `44035c8` · 24 gateway order tests pass · available_cash check and reservation verified |
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

## This week — week 2 (4–10 Sep)

Dev B's tasks with their Success Criteria as a live checklist. Deleted when the week closes.

**2.1 Redis Streams, durability, halt state** — *unblocks Dev A 4.2, must not slip*
- [x] An order is `XADD`ed and read back with a monotonically increasing stream ID
- [x] Sustained `XADD` throughput measured and recorded, with the `appendfsync` setting and the
      reasoning beside it
- [x] `XREAD COUNT n` returns genuine batches under load; per-record cost recorded at 1, 10, 100
- [x] Stopping Redis produces a visible halt state that rejects orders with a reason
- [x] Restarting Redis clears the halt state without a gateway restart

**2.2 Ledger writer and replay rebuild**
- [x] A fill moves both counterparties' cash and positions correctly
- [x] Fees land in the house account; user cash + reservations + fee account is constant
      except at deposits
- [x] Killing the ledger and restarting reproduces byte-identical balances by replay
- [x] No balance is ever written to PostgreSQL from any source other than the stream

**5.4b Auth screens**
- [x] Login works and the session survives a page reload

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
| 2026-08-31 | `INITIAL_CASH_TICKS` in `config/settings.py`, default 1,000,000 ticks | The grant amount is specified nowhere in the plan or the 19 open issues; 4.4's bots will need their own figure and this is where it goes | — |
| 2026-08-31 | Gateway maps `RequestValidationError` to `400` | FastAPI's default is `422`; OI 008 §9h and 1.3 criterion 3 both require `400`. Without the handler the criterion fails silently | — |
| 2026-08-31 | Money and timestamp columns declared `BIGINT` explicitly | A bare SQLModel `int` is a 32-bit `INTEGER` and holds neither an int64 tick amount nor a nanosecond timestamp | — |
| 2026-08-31 | **Configuration and infrastructure are split.** Domain parameters live in `config/quant_arena.toml` and nowhere else — no env override, no code default, a missing value raises. Connection URLs, secrets and cookie `Secure` come from the environment and are **not** hashed | 1.4's boundary ("one file is the point") cannot be absolute: the gateway reaches PostgreSQL at `postgres` in Docker and `localhost` on a laptop, and a password must not be version controlled. More importantly, a connection URL inside the hash would change it when the *configuration* had not — destroying the one question the hash answers | interprets 1.4's boundary; OI 007 §8d |
| 2026-08-31 | `[symbols]` left empty in the config file | The ten symbols and tick sizes are Task 5.1's to decide from replayed crypto history. An invented list would look settled without being so | — |
| 2026-08-31 | Config hash goes to the **log** in 1.4, and to the stream in 2.1 | OI 007 §8d wants it stamped into the event stream; there is no stream until 2.1 | stages OI 007 §8d |
| 2026-08-31 | `react-router-dom` added to the closed stack list | React ships no router and the three screens need one; the ~40-line hand-rolled alternative was weighed and rejected because 5.4b also needs a protected-route wrapper | **amends `CLAUDE.md` and OI 019** — applied in `3df522c` |
| 2026-08-31 | No JavaScript test framework | Node 24 strips TypeScript natively, so the route table is inspected and the built app served from the existing pytest suite. Keeps the closed list one dependency wider, not three | — |
| 2026-08-31 | Frontend stays out of `docker-compose.yml` | 1.4 is closed and deployment is 3.3's. The Vite dev server runs on the host against the containerised gateway | — |

## Deviations from the plan

Task moved weeks · scope cut · estimate blown · criterion waived — with the reason. Distinct from
decisions: a schedule deviation is not a design change.

- **1.1's joint session (Appendix D.2) happened as async review, not a half-day together.**
  Dev B wrote the full contract as a proposal; Dev A reviewed and signed off the same day.
  Outcome as specified, mechanism not. No schedule impact.

## How to run it right now

```bash
docker compose up --build        # the whole system. Docs at localhost:8000/docs
docker compose logs gateway | grep config_hash

# developing, with reloads:
docker compose up -d redis postgres
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt   # first time only
.venv/bin/python -m uvicorn services.gateway.app:app --reload --port 8000

python contracts/v1/generate.py --check    # exit 1 if generated/ is stale
.venv/bin/python -m pytest -q               # 331 tests, against the compose stores

cd web && npm install && npm run dev        # frontend at localhost:5173, proxied to the gateway
```
`test_sizes.py` needs a C++20 compiler and **skips loudly** without one — a green run carrying
that skip has not verified 1.1's Success Criterion 3.

## Open questions

*(none)*

## Archive

*(one line per closed week — everything else from that week is deleted; git history is the record)*

- **Week 1 (28 Aug – 3 Sep) — closed 31 Aug, 3 days early.** 1.1 contracts frozen and merged ·
  1.3 gateway skeleton · 1.4 Docker stack and shared config · 5.4a frontend scaffold. All Dev B
  and joint criteria passed with named tests; 331 tests green. Dev A's 1.2 and 2.3 not reported.
