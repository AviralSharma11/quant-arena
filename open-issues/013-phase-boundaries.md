# Open Issue 013 — Phase Boundaries and Definition of Done

**Status:** OPEN — PROPOSED, not final
**Opened:** 2026-08-27
**Consolidates:** every issue in this directory
**Owner:** _unassigned_

---

## 1. Why this issue exists

The original brief asked for a clear separation between proof-of-concept requirements, later
phases, and production-scale ideas. Twelve issues later, that separation exists but is
scattered across them. This issue collects it into one place, and adds the thing none of the
others provide: **a definition of done that decides whether Phase 1 shipped.**

The scoping principle recorded in Open Issue 011 §11.1 governs every line below:
**demonstrate the capability; do not build a research-accurate platform.**

---

## 2. Phase 1 — by 15 October 2026

### Exchange core
- C++ matching engine, single writer, limit orders, cancels, partial fills, price-time priority
- Market orders as marketable limit orders with a price band (OI 004 sub-decision 4d)
- Self-trade prevention
- Naive Python model retained as executable specification and differential-test oracle
- nanobind adapter for tests and backtesting

### Order flow and accounts
- Accounts, authentication, virtual cash
- Risk checks with pessimistic reservation in the gateway (OI 004)
- Idempotency via required client order IDs; cancel-by-client-order-id (OI 008)
- Acknowledgement-shaped order API; outcomes arrive on the private stream
- Maker/taker fees in basis points (OI 011 §11.2)
- Retail accounts are cash accounts; designated market makers may hold negative inventory

### Data and durability
- Durable ordered event stream (S1 or S2, pending OI 009)
- Snapshot plus replay recovery, identical to the normal read loop
- Archiver: trades, 1s/1min OHLCV bars, 1 Hz L2 snapshots

### Market and participants
- 8–12 symbols, fair value driven by replayed real crypto history at 1 s : 1 min
- Designated market maker with measurable quoting obligations; noise traders
- Bots run as real API clients, and double as the load generator
- Bots visibly labelled as bots

### Real-time
- WebSocket; tiered L1 and L2 feeds, N = 10, conflated at 20 Hz
- Un-conflated trade tape
- Separate fan-out process
- Private per-user stream with sequence numbers and REST re-synchronisation

### Research
- Backtester over real crypto history
- Fill simulation through the real engine against reconstructed L2 snapshots
- Metrics including maximum drawdown, Sharpe, and a mandatory buy-and-hold comparison
- Three built-in strategies; no user-submitted code
- Run manifest; reproducibility asserted as a test

### Engineering
- T1–T6 test layers; invariants I1–I12; regression corpus
- Generated tests nightly; fixed tests per commit
- Open-loop load generation with HDR histograms; B1/B2/B3 reported separately
- Structured logs; `trace <client_order_id>` tool; offline analysis plots
- Kill-the-engine recovery script
- Docker, CI, public deployment
- Benchmark report

---

## 3. Explicitly NOT in Phase 1

Recorded so that each is a decision rather than an omission.

| Excluded | Why | Where it goes |
|---|---|---|
| User-submitted strategy code | Requires sandboxing; 150 h or more | Phase 2 |
| Sandboxed execution (Goal 4) | Depends on the above | Phase 2 |
| Competitions and leaderboards | Depends on both of the above | Phase 2 |
| Retail short selling and margin | Requires a real risk engine | Phase 2 |
| Momentum and value bots | Real data supplies the structure they would have added | Phase 2 |
| Delta-encoded book feeds | Conflated snapshots suffice at this scale | Phase 2 |
| Binary wire protocol | Worth ~5× where conflation was worth ~85× | Phase 2, after measurement |
| L3 order-by-order feed | Impractical without delta encoding | Phase 2 |
| Multiple gateways / dedicated sequencer | One gateway is not yet the measured constraint | Phase 2 |
| Analytics as a separate service | The one legitimate microservice boundary (OI 007 §12) | Phase 2 |
| OpenTelemetry | The event stream already is the trace | Not planned |
| Prometheus and Grafana | Replaced by logs and offline analysis | Not planned |
| Kubernetes | Excluded by README.md; solves no problem this system has | Not planned |
| Engine sharding across threads or hosts | One thread runs at ~4% utilisation | Phase 3 |
| Multi-region, high availability | No requirement | Phase 3 |
| Market-impact modelling in backtests | Research-grade | Phase 3 |
| **Real money; real exchange integration** | Out of scope permanently | **Never** |

---

## 4. Phase 1.5 — late October

- Real users: friends, a college club, a small public cohort
- Deployment hardening: TLS, backups, an actual restart runbook
- Recovery test promoted into CI
- Backtesting against this platform's own accumulated market history, once there is enough of it

---

## 5. Phase 2 — November onward

Ordered by value rather than by ease:

1. **User-submitted strategies with sandboxed execution.** The largest single addition and the
   whole of Goal 4. Subprocess isolation with CPU, memory and wall-clock limits; a restricted
   import set; no network.
2. **Competitions.** Depends on (1). Leaderboards ranked by risk-adjusted return rather than
   raw profit.
3. **Margin accounts and retail short selling.** Requires a genuine risk engine, and is the
   natural sequel to the cash-account simplification made in Phase 1.
4. **Analytics as a separate service.** The honest place to demonstrate a service boundary:
   read-only, stream-consuming, independently scalable, with its own database.
5. **Performance work, driven by Phase 1 measurements.** Delta encoding, binary protocol, and
   multiple gateways — each with before/after numbers, and each justified by a bottleneck that
   was actually observed.

