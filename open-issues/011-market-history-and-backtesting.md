# Open Issue 011 — Market History and the Backtesting Engine

**Status:** RESOLVED (2026-08-28) — all sub-decisions decided; Option 3 deferred to Phase 2
**CURRENT STATE:** Real crypto history as the Phase 1 dataset · 1 Hz archived L2 snapshots ·
Option 3 fill simulation through the real engine · maker/taker fees in basis points.
**Opened:** 2026-08-27
**Serves:** README.md Goal 3 (quantitative research and backtesting)
**Depends on:** Open Issue 003 §8.4 (archiver), Open Issue 005 (data sources), Open Issue 002 (nanobind adapter)
**Owner:** _unassigned_

---

## 1. The problem

`README.md` Goal 3 requires that a user select a strategy, choose market data, run a
backtest, and receive meaningful performance and risk metrics — reproducibly.

Four questions have to be answered, and the third is the one that determines whether the
results mean anything:

1. Where does historical data come from?
2. In what form is it stored?
3. **How are the strategy's orders filled against that history?**
4. What is reported?

---

## 2. Sub-decision 11a — Data sources

Two, established in earlier issues, and they are complementary rather than competing:

| Source | Origin | Purpose |
|---|---|---|
| **This platform's own market** | Archived from the outbound event stream | Demonstrates the loop: the exchange generates the data its own research tools consume |
| **Real crypto history** | Open Issue 005 §10.1, use U2 | Independently credible; a reader can sanity-check a result against a market they can look up |

Supporting both separates two questions that are otherwise entangled: *is the backtester
correct?* and *is the simulated market realistic?* A strategy that behaves sensibly on real
BTC history and on this platform's own recorded market is far more convincing than one
validated against either alone.

---

## 3. Sub-decision 11b — What is archived, and at what resolution

The archiver (Open Issue 003 §8.4) tails the outbound event stream and writes three
derivatives:

| Artefact | Resolution | Daily volume (12 symbols) | Purpose |
|---|---|---|---|
| **Trades** | Every trade | Small — trades are far rarer than orders | The tape; bar construction; fill validation |
| **OHLCV bars** | 1 second, aggregated to 1 minute | Negligible | Strategy signals; charts |
| **L2 book snapshots** | **1 Hz** | ~0.4 GB | Fill simulation (§4) |

The book snapshots are the notable line. Fan-out already produces L2 snapshots at 20 Hz
(Open Issue 006), so archiving is a matter of sampling a stream that already exists. At 20 Hz
this would be roughly 8.3 GB/day; **at 1 Hz it is about 0.4 GB/day**, which is ample for
backtests operating on 1-minute bars.

Bar aggregation is not backtester-specific work — the live chart needs exactly the same
pipeline, so it is built once and used twice.

---

## 4. Sub-decision 11c — Fill simulation (the decision that matters)

How a backtester fills orders determines whether its results are meaningful. This is where
most amateur backtesters quietly become worthless.

### Option 1 — Fill at the next bar's open

**For:** roughly 4 hours; trivial.

**Against:** ignores spread, liquidity, and partial fills entirely. Every strategy looks
better than it would be in reality — often dramatically so for anything trading frequently.
A backtest that cannot lose money for the right reasons cannot demonstrate anything.

### Option 2 — Bar-based with a slippage and fee model

Add a fixed or volume-scaled penalty per trade.

**For:** cheap; considerably more honest than Option 1.

**Against:** the penalty is an assumption, not a measurement. It is a guess about liquidity
dressed as a model.

### Option 3 — Reconstruct the book from an archived L2 snapshot and match with the real engine

At each decision point, load the archived L2 snapshot for that timestamp, reconstruct a book
inside a real engine instance via the **nanobind adapter** (Open Issue 002 §5), inject the
strategy's order, and let it match under exactly the live matching rules.

**For:**
- Spread, queue position, partial fills, and walking multiple price levels all emerge from
  the actual engine rather than from an assumed penalty.
- **Backtest fills obey exactly the same rules as live trading, because it is the same
  engine.** Very few simulated backtesters can make that claim, and it is the specific reason
  the nanobind adapter was justified in Open Issue 002.
- The archived snapshots already exist as a by-product of fan-out.

**Against:** more work than Option 1 — roughly 12 hours. Requires the archived snapshots.

### Option 4 — Full market-impact model

The strategy's orders move the price and other participants react.

**Against:** research-grade work, far beyond the available time. Phase 3 at the earliest.

**Proposed: Option 3**, with Option 1 retained as a fast approximate mode for iterating on a
strategy before running it properly.

### The honest limitation, which must be stated in the results

