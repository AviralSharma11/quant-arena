# Status — Quant Arena, Dev B

**Last updated:** 2026-09-05 · **HEAD** `8b6aa57` · **Week 5** (25 Sep – 1 Oct) — running well ahead
**State:** Weeks 3 and 4 closed, **5.2b done**. **597 tests pass**, 1 skipped. Nine bot-session
tests error on this machine: `dmm_qaa` exists in the dev database under a forgotten password —
a data condition, not code (`docker compose down -v` clears it). Eleven commits on
`task/4.0-week4-prework`, **not yet pushed** — the whole week goes up as one pull request.
A live market runs, and fan-out now serves it: `docker compose up` includes `fanout` on :8001.
**Contracts (1.1):** **FROZEN 2026-08-31**, agreed by both developers. `contracts/v1/`.

> This file is **Dev B's**. Dev A's rows are reported by me, never inferred from the repo.
> Update protocol is in `CLAUDE.md` — propose a diff, wait for confirmation, never write unasked.

---

## NOW

**Task 5.1 · Crypto fair value, replay clock, ten symbols** — Dev B, week 5

- **Step:** replayed crypto history as the fair-value process · the replay clock at one real
  second to one simulated minute · the ten-symbol table with real tick sizes · the deterministic
  offline fallback generator.
- **Files:** `services/bots/fairvalue.py` (the seeded walk stays, for tests and offline work),
  `config/quant_arena.toml` `[symbols]` and `[replay]`, a pinned data file.
- **Done when:** ten symbols show distinct, realistically moving prices · the replay ratio
  appears in configuration *and in the stream* · the system runs fully offline from pinned data ·
  the fallback generator is deterministic with no data file · the README states the price paths
  derive from anonymised historical data.
- **Carry into it:** `[symbols]` is provisional QAA/QAB, replaced wholesale here.
  `market_data.bar_bucket_seconds` is where "a one-minute bar" stops being ambiguous.

*If NOW is empty or stale, ask. Do not pick a task yourself.*

## Then next

1. **6.1a** Trading screen begins (Dev B, wk 5)
2. **3.3** Deployment half — HTTPS and a one-command redeploy (Dev B, carried from wk 3)

## Blocked / waiting

- **On Dev A:** `naive_model.match()` prints the taker's price when the seller aggresses; the
  frozen schema requires the resting (maker) price. Reported, not fixed — Dev A's file. Task
  4.1's differential tests will disagree here until it lands.
  Also **five contract questions**, written up as `HANDOFF.md` and awaiting a reply. None blocks
  Dev B: each is implemented under a stated reading isolated to one function per side. Q2 is
  the one that matters most — `resumed` amends a frozen document. Q4 (`BookChanged`) is the one
  that could cost *Dev A* rework, so it wants an answer before 4.2 is built.
  *(Integration point 1 is passed. The next is end of week 5: the C++ engine process replaces
  the naive model. Appendix D.2 — expected to be a no-op for fan-out, which is engine-agnostic.)*
- **On a decision from me:** nothing.
- **On something external:** nothing. (The iCloud duplicate files cleared in `f729ed3`.)

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
| 2.4  C++ engine — order book and match loop | A | 2 | A:unknown | merged to main as `e4352c1`; not reported by Dev A |
| INT1 Stub engine → naive model over the stream | B | 2 | done | `8210d3a` · 21 matcher tests · live restart replayed 26, appended 0 |
| 2.1  Redis Streams, durability, halt state | B | 2 | done | `8fd72bc` · 359 tests pass · durability 72k/s measured |
| 2.2  Ledger writer + replay rebuild | B | 2 | done | `2948867` · 11 ledger tests pass · replay rebuild and maker/taker fees verified |
| 5.4b Auth screens | B | 2 | done | `bd321b3` · login, registration, and session rehydration verified · 302 tests pass |
| 3.4  C++ engine complete + nanobind | A | 3 | A:unknown | — |
| 3.1  Risk checks + in-memory reservations | B | 3 | done | `6c67e01` · sells reserve inventory not cash; `INSUFFICIENT_POSITION` now raised · 5 of 6 criteria; criterion 3 unverified, see Deviations |
| 3.2  Idempotency — atomic claim-and-append | B | 3 | done | merged `6213651`, defects fixed `fb2cd8a` · 6 of 6 criteria · concurrent burst now yields exactly one order · **unblocks Dev A 6.4** |
| 3.3  Public deployment and CI | B | 3 | todo | **half merged** `5efe462` — CI gate, nightly, multi-arch images. Criteria 1 and 3 (HTTPS, one-command redeploy) outstanding; see Deviations |
| 4.1  Differential/property/determinism (T2–T4) | A | 4 | A:unknown | — |
| 4.4  Bots — market maker and noise traders | B | 4 | done | `68c7821` · 43 tests · live: 130 fills in 45s, both makers meeting their uptime obligation, units conserved at 0 per symbol |
| 5.2a Fan-out process begins | B | 4 | done | `0eb5329` · 38 tests · derived book matches the matcher order-for-order across a 400-record sequence; live 706 records recovered |
| 5.4c WS client, gap detection, rAF loop | B | 4 | done | `78ed4dc` · 26 tests · reconnect re-subscribes, a deliberate private gap triggers exactly one resync; criterion 5 mechanism-verified, profiler check manual |
| 4.2  Engine process, Redis, replay recovery | A | 5 | A:unknown | — |
| 4.3  Native engine benchmark (B1) | A | 5 | A:unknown | — |
| 5.3  Kill-the-engine recovery script (T6) | A | 5 | A:unknown | — |
| 5.2b Fan-out completes — conflation, WS server | B | 5 | done | `1c87a13` · 37 tests · 5 of 5 criteria · 400 encodes at 1, 50 and 200 clients; ack median 3.50→3.65 ms · **unblocks Dev A 6.3** |
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

