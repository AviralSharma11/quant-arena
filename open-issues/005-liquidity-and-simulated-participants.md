# Open Issue 005 — Liquidity and Simulated Participants

**Status:** OPEN — PROPOSED, not final
**Opened:** 2026-08-27
**Independent of:** Open Issue 003 (can be decided in parallel)
**Touches:** Open Issue 004 sub-decision on short selling
**Owner:** _unassigned_

---

## 1. Why this matters more than it appears

`README.md` describes users matching against one another. With two developers and a small
demo audience the order book will be empty: the first order rests, nothing trades, the chart
has no points, and the "live market" is a static page. **The proof of concept does not
demonstrate anything without synthetic order flow.**

That much is obvious. The part that is not obvious, and that raises this from a demo prop to
a real design problem:

> **The simulated participants are the data-generating process for the entire quantitative
> half of the project.**

Every price, every trade, and every candle the backtester consumes is produced by these
bots. If they generate a price series with no realistic statistical structure — no trends,
no mean reversion, no volatility clustering — then every backtest returns noise, no strategy
can be shown to work or fail for an intelligible reason, and Goal 3 becomes an empty
exercise. The bots determine whether the backtester is meaningful.

They also serve two further purposes, which is why the design of where they run matters:

- They are the **load generator** for the Goal 5 benchmarks.
- They are the **historical data source** for Goal 3.

---

## 2. Sub-decision 5a — What drives the price?

### Option 1 — Pure random walk / geometric Brownian motion

**For:** trivial to implement, ~2 hours. Defensible as an efficient-market baseline.

**Against:** no mean reversion, no volatility clustering, no trends. Momentum and
mean-reversion strategies both produce approximately zero expected return, so the backtester
cannot demonstrate anything except that noise is noise.

### Option 2 — Ornstein–Uhlenbeck (mean-reverting)

**For:** mean-reversion strategies work and can be shown to work.

**Against:** trend-following strategies systematically lose. Only half the strategy space is
demonstrable.

### Option 3 — Regime-switching: drifting random walk with jumps and shifting volatility

A base random walk whose drift and volatility change slowly between regimes, plus occasional
discrete jumps representing news events.

**For:** both trend-following and mean-reversion have something to find, in different
regimes — which is realistic, and is what makes a backtest interesting rather than
tautological. Produces volatility clustering, which is the most robust empirical property of
real markets. Roughly 4 hours.

**Against:** more parameters, and therefore more temptation to tune (see §6).

### Option 4 — Replay real historical market data as the fair-value driver

**For:** realistic statistical properties for free, with no modelling.

**Against:** introduces an external data dependency and licensing questions, and weakens the
claim that the market is simulated end to end.

**Proposed:** Option 3.

---

## 3. Sub-decision 5b — Participant archetypes

The elegant version does not simulate the price directly. A fair-value process drives the
market maker's quotes; the interaction of the archetypes then *produces* the observable price
and its statistical structure as an emergent property.

| Archetype | Behaviour | Contributes | Est. hours |
|---|---|---|---|
| **Market maker** | Quotes two-sided around fair value; skews quotes by inventory | Liquidity, a tight spread, a non-empty book | 8 |
| **Noise trader** | Poisson-arrival random buys and sells | Order flow, trade prints, price movement | 4 |
| **Momentum trader** | Buys strength, sells weakness | Trends and positive autocorrelation | 5 |
| **Value / mean-reversion trader** | Fades moves away from fair value | Prevents runaway prices; negative autocorrelation | 5 |

**Inventory skewing in the market maker is the detail that matters most.** A market maker
that quotes symmetrically regardless of position will accumulate an unbounded one-sided
inventory and eventually stop being able to quote. Skewing quotes against inventory is what
real market makers do and it is what keeps the simulation stable over hours rather than
minutes.

**Proposed:** market maker plus noise trader are mandatory (~12 h); momentum and value
traders are strongly recommended (~10 h) because without them the price series has no
autocorrelation structure for a strategy to exploit.

---

## 4. Sub-decision 5c — Where do the bots run?