If the strategy's order fills against a historical resting order, **in reality that order
would have filled against someone else.** History is being altered. This is the fundamental
limitation of every backtest, not a defect of this implementation.

The standard treatment is to assume order sizes are small enough that market impact is
negligible, which holds for realistic retail sizes and fails for large ones. **Proposed:** the
backtest report states this assumption explicitly and warns when an order consumes more than
a configurable share of displayed depth. That warning costs almost nothing and is the
difference between a result that is honest about its limits and one that is quietly wrong.

---

## 5. Sub-decision 11d — Strategy interface

Phase 1 uses **built-in strategies only** — no user-submitted code, and therefore no sandbox
(Goal 4 remains Phase 2 or later, per the Phase 1 scope).

**Proposed interface:** a callback receiving the current bar and portfolio state and returning
zero or more orders. Deliberately minimal, with no access to future bars — which is enforced
structurally by feeding bars one at a time rather than by convention.

Lookahead bias is the most common way a backtest becomes silently wrong. **Structural
prevention is worth more than a warning in the documentation.**

Built-in strategies for Phase 1: **SMA crossover** (trend), **mean reversion** (fade
deviations from a moving average), and **buy and hold** as the baseline every result is
compared against.

---

## 6. Sub-decision 11e — Metrics

| Metric | Note |
|---|---|
| Total P&L, and return % | |
| Number of trades | Exposes strategies that look good but trade too rarely to be meaningful |
| Win rate | Reported, but weak in isolation |
| **Maximum drawdown** | Named in README.md; the most useful single risk number |
| Volatility of returns | |
| Sharpe ratio | Return per unit of risk |
| Average trade, best, worst | |
| Time in market | Distinguishes a strategy from luck about being invested |
| **Buy-and-hold comparison** | Proposed as mandatory on every report |

The buy-and-hold comparison deserves the emphasis. A strategy returning 8% in a market that
returned 20% has lost money in the only sense that matters, and reporting the absolute number
alone conceals that. Making the comparison mandatory is roughly ten lines of code and it is
the single most honest thing the report can do.

---

## 7. Sub-decision 11f — Reproducibility

`README.md` Goal 3 defines success as the same strategy, data, and configuration producing
the same result on a rerun.

**Proposed:** every backtest run records a **run manifest** — strategy identifier and
parameters, dataset identifier and date range, configuration content hash (Open Issue 007
sub-decision 8d), engine version, and random seed. Reproducibility then becomes a testable
property rather than an aspiration: **run twice, assert byte-identical results.** That test
belongs in the per-commit suite from Open Issue 010 §10a, since it is fixed and fast.

---

## 8. Cost summary

| Item | Hours |
|---|---|
| Archiver: event stream → trades, bars, L2 snapshots | 8 |
| Bar aggregation (shared with the live chart) | 6 |
| Backtest runner: bar iteration, strategy invocation, portfolio tracking | 10 |
| Fill simulation via engine and snapshot reconstruction (Option 3) | 12 |
| Metrics and buy-and-hold comparison | 8 |
| Three built-in strategies | 6 |
| Results page: equity curve, drawdown chart, metrics table | 10 |
| Run manifest and reproducibility test | 3 |
| **Total** | **63** |

Against **45 hours** previously budgeted for the "thin slice" backtester, plus 8 for the
archiver, giving 53. The gap is roughly **10 hours**, and it is attributable almost entirely
to choosing Option 3 over Option 1 for fill simulation.

**The cut, if week 6 is tight:** fall back to Option 1 fill simulation (−12 h) and drop to one
built-in strategy (−4 h). That preserves a working, honest backtester and loses the strongest
claim it could otherwise make.

---

## 9. Questions to resolve

1. **Option 3 fill simulation, or Option 1?** Option 3 costs about 12 hours and produces the
   claim that backtest fills obey the same rules as live trading. It is also the reason the
   nanobind adapter was justified in the first place.
2. **Fees — this now needs an answer.** Open Issue 004 §6.3 left it open. It matters more here
   than anywhere else: fees are what kill most naive strategies, and a fee-free backtest will
   make high-frequency strategies look profitable when they are not. Roughly two hours for a
   flat per-trade fee.
3. **Is 1 Hz the right archive resolution for L2 snapshots?** Adequate for 1-minute-bar
   strategies; insufficient if intraday strategies are ever wanted at finer resolution.
4. **Should backtests run against real crypto history in Phase 1, or only against this
   platform's own recorded market?** Supporting both costs little once the bar format is
   shared, but it is another moving part in week 6.

---

## 10. Decision log

| Date | Status | Note |
|---|---|---|
| 2026-08-27 | OPEN | Sub-decisions 11a–11f proposed; Option 3 fill simulation recommended; budget gap of ~10 h identified. Nothing final. |