## This week — week 5 (25 Sep – 1 Oct)

Dev B's tasks with their Success Criteria as a live checklist. Deleted when the week closes.

**5.1 Crypto fair value, replay clock, ten symbols**
- [ ] Ten symbols show distinct, realistically moving prices
- [ ] The replay ratio appears in configuration and in the stream
- [ ] The system runs fully offline from pinned data
- [ ] The fallback generator produces a deterministic path with no data file present
- [ ] The README states that price paths derive from anonymised historical data

**6.1a Trading screen begins** — no criteria of its own; 6.1b carries them.

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
| 2026-09-02 | Recovery counts **anchors** — one outbound record per inbound record — instead of a checkpoint | A replay that re-appended its outbound records would duplicate fills that moved real positions. No snapshot (OI 018 §13.1) and no field added to a frozen schema | implements OI 018 §13.1 |
| 2026-09-02 | The matcher holds **one book per `symbol_id`** | `naive_model.OrderBook.match()` crosses on price alone; a single book would trade symbol 3 against symbol 7 | — |
| 2026-09-02 | The adapter emits the **maker's** price, discarding `naive_model`'s | `schema.toml` says `Fill.price_ticks` is always the resting price, and contracts v1 is frozen | flags a defect in Dev A's 1.2 |
| 2026-09-02 | `StubEngine`, `engine_port.py` and the `Engine` dependency deleted | The seam CLAUDE.md asked to keep behind a thin interface is now the stream itself | closes the one load-bearing stub, Appendix D.2 |
| 2026-09-02 | One image for gateway and matcher; `engine/` un-ignored in `.dockerignore` | They differ only in their command, so one image means they cannot drift onto different dependency sets. `engine/cpp` stays excluded — nothing in the image compiles it | — |
| 2026-09-02 | `IdempotencyStore.claim()` reports **which caller won the key** | The Lua claim was always atomic, but the route collapsed "I claimed it" and "someone else holds it, in flight" into one status and submitted in both cases. That distinction is the whole of Success Criterion 3.2.2 | — |
| 2026-09-02 | The risk replay **completes during startup**, before the first request is served | Starting the watcher and yielding leaves a window in which the gateway answers with every commitment forgotten | implements 3.1 criterion 4 |
| 2026-09-02 | A reservation is released at the **limit** price, never the fill price | It was taken at the limit, so releasing at the fill strands the price improvement for the life of the process | implements 3.1 criterion 2 |
| 2026-09-02 | Market-order band is `limits.market_order_band_bps`, 500 bps | 3.1 names `best_ask × 1.05`. It is the only thing between a market order and a thin book, so it is configuration, not a constant. Per-symbol bands belong with the symbol table in 5.1 | — |
| 2026-09-02 | Top of book is **derived** from the resting orders the gateway already tracks | The gateway records every `OrderAccepted` for reservation accounting anyway, so a band needs a query, not a second book to keep in step | — |
| 2026-09-04 | Registration appends `CreateAccount` to the stream; the response carries no `cash_ticks` | Writing the `accounts` row directly made PostgreSQL the source of truth for a balance, and left `LedgerConsumer` unable to run — it rebuilds accounts by replay, and a stream without the grant replays every balance to zero | implements OI 004; amends 1.3's criterion, see Deviations |
| 2026-09-04 | Rate limiting runs **ahead of** the idempotency claim | A 429 after the claim answers that `client_order_id` "rejected" for the whole TTL, so the client's correct retry keeps getting the refusal — a transient limit turned into a permanently dead order id | implements OI 015 §11.1 |
| 2026-09-04 | Designated market makers are an explicit **list of usernames** in `bots.designated_market_maker_accounts` | A prefix rule like `dmm_*` would let anyone register into the exemption. Config is version controlled and hashed, so the grant is auditable | implements OI 005 §10.6 |
| 2026-09-04 | `[symbols]` holds provisional QAA/QAB, and `GET /symbols` serves them | 4.4's bots need something to quote and 5.4c cannot resolve a `symbol_id` without it. Names deliberately meaningless — a placeholder reading like a real ticker is the one that survives into the demo | 5.1 replaces the block wholesale |
| 2026-09-04 | A bot's `client_order_id` starts from a **millisecond timestamp**, never 1 | A counter restarting at 1 re-sent keys the idempotency store had already answered: every order acknowledged, none appended, the market dead while every participant reported success | — |
| 2026-09-04 | `seq` means different things per channel: gap detection is **private-only** | On market data `seq` is the stream id and the channel carries only some of the stream's records, so a jump is normal. Book gaps are self-healing anyway (§3.5). Only the private counter is dense | pending Dev A, see Blocked |
| 2026-09-04 | Bots run behind a compose **profile**; fan-out gets no compose service until 5.2b | A stack that always has a live market is wanted for a demo and unwanted under a test suite. A container serving nobody is a container doing nothing observable | — |
| 2026-09-04 | Fan-out **derives** the book from OrderAccepted/Fill/OrderCancelled rather than from `BookChanged` | Nothing emits `BookChanged`; making the matcher emit it would require the same of Dev A's C++ engine in week 5, creating the same-week cross-developer dependency D.4 forbids. Derived is also engine-agnostic, so the week-5 swap does not touch fan-out | `services/fanout/README.md` §1 |
| 2026-09-04 | Fan-out keeps its **own** `Book` rather than sharing a resting-order projection | risk.py, ledger.py and matcher/adapter.py already each rebuild resting orders; four shapes differ enough that one abstraction serving all would be worse. Duplication chosen with open eyes — revisit at a fifth consumer, or at the first disagreement | `services/fanout/README.md` §2 |
| 2026-09-04 | Price levels are **aggregated on read**, not maintained | A maintained map is a second structure that can drift from the first; summing cannot. First file to open when fan-out is measured as the bottleneck | `services/fanout/README.md` §3 |
| 2026-09-04 | Bars bucket on **stream time**, with the width in `market_data.bar_bucket_seconds` | The replay clock in 5.1 makes "one-minute bar" ambiguous — one real minute or one simulated minute — and 7.1's backtester consumes whichever it means. Configuration so 5.1 answers it by changing a line | stages Open Issue 005 §10.5 |
| 2026-09-05 | `seq` on `private` is a **dense per-user counter**, not the stream id | §3.4's example shows a stream id, which counts every record on the stream and so is dense for nobody — read literally, no private gap is detectable and 5.2's fourth criterion is unmeetable by any implementation | reads §3.4 against §3.5 and OI 006 §7c; HANDOFF Q1 |
| 2026-09-05 | A market frame for a busy client is **skipped**; a private message is **buffered, then the connection closed** | The droppable/non-droppable distinction at the last hop. The next snapshot supersedes a skipped frame; nothing supersedes a lost fill | implements OI 006's slow-client policy |
| 2026-09-05 | The gateway **publishes** its halt state to `qa:halt`; fan-out relays it | The halt flag is in gateway memory (OI 004) and fan-out is another process, so nothing could send §3.6's `halted`. Fan-out pinging Redis was rejected — "fan-out can reach Redis" is not "the gateway can durably record orders", and a readable-but-not-writable store separates them in the direction that matters | — |
| 2026-09-05 | Session lookups use a **blocking** Redis pool | redis-py's default pool *raises* when exhausted: 200 browsers reconnecting at once refused 73 of themselves. A session lookup is one local GET, so queueing is invisible and failing is a dead feed | found by `benchmarks/bench_fanout.py` |