| Option | For | Against |
|---|---|---|
| **In-process with the gateway** | Fastest; simplest | Bypasses the real API path, so bots exercise neither authentication nor validation nor idempotency, and are worthless as a load test |
| **Separate process, over the real API** | Bots are genuine clients; the full path is exercised; **doubles as the load generator for free** | HTTP overhead limits per-process order rate |
| **Separate process, writing directly to the inbound stream** | Highest throughput | Bypasses gateway risk checks; two entry paths to keep consistent |

**Proposed:** separate process over the real API.

If HTTP framing becomes the throughput limit, the answer is a binary order-entry path over
WebSocket rather than moving the bots in-process. That is not a workaround: real exchanges
run a separate high-performance order-entry protocol for algorithmic participants alongside
the web interface. Building one is realistic architecture, not a shortcut.

---

## 5. Sub-decisions 5d and 5e

### 5d — Determinism

Bots must draw from **seeded** random number generators, with seeds recorded in
configuration and in the event stream, so a scenario can be reproduced exactly.

A clarification worth recording, because it is easy to conflate: determinism is a property of
**replaying the log**, not of reproducing a live session. Live bots run in real time and
interleave with human orders non-deterministically. That is fine and expected — the log
captures whatever sequence actually occurred, and replaying that log reproduces the outcome
exactly.

### 5e — Do bots hold accounts, and may they go short?

**Proposed: yes, bots are ordinary accounts** with balances, positions, and the same risk
checks as human users. This exercises one code path rather than two, prevents a bot defect
from creating money from nothing, and keeps the cash- and quantity-conservation invariants in
Open Issue 004 §5 globally true.

**This collides with a proposal in Open Issue 004.** That issue proposes no short selling in
Phase 1, which keeps `position ≥ 0` trivially true. But a market maker that cannot sell what
it does not hold will run out of inventory and the book will go one-sided — precisely the
failure the market maker exists to prevent.

Two ways out:

1. Give market-maker accounts very large starting inventory. Simple, but only defers the
   problem and distorts the quantity-conservation invariant's interpretation.
2. Introduce a **designated market maker account type** permitted to hold negative
   positions, while retail accounts cannot. This is realistic — real exchanges grant
   designated market makers exactly this privilege in exchange for quoting obligations — and
   it confines short selling to accounts the platform controls.

**Proposed:** option 2.

---

## 6. An integrity constraint worth writing down

The price process and the bot parameters must be **fixed before** the demonstration
strategies are written, and the process must be documented in the README.

Tuning the simulated market until a chosen strategy shows a profit is curve-fitting one's own
universe. It would produce an impressive-looking backtest that means nothing, and it is the
kind of thing an informed reader will ask about directly. Choosing the parameters first, and
saying so publicly, costs nothing and is the difference between a credible result and a
worthless one.

---

## 7. Cost summary

| Item | Hours |
|---|---|
| Fair-value process (regime-switching with jumps) | 4 |
| Market maker with inventory skewing | 8 |
| Noise trader | 4 |
| Momentum trader | 5 |
| Value / mean-reversion trader | 5 |
| Bot runner, configuration, seeding, supervision | 6 |
| **Total** | **32** |

Against the ~25 hours previously budgeted for "robot traders." Roughly 7 hours over, and
justified by §1: these components generate the data the entire quantitative half of the
project depends on.

---

## 8. Questions to resolve

1. **Two archetypes or four?** Market maker plus noise trader produces a live, functioning
   market for roughly 12 hours of work. Adding momentum and value traders costs 10 more and
   is what gives the price series exploitable structure. Cutting them makes the backtester
   substantially less interesting.
2. **Is a designated-market-maker account type acceptable**, or is "no short selling
   anywhere in Phase 1" preferred, with large starting inventories instead?
3. **Should bots be visibly labelled as bots in the UI?** Showing them is honest and makes
   the market legible; hiding them makes the demo feel more populated. Recording the choice
   matters more than which way it goes.
4. **How many bots per symbol?** Roughly 1 market maker plus 5–15 others per symbol seems
   right for 8–12 symbols, but this should be tuned against the benchmark targets once the
   system runs.

---

## 9. Decision log

| Date | Status | Note |
|---|---|---|
| 2026-08-27 | OPEN | Sub-decisions 5a–5e proposed. Nothing final. |

---

## 10. Amendment 2026-08-27 — real market data, and its consequences