---

## 11. Amendment 2026-08-27 — answers recorded

- **11c — Option 3 confirmed.** Fill simulation reconstructs a book from an archived L2
  snapshot and matches through the real engine via the nanobind adapter.
- **11b — 1 Hz L2 archive resolution confirmed**, under the scoping principle in §11.1.
- **11g — fees are adopted** (see §11.2).
- **11a — real crypto history is the Phase 1 backtest dataset.** This platform's own recorded
  market becomes a second dataset once enough history has accumulated.

### 11.1 Scoping principle recorded

Stated during this decision and worth carrying into every remaining cut: **the objective is to
demonstrate the capability, not to build a research-accurate platform.**

Applied here, 1 Hz L2 snapshots are adequate. They are not adequate for genuine
high-frequency research, and the report should not imply otherwise. Where a choice lies
between "correct enough to demonstrate the technique" and "accurate enough to trust with real
money," Phase 1 takes the former — deliberately, and stated as such.

### 11.2 Sub-decision 11g — fee model

Fees affect the ledger, the invariants, and the backtester, but **not the engine**, which
stays money-blind per Open Issue 004.

**Proposed: a maker/taker model in basis points**, rather than a flat per-trade fee. The cost
is roughly the same — about two hours — and it is materially more realistic:

| Side | Meaning | Example rate |
|---|---|---|
| **Taker** | The aggressive order that crossed the spread and removed liquidity | 10 bps |
| **Maker** | The resting order that was hit and had provided liquidity | 2 bps |

Three reasons this is worth the same two hours as a flat fee:

1. It is what real exchanges — and every crypto venue, matching the chosen dataset — actually
   charge.
2. It creates a genuine incentive structure: strategies that cross the spread constantly pay
   for it, which is exactly the effect that makes a fee-free backtest lie about
   high-frequency strategies.
3. It gives the designated market maker (Open Issue 005) an economically coherent reason to
   exist, since it earns the maker rate for providing liquidity.

**One small engine consequence.** Maker/taker requires knowing which side was the aggressor.
The engine already knows — the incoming order is always the taker — so the fill event gains an
`aggressor_side` field. This is a schema addition, not accounting logic, so the engine remains
money-blind.

**Consequence for Open Issue 004 invariant I10.** Cash conservation must now include a house
fee account: the sum of all user cash, all reservations, and the fee account is constant
except at explicit deposit events. Without that term the invariant would fail as soon as fees
are charged.

### 11.3 Consequence of 11a — a scheduling benefit

Using real crypto history first is not only a data-quality decision; it **removes a
dependency from week 6**. This platform's own recorded market requires the exchange to have
been running long enough to accumulate history, which competes directly with the weeks in
which the exchange is still being built. Real history is available on day one, so the
backtester can be developed and tested well before the platform has generated anything of its
own.

## 12. Decision log

| Date | Status | Note |
|---|---|---|
| 2026-08-27 | OPEN | Sub-decisions 11a–11f proposed; Option 3 recommended |
| 2026-08-27 | AMENDED | Option 3 confirmed; 1 Hz confirmed; maker/taker fees adopted (11g); real crypto history as the Phase 1 dataset; scoping principle recorded in §11.1. Still not final. |

---

## 13. Option 3 reconfirmed 2026-08-28

Sub-decision 11c — **Option 3 fill simulation** — is reconfirmed. It was already recorded in
§11; this restates it against the completed picture rather than changing anything.

At each decision point the backtester loads the archived L2 snapshot for that timestamp,
reconstructs a book inside a real engine instance through the **nanobind adapter**, injects the
strategy's order, and lets it match under exactly the live matching rules. Spread, queue
position, partial fills and walking multiple price levels all emerge from the engine rather
than from an assumed slippage penalty.

**This is the decision that justifies the nanobind adapter** in Open Issue 002 §5, and it
produces the claim that backtest fills obey the same rules as live trading — because it is the
same engine.

**The stated limitation stands and must appear in the report:** if the strategy's order fills
against a historical resting order, in reality that order would have filled against someone
else. History is being altered. Order sizes are assumed small enough for market impact to be
negligible, and the report **warns when an order consumes more than a configurable share of
displayed depth**.

### 13.1 Still open in this issue

| # | Proposal |
|---|---|
| **11d** | **Strategy interface**: a callback receiving the current bar and portfolio, returning zero or more orders. Bars fed one at a time so lookahead is prevented **structurally**, not by convention. Three built-in strategies in Phase 1 — SMA crossover, mean reversion, buy-and-hold — and no user-submitted code |
| **11e** | **Metrics**: P&L, return %, trade count, win rate, maximum drawdown, volatility, Sharpe, average/best/worst trade, time in market, and a **mandatory buy-and-hold comparison** on every report |
| **11f** | **Reproducibility**: every run records a manifest — strategy and parameters, dataset and date range, configuration content hash, engine version, seed — making "run twice, assert byte-identical" a fixed, fast test in the per-commit suite |

