# Open Issue 018 — Simplification Pass and Three-Phase Structure

**Status:** CONFIRMED (2026-08-28) — simplification pass and three-phase structure agreed
**CURRENT STATE:** Phase 1 ~358 h against ~430 effective. Supersedes OI 013 §2–§5.
**Opened:** 2026-08-28
**Supersedes:** Open Issue 013 §2–§5 (phase lists), if adopted
**Owner:** _unassigned_

---

## 1. Why this pass

Phase 1 stood at **412 hours against roughly 430 effective**, with estimates that historically
run 30–50% over for unfamiliar work. That is not a plan; it is a hope with a deadline attached.

The test applied to every decision below is deliberately narrow:

> **Does this contribute to the demonstration, the headline claim, or the evidence?**

A resume project needs a working system, one or two genuinely strong technical claims, a README
that explains the reasoning, and evidence that the claims are true. It does **not** need
completeness. Anything that is merely *good* rather than *load-bearing* moves to a later phase.

A second principle, applied throughout: **several things are better as Phase 2 upgrades than as
Phase 1 features**, because arriving second lets them carry a before/after comparison. That is a
stronger artifact than having built the sophisticated version first.

---

## 2. Issue-by-issue verdict

| Issue | Verdict | Reasoning |
|---|---|---|
| **001** Exchange core | **Keep whole** | This is the project. Single-writer, deterministic, replayable. Nothing to remove |
| **002** C++ engine + model + adapters | **Keep whole** | The C++ engine is the headline claim for quant-adjacent roles. Cutting it removes the reason the project exists. Both adapters earn their place: the standalone binary runs live, the nanobind adapter serves tests |
| **003** Redis Streams | **Keep** | Already the simpler of the two options |
| **004** Money and reservations | **Keep whole** | Approach A is the simplest correct answer. Market orders as banded limits cost ~3 h and prevent a thin book being swept |
| **005** Bots | **Trim** | 12 symbols → **5**. See §3.1 |
| **006** Fan-out | **Keep whole** | 35 h, and it is where the interesting performance story lives |
| **007** Deployment | **Keep whole** | 20 h. Config hash stamping is 2 h and removes a category of debugging |
| **008** Idempotency | **Keep whole** | Required by README.md Problem 4, and the mechanism is now settled |
| **010** Testing | **Trim slightly** | T1–T4 whole; **thin T5** by 4 h. T6 stays — it is the demo's strongest beat |
| **011** Backtesting | **Cut hard** | 63 h → **25 h**. The largest and most valuable simplification. See §3.2 |
| **012** Benchmarking | **Trim** | Fold the B3 fan-out harness into B2 (−4 h). Everything else stays |
| **014** Frontend | **Trim** | Follows the backtester simplification (−4 h) |
| **015** Security | **Trim** | Email verification → Phase 2 (−4 h). See §3.3 |
| **016** Schema | **Keep whole** | Week-1 work that everything else is written against. Codegen is cheap insurance against silent drift |
| **017** Sandboxing | Already Phase 2 | Only the zero-cost Phase 1 constraint applies |

---

## 3. The four cuts that matter

### 3.1 Symbols: 12 → 5

Twelve symbols was chosen to make the market feel real. Five does that just as well, and it
reduces bot configuration, fan-out load, archive volume, and interface complexity.

Nothing in the design depends on the count. The fan-out benchmark needs *several* symbols to
exercise subscription filtering, not twelve. **Saving: ~3 hours**, plus a permanently smaller
system to reason about.

### 3.2 Backtester: 63 h → 25 h — the important one

The backtester was 15% of Phase 1 for what is the project's **second** headline. Two half-told
stories are worth less than one complete one.

| Component | Phase 1 was | Phase 1 now | Moves to |
|---|---|---|---|
| Archiver | 8 | 8 | — |
| Bar aggregation | 6 | 6 | — |
| Backtest runner | 10 | 8 | — |
| **Fill simulation** | **12 (Option 3, engine-based)** | **4 (Option 1, bar-based)** | **Phase 2** |
| Metrics + buy-and-hold | 8 | 6 | — |
| Built-in strategies | 6 (three) | 2 (one) | Phase 2 |
| Results page | 10 (equity curve + drawdown charts) | 4 (metrics table) | Phase 2 |
| Run manifest | 3 | 3 | — |
| **Total** | **63** | **~25** | |

**This reverses the Option 3 decision, and deliberately.** The reasoning has changed because the
phase structure has:

Option 3 in Phase 1 is a claim — *"backtest fills obey live rules."* Option 3 in **Phase 2** is a
**comparison**: *"we replaced naive bar-based fills with engine-based fills against reconstructed
order books; here is how much the reported returns changed."*