### 10.1 Two separable uses of real data

"Use a real market dataset" can mean two different things, and they are independent. Both
are viable; neither excludes the other.

| Use | What it means | Effect |
|---|---|---|
| **U1 — Fair-value driver** | A real historical price series is replayed as the "true value" the market maker quotes around. The exchange still matches every order; the book is still produced by simulated participants. | Realistic price dynamics with no modelling work |
| **U2 — Backtest dataset** | Backtests run against real historical bars, in addition to the market this platform recorded itself. | The backtester becomes independently credible |

**U1 does not weaken the claim that this is a real exchange.** The matching engine still
does all matching; the order book is still produced by order flow. Real data only informs
what price the bots *believe* is fair. What is simulated is the market microstructure, which
is the part being built.

**U2 is worth taking seriously.** Backtesting against a known real dataset separates two
questions that are otherwise entangled: *is the backtester correct?* and *is the simulated
market realistic?* Supporting both sources — real history and this platform's own recorded
market — is stronger than either alone, and costs little once the bar format is shared.

### 10.2 U1 supersedes sub-decision 5a Option 3

A replayed real price series already contains volatility clustering, regime changes,
intraday patterns, and genuine trends. Modelling those with a regime-switching process
becomes redundant.

**Revised proposal for 5a:** Option 4 (real data as fair-value driver), replacing the
proposed regime-switching model. Roughly 3 hours for a loader and replay clock, against 4
for the model — cheaper *and* more realistic. Retain a synthetic fallback generator for
tests and for offline development, where a deterministic price path with no data dependency
is more convenient.

### 10.3 Consequence for sub-decision 5b — the archetype question resolves itself

The open question in §8.1 was whether to build two archetypes or four. The reason for the
extra two was that without momentum and value traders, bot interaction alone produces a
price series with no autocorrelation structure for a strategy to exploit.

**If a real price series drives fair value, that structure arrives with the data.** The
market maker and noise traders transmit it into the traded price. Momentum and value traders
still improve *order book* realism — queue dynamics, order-flow imbalance — but they are no
longer load-bearing for the backtester.

**Revised proposal for 5b:** market maker plus noise trader for Phase 1 (~12 h). Momentum
and value traders move to Phase 2 as microstructure realism rather than a Phase 1
requirement.

Net effect: roughly 22 h of archetype work drops to about 15 h including the data loader,
while the resulting price series becomes *more* realistic rather than less.

### 10.4 New sub-decision 5f — which dataset?

| Source | Cost / licence | Resolution | Notes |
|---|---|---|---|
| **Crypto (Binance public API)** | Free, no key for public data, no licensing obstacle, well documented | Trades and 1-minute bars, full history | 24/7 with no market-hours gaps; the pragmatic choice |
| NSE / BSE official | Daily bars partly free; intraday tick data is expensive | Daily freely, intraday paid | Licensing is a genuine obstacle |
| Yahoo Finance (`yfinance`) | Free; terms of service are ambiguous for redistribution | Daily, limited intraday | Widely used in education; do not redistribute the data |
| Kaggle datasets | Free, licence varies per dataset | Varies | Check each dataset's licence individually |

**Proposed:** a crypto dataset, for three reasons — no licensing obstacle, tick and
1-minute resolution available for free, and no market-hours gaps to special-case.

**Daily bars are unusable.** An intraday simulated exchange needs at least 1-minute
resolution; trade-level data is better.

### 10.5 New sub-decision 5g — presentation, determinism, and replay clock

Three requirements follow from choosing U1, and all three are easy to get wrong:

1. **Map real prices onto fictional symbols.** A real BTC or RELIANCE price path should
   drive `QA-TECH`, not a symbol bearing the real instrument's name. Otherwise users will
   reasonably believe they are trading the real instrument, and the prices will not match
   reality. The README should state that price paths are derived from anonymised historical
   data.
2. **Pin the dataset; never fetch live.** The data file is versioned and checked in (or
   fetched once at setup from a pinned URL with a recorded checksum). Fetching live at run
   time destroys reproducibility, which is a stated project goal.
