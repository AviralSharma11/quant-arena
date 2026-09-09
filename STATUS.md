# Status — Quant Arena, Dev B

**Last updated:** 2026-09-09 · **HEAD** `fbf81ce` (branch `task/7.1-backtester`, unpushed;
`main` is `7721be6`) · **Week 7**
**State:** Week 5 closed. **6.2 merged (PR #19)** — week 6's Dev B tasks are both done.
**7.1 is done — 5 of 5 criteria.** The backtester runs, and on QAA it reports the strategy
losing 0.72% against the market's 0.87%, with fees alone costing 0.82% of the account.
**796 pass, 7 skipped, none failing** on `task/7.1-backtester`, with the matcher restarted
first. No xfails remain. **Two branches are unpushed and both edit this file** — see NOW.
**3.3 is on hold by your decision (2026-09-09)**, before the deployment target was chosen.
**The bots stopped trading after twelve hours and nothing noticed** — sessions expire on an
absolute TTL and `BotClient` logged in once, so `POST /orders` returned 401 for nine hours
behind a `(healthy)` gateway. Fixed in `396a8d2`.
The nine bot-session errors are **gone** — the cause was two things, a stale `dmm_qaa` password
*and* a grant that Task 5.1's price scale had left unusable. Both fixed; 43/43 bot tests pass on
a fresh stack.
**Dev A reported 1.2, 2.3, 2.4, 3.4 and 4.1 done (2026-09-05)**; the stack matches in C++ and
serves ten symbols on one `config_hash`.
**Contracts (1.1):** **FROZEN 2026-08-31**, agreed by both developers. `contracts/v1/`.

> This file is **Dev B's**; Dev A's rows are reported by me, never inferred from the repo.
> Update protocol is in `CLAUDE.md` — propose a diff, wait for confirmation, never write unasked.

---

## NOW

**Open both PRs, in order, then restart the bots** — Dev B · **next**

- **Two branches off `main`, both unpushed, both touching `STATUS.md`.** `gh` is not installed
  here, so the PRs are yours. Order matters — merge `fix/bot-session-reauth` (`3103d3b`) first,
  then rebase `task/7.1-backtester` (`a4eac8d`) onto it, or the second PR conflicts on this file.
- **The live market is down and will stay down until you start it.** The bots image is already
  built, so nothing is left in the command but the secret:
  `QA_BOT_PASSWORD=... docker compose --profile bots up -d --build`
  Then confirm `{"event":"session_reauth",...}` appears in `docker compose logs bots` at the
  twelve-hour mark with trading continuing past it.
- **Then 7.2, the backtest screen** — and **its gateway endpoint is Dev B's** (your decision,
  2026-09-09). 7.2's own tech stack lists only TypeScript and React, so `/backtests` was
  unowned until now. Ask before starting.
- **Restart the matcher after any full `pytest` run** until Dev A takes `HANDOFF.md` §3a.
- **`STATUS.md` is 314 lines against a 250 cap** and this diff adds again. The overflow is in
  Decisions (57 rows) and Deviations, both append-only, so trimming needs your call on what goes.
- **`schema_version` is 2.** A stack carrying pre-Amendment-2 records needs one
  `docker compose down -v`.

*If NOW is empty or stale, ask. Do not pick a task yourself.*

## Then next

1. **7.2** Backtest screen + the `/backtests` gateway endpoint (Dev B, wk 7)
2. **7.3** Integration tests, T5 thinned (Dev B, wk 7) — re-check the anchor defect first
3. **3.3** Deployment half — **on hold**, and blocked on a deployment target and a TLS
   terminator, neither of which is mine to choose. See Blocked.

## Blocked / waiting

- **All five round-1 `HANDOFF.md` questions are answered**, and both integration points passed.
- **Amendment 2 is done** — `ConfigureReplay` (type 5) / `ReplayConfigured` (type 17),
  `schema_version` 2, verified end to end on a live stack. Its **second half is still unsigned**:
  three `rest_and_ws.md` §2.2 corrections, documentation only, no code either way.
- **Ask Dev A to report 4.2, 4.3 and 5.3.** PRs #16 and #17 merged, but a merged PR is not a
  report and this file does not infer Dev A's status from the repository.