The second is a better artifact and it is cheaper, because it arrives after the archive, the
adapter and the test suite already exist. It also demonstrates something the first cannot — that
the team understood **why** naive fill simulation overstates returns, and then measured it.

The nanobind adapter is unaffected; it is still built in Phase 1 for the test suite, which is
where Open Issue 002 justified it in the first place.

### 3.3 Email verification → Phase 2

Confirmed as non-blocking in Phase 1, which means it currently **does nothing** — a user can
trade immediately whether or not they click the link. Four hours, a mail-provider dependency,
and a new failure mode, in exchange for no behaviour.

It becomes worthwhile in Phase 2, when competitions give throwaway accounts a reason to be
prevented. **Saving: 4 hours and one external dependency.**

### 3.4 Smaller trims

| Trim | Saving |
|---|---|
| Fold the B3 fan-out benchmark into B2 rather than a separate harness | 4 |
| Thin T5 integration tests to the critical path only | 4 |
| Frontend backtest screen simplifies with the backtester | 4 |

---

## 4. Revised Phase 1 budget

| | Hours |
|---|---|
| Before this pass | 412 |
| Backtester simplification (§3.2) | −38 |
| Email verification deferred (§3.3) | −4 |
| Symbols 12 → 5 (§3.1) | −3 |
| B3 folded into B2 | −4 |
| T5 thinned | −4 |
| Frontend follows the backtester | −4 |
| **Revised** | **~355** |
| Effective budget | ~430 |

**Roughly 75 hours of slack — about 20%.** That is the first point in this planning process
where the schedule can absorb the overrun rate that work of this kind actually incurs.

---

## 5. PHASE 1 — the resume project (by 15 October)

**Headline claim:** *a correct exchange core — C++ matching engine, deterministic, differentially
tested against a reference implementation, with honestly measured performance.*

**Exchange**
- C++ single-writer matching engine: limit orders, cancels, partial fills, price-time priority
- Market orders as marketable limit orders with a price band
- Self-trade prevention
- Naive Python model as executable specification and test oracle
- nanobind adapter for tests

**Event flow**
- Redis Streams inbound and outbound; stream ID is the sequence
- Snapshot plus replay recovery
- Archiver: trades, bars, 1 Hz L2 snapshots
- Halt state when the stream is unreachable

**Gateway**
- Sessions, Argon2id, Redis-backed
- Risk checks with in-memory reservations
- Idempotency: mandatory client order IDs, atomic claim-and-append
- Rate limit, input validation and bounds

**Market**
- **5 symbols**, fair value from replayed real crypto history at 1 s : 1 min
- Designated market maker with quoting obligations, plus noise traders
- Bots as real API clients, doubling as the load generator

**Real time**
- WebSocket; L1 and L2 (N = 10) conflated at 20 Hz; un-conflated tape
- Separate fan-out process; private stream with sequence numbers

**Research**
- Bar-based backtester, **one** built-in strategy, metrics table with mandatory buy-and-hold
  comparison, run manifest and reproducibility test

**Frontend**
- Three screens: trading, auth, backtest. React, Lightweight Charts, rAF render loop

**Engineering**
- T1–T4 in full, T5 thinned, T6 complete
- Invariants I1–I12; regression corpus per commit, generated tests nightly
- Open-loop load generation; B1 and B2 reported separately; benchmark report
- Structured logs, `trace <client_order_id>`, offline analysis plots
- Docker, CI, public deployment

---

## 6. PHASE 2 — depth (November–December)

**Headline claim:** *we measured, then improved — and here are the numbers on both sides.*

Ordered by value:

1. **Engine-based backtest fills (Option 3)**, published as a before/after comparison against
   the Phase 1 bar-based results
2. **User strategies**: restricted DSL first, then sandboxed Python on OS limits
3. **Competitions**, ranked by risk-adjusted return; email verification becomes meaningful here
4. Additional strategies, equity-curve and drawdown charts
5. Momentum and value bots for microstructure realism
6. **Analytics as a separate service** — the one legitimate microservice boundary
7. Margin accounts and retail short selling
8. Delta encoding and a binary protocol, each justified by a measured bottleneck
9. Backtesting against this platform's own accumulated history

---

## 7. PHASE 3 — the last improvements

Genuinely optional; each is a self-contained piece of work with a publishable result.

1. **Memory-mapped log replacing Redis Streams**, with before/after latency numbers — the
   migration analysed and costed in Open Issue 009 §8
2. Engine sharding by symbol across threads or processes
3. Multiple gateways behind a dedicated sequencer
4. L3 order-by-order market data feed
5. Market-impact modelling in backtests
6. Multi-region and high availability

**Never:** real money, or integration with a real exchange.

---

## 8. What this pass deliberately did not cut

Recorded so these are understood as decisions rather than oversights:

- **The C++ engine.** It is the headline. A Python engine would meet the throughput target and
  remove ~46 hours, and it would also remove the reason the project is worth showing.
