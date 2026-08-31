# Open Issue 014 — Frontend Architecture

**Status:** closed by user
**Opened:** 2026-08-27
**Serves:** README.md Goal 2 (real-time user experience), Product Success
**Depends on:** Open Issue 006 (stream shapes), Open Issue 008 §9h (acknowledgement-shaped API)
**Owner:** _unassigned_

---

## 1. Why this issue exists, and why it is late

At **50 hours** the frontend is the largest single line item in the Phase 1 budget — larger
than the C++ engine, larger than the backtester, larger than the entire testing programme.
Every decision so far has been about the backend, and `README.md` says almost nothing about
the interface.

It also carries disproportionate weight for two reasons:

1. **The trading screen is the demonstration.** Beats 1 through 4 of the five-minute demo
   (Open Issue 013 §7) are all this one screen. Audience (a) and audience (b) both see it
   before they see anything else.
2. **It is the only place where "real-time" is actually experienced.** Everything upstream —
   conflation, fan-out, the private stream — exists to make this screen feel immediate. If the
   UI drops frames or lags, none of that work is visible.

---

## 2. A constraint already fixed

Open Issue 008 §9h established that order submission returns an **acknowledgement, not a
result**: fills arrive later on the private stream.

**The UI must therefore be built around streaming state from the first commit**, not around
request/response with streaming bolted on. An order ticket that waits for an HTTP response to
show a fill will have to be rewritten. This is cheap to honour now and expensive to retrofit
in week 5.

---

## 3. Sub-decision 14a — The rendering problem

This is the interesting decision, and it mirrors the backend one.

The book updates at **20 Hz**. A conventional framework treats every update as a state change
and re-renders the component tree. At 20 Hz, across a book, a tape, and a portfolio, that is
enough work to drop frames on a modest laptop — and dropped frames in a trading UI read as
"the site is slow," which undermines the entire real-time claim.

**The same insight that solved the server side applies here: conflate at the boundary.**

| Layer | Rate | Mechanism |
|---|---|---|
| Server fan-out | 20 Hz | Conflation (Open Issue 006) |
| **Browser receive** | 20 Hz | Write into a plain mutable buffer — **not** framework state |
| **Browser render** | ≤60 Hz, `requestAnimationFrame` | Read the buffer and paint whatever is current |

Receiving and rendering are decoupled. If a burst arrives, the buffer is simply overwritten
and the next frame paints the latest value — the browser-side equivalent of server-side
conflation, and correct for exactly the same reason: **every book message is a complete
snapshot, so an intermediate one that is never painted has been lost harmlessly.**

**Proposed:** high-frequency data (book, tape, last price) lives outside framework state and
renders on `requestAnimationFrame`. Framework state owns only low-frequency chrome — open
orders, portfolio, balance, connection status, forms.

This is worth writing up in the README. It is the same architectural idea appearing at a
second layer, and demonstrating that the principle was understood rather than copied is more
valuable than the frontend itself.

---

## 4. Sub-decision 14b — Framework

| Option | For | Against |
|---|---|---|
| **React** | Most familiar; largest ecosystem; strongest hiring signal | The re-render problem in §3 needs deliberate care |
| Svelte | Fine-grained reactivity sidesteps much of §3; less boilerplate | Less familiar; smaller ecosystem |
| Vanilla + a charting library | No framework overhead; total control | All forms, routing and auth screens hand-built — expensive where it is least interesting |
| Server-rendered templates | Simplest for auth and static pages | Fights the streaming requirement everywhere that matters |

**Proposed: React** — unless one of the two developers is already fluent in Svelte, in which
case Svelte is the better technical fit.

The reasoning is deliberately unromantic: §3 removes the high-frequency data from framework
state anyway, so the framework's re-render characteristics stop being the deciding factor.
What remains is familiarity, documentation, and hiring signal, and React wins all three for
this team.

---

## 5. Sub-decision 14c — Charting

**Proposed: TradingView Lightweight Charts.** Free, roughly 45 KB, purpose-built for financial
data, and designed for streaming updates rather than static datasets. It is what real trading
interfaces use.

The alternatives are worse for this specific job: Chart.js and Recharts have no first-class
candlestick support and are not built for continuous updates; D3 is powerful but would consume
a large share of the 50 hours building what Lightweight Charts provides directly.

---

## 6. Sub-decision 14d — Screens (this is where the 50 hours is won or lost)

**Three screens. Nothing else.**

### 1. Trading screen — the product

| Panel | Source | Rate |
|---|---|---|
| Candlestick chart | Bar stream | 1 s bars |
| Order book (L2, 10 levels) | Book stream | 20 Hz, rAF-rendered |
| Trade tape | Trade stream | Un-conflated |
| Order ticket | REST submit | On action |
| Open orders | Private stream | Low |
| Portfolio and balance | Private stream | Low |