- **On Dev A — `HANDOFF.md` §3a, reported not asked.** `CppMatcher.run()` has no exception
  handling, so a Redis restart kills the matcher permanently and silently — `tests/gateway/
  test_halt.py` restarts Redis by design, so **every full suite run leaves a dead matcher**.
  Proven: 176 inbound records, 0 outbound, container still "healthy". The obvious three-line fix
  activates a second defect (`step()` respawns a dead engine with an empty book and `order_id`
  back to 1, colliding across all three consumers), so it is written up rather than patched.
- **On a decision from me:** nothing. **External:** nothing outstanding.
- **On you, for 3.3:** a **deployment target** (a VM you control, with a domain), and approval
  for a **TLS terminator + static server** — one image outside `CLAUDE.md`'s closed stack list.
  Criterion 1 is unreachable without a host, so 3.3 cannot close on this machine alone.
- **On you, to restart the live market:** `secrets-local.txt` is `deny`-listed in
  `.claude/settings.local.json` and the denial is enforced above that file, so I cannot read
  `QA_BOT_PASSWORD` by any route. Every step around it is done; the one command is in NOW.

---

## Task board

`Wk` is the **scheduled** week from Appendix D.3 — not the chapter a task is documented under.

| Task | Own | Wk | Status | Evidence / note |
|---|---|---|---|---|
| 1.1  Contracts — schema, REST, WS shapes | AB | 1 | done | `1ced47a` built, `a37b49b` frozen · 233 tests pass |
| 1.2  Naive Python model engine | A | 1 | A:done | reported 2026-09-05 · maker-price fix `e8bc8e9` |
| 2.3  Hand-written matching scenarios (T1) | A | 1 | A:done | reported 2026-09-05 |
| 1.3  Gateway skeleton + stub engine | B | 1 | done | `4c09dfa` · 52 gateway tests pass · real Redis + Postgres |
| 1.4  Local Docker stack + shared config | B | 1 | done | `84c270e` · verified from a fresh clone, 34s to all-healthy |
| 5.4a Frontend scaffold (Vite/TS/React) | B | 1 | done | `94fbe04` · 10 tests · builds, serves, 3 routes |
| 2.4  C++ engine — order book and match loop | A | 2 | A:done | reported 2026-09-05 · `e4352c1`, arrival-order fix `e8bc8e9` |
| INT1 Stub engine → naive model over the stream | B | 2 | done | `8210d3a` · 21 matcher tests · live restart replayed 26, appended 0 |
| 2.1  Redis Streams, durability, halt state | B | 2 | done | `8fd72bc` · 359 tests pass · durability 72k/s measured |
| 2.2  Ledger writer + replay rebuild | B | 2 | done | `2948867` · 11 ledger tests pass · replay rebuild and maker/taker fees verified |
| 5.4b Auth screens | B | 2 | done | `bd321b3` · login, registration, and session rehydration verified · 302 tests pass |
| 3.4  C++ engine complete + nanobind | A | 3 | A:done | reported 2026-09-05 · **no nanobind in the repo — see Deviations** |
| 3.1  Risk checks + in-memory reservations | B | 3 | done | `6c67e01` · sells reserve inventory not cash; `INSUFFICIENT_POSITION` now raised · 5 of 6 criteria; criterion 3 unverified, see Deviations |
| 3.2  Idempotency — atomic claim-and-append | B | 3 | done | merged `6213651`, defects fixed `fb2cd8a` · 6 of 6 criteria · concurrent burst now yields exactly one order · **unblocks Dev A 6.4** |
| 3.3  Public deployment and CI | B | 3 | todo | **half merged** `5efe462` — CI gate, nightly, multi-arch images. Criteria 1 and 3 (HTTPS, one-command redeploy) outstanding; see Deviations |
| 4.1  Differential/property/determinism (T2–T4) | A | 4 | A:done | reported 2026-09-05 · `2930cb2` · 4 tests, 2 Hypothesis properties at 100 examples |
| 4.4  Bots — market maker and noise traders | B | 4 | done | `68c7821` · live: 130 fills in 45s, both makers meeting their uptime obligation, units conserved at 0 per symbol · session re-auth `396a8d2`, 55 bot tests |
| 5.2a Fan-out process begins | B | 4 | done | `0eb5329` · 38 tests · derived book matches the matcher order-for-order across a 400-record sequence; live 706 records recovered |
| 5.4c WS client, gap detection, rAF loop | B | 4 | done | `78ed4dc` · 26 tests · reconnect re-subscribes, a deliberate private gap triggers exactly one resync; criterion 5 mechanism-verified, profiler check manual |
| 4.2  Engine process, Redis, replay recovery | A | 5 | A:todo | reported unfinished 2026-09-05 · `b644130` merged and live in compose |
| 4.3  Native engine benchmark (B1) | A | 5 | A:unknown | — |
| 5.3  Kill-the-engine recovery script (T6) | A | 5 | A:unknown | — |
| 5.2b Fan-out completes — conflation, WS server | B | 5 | done | `1c87a13` · 37 tests · 5 of 5 criteria · 400 encodes at 1, 50 and 200 clients; ack median 3.50→3.65 ms · **unblocks Dev A 6.3** |
| 5.1  Crypto fair value, replay clock, 10 symbols | B | 5 | done | `a67f1a4`, criterion 2 closed by Dev A's `0faeea7` · **5 of 5 criteria** · `test_the_replay_ratio_reaches_the_stream`, `test_gateway_stamps_replay_configuration_at_startup`; the strict `xfail` is removed, having failed the day the amendment landed |
| 6.1a Trading screen begins | B | 5 | done | `34b7cb0` · 56 web tests · book, tape, chart, ten live symbol tabs; no criteria of its own, 6.1b carries them |
| 6.3  Open-loop load generator | A | 6 | A:unknown | — |
| 6.4  Duplicate injection in load harness | A | 6 | A:unknown | — |
| 6.1b Trading screen completes | B | 6 | done | `e9bff3f`, merged `1ee2391` · `tests/web/test_trading_private.py`, 17 tests · 4 of 5 criteria verified in a browser against the live market; see Deviations |
| 6.2  Archiver | B | 6 | done | `ab40487` · 32 archiver tests · **4 of 4 criteria** · live: 702,432 records → 4,666 files over 6.28 h; container restart resumed from `1788885539965-0` |
| 7.4  Benchmarks, trace tool, report | A | 7 | A:unknown | — |
| 7.1  Backtester | B | 7 | done | `fbf81ce` · 43 tests, all in the per-commit suite (+0.67s) · **5 of 5 criteria** · `test_two_runs_of_one_manifest_are_byte_identical`, `test_the_strategy_is_handed_one_bar_at_a_time_and_never_a_series`, `test_every_fill_pays_the_takers_fee_from_the_ledger_not_a_local_copy` |
| 7.2  Backtest screen | B | 7 | todo | **includes the `/backtests` gateway endpoint** — assigned to Dev B 2026-09-09; 7.2's stated stack is TS/React only and left it unowned |
| 7.3  Integration tests (T5, thinned) | B | 7 | todo | — |
| 7.5  Definition-of-done walk | AB | 7 | todo | — |