- **The differential test harness.** Two independent implementations agreeing across millions of
  generated operations is the strongest correctness claim available, and it is what makes
  optimising the C++ engine safe.
- **T6, the kill-the-engine recovery test.** Five hours, and it is simultaneously the demo's
  strongest beat, the Reliability evidence, and the recovery-time measurement.
- **The separate fan-out process.** Four hours more than running it inside the gateway, and it
  is what keeps order-acknowledgement latency clean under connection load.
- **Open-loop load generation.** The methodology *is* the claim; a closed-loop benchmark would
  produce numbers that do not survive scrutiny.

---

## 9. Questions to resolve

1. **Is the backtester cut acceptable?** It reverses the Option 3 decision. The argument is that
   Option 3 is worth more as a Phase 2 comparison than as a Phase 1 feature — but it does mean
   Phase 1 ships a backtester that is honest about its own limitations rather than one that
   models fills properly.
2. **Five symbols, or a different number?**
3. **Is one built-in strategy enough for Phase 1**, with two more in Phase 2?
4. **Does the Phase 2 ordering match your priorities?** As written it leads with the backtest-fill
   comparison rather than with user strategies.

---

## 10. Decision log

| Date | Status | Note |
|---|---|---|
| 2026-08-28 | OPEN | Simplification pass across all issues; Phase 1 reduced 412 h → ~355 h; three-phase structure proposed. Nothing final. |

---

## 11. Confirmed 2026-08-28

| # | Answer |
|---|---|
| 1 | **Backtester cut accepted.** Option 3 engine-based fills move to Phase 2 as a before/after comparison |
| 2 | **10 symbols**, not 5 |
| 3 | **One built-in strategy** in Phase 1; two more in Phase 2 |
| 4 | **Phase 2 ordering confirmed** — leading with the fill comparison, then user strategies |

### 11.1 Ten symbols — cost, and why it is small

Reverting from 5 to 10 costs roughly **3 hours**, not more, because **the symbol count is
configuration rather than code**. Nothing in the engine, gateway, fan-out or archiver varies
with it; each symbol is an entry in the shared configuration file (Open Issue 007 sub-decision
8d) plus a set of bot instances.

| Dimension at 10 symbols | Figure |
|---|---|
| Archived L2 snapshots at 1 Hz (~400 B each) | ~345 MB/day |
| Bot instances | 10 market makers plus noise traders |
| Engine utilisation | Still a single thread at low single-digit percent |
| Fan-out | Subscription filtering means a client still receives one symbol |

The 3 hours are bot configuration and tuning, not implementation. **Revised Phase 1 total: ~358
hours against ~430 effective — roughly 72 hours of slack.**

### 11.2 The Phase 1 backtester, stated plainly

With Option 3 deferred, Phase 1 ships a backtester that is **honest about its own limitations
rather than one that models fills properly**. That framing should appear in the README and in
the report itself, not be discovered by a reader:

> Fills are simulated at the next bar's open. This ignores spread, queue position and available
> liquidity, and therefore **overstates the returns of any strategy that trades frequently**.
> Engine-based fill simulation against reconstructed order books is Phase 2.

Stating the limitation is worth more than concealing it. It also sets up the Phase 2 comparison
as an answer to a question the reader has already been invited to ask.

## 12. Decision log

| Date | Status | Note |
|---|---|---|
| 2026-08-28 | OPEN | Simplification pass; Phase 1 reduced 412 h → ~355 h; three phases proposed |
| 2026-08-28 | **CONFIRMED** | Cuts accepted; 10 symbols (~358 h total); one Phase 1 strategy; Phase 2 ordering agreed. |

---

## 13. Remaining sub-decisions resolved 2026-08-28

Decided against three criteria: interview and resume value; low complexity; and a path to a more
sophisticated solution in a later phase.

Nine of the eleven are taken as proposed. **Two have a simpler answer than was proposed**, and
both improve on all three criteria at once.

### 13.1 SIMPLIFIED — 4b: no snapshots or checkpointing in Phase 1

**Proposed was:** periodic checkpoint (offset plus state snapshot), replay forward on restart —
for both the engine and the gateway's risk state.

**Decided:** **no snapshots. Every consumer rebuilds by replaying the retained stream from its
start.**

| Criterion | Why the simpler answer wins |
|---|---|
| Interviews | *"State is rebuilt by replaying the log"* is the answer that demonstrates the idea. Checkpointing is an **optimisation of** that idea, not the idea itself |
| Complexity | Removes a snapshot format, a snapshot write path, and the consistency question of whether a snapshot matches its offset — the subtlest correctness risk in the recovery path |
| Later phase | Phase 2 adds checkpointing **with a measured before/after on recovery time**, which `README.md` Goal 5 already names as a metric |