### 2. Login and registration
Minimal. Deliberately unremarkable.

### 3. Backtest screen
Strategy dropdown, symbol, date range, run button, results — equity curve, drawdown chart,
metrics table, buy-and-hold comparison.

### Explicitly not built

Settings pages; admin panels; user profiles; a design system; mobile-responsive layouts beyond
not breaking; dark/light theming; onboarding flows; notification centres.

**A dense, functional trading screen reads better to audience (a) than a polished but sparse
one.** Effort spent on chrome is effort not spent on the panels that constitute the demo.

---

## 7. Sub-decision 14e — Reconnection and re-synchronisation

The WebSocket will drop. Behaviour on reconnect divides cleanly along the same line as
everything else:

| Stream | On reconnect |
|---|---|
| **Market data** | Re-subscribe and wait. The next conflated snapshot is complete, so recovery is automatic and needs no special handling |
| **Private data** | Compare the per-user sequence number (Open Issue 006 sub-decision 7c) against the last one seen. On a gap, re-fetch open orders and portfolio over REST |

The asymmetry is not an accident — it is the same distinction between droppable and
non-droppable data that shaped the server design, surfacing again on the client.

**Connection state must be visible in the UI**: connected, reconnecting, or halted (Open Issue
003 §8.5). A trading interface that silently shows stale prices is worse than one that admits
it is disconnected.

---

## 8. Cost summary

| Item | Hours |
|---|---|
| Project setup, routing, auth pages | 6 |
| WebSocket client: subscriptions, reconnect, sequence-gap detection | 8 |
| rAF render loop and the buffer layer (§3) | 6 |
| Order book panel | 6 |
| Trade tape panel | 3 |
| Chart integration | 5 |
| Order ticket, open orders, cancel | 6 |
| Portfolio and balance | 4 |
| Backtest screen and results | 10 |
| **Total** | **54** |

Against **50 hours** budgeted, following the earlier decision to trim the frontend. The 4-hour
gap is within noise at this level of estimation.

---

## 9. Questions to resolve

1. **React, or Svelte if someone already knows it?** §3 makes this less consequential than it
   would otherwise be.
2. **Is the three-screen limit acceptable?** It is the main defence against the 50 hours
   becoming 80.
3. **Is TradingView Lightweight Charts acceptable as a dependency?** It is a third-party
   library doing significant work, though a purpose-built and widely used one.
4. **Who builds this?** Open Issue 002 §6 splits the work as Dev A on the engine and Dev B on
   the platform. At 54 hours the frontend is roughly a fifth of the total, and loading it
   entirely onto Dev B alongside the gateway, ledger, bots, fan-out and backtester is likely
   unbalanced.

---

## 10. Decision log

| Date | Status | Note |
|---|---|---|
| 2026-08-27 | OPEN | Sub-decisions 14a–14e proposed. Nothing final. |

---

## 11. Amendment 2026-08-27 — answers recorded

- **14b — React.**
- **14c — TradingView Lightweight Charts accepted** as a dependency.
- **14d — the three-screen limit is accepted** (see §11.1 for what it means in practice).
- **14f — Dev A builds the frontend** (see §11.2, which shows this is necessary but not
  sufficient).

### 11.1 What the three-screen limit actually means

It is a **scope commitment made in advance**: the team agrees now that Phase 1 contains
exactly three pages, and that anything else is a scope change to be raised explicitly rather
than added because it seemed small at the time.

It exists because user-interface work has no natural stopping point. Every screen suggests
another — a portfolio page suggests a history page, which suggests filters, which suggest a
settings page to remember them. None of those additions feels large on the day it is made, and
collectively they are how a 50-hour estimate becomes 80. There is no equivalent pressure on
the matching engine, which is finished when the tests pass.

| Built | Not built |
|---|---|
| Trading screen — chart, L2 book, tape, ticket, open orders, portfolio | Settings pages |
| Login and registration | Admin panels |
| Backtest — strategy picker, run, results | User profiles |
| | A design system |
| | Mobile-responsive layouts beyond not breaking |
| | Dark/light theming |
| | Onboarding flows |
| | Notification centres |

The commitment is worth more than the list. When someone proposes a fourth screen in week 4,
the answer is already recorded, along with the reason.

### 11.2 Consequence of 14f — the developer split needs more than this

Moving the frontend to Dev A is the right direction, but it does not on its own balance the
work. Approximate buckets, at the precision these estimates support:

