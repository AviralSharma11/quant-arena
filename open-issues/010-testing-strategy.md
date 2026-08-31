# Open Issue 010 — Testing Strategy for the Exchange Core

**Status:** CONFIRMED (2026-08-28) — full layer set T1–T6; T1–T4 are the priority
**CURRENT STATE:** All six test layers in scope. Invariants I1–I12. Generated tests nightly,
fixed tests and the regression corpus per commit. Budget increase of 20–25 h accepted.
**Opened:** 2026-08-27
**Serves:** README.md Goal 1 (correct exchange core), Definition of Success — Correctness
**Depends on:** Open Issue 002 (naive Python model as oracle), Open Issue 004 (invariants)
**Owner:** _unassigned_

---

## 1. The problem

"We wrote unit tests" is not a correctness claim. Every project says it, and it distinguishes
nothing.

For an exchange the bar is higher, because the failure modes are not crashes — they are
**silent, plausible, wrong results**. An order skipped in the queue, a fill of the wrong size,
a cancelled order that trades anyway. Nothing throws. The system keeps running. The numbers
are merely wrong, and nobody notices until someone reconciles.

The genuinely interesting question is therefore: **what would let us claim the matching engine
is correct, in a way a sceptical reader would accept?**

---

## 2. Why this design admits a stronger answer than most

Three properties established in earlier issues make techniques available that are usually
impractical:

1. **The engine is a pure function.** A list of requests in, a list of events out. No
   database, no network, no clock, no randomness (Open Issue 001, Approach B). It can be
   driven directly, at speed, with no fixtures or mocks.
2. **A naive reference model exists** — a deliberately unoptimised ~200-line implementation
   kept permanently as an executable specification (Open Issue 002, Option D).
3. **Everything is deterministic.** The same input sequence must produce the same output, or
   replay-based recovery would not work either.

Together these permit **exhaustive testing of small cases, property-based testing of large
random cases, and differential testing between two independent implementations.** That trio
is rare, and it is what turns "we tested it" into something specific.

---

## 3. The layers

| # | Layer | What it catches | Hours |
|---|---|---|---|
| T1 | Hand-written scenarios against the model | Rule misunderstandings; the classic cases | 6 |
| T2 | Property-based tests (invariants over generated order flow) | Cases nobody thought to write | 12 |
| T3 | Differential testing: model versus C++ engine | Optimisation bugs in the C++ implementation | 8 |
| T4 | Determinism and replay tests | Hidden non-determinism; broken recovery | 4 |
| T5 | Integration tests through the real API | Wiring, serialisation, risk checks, idempotency | 8 |
| T6 | Recovery test — kill the engine under load | Checkpointing and replay correctness | 5 |
| T7 | Load tests | Throughput, latency, degradation (Goal 5) | — |

### T1 — Hand-written scenarios

The classic cases, written first, run against the naive model:

full fill; partial fill; multiple partial fills; no match (rests on the book); price
improvement (a buy at 102 filling at 101); price-time priority across equal prices; cancel
before any fill; cancel after a partial fill; cancel racing a fill; self-trade attempt; order
crossing several price levels; order exhausting one side entirely; zero and negative
quantities rejected; a price outside the tick grid rejected.

These are cheap, they are readable as documentation of the rules, and they catch
misunderstandings before anything is built on them.

### T2 — Property-based testing

Hypothesis generates arbitrary sequences of submissions and cancels; the test asserts that
invariants hold after **every** step, not merely at the end.

**Engine-level invariants:**

| # | Invariant |
|---|---|
| I1 | The book is never crossed: after processing, `best_bid < best_ask` |
| I2 | Quantity is conserved: every unit that leaves the book appears in exactly one fill |
| I3 | A fill never exceeds the remaining quantity of either order |
| I4 | A cancelled order never subsequently fills |
| I5 | **Price-time priority is never violated** (see §4) |
| I6 | Every resting order's remaining quantity is strictly positive |
| I7 | The same input sequence produces byte-identical output |

**System-level invariants** (from Open Issue 004 §5):

| # | Invariant |
|---|---|
| I8 | `settled_cash − reserved ≥ 0` for every user at every step |
| I9 | `position ≥ 0` for every retail user (designated market makers exempt, per OI 005) |
| I10 | Cash is conserved across all accounts except at explicit deposit events |
| I11 | Units of each symbol are conserved across all accounts |
| I12 | Replaying from genesis reproduces current balances exactly |

I10 is the one that automatically catches the double-spend described in Open Issue 004 §1.

### T3 — Differential testing