11d also carries the Phase 1 constraint from Open Issue 017 §6: the strategy receives plain
data, returns plain data, performs no I/O, and holds no reference to the engine, the adapter or
the dataset. Honouring that now makes the Phase 2 sandbox a serialisation task rather than a
redesign.

## 14. Decision log

| Date | Status | Note |
|---|---|---|
| 2026-08-27 | OPEN | 11a–11f proposed; Option 3 recommended |
| 2026-08-27 | AMENDED | Option 3 confirmed; 1 Hz confirmed; maker/taker fees; real crypto history first |
| 2026-08-28 | **CONFIRMED** | Option 3 reconfirmed against the completed picture. 11d, 11e, 11f remain open. |

---

## 15. Sub-decision 11d confirmed 2026-08-28 — a controlled Python interface

The strategy interface is a **controlled Python interface**: strategies are ordinary Python, but
what the interface hands them and accepts back is tightly constrained.

| Property | Rule |
|---|---|
| Input | **Plain data** — the current bar and the strategy's own portfolio. Never live objects |
| Output | **Plain data** — zero or more order records, reusing the Open Issue 016 schema |
| I/O | None in the contract |
| References | No handle to the engine, the nanobind adapter, or the dataset |
| Time | Bars are fed **one at a time**, so lookahead is prevented structurally rather than by convention |

Phase 1 ships **three built-in strategies** — SMA crossover, mean reversion, and buy-and-hold as
the baseline — with no user-submitted code.

### 15.1 Why "controlled" is the operative word

The same four rules serve two entirely different purposes, which is what makes them worth
honouring precisely:

1. **Correctness now.** Feeding bars one at a time makes lookahead bias impossible rather than
   discouraged. Lookahead is the most common way a backtest becomes silently wrong, and a
   structural guarantee beats a documented warning.
2. **Security later.** These are exactly the constraints in Open Issue 017 §6. A contract that
   is already plain-data-in, plain-data-out with no references and no I/O can be moved across a
   process boundary in Phase 2 as **serialisation work rather than redesign**.

Breaking a rule for convenience — passing the backtester's engine handle into a built-in
strategy, say — would cost nothing visible in Phase 1 and would convert the Phase 2 sandbox into
a rewrite. That is why the constraint is recorded in two issues.

### 15.2 Still open in this issue

| # | Proposal |
|---|---|
| **11e** | Metrics: P&L, return %, trade count, win rate, maximum drawdown, volatility, Sharpe, average/best/worst trade, time in market, and a **mandatory buy-and-hold comparison** on every report |
| **11f** | Run manifest — strategy and parameters, dataset and range, configuration hash, engine version, seed — making "run twice, assert byte-identical" a fixed, fast per-commit test |

## 16. Decision log

| Date | Status | Note |
|---|---|---|
| 2026-08-27 | OPEN | 11a–11f proposed; Option 3 recommended |
| 2026-08-27 | AMENDED | Option 3 confirmed; 1 Hz; maker/taker fees; real crypto history first |
| 2026-08-28 | CONFIRMED | Option 3 reconfirmed |
| 2026-08-28 | **CONFIRMED** | 11d confirmed as a controlled Python interface; the four rules serve both lookahead prevention and the Phase 2 sandbox. 11e, 11f remain open. |

---

## Amendment 2026-08-28 — simplification pass (Open Issue 018)

Option 3 engine-based fill simulation moves to **Phase 2**, published as a before/after comparison against Phase 1 bar-based results. Phase 1 uses **Option 1** — fill at the next bar open — with **one** built-in strategy and a metrics table rather than equity-curve charts. Sub-decisions 11a, 11b, 11g and the controlled Python interface in 11d are unaffected. The nanobind adapter is still built in Phase 1, for the test suite.

---

## Amendment 2026-08-28 — sub-decisions resolved (Open Issue 018 §13)

- **11e confirmed, list trimmed.** Retained: P&L, return %, trade count, win rate, maximum
  drawdown, volatility, Sharpe, and the mandatory buy-and-hold comparison. Dropped from Phase 1:
  average/best/worst trade, and time in market — they add rows without adding insight, and the
  buy-and-hold comparison already answers the question that matters most.
- **11f confirmed.** Every run records a manifest (strategy and parameters, dataset and range,
  configuration hash, engine version, seed), making "run twice, assert byte-identical" a fixed,
  fast per-commit test rather than an aspiration.