**Vocabulary.**

- Dev B and joint rows: `todo` · `wip` · `blocked` · `done`. **`done` requires a commit SHA or a
  named passing test in Evidence. No evidence, no `done`.**
- Dev A rows: `A:todo` · `A:wip` · `A:done` · `A:unknown`, always with `reported YYYY-MM-DD`.
  **Reported by me, never inferred.** Do not read the repo and conclude a Dev A task is finished
  — Dev A may push work in progress, or finish without pushing. `A:unknown` is expected.
- **At most one row may be `wip`**, and it must match NOW. None is `wip` today: 5.1 is `blocked`
  on Dev A, not in progress. (The old wording said "exactly one", which the file itself broke on
  2026-09-07 — 5.1 was `wip` while NOW read 6.1b.)

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
| 2026-09-05 | Both matching engines use the **resting maker's** price | Arrival sequence is tracked explicitly, covering a seller crossing a higher resting bid; Python, C++ and adapter regression tests agree | resolves the maker-price defect |
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
| 2026-09-05 | **Q1 answered: `seq` on `private` is the dense per-user counter**, as implemented | Dev A confirmed the reading. §3.4's stream-id example is illustrative and wrong; the counter is what makes 5.2's fourth criterion measurable | closes HANDOFF Q1 |
| 2026-09-05 | **Q2 answered: `resumed` accepted. Contracts v1 Amendment 1**, agreed by both developers | A halt clears on its own, and the four original codes cannot say so. Additive; `schema_version` **not** bumped — the binary records are untouched, this is the browser wire | amends `contracts/v1/rest_and_ws.md` §3.6 |
| 2026-09-05 | **Q3 answered: `bars:*` carries `seq`**, as implemented | The rule in §3.5 governs; §3.3's example is abbreviated. A channel without `seq` is the one channel no client could gap-check | closes HANDOFF Q3 |
| 2026-09-05 | **Q4 answered: the C++ engine emits no `BookChanged`** | `engine/cpp/stream_engine.cpp` enumerates its outputs and the type is absent. Fan-out's derived book stands and the engine swap did not touch it | closes HANDOFF Q4 |
| 2026-09-05 | **Q5 answered: both engines price at the resting maker** | Dev A's `e8bc8e9` tracks arrival order in the Python model and the C++ book; the adapter's override was removed | closes HANDOFF Q5 |
| 2026-09-07 | The trading screen owns **one** frame loop; panels register paint callbacks | `takeChanged()` clears the changed set, so with a loop per panel the first to run consumes the change and the rest paint nothing. A correctness constraint, not a performance preference | — |
| 2026-09-07 | 6.1a is the **market half**, 6.1b the **private half** | Market data is high-frequency and droppable and stays out of React; private data is low-frequency and never dropped, and React state is right for it. `buffer.ts`'s rule is about frequency, not principle | splits 6.1a/6.1b |
| 2026-09-07 | `formatTicks` **throws** without a tick size rather than defaulting to 1 | A default renders 7,983,040 ticks as "7983040" beside a correct price — a plausible number instead of a visible failure. OI 014 §14e covers a misplaced decimal point too | — |
| 2026-09-07 | `STREAM_SYMBOLS` **deleted**, not lengthened to ten | A hard-coded list of ten has the same defect one listing later. §2.3 makes `GET /symbols` the only source of names and scales | closes a 5.1 loose end |
| 2026-09-07 | A repeated `bar_open_ns` **replaces**; the series is bounded at 600 | A reconnecting client re-receives bars it has drawn, and appending them puts two candles at one x position — which reads as an exchange bug, not a client one | — |
| 2026-09-07 | `lightweight-charts` 5.2.1 added | Named on `CLAUDE.md`'s closed stack list, so no approval needed. Fed from the bar buffer on the frame loop, never from React state | — |
| 2026-09-06 | `bars:*:1m` means one **simulated** minute; both bucket widths kept | A real minute holds sixty simulated minutes of price action, so a chart on real minutes compresses an hour into one candle. This is the question the config file parked for 5.1 | settles `market_data.bar_bucket_seconds` |
| 2026-09-06 | The replay clock is a **function of elapsed real time**, never a counter | A counter drifts whenever a quoting loop runs late, and two bots each keeping their own would disagree about what time it is — two symbols replaying at different speeds, unreproducibly | — |
| 2026-09-06 | `next_ticks()` is a **lookup, not a step** | Indexing on the clock means a slow loop rejoins the market rather than walking forward through stale prices | — |
| 2026-09-06 | The dataset's **checksum** lives in `[replay].data_sha256`, its **path** in code | *Which* prices the market replays is a domain parameter every process must agree on, so it is hashed; *where the file sits* is infrastructure. One source of truth, nothing for a sidecar to drift from | applies the 2026-08-31 config/infrastructure split |
| 2026-09-06 | Ten symbols carry the **real tick sizes** of the instruments behind them | Four distinct values, not the provisional 1. A table where every tick size was 1 asserts all ten trade on one scale, which is what made the old block provisional | — |
| 2026-09-06 | A symbol missing from the data file **falls back**; a data file that fails its checksum **raises** | Absent means "you are offline"; wrong means "you are about to generate a session nobody can reproduce" | — |
| 2026-09-05 | Session lookups use a **blocking** Redis pool | redis-py's default pool *raises* when exhausted: 200 browsers reconnecting at once refused 73 of themselves. A session lookup is one local GET, so queueing is invisible and failing is a dead feed | found by `benchmarks/bench_fanout.py` |
| 2026-09-08 | **The grant is 10,000,000,000 ticks**, was 1,000,000 | Task 5.1 raised prices to 8,208,718 ticks and left the week-1 grant behind: it could not buy one unit of six of the ten symbols, and every market maker reported `two_sided_uptime: 0.0` with `INSUFFICIENT_CASH` on every bid — one-sided books, the exact failure OI 005 §5e says a DMM prevents. Sized at 12 full quotes of the dataset's peak | resizes the 2026-08-31 grant |
| 2026-09-08 | A bar channel is named in **simulated** time: the 1-second bucket is `1m`, the 60-second bucket `1h` | `_WIDTH_SUFFIX` labelled by literal seconds, so `bars:*:1m` carried 60-second buckets — one candle per *real* minute, the compression the 2026-09-06 decision was written to prevent. `width_label` now divides by the ratio | implements the 2026-09-06 decision |
| 2026-09-08 | The idempotency **in-flight sentinel** expires in 30 s; only a recorded outcome keeps the hour | One key served two lifetimes. A gateway dying between claim and record left `in_progress` with no outcome, so every retry got `202 in_progress` for a full hour — an order neither placed nor refused | — |
| 2026-09-08 | `MarketBuffer` keys bar series by **symbol and width** | The router discarded the width, so two widths would fold into one array and the chart would draw two timeframes as one line. Latent at one width; wrong at two, which 7.1 will need | — |
| 2026-09-08 | The client **resyncs on every connect**, not once per session | `StreamClient` resets its tracker on open, which is an admission that anything missed while disconnected is unrecoverable — so a connect *is* a gap. Found in the browser: signing in after the socket started left cash on "awaiting the grant…" forever | implements OI 014 §14e |
| 2026-09-08 | The client mirrors the ledger's maker/taker fees, guarded by a test that reads both | The private stream carries no balance — no record can, the engine is money-blind — so cash cannot move on a fill without it. §3.4 puts `role` on the wire for exactly this | one deliberate duplication |
| 2026-09-08 | `LiveQuote.tsx` deleted | Unused, ran its own frame loop against the one-loop decision, and rendered raw ticks with no tick size — the template a future session would have copied | — |
| 2026-09-08 | The archiver's checkpoint is a **low-water mark** — the stream id of the oldest record not yet written to a file — not the last record applied | Every file on disk is then complete for everything strictly before it, and nothing after it has been written, so a restart re-derives the in-flight buckets and writes each file exactly once. 6.2's third criterion ("neither a gap nor a duplicate") needs no overwrite semantics and no dedup pass | implements 6.2 criterion 3 |
| 2026-09-08 | The archiver **imports** `services/fanout/` bars, book and state rather than moving them to a shared package | 6.2's Boundaries say bar aggregation is built once; a move would edit a finished, tested process for cosmetics. Revisit if a third consumer needs them | implements 6.2's Boundaries |
| 2026-09-08 | Archived 1 Hz L2 snapshots are cut on **`timestamp_ns`**, like bars — the snapshot for stream-second N is written when the first record of N+1 arrives | A wall-clock sampler would put a different number of snapshots in a replay than in the live run, making the archive unreproducible. `timestamp_ns` is real gateway time, so 1 Hz on stream time is the 1 Hz behind the ~345 MB/day estimate | implements OI 011 §11b |
| 2026-09-08 | A **sealed flush unit is never reopened**; rows arriving for one are counted as `late_rows` and dropped | A backwards-stamped record reopened its old unit and built a second, partial one — which the writer would then write to the same deterministic path, replacing a complete file with an incomplete one. Filing it into whichever unit is open instead would put a trade under the wrong minute, which is a wrong archive nothing would report | found by `test_a_row_for_a_written_unit_is_counted_not_misfiled` |
| 2026-09-08 | The checkpoint is **exclusive** — the ID before the oldest open unit's first record, not that record | `read_records(last_id=X)` returns records strictly after X, so a checkpoint naming a record still to be re-read skips exactly that record on every restart. One lost trade per restart, in a file nobody would check | `Unit.resume_after` |
| 2026-09-08 | The image creates `/archive` owned by `quant`, and the archiver's healthcheck asserts **writability**, not just a Redis ping | The container ran non-root against a root-owned volume, failed every `mkdir` and reported healthy while writing nothing. Third instance of healthy-but-idle in this project, after fan-out not running and the matcher dead | `Dockerfile`, `docker-compose.yml` |
| 2026-09-08 | The archive root is `QA_ARCHIVE_DIR`, a named volume — **infrastructure, not configuration** | A path differs between laptop, CI and container while the configuration is identical; inside `config_hash` it would change the hash when nothing about the configuration had | applies the 2026-08-31 split |
| 2026-09-09 | A bot meeting a **401 logs in again and re-sends the same request once** | Sessions expire on an absolute TTL, so a bot quoting every second still dies at hour twelve. The resend is a retry and not a duplicate because a 401 comes from the `CurrentUser` dependency, which resolves *before* the rate limiter and before `idempotency.claim` — nothing was recorded against that `client_order_id` | implements OI 008's retry protocol at a new door |
| 2026-09-09 | **The gateway session TTL stays absolute** — renewal on use was rejected | Renewing on read is the other possible fix, and it is a change to auth semantics settled in OI 015: a browser session that never expires while a tab is open is a different decision from a bot that reconnects. The client is the layer that knows it is a bot | declines to amend OI 015 |
| 2026-09-09 | **The `/backtests` gateway endpoint is Dev B's**, inside 7.2 | 7.2's tech stack lists only TypeScript and React, yet its first criterion is "a user runs a backtest from the interface", which needs a server route. `vite.config.ts` has proxied `/backtests` since 5.4a. Unowned work in week 7 is work that does not happen | closes a gap between 7.1's and 7.2's deliverables |
| 2026-09-09 | A backtest bar is **built** by aggregating `bar_minutes` minute closes into OHLC, and the width is a **manifest field, not configuration** | The pinned dataset is `symbol/minute_index/close_ticks` with no OHLC on disk, so a bar must be constructed. The width is a property of one research run: two runs at different widths must be comparable without changing the hash of the whole exchange | — |
| 2026-09-09 | Every backtest fill is a **taker**, and the rate is **imported** from `services/ledger/ledger.py` | Filling at the next bar's open is crossing whatever is there, so the order is always the aggressor. Importing `calculate_fees` means the backtester and the exchange cannot charge different fees for the same trade | implements 7.1 criterion 5 |
| 2026-09-09 | The manifest records **no `engine_version`**; `fill_model` and `schema_version` instead | OI 011 §11f asks for one, but OI 018 §3.2 reversed 11c — no engine takes part in a Phase 1 backtest. A field naming one would assert a result depended on something it never touched, which is the class of claim a manifest exists to prevent | corrects OI 011 §11f for Phase 1 |
| 2026-09-09 | Sharpe is **per bar and not annualised**, and is named `sharpe_per_bar` | The clock is simulated minutes at one real second each, so there is no honest number of them in a year. Annualising would mean inventing the figure's most load-bearing constant | — |
| 2026-09-09 | A backtest's starting cash defaults to **ten times the first bar's open**, not `exchange.initial_cash_ticks` | The grant is 10,000,000,000 ticks because a market maker needs twelve full quotes of the dataset's peak. Handed to a backtest it makes a one-unit strategy 0.08% invested and rounds every metric toward zero — a run that appears to say the strategy is flat when it says the account was too big to notice it | reads the 2026-09-08 grant resize |
| 2026-09-09 | `SmaCrossover` states a **target position** and closes the gap to `portfolio.position`, rather than tracking its own long/flat flag | The flag never traded on the first full window, so a run beginning in an uptrend sat out the whole first trend. Worse, it could disagree with reality: a buy refused for want of cash left it saying "long" against a position of zero, and the strategy never tried again. Reading the real position makes a refusal self-correct | found by `test_a_rise_then_a_fall_buys_then_sells` |
| 2026-09-09 | Re-login is **login-only**, and does not cancel resting orders | `sign_in()` registers first and its 401 branch raises about a stale `QA_BOT_PASSWORD` — the right diagnosis at start-up, the wrong one for a session that aged out. And the session expired; the orders on the book did not, so cancelling would pull a live two-sided market for nothing | — |