## Deviations from the plan

Task moved weeks · scope cut · estimate blown · criterion waived — with the reason. Distinct from
decisions: a schedule deviation is not a design change.

- **1.1's joint session (Appendix D.2) happened as async review, not a half-day together.**
  Dev B wrote the full contract as a proposal; Dev A reviewed and signed off the same day.
  Outcome as specified, mechanism not. No schedule impact.
- **3.1 Success Criterion 3 is unverified, not passing.** "A cancel that loses the race to a
  fill releases nothing" could not be constructed reliably against a live matcher. Recorded
  rather than claimed. A deterministic harness for it is a natural fit for 4.1.
- **Tasks 3.1 and 3.2 were marked `done` on their branch while four success criteria failed.**
  Found by an audit on 2 Sep and fixed the same day. The lesson is in the checklist above:
  a criterion is verified by exercising it, not by the task's own unit tests passing.
- **3.3 was split.** CI, the nightly stub and multi-arch images merged as `5efe462`; the
  deployment half (HTTPS, one-command redeploy) was deferred out of week 3 by decision and is
  now carried behind week 5. Criteria 2 and 4 pass; 1 and 3 are untouched.
- **Two further defects in finished Task 3.1**, found in week 4 by running it rather than
  reading it: a sell reserved cash it never released (two sells of half the grant exhausted an
  account that had spent nothing), and nothing checked the position at all, so every account
  could short. Same lesson as the week-3 audit, one week later.