Generate a random order sequence; run it through both the naive model and the C++ engine;
assert the two event streams are **identical, field by field, in order**.

This is the strongest single correctness artifact available to this project. Two independently
written implementations agreeing across millions of generated operations is a much stronger
statement than any coverage percentage. It is also what database and consensus teams actually
do, which makes it defensible rather than novel.

**The C++ engine must not be optimised until this harness exists.** Its purpose is to make
optimisation safe, so building it afterwards inverts the value.

### T4 — Determinism and replay

Two distinct tests, easily conflated:

- **Determinism:** the same input twice produces byte-identical output. Catches accidental
  clock reads, unseeded randomness, and iteration over unordered containers — the failure mode
  Open Issue 001 warns is silent until a replay disagrees.
- **Replay:** process N requests, snapshot, process M more; then restore the snapshot and
  replay those M; assert the final state matches. This is the recovery path, and it must be
  tested as a unit rather than only through T6.

---

## 4. Invariant I5 deserves its own treatment

Price-time priority is the rule the whole exchange exists to enforce, and it is the hardest
invariant to check, because it is a statement about what **did not** happen: no better-priced
or earlier same-priced order was passed over.

**Proposed:** a checker that maintains an independent view of the book and, for every fill,
asserts that no order existed with strictly better price, or with equal price and an earlier
sequence number, that could have filled instead.

This is roughly 4 hours of the 12 allocated to T2, and it is worth calling out separately for
two reasons: it is the invariant most likely to be quietly skipped as too fiddly, and it is
the one an informed reader will ask about first.

---

## 5. The oracle must itself be tested

A subtlety that is easy to miss: **the naive model is the standard everything else is measured
against, so if the model is wrong, T3 proves only that both implementations are wrong in the
same way.**

Two mitigations:

1. The model is deliberately small — around 200 lines — and reviewable by eye. That is not an
   accident of its construction; it is the reason for it.
2. T1's hand-written scenarios run against the **model**, not the C++ engine. They are what
   establishes that the oracle encodes the rules correctly.

The model is verified by inspection and by example; the C++ engine is verified against the
model. The chain only holds if the first link is deliberately kept simple.

---

## 6. Regression corpus

Whenever a generated sequence finds a failure, **that exact sequence is saved to a versioned
corpus and replayed forever after**.

This costs almost nothing, and it converts every bug found into a permanent test. It also
produces something concrete to point at: a directory of numbered failing cases, each with the
defect it caught. Hypothesis supports this natively through its example database, but the
corpus should be committed to the repository rather than left in a local cache.

---

## 7. Where to stop

Testing has diminishing returns and a deadline exists. Deliberately **not** planned:

- **Coverage percentage targets.** Chase invariants, not line coverage. A high figure over
  weak assertions proves nothing.
- **Mutation testing.** Genuinely valuable and genuinely slow. Phase 2.
- **Formal verification / TLA+.** Interesting, and far beyond the available time.
- **Exhaustive UI testing.** A handful of smoke tests on the critical path; no more.
- **Testing Redis, Postgres, or the web framework.** They are not the system under test.

---

## 8. Cost, and an honest budget gap

| Layer | Hours |
|---|---|
| T1 hand-written scenarios | 6 |
| T2 property tests (including 4 h for the priority checker) | 12 |
| T3 differential harness | 8 |
| T4 determinism and replay | 4 |
| T5 integration tests | 8 |
| T6 recovery script | 5 |
| **Total** | **43** |

The Phase 1 budget allocated **12 hours** to "differential / property tests". Some of T5 is
already absorbed in other line items, but the honest gap is **roughly 20–25 hours**.

Weighed against it: the 7-week schedule carries roughly 65 hours of slack, and Open Issue 003
returns about 13 hours if S2 is confirmed. So it fits — but it fits *because* the deadline
moved, and it should be booked deliberately rather than discovered in week 3.

The argument for booking it: this is the single strongest artifact the project produces for
audience (a), and `README.md` Goal 1 defines success for the exchange core as "strong
automated tests that prove the matching engine behaves correctly" — not as the engine merely
working.

---

## 9. Questions to resolve

1. **Is a 20–25 hour budget increase for testing acceptable?** The alternative is dropping
   T5 integration tests (−8 h) and thinning T2, which weakens the central claim.
2. **When is the differential harness built?** Proposed: before any C++ optimisation, since
   its purpose is to make optimisation safe. That places it in week 2, ahead of the engine
   being fast.
3. **Should property tests run in CI on every commit, or nightly?** Generated tests are slow
   and can be flaky under time limits. Proposed: a small fixed budget of examples on every
   commit, a large run nightly.