## Deviations from the plan

Task moved weeks · scope cut · criterion waived — with the reason. A schedule deviation is not
a design change.

- **3.1 Criterion 3 is unverified, not passing.** "A cancel that loses the race to a fill
  releases nothing" could not be built reliably against a live matcher. Recorded, not claimed.
- **3.3 was split.** CI, nightly and multi-arch images merged as `5efe462`; the deployment half
  (HTTPS, one-command redeploy) sits behind week 5. Criteria 2 and 4 pass; 1 and 3 untouched.
- **6.1 criteria 1, 3, 4 and 5 are browser-verified; criterion 2 is not measured.** On 2026-09-08,
  against the live bot market: a market buy on QAA filled at 84180.76 as taker and cash moved
  100,000,000.00 → 99,915,735.06 with position QAA 1, no refresh (notional 8,418,076 + taker fee
  8,418 = exactly the ticks deducted); a limit bid at 50000.00 rested, appeared in the L2 book,
  and cancelled cleanly; candles streamed once a second. **Criterion 2 ("no visible frame drops")
  is confirmed by eye under full bot load and not profiled** — the automated tab reports
  `visibilityState: "hidden"`, so `requestAnimationFrame` is suspended and zero frames can be
  sampled. That is `frameLoop.ts` behaving as documented, and it is why the number is missing.