- **`RiskState.watch_stream` died on the first Redis error** — the one long-running loop in the
  system with no exception handling. `/health` recovered while the gateway stayed permanently
  blind to the stream. Task 2.1's fifth criterion passed while the property behind it did not.
- **5.4c's Success Criterion 5 is mechanism-verified, not profiler-verified.** "No React
  re-render, verifiable in the React profiler" needs a human at a browser, and there is no JS
  test framework by decision. What is proven: the buffer cannot notify, the modules cannot
  reach React, and 1,000 messages between two frames produce exactly one paint.
- **`resumed` is a fifth WebSocket error code, and a deviation from the frozen contract.**
  §3.6 enumerates four codes, all failures, with no way to say a halt has *ended* — while a halt
  clears on its own within one watchdog interval. Additive, so an unknown code is ignorable.
  Implemented and flagged; **needs Dev A's sign-off** (HANDOFF Q2).
- **5.2's Criterion 1 was measured on one machine, not a separated topology.** Docker Hub was
  unreachable, so gateway, fan-out, 200 sockets and the harness shared ten cores. The median
  (+4.2%) and the encode counts hold; the p95 and max are pessimistic — re-measure for 7.4.

## How to run it right now

```bash
docker compose up --build        # the whole system. Docs at localhost:8000/docs
docker compose logs gateway | grep config_hash

# developing, with reloads:
docker compose up -d redis postgres
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt   # first time only
.venv/bin/python -m uvicorn services.gateway.app:app --reload --port 8000

python contracts/v1/generate.py --check    # exit 1 if generated/ is stale
.venv/bin/python -m pytest -q               # 597 tests, against the compose stores
curl localhost:8001/health                  # fan-out: stream position, ticks, serialisations
QA_BOT_PASSWORD=... docker compose --profile bots up -d   # a live market: makers + noise

cd web && npm install && npm run dev        # frontend at localhost:5173, proxied to the gateway
```
`test_sizes.py` needs a C++20 compiler and **skips loudly** without one — a green run carrying
that skip has not verified 1.1's Success Criterion 3.

## Open questions

*(none — the ledger now runs as its own compose service; see the decision log)*

## Archive

*(one line per closed week — everything else from that week is deleted; git history is the record)*

- **Week 1 (28 Aug – 3 Sep) — closed 31 Aug, 3 days early.** 1.1 contracts frozen and merged ·
  1.3 gateway skeleton · 1.4 Docker stack and shared config · 5.4a frontend scaffold. All Dev B
  and joint criteria passed with named tests; 331 tests green. Dev A's 1.2 and 2.3 not reported.
- **Week 2 (4–10 Sep) — closed 2 Sep.** 2.1 streams and durability · 2.2 ledger and replay ·
  5.4b auth screens · integration point 1: the stub engine swapped for Dev A's naive model over
  the real stream. 398 tests green.
- **Week 3 (11–17 Sep) — closed 4 Sep.** 3.1 risk and reservations · 3.2 idempotency with an
  atomic Lua claim · 3.3 CI, nightly and multi-arch images (deployment half deferred). Four
  defects found by audit and two more by running it; all fixed.
- **Week 4 (18–24 Sep) — closed 4 Sep.** Pre-work: symbol registry, rate limiting, and a ledger
  that finally runs · designated market makers · 4.4 bots · 5.2a fan-out begins · 5.4c
  WebSocket client and rAF loop. 565 tests green. Seven commits, unpushed.