**Saving: roughly 12 hours** across the engine and the gateway.

**Recovery time becomes a real, honest number.** At a retained window of ~2 M entries, a full
replay is on the order of ten seconds — acceptable, visible in the demonstration's beat 6, and
precisely the kind of figure a Phase 2 optimisation improves dramatically.

**What this requires (§13.2):** the retained window must comfortably exceed any session the
system is expected to survive.

### 13.2 SIMPLIFIED — 4a: the archive is not correctness-critical in Phase 1

**Proposed was:** the Redis stream is authoritative over the retained window and **the archive is
authoritative beyond it** — which made the archiver load-bearing for correctness, since falling
behind trimming would lose history permanently.

**Decided:** set `MAXLEN` generously — around **2 million entries** — so the retained window
exceeds any realistic Phase 1 session. The stream alone is then authoritative, and **the archive
exists solely to produce backtest data**, not to preserve correctness.

This works because of a scheduling consequence that was easy to miss: **Phase 1 backtests run
against real crypto history** (Open Issue 011 sub-decision 11a), not against this platform's own
market. Own-market history is a Phase 2 need. So nothing in Phase 1 depends on retaining a long
archive.

Two operating rules make the window sufficient:

| Rule | Reason |
|---|---|
| Bots run at a **modest idle rate** (roughly 10–50 orders/sec) on the demo deployment | 2 M entries then covers 11–55 hours — longer than any session |
| **Benchmarks run on a scratch deployment**, not the demo instance | A load test at 20k/sec consumes 2 M entries in 100 seconds. Isolating benchmark runs is honest practice anyway, and it keeps benchmark numbers uncontaminated |

**Result:** one less correctness dependency, one less way to lose data silently, and the archiver
demoted from critical to convenient. Phase 2 restores the archive to an authoritative role when
own-market history begins to matter.

**Statement of 4a as decided:** the Redis stream is the source of truth for money. The relational
database is a derived read model, never authoritative, and rebuildable from the stream. The
archive produces backtest data.

### 13.3 Taken as proposed

| # | Decision | Note against the criteria |
|---|---|---|
| **4d** | Market orders as marketable limit orders with a price band | ~3 h. Price bands and circuit breakers are a genuine domain detail, and they prevent a thin book being swept |
| **8a** | Process inventory as recorded, plus Redis and the archiver | No work; confirmation only |
| **8d** | One version-controlled configuration file, content hash stamped into the stream at startup | 2 h. Every recorded session and benchmark result becomes self-describing |
| **8e** | Record the scaling paths the design admits without walking them | Documentation only. Keeps the benchmark report from overstating |
| **9d** | 1-hour TTL on idempotency keys | A configuration value |
| **9e** | Cancels carry a key for uniformity; `CreateAccount` and `CreditCash` use the same mechanism and are the dangerous cases | No added complexity |
| **9f** | Load harness injects duplicates at a configurable rate | 2 h, and the **highest interview value per hour in this list**: it converts a defensive mechanism into a measured claim |
| **11f** | Run manifest; "run twice, assert byte-identical" as a per-commit test | 3 h. Reproducibility is a stated Goal 3 success condition, and this makes it testable rather than aspirational |

### 13.4 11e — taken, with the list trimmed

Retained: **P&L, return %, trade count, win rate, maximum drawdown, volatility, Sharpe, and the
mandatory buy-and-hold comparison.**

Dropped from Phase 1: average/best/worst trade, and time in market. They add rows without adding
insight, and the buy-and-hold comparison already carries the "was this better than doing nothing"
question that matters most. **Saving: ~1 hour**, and a less cluttered report.

### 13.5 Revised budget

| | Hours |
|---|---|
| After the simplification pass, with 10 symbols | 358 |
| No snapshots or checkpointing (§13.1) | −12 |
| Metrics list trimmed (§13.4) | −1 |
| **Phase 1 total** | **~345** |
| Effective budget | ~430 |

**Roughly 85 hours of slack — about 20%**, with every remaining item load-bearing for the
demonstration, the headline claim, or the evidence.

### 13.6 What moved into Phase 2 as a result

Both additions strengthen Phase 2 rather than padding it, because each arrives with a
measurement attached:

1. **Checkpointing and snapshots**, with a before/after on recovery time
2. **The archive restored to an authoritative role**, once own-market history is needed for
   backtesting

## 14. Decision log

| Date | Status | Note |
|---|---|---|
| 2026-08-28 | OPEN | Simplification pass; three phases proposed |
| 2026-08-28 | CONFIRMED | Cuts accepted; 10 symbols; one Phase 1 strategy; Phase 2 ordering agreed |
| 2026-08-28 | **RESOLVED** | Eleven remaining sub-decisions decided. 4b and 4a simplified (no snapshots; archive not correctness-critical). Phase 1 now ~345 h. |