- **Dev A's C++ uses neither CMake, Catch2 nor nanobind** (all on the closed stack): a bare
  `g++` line, a hand-rolled test, a subprocess instead of a binding. Observed, not judged.
- ~~`stream_engine.cpp` hand-codes record sizes and offsets~~ **Closed by Dev A's `ad65172`**: it
  now uses the generated layouts, so `generate.py --check` is meaningful for the C++ again.
- ~~5.1 Criterion 2 is half met~~ **Closed by Dev A's `0faeea7`.** `ConfigureReplay` is the first
  record on every session's inbound stream and carries the ratio plus
  `config_hash_hi`/`config_hash_lo` — which also closes the 31 Aug decision "config hash to the
  stream in 2.1", never implemented at the time. `schema_version` is now 2.
- **6.2 criterion 4 came in under the estimate, not on it.** 55 MB/day measured over 6.28 h of the
  live ten-symbol bot market (14.5 MB total); 113 MB/day synthetic. Open Issue 018 §11.1 says
  ~345 MB/day, but that figure is uncompressed at full ten-level depth, and this writes zstd over a
  long table while the bot book quotes two or three levels a side. The test band is written around
  the measurement. Re-measure under 6.3's load generator, where the book is deeper.
- **Bars occupy more bytes than snapshots while holding a third of the rows** — 1,553 files at a
  median 3.4 kB, mostly Parquet footer. One file per flush unit per symbol per dataset is the price
  of the low-water checkpoint; a longer flush unit means fewer, fatter files and a proportionally
  longer checkpoint lag. One line to change if the file count ever matters.
