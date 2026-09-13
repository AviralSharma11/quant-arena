# Status — Quant Arena, Dev B

**Last updated:** 2026-09-13 · **HEAD** `8765203` (branch `task/ui-redesign`, uncommitted work;
PRs #21 and #22 merged) · **Week 7**
**State:** Weeks 5 and 6 closed — see Archive. **7.1 and 7.2 are done.** The backtester runs
and the third screen exists, so **all three screens are built**. On QAA the strategy loses 0.72% against the market's 0.87%, with fees
alone costing 0.82% of the account — the comparison earning its place.
**835 pass, 7 skipped, none failing**, with the matcher restarted first. No xfails remain.
**The two branches are now genuinely stacked** — they had diverged at `e624909`, with 7.2's
status commit sitting on the 7.1 branch. Rebased 2026-09-10: `task/7.1-backtester` ends at
`e624909`, `task/7.2-backtest-screen` is that plus `3941931` plus `6485c50`. Both unpushed.
**3.3 is on hold by your decision (2026-09-09)**, before the deployment target was chosen.
**The market is live and the twelve-hour re-auth is now observed** — `396a8d2` (PR #20)
answered the 401s that had stopped the bots; 40 `session_reauth` events over a 21-hour run,
first at 2026-09-09T20:25:27Z, no bot errors since.
**Dev A reported 1.2, 2.3, 2.4, 3.4 and 4.1 done (2026-09-05)**; the stack matches in C++ and
serves ten symbols on one `config_hash`.
**Contracts (1.1):** **FROZEN 2026-08-31**, agreed by both developers. `contracts/v1/`.

> This file is **Dev B's**; Dev A's rows are reported by me, never inferred from the repo.
> Update protocol is in `CLAUDE.md` — propose a diff, wait for confirmation, never write unasked.

---

## NOW

**Commit the UI redesign and verify the backtest screen against a live stack** — Dev B · **next**
- Redesign is complete in the working tree (all three screens); tsc and build pass. The backtest
  results view has only been checked with mocked responses, because Docker was down.
- `tests/integration/test_critical_path.py` exists without its `conftest.py` — 7.3 is `wip`.
- **Restart the matcher after any full `pytest` run** until Dev A takes `HANDOFF.md` §3a.
- **`schema_version` is 2.** A stack carrying pre-Amendment-2 records needs one
  `docker compose down -v`.

*If NOW is empty or stale, ask. Do not pick a task yourself.*

## Then next

1. **7.3** Integration tests, T5 thinned (Dev B, wk 7) — no `tests/integration/` exists yet
2. **7.5** Definition-of-done walk (joint) — blocked on Dev A's 7.4, `A:unknown` since 05 Sep
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
- **Balances do not survive a restart once the stream has been trimmed — needs a decision, you
  and Dev A.** Cash grants are `CreateAccount` events on the inbound stream, trimmed at
  `maxlen = 2000000` (`config/quant_arena.toml:134`). With no snapshots in Phase 1, a restart
  replays only the retained window: fills without their grants. Observed 2026-09-13: 30 accounts
  rebuilt at or below zero (`dmm_qaa` −2,716,652,209 ticks), bots crash-looping on
  `dmm_qaa was never funded`. Cleared by `docker compose down -v`, which deletes every account.
  It recurs after the next ~2M records. Snapshots are Phase 2, so the answer is a decision, not a patch.
- **On you, for 3.3:** a **deployment target** (a VM you control, with a domain), and approval
  for a **TLS terminator + static server** — one image outside `CLAUDE.md`'s closed stack list.
  Criterion 1 is unreachable without a host, so 3.3 cannot close on this machine alone.
- **On Dev A — 6.3, 6.4 and 7.4 have never been reported**, and 7.4 blocks the joint 7.5. The
  existing ask names only 4.2, 4.3 and 5.3; these three belong in it.
- **On you, to restart the live market:** `secrets-local.txt` is `deny`-listed in
  `.claude/settings.local.json` and the denial is enforced above that file, so I cannot read
  `QA_BOT_PASSWORD` by any route. Every step around it is done; the one command is in NOW.

---

## Task board

`Wk` is the **scheduled** week from Appendix D.3 — not the chapter a task is documented under.

| Task | Own | Wk | Status | Evidence / note |
|---|---|---|---|---|
| 3.3  Public deployment and CI | B | 3 | todo | **half merged** `5efe462` — CI gate, nightly, multi-arch images. Criteria 1 and 3 (HTTPS, one-command redeploy) outstanding; see Deviations |
| 4.2  Engine process, Redis, replay recovery | A | 5 | A:todo | reported unfinished 2026-09-05 · `b644130` merged and live in compose |
| 4.3  Native engine benchmark (B1) | A | 5 | A:unknown | — |
| 5.3  Kill-the-engine recovery script (T6) | A | 5 | A:unknown | — |
| 6.3  Open-loop load generator | A | 6 | A:unknown | — |
| 6.4  Duplicate injection in load harness | A | 6 | A:unknown | — |
| 7.4  Benchmarks, trace tool, report | A | 7 | A:unknown | — |
| 7.1  Backtester | B | 7 | done | `fbf81ce` · 43 tests, all in the per-commit suite (+0.67s) · **5 of 5 criteria** · `test_two_runs_of_one_manifest_are_byte_identical`, `test_the_strategy_is_handed_one_bar_at_a_time_and_never_a_series`, `test_every_fill_pays_the_takers_fee_from_the_ledger_not_a_local_copy` |
| 7.2  Backtest screen | B | 7 | done | `3941931` · 27 tests (16 gateway, 11 web) · criterion 2 by test; criterion 1's server half verified live, browser half not — see Deviations · `tests/web/` + `tests/backtest/` 129 pass on `6485c50` · includes the `/backtests` endpoint, Dev B's by the 2026-09-09 decision |
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
| 2026-09-10 | The anchor defect is **dormant, not live**; nothing is owed before 7.3 | `ConfigureReplay` (type 5) is forwarded by `adapter.py:_forward` as one `ReplayConfigured`, and `ReplayConfigured` is in `runner.py:is_anchor` — Amendment 2 closed its own candidate as it landed. Inbound types 1–5 each produce exactly one anchor, so no zero-anchor type exists at `schema_version` 2 | re-checks the 2026-09-09 entry |
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
| 2026-09-09 | A backtest's id **is its manifest hash**, so `POST /backtests` is idempotent | 7.1 already guarantees byte-identical output under a content hash of every input that moves a number. Two identical requests therefore collapse onto one stored result rather than a second copy under a second id — a property that was proven rather than a mechanism added. It also makes a 404 honest: the result is recomputable, so nothing is lost that cannot be asked for again | uses 7.1 criterion 2 |
| 2026-09-09 | Backtest results live in **Redis with an hour's TTL**, never in PostgreSQL | OI 004 makes PostgreSQL a derived read model *rebuildable from the stream*. A backtest result is derived from no stream and could never be rebuilt from one, so a table of them would quietly break the property that definition rests on | implements OI 004 |
| 2026-09-09 | The backtest runs under `asyncio.to_thread`; Redis stays on the async client | 30–230 ms of CPU on the loop of the single *producer* would delay every order acknowledgement in flight (OI 007). A fully synchronous handler would free the loop too, but needs a second connection pool to size, to fail and to explain | implements OI 007 |
| 2026-09-09 | The screen's range is in **simulated days**, not dates | The pinned dataset is `minute_index` with no wall-clock time anywhere, so a date picker would show an invented fact. 10,080 minutes is exactly seven simulated days of 1,440 | applies OI 005 §5g |
| 2026-09-09 | A bar range is **sliced before bars are built**, never after | Slicing built bars leaves the first bar of a range straddling the boundary — open from outside it, close from inside — so two runs over adjacent ranges would disagree about a bar they both think they own | — |
| 2026-09-09 | The pinned dataset is **cached per process** | Re-reading 100,800 Parquet rows per request put a fifth of a second of avoidable work on the process that acknowledges orders. The file is checksum-pinned, so it cannot change under the cache without the checksum having already refused it | — |
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
- **Latent defect, found not fixed: `runner.py` documents "every inbound record produces exactly
  one anchor" while `adapter.py` returns `[]` for unknown types.** Recovery counts anchors as its
  bookmark, so the first type producing none makes a restarted matcher re-process an answered
  order and duplicate a fill. Re-checked 2026-09-10: Amendment 2 did **not** create such a type
  — `ConfigureReplay` is forwarded as one `ReplayConfigured` and `ReplayConfigured` is in
  `is_anchor`. Inbound types 1–5 each produce exactly one anchor, so it stays latent for a
  future type. **`runner.py`'s anchor table omits `ConfigureReplay` while `is_anchor` counts
  it** — documentation only, a 7.5 hardening item.
- **7.2's status commit was made on the wrong branch.** `b999bc7` recorded 7.2 done on
  `task/7.1-backtester`, which held none of 7.2's code, and this file's HEAD stamp named a
  branch that was not checked out. Rebased onto 7.2 as `6485c50` on 2026-09-10.
- **5.2's Criterion 1 was measured on one machine, not a separated topology.** Gateway, fan-out,
  200 sockets and the harness shared ten cores. Median (+4.2%) and encode counts hold; p95 and
  max are pessimistic — re-measure for 7.4.
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
- **7.2 criterion 1 is half verified.** "A user runs a backtest from the interface and sees
  results" — the server round trip is proven live against the containerised gateway (run 201,
  a re-run returning the same id, retrieve 200, a 400 for an unknown symbol). That a human can
  click the button and read the table is **not** measured, and it is the same
  browser-verification deviation 5.4c and 6.1 both carry. Criterion 2 is proven by test.
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
.venv/bin/python -m pytest -q                    # 842 collected: 835 pass, 7 skip
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
- **Week 3 — closed 4 Sep.** 3.1 · 3.2 · 3.3 (deployment half deferred). Six defects found by
  audit and by running it: a criterion is verified by exercising it, not by unit tests passing.
- **Week 4 — closed 4 Sep.** Symbol registry, rate limiting, a ledger that runs · market makers
  · 4.4 bots · 5.2a · 5.4c. 565 tests green.
- **Week 5 — closed 8 Sep.** 5.1 crypto fair value and replay clock · 5.2b conflation and WS
  server · 6.1a. Amendment 2 (`ConfigureReplay`) landed and both integration points passed.
- **Week 6 — closed 9 Sep.** 6.1b · 6.2 archiver, 702,432 records to 4,666 files over 6.28 h.
  The bot session-expiry fault was found and fixed here (PR #20), not by a test.