4. **Is the priority checker (§4) worth its 4 hours?** It is the fiddliest test and the most
   convincing one.

---

## 10. Decision log

| Date | Status | Note |
|---|---|---|
| 2026-08-27 | OPEN | Layers T1–T6 proposed; invariants I1–I12 enumerated; budget gap of 20–25 h identified. Nothing final. |

---

## 11. Amendment 2026-08-27 — answers recorded

- **§9.1 — the 20–25 hour budget increase for testing is accepted.** Booked deliberately
  against the schedule slack created by the 15 October deadline, rather than absorbed
  silently.
- **§9.4 — the I5 price-time-priority checker is confirmed**, at roughly 4 hours.
- **§9.3 — generated tests run nightly**, not on every commit.

### 10a — What runs when

The nightly decision requires drawing a line between *generated* tests, which are slow and
variable, and *fixed* tests, which are fast and deterministic. Generated tests go nightly.
Fixed tests — **including the regression corpus** — run on every commit.

| Trigger | Contents | Target duration |
|---|---|---|
| **Every commit** | T1 hand-written scenarios; T4 determinism and replay; **the regression corpus (§6)**; unit tests; a differential run over a small fixed corpus | under 2 minutes |
| **Nightly** | T2 property generation at volume; T3 differential fuzzing at volume; T6 recovery test | unbounded |

The regression corpus belongs on the per-commit side despite originating from generated
tests. Once a failing sequence has been captured it is no longer generated — it is a fixed,
fast, deterministic test for a bug that has already occurred once, and it is exactly the
test most likely to catch a reintroduction. Excluding it would mean known bugs could return
and go unnoticed for up to a day.

The accepted consequence of nightly-only generation: a **new** class of defect can survive in
the main branch for up to 24 hours. For a two-developer project this is a reasonable trade
against keeping the commit loop under two minutes.

## 12. Decision log

| Date | Status | Note |
|---|---|---|
| 2026-08-27 | OPEN | Layers T1–T6 proposed; invariants I1–I12; budget gap identified |
| 2026-08-27 | AMENDED | Budget increase accepted; I5 checker confirmed; generated tests nightly, fixed tests and regression corpus per commit (§10a). Still not final. |

---

## 13. Confirmed 2026-08-28 — full layer set, with T1–T4 prioritised

**All six layers are in scope. T1, T2, T3 and T4 are the priority.**

That grouping is coherent rather than arbitrary: T1–T4 are precisely the layers that test the
**engine as a pure function** — hand-written scenarios, property tests over generated flow,
differential comparison against the naive model, and determinism plus replay. They need no
database, no network and no running system, they are the fastest to write and the fastest to
run, and they are what substantiates the correctness claim in `README.md` Goal 1.

T5 (integration through the real API) and T6 (kill-the-engine recovery) are system-level. They
remain in scope and are the natural first candidates if time runs short.

### 13.1 One caution before T6 is treated as expendable

**T6 is not only a test.** The kill-the-engine recovery script is also:

- **Beat 6 of the five-minute demonstration** (Open Issue 013 §7) — the moment that separates
  this from a trading-themed web application.
- The evidence for the **Reliability** section of the definition of done (Open Issue 013 §6).
- The measurement behind **recovery time**, which `README.md` Goal 5 names explicitly.

At 5 hours it is the cheapest layer in the entire programme and it carries three obligations
beyond testing. **Recommended cut order if the schedule tightens: thin T5 first, then thin T2's
generated-example budget. T6 should be the last thing to go, not the first.**

### 13.2 What being "priority" means in practice

- T1 and the naive model land in **week 1**, before any C++ exists.
- T3, the differential harness, is built **before any C++ optimisation** — its purpose is to
  make optimisation safe, so building it afterwards inverts the value.
- T2 and T4 accompany the C++ engine in weeks 2–3.
- T5 and T6 follow the platform, in weeks 4–5.

## 14. Decision log

| Date | Status | Note |
|---|---|---|
| 2026-08-27 | OPEN | T1–T6 proposed; invariants I1–I12; budget gap identified |
| 2026-08-27 | AMENDED | Budget increase accepted; I5 checker confirmed; nightly generated, per-commit fixed |
| 2026-08-28 | **CONFIRMED** | Full layer set, T1–T4 prioritised. Cut-order caution on T6 recorded in §13.1. |

---

## Amendment 2026-08-28 — simplification pass (Open Issue 018)

**T5 integration tests are thinned to the critical path only** (−4 h). T1–T4 and T6 are unaffected; T6 in particular is explicitly protected, per §13.1.