---

## 6. Definition of done for Phase 1

Phase 1 has shipped when every line below is true. It is a checklist, not a set of
aspirations — anything unchecked on 15 October is a miss to be stated plainly rather than
quietly reinterpreted.

**Product**
- [ ] A new user can register, receive virtual capital, and place an order within two minutes
- [ ] The market is visibly alive on load: prices move, trades print, the book updates
- [ ] An order fills, and the portfolio updates without a page refresh
- [ ] An open order can be cancelled, and a cancel that loses a race to a fill behaves correctly
- [ ] A backtest can be run and produces a report including a buy-and-hold comparison

**Correctness**
- [ ] Invariants I1–I12 hold across nightly generated runs
- [ ] The C++ engine and the naive model agree across millions of generated operations
- [ ] Replay from genesis reproduces balances exactly
- [ ] Duplicate submissions produce zero duplicate fills under load

**Reliability**
- [ ] The engine can be killed mid-load and recovers with no lost or duplicated fills
- [ ] Redis or stream failure produces a visible halt state, not silent failure

**Performance**
- [ ] B1, B2 and B3 measured and reported separately, with distributions
- [ ] The benchmark report states its methodology, including how coordinated omission was avoided
- [ ] At least one before/after optimisation is documented with numbers
- [ ] The report states its own limitations

**Engineering**
- [ ] Deployed and publicly reachable
- [ ] CI runs the fixed suite on every commit
- [ ] `trace <client_order_id>` reconstructs an order's full path
- [ ] README explains what each component solves and why the architecture is shaped this way

---

## 7. The five-minute demonstration

For audience (a) this determines whether any of the rest registers. It should be rehearsed,
not improvised, and recorded as a video with the link in the README.

| # | Beat | What it proves |
|---|---|---|
| 1 | Open the app — market already moving, book updating, tape scrolling | It is a live market, not a mock-up |
| 2 | Place a limit order that rests; show it in the book | The book is real and the order is in it |
| 3 | Place a crossing order; show the partial fill and the portfolio updating live | The full path works end to end |
| 4 | Cancel the remainder | Cancellation and race handling |
| 5 | Run the load generator; watch throughput climb and the spread widen | Behaviour under load, and market quality degrading measurably |
| 6 | **Kill the engine mid-load; show it recover with no lost fills** | Reliability — the strongest single moment |
| 7 | Run a backtest; show the metrics and the buy-and-hold comparison | The research half is real |

Beat 6 is the one that separates this from a trading-themed web application. It should be
rehearsed until it is reliable.

---

## 8. Questions to resolve

1. **Is the Phase 1 list correct, and is the exclusion list complete?** Anything not on either
   list will be argued about in week 4.
2. **Is the definition of done acceptable as a pass/fail checklist?** Its value depends
   entirely on being applied literally on 15 October.
3. **Open Issue 004 sub-decision 4c is still unanswered:** do account creation and cash grants
   flow through the inbound stream — one global ordering, one rebuild path, at the cost of the
   engine forwarding non-order records — or through a second stream requiring a deterministic
   merge rule?
4. **Should the five-minute demonstration be scripted and rehearsed as an explicit work item?**
   Roughly two hours, and it is what audience (a) actually experiences.

---

## 9. Decision log

| Date | Status | Note |
|---|---|---|
| 2026-08-27 | OPEN | Phase boundaries consolidated; definition of done and demonstration script proposed. Nothing final. |

---

## 10. Amendment 2026-08-27 — answers recorded

- **§8.1 — the Phase 1 scope and the exclusion list are confirmed complete.** Anything
  appearing on neither list during the build is a scope change, to be raised explicitly rather
  than absorbed.
- **§8.3 — Open Issue 004 sub-decision 4c is resolved**: a single inbound stream, with the
  engine forwarding non-order records. Recorded in Open Issue 004 §8.
- **§8.4 — the demonstration is improvised on the day**, not built as a scripted work item.
  Two hours are saved and returned to the schedule.

### Note on the improvised demonstration

Recorded once and not revisited: six of the seven beats are ordinary interaction with a
working system and improvise safely. **Beat 6 — killing the engine mid-load — is the
exception**, because it depends on the recovery script, the load generator, and the UI all
behaving together under stress, and it is also the beat that carries the most weight.

The recovery script from Open Issue 007 sub-decision 8c is being built regardless, so running
it once end to end before showing it to anyone costs close to nothing. That is a suggestion
rather than a work item, and it does not change the decision.

### Revised total

| | Hours |
|---|---|
| Running total at OI 012 §12e | 421 |
| Scripted demonstration removed | −2 |
| **Revised** | **419** |
| Effective budget | ~430 |

## 11. Decision log

| Date | Status | Note |
|---|---|---|
| 2026-08-27 | OPEN | Phase boundaries consolidated; definition of done and demonstration script proposed |
| 2026-08-27 | AMENDED | Scope and exclusion list confirmed complete; 4c resolved; demonstration improvised. Still not final. |

---

## Superseded 2026-08-28

Sections 2 through 5 of this issue — the Phase 1 list, the exclusion list, Phase 1.5 and Phase 2
— are **superseded by Open Issue 018**, which applied a simplification pass and restructured the
work into three phases.

**Still current and not superseded:** §6 (definition of done), §7 (the five-minute
demonstration), and §3's reasoning about why each exclusion is a decision rather than an
oversight. The definition of done needs one revision — the backtest checklist item should read
"produces a report including a buy-and-hold comparison **and a stated fill-model limitation**",
per Open Issue 018 §11.2.
