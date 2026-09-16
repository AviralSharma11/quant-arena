# Status — Quant Arena, Dev B

**Last updated:** 2026-09-16 · **HEAD** `a4c2769` (branch `task/020-checkpointing`;
`origin/main` is `1ebab7d`, PR #27 merged) · **Week 7**
**State:** Weeks 5 and 6 closed — see Archive. **7.1 and 7.2 are merged** (PRs #21, #22), so all
three screens are built; on QAA the strategy loses 0.72% against the market's 0.87%, fees alone
costing 0.82% of the account. **7.3 is merged** (PR #25) — four integration tests against the
live stack, both layer-blame branches proven by breaking it on purpose. **The UI redesign is
merged** (PR #26). **7.4 is merged** (PR #27). **7.5 was completed by Dev B on 2026-09-16 by your decision and all four deliverables are committed.** **3.3 is on hold by your decision (2026-09-09)**. **The market
is live**, and the twelve-hour bot re-auth was observed on 2026-09-09 (see Archive).
**Dev A reported 1.2, 2.3, 2.4, 3.4 and 4.1 done (2026-09-05)**; the stack matches in C++ and
serves ten symbols on one `config_hash`. **Contracts (1.1) FROZEN 2026-08-31**, `contracts/v1/`.

> This file is **Dev B's**; Dev A's rows are reported by me, never inferred from the repo.
> Update protocol is in `CLAUDE.md` — propose a diff, wait for confirmation, never write unasked.

---

## NOW

**Merge PR #29, then PR #30** (checkpointing, Open Issue 020) — Dev B · **next**
- #29: ledger read model froze 4 h behind (full-table rewrite per batch); chart crash on
  re-delivered bars. Verified in browser. #30: checkpointing, verified by restart-after-trim live.
- Then pick from `BUGS.md`: BUG-001 cash scale needs a decision; BUG-002/003/004 are small.

**Open the PR for `task/7.5-definition-of-done` to close Phase 1** — Dev B · **next**

- **`task/7.4-benchmark-report` is merged** (`1ebab7d`).
- **`task/7.5-definition-of-done` is 1 commit ahead of `origin/main`** (`8479f3e`): `scripts/backup_postgres.sh`, `RUNBOOK.md`, `DEFINITION_OF_DONE.md`, `README.md`.
- **matplotlib needs your approval.** Added to `requirements-dev.txt` only, for
  `benchmarks/plot_results.py`. Task 7.4's Tech Stack names it, but it is not on `CLAUDE.md`'s
  closed stack list, so it is flagged rather than assumed. No service imports it.
- **A second session is working in this checkout** — a locked worktree at
  `.claude/worktrees/docs-stitch-spec` (branch `worktree-docs-stitch-spec`, `STITCH_BRIEF.md`).
  Do not rewrite shared history without checking with it first.
- **The benchmark runs left resting probe orders in every symbol's book.** One tick, never trade,
  excluded from measurement by price. Harmless to prices; a future measurement that forgets them
  will misread the book. Cleared only by `docker compose down -v`.
- **Wrong in this file:** NOW said the benchmark probe orders were harmless. There were ~228k of
  them, they froze the ledger, and they were cleared by `docker compose down -v` on 2026-09-16.
- **`f3e5f50` on the already-merged `task/7.2-backtest-screen` deletes the whole backtester** —
  712 lines: `bars.py`, `manifest.py`, `runner.py`, `test_runner.py`. `main` is intact. Do not
  merge that branch again; decide whether to keep or delete it.
- **7.2's criterion 1 is still half verified.** Run `cd web && npm run dev`, sign in, open
  `/backtest` against the live market. The stack is up and trading — wiped 2026-09-13
  (`docker compose down -v`), so register a fresh account first.
- **Restart the matcher after any full `pytest` run** until Dev A takes `HANDOFF.md` §3a.
- **A restarted matcher is silent for 85–110 s** while it replays (measured twice: 1.63 M
  records in 85.7 s and 109.9 s). Look for `replayed N inbound records` before calling it dead.
- **`schema_version` is 2.** A stack carrying pre-Amendment-2 records needs one
  `docker compose down -v`.

*If NOW is empty or stale, ask. Do not pick a task yourself.*

## Then next

1. **3.3** Deployment half — **on hold**, and blocked on a deployment target and a TLS
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
- **On Dev A — 6.3 and 6.4 have never been reported.** 7.4 is no longer among them. The existing
  ask names only 4.2, 4.3 and 5.3; these two belong in it.
- **4.3 may now be duplicated.** 4.3 is Dev A's "Native engine benchmark (B1)" and is
  `A:unknown`; the repository held no B1 harness, and 7.4's criterion 1 cannot be met without B1
  numbers, so `engine/cpp/bench_order_book.cpp` was written. If Dev A lands 4.3, one of the two
  should go — ask before they build it.
- **On Dev A — an inbound record the engine never answered.** `client_order_id`
  1789541146582505 (user 70) sits in the inbound stream at `1789541146596-0` with no outbound
  record of any kind; 2,431 subsequent outbound records reference nothing. Not reproduced in six
  attempts; matcher `RestartCount` 0, up since 2026-09-13. The record is still in the stream.
  Found by `scripts/trace.py` on its first real use. Reported, not diagnosed — it is their code.

---

## Task board

`Wk` is the **scheduled** week from Appendix D.3 — not the chapter a task is documented under.

| Task | Own | Wk | Status | Evidence / note |
|---|---|---|---|---|
| 3.3  Public deployment and CI | B | 3 | todo | **half merged** `5efe462` — CI gate, nightly, multi-arch images. Criteria 1 and 3 (HTTPS, one-command redeploy) outstanding; see Deviations |
| 4.2  Engine process, Redis, replay recovery | A | 5 | A:todo | reported unfinished 2026-09-05 · `b644130` merged and live in compose |
| 4.3  Native engine benchmark (B1) | A | 5 | A:unknown | **possible duplicate** — see Blocked |
| 5.3  Kill-the-engine recovery script (T6) | A | 5 | A:unknown | — |
| 6.3  Open-loop load generator | A | 6 | A:unknown | — |
| 6.4  Duplicate injection in load harness | A | 6 | A:unknown | — |
| 7.4  Benchmarks, trace tool, report | ~~A~~ B | 7 | done | `add6486` · **5 of 5 criteria** · B1 `engine/cpp/bench_order_book.cpp` (matching 166 ns p50), B2 `benchmarks/bench_e2e.py` (open loop, knee 200–300/s), B3 `bench_scaling.py` (800 clients, 0 refused), `bench_conflation.py`, `bench_market_quality.py` (0 obligation breaches), `scripts/trace.py` + 5 tests, `benchmarks/results/7.4-benchmark-report.md` · **one Boundary unmet — see Deviations** |
| 7.1  Backtester | B | 7 | done | `fbf81ce` · 43 tests, all in the per-commit suite (+0.67s) · **5 of 5 criteria** · `test_two_runs_of_one_manifest_are_byte_identical`, `test_the_strategy_is_handed_one_bar_at_a_time_and_never_a_series`, `test_every_fill_pays_the_takers_fee_from_the_ledger_not_a_local_copy` |
| 7.2  Backtest screen | B | 7 | done | `3941931` · 27 tests (16 gateway, 11 web) · criterion 2 by test; criterion 1's server half verified live, browser half not — see Deviations · `tests/web/` + `tests/backtest/` 129 pass on `6485c50` · includes the `/backtests` endpoint, Dev B's by the 2026-09-09 decision |
| 7.3  Integration tests (T5, thinned) | B | 7 | done | `18e63db` · `tests/integration/`, 4 tests · **2 of 2 criteria** · criterion 2 demonstrated by breaking the stack: `stop ledger` names the ledger, `stop matcher` names the matcher |
| 7.5  Definition-of-done walk | ~~AB~~ B | 7 | done | `8479f3e` · **4 of 4 criteria** · `scripts/backup_postgres.sh`, `RUNBOOK.md`, `DEFINITION_OF_DONE.md` (30 pass, 4 partial, 1 fail with stated misses), `README.md` rewrite |

**Vocabulary.**

- Dev B and joint rows: `todo` · `wip` · `blocked` · `done`. **`done` requires a commit SHA or a
  named passing test in Evidence. No evidence, no `done`.**
- Dev A rows: `A:todo` · `A:wip` · `A:done` · `A:unknown`, always with `reported YYYY-MM-DD`.
  **Reported by me, never inferred.** Do not read the repo and conclude a Dev A task is finished
  — Dev A may push work in progress, or finish without pushing. `A:unknown` is expected.
- **At most one row may be `wip`**, and it must match NOW. None is `wip` today.

---

## Decisions made during the build

Append-only, one line each. **May not reverse anything in `CLAUDE.md`** — a reversal is a
`CLAUDE.md` edit plus an `open-issues/` amendment, proposed separately.

| Date | Decision | Why | Amends |
|---|---|---|---|
| 2026-09-16 | **Checkpointing moved into Phase 1 (020), reversing 018 §13.1–13.2**; Dev B took over the engine/matcher work it needs | — | `CLAUDE.md` amended |
| 2026-09-16 | **Dev B takes over 7.5** | Executed solo per explicit user direction to close Phase 1 deliverables | schedule only; ownership in `CLAUDE.md` Appendix D unchanged |
| 2026-09-16 | **Dev B takes over 7.4** | Dev A's 7.4 was `A:unknown` since 05 Sep and blocked the joint 7.5. Your call, made explicitly rather than drifted into | schedule only; ownership in `CLAUDE.md` Appendix D unchanged |
| 2026-09-16 | **Private frames are handed to the writer when offered, not on the conflation tick** | OI 006 exempts private data from conflation; the implementation honoured that as "never dropped" while still making it wait a uniform 0–50 ms for a tick. Measured: ptail p50 ~32 ms → ~3.4 ms, 5.2's criteria 1 and 5 re-verified | implements OI 006 as written |
| 2026-09-16 | **A known-biased metric is deleted, not annotated** | `bench_e2e`'s `book_delay` timed a frame against its own `seq`, which is the newest record folded in — it read 5–9 ms against a 50 ms window. A wrong number left in a tool's output gets quoted eventually | — |
| 2026-09-16 | **B2 load is dealt across accounts and 429s are never retried** | `max_orders_per_second` is 1,000 per user, so a single-account ramp measures the rate limiter; a retry would reintroduce coordinated omission through the side door | applies OI 012 §2 |
| 2026-09-10 | T5's diagnostic measures progress by **stream id, never by `XLEN`** | `streams.maxlen` is 2,000,000 with `MAXLEN ~` trimming, so on a day-old stack the length has plateaued and two readings can be equal while records pour through. Stream ids are monotonic and never reused (OI 003) | applies OI 003 |
| 2026-09-10 | T5 asks whether **our anchor** is on the outbound stream, not whether the stream moved | The market makers trade continuously, so outbound always advances; reading that as "the matcher is fine" blames the ledger for a matcher that has not reached us yet. One anchor per inbound record is `runner.py`'s own definition, reused rather than restated | — |
| 2026-09-10 | The anchor scan origin is sampled **before the request**, never after | The matcher answers in single-digit milliseconds, so an origin taken at the start of the wait begins after the anchor was written — the anchor is not found and the matcher is blamed for work it had done | — |
| 2026-09-10 | The anchor defect is **dormant, not live**; nothing is owed before 7.3 | `ConfigureReplay` (type 5) is forwarded by `adapter.py:_forward` as one `ReplayConfigured`, and `ReplayConfigured` is in `runner.py:is_anchor` — Amendment 2 closed its own candidate as it landed. Inbound types 1–5 each produce exactly one anchor, so no zero-anchor type exists at `schema_version` 2 | re-checks the 2026-09-09 entry |
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

- **`18e63db` was committed to `main`, not to a task branch.** The checkout moved from
  `task/7.3-integration-tests` back to `main` between the branch being created and the commit
  being made (reflog `HEAD@{4}`), and the branch was not re-checked before committing. Corrected
  2026-09-10, nothing lost. Recorded because the PR-only rule is what it broke.
- **`.claude/worktrees/` is untracked and not ignored.** `.gitignore` covers only
  `.claude/settings.local.json`, so a full nested worktree sits in the repo as untracked.
  `f3e5f50` adds the `.claude/` line, but that commit is stranded on a merged branch.
- **7.3's liquidity precondition is a loud skip, not a guarantee.** The fill half of the
  critical path needs a counterparty, and the only source is the bots profile. The test probes
  with a market order and skips naming the reason if the ask side is empty. It also skips
  whenever the stack is unreachable — which is every CI run, since `ci.yml` runs
  `-m "not property"` and provides only Redis and Postgres. **A green CI run has not verified
  Task 7.3.**
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
- **7.4's Boundary "run on the scratch deployment" is unmet, and unmeetable.** There is no
  scratch deployment: 3.3's deployment half is on hold pending a deployment target. Everything
  ran on one laptop, harness included. Stated before any number in the report and first in its
  §9. Every harness is parameterised by host, so a second machine is a flag, not a rewrite.
- **The benchmark report quotes no figure above ~300 orders/sec.** Above that the single-process
  open-loop generator is the bottleneck: when the server slows, pending tasks crowd the event
  loop and delay the wakeups of orders not yet sent. The 800/s row is printed and explicitly
  marked not-evidence (`send_slip` p99 2.9 s) rather than deleted, because the reason generalises.
- **B3 cannot confirm OI 006 §7b on this host.** Ack latency does move with the client count
  (p50 6.41 ms at 50 → 24.63 ms at 800), but the harness holds all 800 sockets on the same ten
  cores as the gateway it is timing, so "connection load reached the order path" and "everything
  is fighting for the same cores" are indistinguishable. At 200 clients — the count 5.2's
  criterion names — it is 7.90 against 6.41, inside noise. This is the re-measurement 5.2's
  results file asked 7.4 for; the answer is that it still needs two hosts.
- **`tests/integration/test_critical_path.py` errors on a clean tree** — 4 errors, an anyio
  async-fixture problem at setup. Verified by stashing: pre-existing, not from this session.
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