- **The `runner.py`/`adapter.py` anchor defect is now live, not dormant.** It was dormant only
  while no record type produced zero anchors; Amendment 2 created the first candidate. Re-check
  before 7.3.
- **Latent defect, found not fixed: `runner.py` documents "every inbound record produces exactly
  one anchor" while `adapter.py` returns `[]` for unknown types.** Recovery counts anchors as its
  bookmark, so the first type producing none makes a restarted matcher re-process an answered
  order and duplicate a fill. Both engines carry it — dormant until Amendment 2 creates the
  first such type, which is why that amendment forwards rather than ignores it.
- **Fan-out was not running and the gateway was a build behind**, found while wiring 6.1a: two
  processes on different `config_hash`es, the split that hash exists to catch. Nothing alarmed.
- **5.2's Criterion 1 was measured on one machine, not a separated topology.** Gateway, fan-out,
  200 sockets and the harness shared ten cores. Median (+4.2%) and encode counts hold; p95 and
  max are pessimistic — re-measure for 7.4.
- **The bot session tests need a stack the compose bots have not been running on.** They share
  the same thirty-one accounts, so accumulated inventory and resting orders make five of them
  fail. 43/43 pass on a fresh stack; `docker compose stop bots` alone is not enough.
- **`docker compose up --build -d` never rebuilds the bots image**, because the `bots` profile is
  not active for that command. Caught when the bots came up on a hash matching neither the file
  nor the other four processes, reporting `"market_makers":2` — the pre-5.1 table. Pass `--build`
  with the profile, and read the startup hash line.