3. **Choose a replay clock.** Real time means a demo where almost nothing visibly happens.
   **Proposed:** configurable time compression — for example, one real second maps to one
   simulated minute — recorded in configuration and stamped into the event stream, since it
   affects the timestamps the backtester consumes. A year of 1-minute bars is roughly
   130,000 points, which is ample; at the end of the dataset, loop from a randomised offset.

### 10.6 Answers recorded

- **§8.2 — designated market maker.** Confirmed as the realistic option, and realistic on
  both sides of the rule. Real exchanges register market makers who receive privileges
  (including short-selling exemptions) in exchange for quoting obligations, while retail
  short selling is restricted and requires a margin account. So "retail accounts are cash
  accounts; bot market makers are designated market makers permitted to hold negative
  inventory" is not a simplification away from reality — it *is* the real-world structure,
  with margin accounts deferred to a later phase.
- **§8.3 — bots are visibly labelled as bots in the UI.** Decided.

### 10.7 Revised cost summary

| Item | Hours |
|---|---|
| Historical data loader + replay clock + time compression | 3 |
| Synthetic fallback generator (tests, offline development) | 2 |
| Market maker with inventory skewing | 8 |
| Noise trader | 4 |
| Bot runner, configuration, seeding, supervision | 6 |
| **Phase 1 total** | **23** |
| *(Phase 2: momentum and value traders)* | *(10)* |

Down from the 32 hours in §7, against 25 originally budgeted — and with a more realistic
price series.

---

## 11. Decision log

| Date | Status | Note |
|---|---|---|
| 2026-08-27 | OPEN | Sub-decisions 5a–5e proposed |
| 2026-08-27 | AMENDED | Real market data adopted as fair-value driver (U1) and as an additional backtest source (U2). 5a Option 3 superseded; 5b reduced to two archetypes for Phase 1; 5f (dataset) and 5g (presentation, determinism, replay clock) added. Designated market maker and visible bot labelling confirmed. Still not final. |

---

## 12. Amendment 2026-08-27 (second) — answers recorded

- **5f — dataset: crypto.** Free public data, no licensing obstacle, tick and 1-minute
  resolution, no market-hours gaps. Indian equities were considered and set aside because
  intraday tick data requires a paid vendor.
- **5g — replay clock: 1 real second maps to 1 simulated minute** as the starting ratio.
  Configurable, recorded in configuration, and stamped into the event stream because it
  determines the timestamps the backtester consumes. Tune on demo feel once running.
- **5h (new) — the market maker carries measurable quoting obligations.**

### 5h — Quoting obligations

A designated market maker is defined by its obligations, not merely by its privileges. The
bot is configured with, and measured against:

| Obligation | Example |
|---|---|
| Maximum quoted spread | ≤ 0.5% of mid |
| Minimum quoted size, each side | ≥ 100 units |
| Minimum uptime | two-sided quote present ≥ 95% of the session |

Roughly 2 hours to implement and instrument. Three reasons it earns its place:

1. It is what makes the bot a *designated* market maker rather than an unconstrained
   quoting loop, matching the real-world structure adopted in §10.6.
2. Compliance against these thresholds becomes a **market-quality metric** for the Goal 5
   benchmark report — spread, depth, and uptime under load are far more interesting than
   throughput alone, and they degrade visibly when the system is stressed.
3. It gives the inventory-skewing logic a defined failure condition to test against: an
   obligation breach is an assertable event, not a subjective judgement.

Revised Phase 1 cost: **25 hours** (23 from §10.7 plus 2), against 25 originally budgeted.

## 13. Decision log

| Date | Status | Note |
|---|---|---|
| 2026-08-27 | OPEN | Sub-decisions 5a–5e proposed |
| 2026-08-27 | AMENDED | Real data adopted (U1 + U2); 5a Option 3 superseded; 5b reduced to two archetypes; 5f and 5g added |
| 2026-08-27 | AMENDED | Crypto dataset confirmed; replay clock 1 s : 1 min; 5h quoting obligations added. Still not final. |

---

## Amendment 2026-08-28 — simplification pass (Open Issue 018)

Symbol count fixed at **10**. This is configuration rather than code — roughly 3 hours of bot configuration and tuning, with archived L2 snapshots at ~345 MB/day. Momentum and value traders remain Phase 2, as recorded in §10.3.