| Bucket | Hours |
|---|---|
| Engine side — C++ library, adapters, model, replay, checkpointing | ~95 |
| Platform side — gateway, ledger, idempotency, bots, fan-out, archiver | ~200 |
| Frontend | ~54 |
| Cross-cutting — testing, benchmarking, deployment, the S2 spike | ~118 |

With roughly 210 hours available per developer:

- Dev A with engine plus frontend is around **149 hours** — under-loaded.
- Dev B with the platform alone is around **200 hours**, before any share of the 118 hours of
  cross-cutting work.

**Something else must move to Dev A.** The two natural candidates are both engine-adjacent,
which keeps the split coherent rather than arbitrary:

| Candidate | Hours | Why it fits Dev A |
|---|---|---|
| **Testing (T1–T6)** | 43 | The differential harness drives the C++ engine directly; property tests target engine invariants |
| **Backtester** | ~55 | Runs the engine in-process through the nanobind adapter — engine work wearing a different hat |

Moving both would over-load Dev A. **Proposed:** Dev A takes engine, frontend, and testing
(~192 h); Dev B takes the platform, the backtester, benchmarking, and deployment (~227 h),
with the remainder rebalanced once real velocity is known after week 2.

**One risk worth naming rather than hiding:** Dev A now alternates between C++ systems
programming and React. The sequencing helps — engine work is concentrated in weeks 1–3 and
the frontend lands in week 5 — but the context switch is a genuine cost, and with two people
the clean split becomes fiction after week 3 in any case.

## 12. Decision log

| Date | Status | Note |
|---|---|---|
| 2026-08-27 | OPEN | Sub-decisions 14a–14e proposed |
| 2026-08-27 | AMENDED | React; Lightweight Charts; three-screen limit accepted; Dev A owns the frontend, with the split rebalanced in §11.2. Still not final. |

### 11.3 Amendment — Dev A is deliberately loaded

Decided: rather than rebalancing toward Dev B, Dev A absorbs engine, frontend, testing and the
backtester. The three-screen limit stands.

**The totals are workable; the peaks are not.** Dev A's rough shape across seven weeks:

| Week | Dev A work | Hours |
|---|---|---|
| 1 | Naive model, T1 scenarios | ~14 |
| 2–3 | C++ engine, adapters, stream integration, T2–T4 | ~41/wk |
| 4 | Replay, checkpointing, engine benchmarks | ~23 |
| 5 | Frontend | **~54** |
| 6 | Backtester | **~55** |

Weeks 5 and 6 exceed a 40-hour week; weeks 1 and 4 have slack. The total is feasible, the
distribution is not.

**Mitigation, at no cost:** start the frontend in week 4, which has roughly 17 spare hours, and
let the backtester run across weeks 6 and 7. Nothing in the frontend depends on week-4 work
finishing first — it consumes the stream shapes fixed in Open Issue 006, which are settled
before any of it is built.

**Concentration risk, recorded once:** with the engine, the tests, the frontend and the
backtester on one person, a bad week for Dev A slips all four simultaneously. Dev B's work has
no such coupling. This is accepted deliberately, not overlooked.

---

## 13. Amendment 2026-08-30 — frontend ownership moved to Dev B

**Sub-decision 14f is reversed.** The frontend now belongs to **Dev B**, not Dev A, and §11.2
and §11.3 above are superseded.

The reasoning that put it with Dev A was capacity balancing. The reasoning that moves it is
different and stronger: **Dev A's portfolio should be entirely engine, correctness and
performance.** Fifty hours of React is real work that must be done well, but it is not what a
quantitative or low-latency interviewer asks about, and it was the largest block of low-signal
work on Dev A's list.

| | Before | After |
|---|---|---|
| Dev A | 172 h, including 50 h of frontend | **134 h**, no frontend and no infrastructure |
| Dev B | 196 h | **234 h**, including the frontend |

**Two consequences, recorded in `WEEKLY_PLAN.md` Appendix D.3:**

1. Dev B now carries 64% of the work, and the schedule risk concentrates there. Dev A cannot
   absorb a slip, because the engine and testing track does not transfer mid-project.
2. **The coordination machinery got simpler.** Dev B now owns both the frontend and the fan-out,
   so the mock WebSocket server is no longer required to prevent blocking — it survives only as
   optional tooling. Three stubs became one.

**The §11.1 three-screen limit and everything in §3–§7 are unaffected** — the rendering
approach, the framework choice, the charting library and the scope commitment all stand exactly
as decided. Only the owner changed.

### Consequence for the rendering decision

Sub-decision 14a — keeping high-frequency data outside React state and painting on
`requestAnimationFrame` — becomes *easier* to get right under this ownership, not harder. The
developer building the 20 Hz conflation loop in the fan-out process is now the same developer
building the browser-side loop that consumes it. The two halves of the same idea land with one
person.