- **Two tests were pinned to the old grant and one had stopped testing anything.**
  `test_reserved_cash_does_not_leak_on_retry` was sized in absolute ticks against 1,000,000, so
  after the resize its three orders no longer exhausted the account and the assertion that caught
  a double-reservation passed vacuously. Both now derive from `settings.initial_cash_ticks`.
- **Fourth healthy-but-idle fault**, after fan-out not running, the matcher dead-but-healthy, and
  the archiver failing every `mkdir`. The gateway was genuinely fine — it was answering 401
  correctly — so no healthcheck could have caught this one. It was found only by comparing
  `XLEN qa.inbound` twice five seconds apart. The pattern is now four for four: **the container
  is not the thing to check; the output is.**
- **3.3 is on hold at your instruction (2026-09-09)**, not deferred again for schedule reasons.
  The half that is merged still passes criteria 2 and 4; 1 and 3 are untouched and now blocked
  on decisions listed above.
- **7.1 is complete, but two of its inputs were not available as specified.** The plan's manifest
  field `engine_version` has no referent in Phase 1, and OHLCV bars do not exist on disk. Both are
  recorded as decisions above rather than quietly filled with a plausible value.
- **The fee rates are not in the hashed configuration.** `MAKER_FEE_BPS` and `TAKER_FEE_BPS` are
  module constants in `services/ledger/ledger.py`, so the `config_hash` a run manifest records
  does **not** describe them — contradicting the 2026-08-31 rule that domain parameters live in
  `config/quant_arena.toml` and nowhere else. Not fixed here: it would edit a finished Task 2.2
  process from inside 7.1 and change every existing `config_hash`. The manifest records the rates
  themselves so a result stays interpretable. **Hardening item for 7.5.**

## How to run it right now

```bash
docker compose up --build -d     # the whole system. Docs at localhost:8000/docs
docker compose logs gateway | grep config_hash   # every process must print the same one
# a live market: makers + noise. --build is required; the password is in secrets-local.txt
QA_BOT_PASSWORD=... docker compose --profile bots up -d --build
# stop the bots AND reset the stack before a full suite run — they share accounts with it
cd web && npm install && npm run dev        # frontend at :5173, proxied to gateway and fan-out

# developing, with reloads:
docker compose up -d redis postgres
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt   # first time only
.venv/bin/python -m uvicorn services.gateway.app:app --reload --port 8000

python contracts/v1/generate.py --check          # exit 1 if generated/ is stale
python scripts/fetch_market_history.py --check   # the pinned prices are the recorded ones
python -m services.backtest --symbol QAA --bar-minutes 5   # a backtest report
python -m services.backtest --symbol QAA --json            # the same run, canonical JSON
.venv/bin/python -m pytest -q                    # 803 collected: 796 pass, 7 skip
docker compose exec archiver sh -c 'du -sh /archive; cat /archive/_checkpoint.json'
```
`test_sizes.py` needs a C++20 compiler and **skips loudly** without one; a green run carrying
that skip has not verified 1.1's Criterion 3.

## Open questions

*(none — all five `HANDOFF.md` contract questions are answered; see the decision log)*

## Archive

*(one line per closed week — everything else from that week is deleted; git history is the record)*

- **Week 1 — closed 31 Aug, 3 days early.** 1.1 · 1.3 · 1.4 · 5.4a. 331 tests green.
- **Week 2 — closed 2 Sep.** 2.1 · 2.2 · 5.4b · integration point 1. 398 tests green.
- **Week 3 — closed 4 Sep.** 3.1 · 3.2 · 3.3 (deployment half deferred). Six defects fixed by
  audit and by running it; a criterion is verified by exercising it, not by unit tests passing.
- **Week 4 (18–24 Sep) — closed 4 Sep.** Pre-work: symbol registry, rate limiting, and a ledger
  that finally runs · designated market makers · 4.4 bots · 5.2a fan-out begins · 5.4c
  WebSocket client and rAF loop. 565 tests green. Seven commits, unpushed.
