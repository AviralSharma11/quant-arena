# Open Issue 004 — Source of Truth for Money

**Status:** RESOLVED (2026-08-28) — all sub-decisions decided; 4a and 4b simplified per OI 018 §13
**CURRENT STATE:** Gateway owns risk state · reservations live in gateway memory · the C++
engine stays money-blind · the Redis stream ID provides ordering.
**Opened:** 2026-08-27
**Depends on:** Open Issue 003 (which store is the durable ordered record)
**Owner:** _unassigned_

---

## 1. The problem

Everything downstream of the matching engine consumes the outbound event stream, including
the ledger — the component that knows each user's cash and positions. Consumers lag by
definition. But the risk check must happen *before* an order is sequenced, so the risk check
reads a balance that is structurally out of date.

```
t=0.000  Riya has ₹5,000, no open orders.
t=0.001  Order A: BUY 50 @ ₹100 (needs ₹5,000)
         Gateway checks ledger → ₹5,000 → PASS → sequenced #1041
t=0.002  Order B: BUY 50 @ ₹100 (needs ₹5,000)
         Gateway checks ledger → still ₹5,000 → PASS → sequenced #1042
t=0.010  Engine fills both.
t=0.015  Ledger applies both fills → balance = −₹5,000
```

Money is created from nothing. This will not appear in manual testing and will appear
immediately once robot traders run at thousands of orders per second.

**The question: who owns the authoritative answer to "can this user afford this order?"**

---

## 2. Approaches

### A — Risk state in the gateway, updated synchronously

The gateway holds `settled_cash`, `settled_positions`, and `reserved` per user in memory.
At submit it checks `settled_cash − reserved ≥ cost` and, on success, increments `reserved`
immediately — before sequencing. Reservations are released only when the gateway observes a
`Filled` or `Cancelled` event on the outbound stream.

**For:** race-free by construction, and this follows from a decision already made — Open
Issue 001 sub-decision 2b places the gateway on a single event loop as the sequencer, so a
sequencer that also owns reservation state is trivially a single writer over it. Zero added
latency: no database round trip, no lock on the hot path. Risk logic stays in Python, where
limits and margin rules change frequently. **The matching engine stays money-blind**, which
preserves the minimal-engine premise of Open Issue 002.

**Against:** the gateway holds critical state, so a crash loses reservations — recoverable
by replay, see §4. Ties risk to a single gateway process; when Phase 2 adds gateways for
fan-out capacity, this state must move or shard by user.

### B — The engine owns money as well as the book

**For:** correct by construction with no lag anywhere. The engine can reject on insufficient
funds at match time, catching cases a pre-check cannot.

**Against:** the engine stops being a matching engine and becomes an accounting system —
accounts, deposits, multi-asset positions, fees, margin — in C++, on the hot path, where
iteration is slowest and mistakes are segfaults. Snapshots grow from "the book" to "the book
plus every account." Risk rules change weekly during development and should not live in the
component that is recompiled and re-benchmarked.

### C — Database transaction on the submit path

`SELECT ... FOR UPDATE` on the balance row, check, write a reservation row, commit, sequence.

**For:** durable reservations with no custom recovery code. Familiar.

**Against:** places a database round trip and a row lock on the hot path — the same reason
Open Issue 001 rejected its Approach A. Adds 1–5 ms per order. Introduces a second ordering
authority alongside the log, and the two can disagree.

---

## 3. Provisional recommendation: Approach A

The property that makes A safe rather than merely convenient:

> **The lag is conservative.**

Reservations are taken pessimistically at submit and released only on confirmed events.
Between a fill occurring and the gateway observing it, the user's available balance is
*understated*. Understatement can never over-permit; it can only be briefly too strict,
which is the correct bias for a risk system.

Two consequences fall out with no special cases:

- **A buy filling better than its limit refunds itself.** Reserve at the limit price, settle
  at the fill price, release the difference on the fill event.
- **Cancels release on the event, never on the request.** A cancel can lose a race against a
  fill; releasing only on a `Cancelled` event handles that correctly by construction.

---

## 4. Sub-decisions (all provisional)

### 4a — Authoritative store: the event log, or the relational database?

**Proposed:** the outbound event stream is the source of truth for money; the relational
database is a derived read model serving account pages, history, charts, and warm-start. On
disagreement the log wins and the database is rebuilt.

This yields a strong claim: every rupee in the system is explainable by replaying events
from genesis.

**Blocked on Open Issue 003**, which decides what technology holds that stream.

### 4b — Gateway risk-state recovery

The gateway's risk state is a pure function of the outbound event stream. **Proposed:**
periodic checkpoint (offset plus risk-state snapshot), replay forward on restart — the same
mechanism used by the engine, with no new code paths to test.

### 4c — How deposits and initial capital enter the ordering

If a signup grant is written straight to the relational database it is not in the event
stream, so a replay-based rebuild misses it and the two stores diverge.

**Proposed:** every state-changing action flows through the inbound stream, including
account creation and cash grants. The engine treats non-order record types as pass-through:
it stamps them and forwards them to the outbound stream untouched.

This costs the engine roughly five lines and buys exactly one global ordering and exactly
one rebuild path. It is a small deliberate impurity in an otherwise pure engine and is
flagged rather than hidden. The alternative — a second stream with the gateway as its sole
writer — requires a deterministic merge rule between two orderings.

### 4d — Market orders

A market order has no limit price, so a reservation cannot be computed. This, not matching,
is why `README.md` was right to treat market orders as optional.

**Proposed:** implement market orders as **marketable limit orders with a price band** — a
market buy becomes a limit buy at `best_ask × 1.05`, or a fixed per-symbol band. This makes
the reservation computable, prevents a market order sweeping a thin book to an absurd price,
and matches what real exchanges do with price bands and circuit breakers.

---

## 5. Invariants this produces (feeds the testing strategy)

1. `settled_cash − reserved ≥ 0` for every user at every point in a replay.
2. `position ≥ 0` for every user and symbol (no short selling in Phase 1).
3. **Cash conservation:** total cash plus total reservations across all users is constant
   except at explicit deposit events.
4. **Quantity conservation:** for each symbol, total units held across all users is constant.
5. Replaying the full stream from genesis reproduces current balances exactly.

Invariant 3 catches the double-spend in §1 automatically, the first time it occurs.

---

## 6. Questions to resolve

1. **4c is the weakest point.** Routing account creation and cash grants through the
   matching engine's inbound stream feels wrong — the engine has nothing to do with signups.
   One stream is simpler; two streams need a deterministic merge rule. Which cost is
   preferred?
2. **Is "no short selling in Phase 1" acceptable?** It keeps invariant 2 trivially true.
   Shorts require margin, which requires a real risk engine — Phase 2 at the earliest.
3. **Fees?** Zero fees is simpler. A flat per-trade fee costs roughly two hours and makes
   both P&L and backtest realism meaningfully better, since fees are what kill most naive
   strategies. It adds a term to invariant 3.
4. **Does Approach A survive Open Issue 003?** If Redis becomes the state store, gateway
   reservations could live in Redis instead of gateway memory — slower, but durable and
   shareable across multiple gateways. Worth revisiting once 003 is settled.

---

## 7. Decision log

| Date | Status | Note |
|---|---|---|
| 2026-08-27 | OPEN | Approach A proposed with sub-decisions 4a–4d. Nothing final; 4a blocked on Open Issue 003. |

---

## 8. Amendment 2026-08-27 — sub-decision 4c resolved

**All state-changing actions flow through the single inbound stream**, including account
creation and cash grants. The second-stream alternative, with its deterministic merge rule
between two orderings, is rejected.

### 8.1 What this means concretely

The inbound stream carries four record types. The engine handles two and forwards two:

| Record type | Engine behaviour |
|---|---|
| `SubmitOrder` | Matched against the book |
| `CancelOrder` | Removed from the book |
| `CreateAccount` | **Stamped and forwarded unchanged** |
| `CreditCash` | **Stamped and forwarded unchanged** |

Forwarded records still receive a sequence number and still appear on the outbound stream, so
every consumer observes them **in order relative to fills**. That ordering is the entire point:
a cash grant and an order that depends on it can never be seen in the wrong order by the
ledger or by the gateway's risk-state rebuild.

### 8.2 What it costs

The engine gains roughly five lines of "not an order — forward it." This is a deliberate
impurity in an otherwise pure matching engine, and it is the kind of thing that looks wrong in
a design review, so the reasoning is recorded here rather than left to be rediscovered:

**Accepted cost:** the engine has nominal knowledge that record types exist which are not
orders. It does not interpret them, does not act on them, and remains money-blind.

**What it buys:** exactly one global ordering and exactly one rebuild path for all state. The
gateway's risk state, the ledger, and every downstream consumer are each a pure function of a
single stream. The alternative requires a deterministic merge rule between two independent
orderings — code that must be correct, must stay correct, and has no other purpose.

### 8.3 Consequence for idempotency

Per Open Issue 008 §6, `CreateAccount` and `CreditCash` are **more dangerous than orders**
under duplication: a duplicated grant creates money from nothing and breaks invariant I10.
Because they now travel the same path as orders, they use the same required-key deduplication
mechanism with no additional machinery.

## 9. Decision log

| Date | Status | Note |
|---|---|---|
| 2026-08-27 | OPEN | Approach A proposed with sub-decisions 4a–4d |
| 2026-08-27 | AMENDED | Fees adopted as maker/taker (see OI 011 §11.2); invariant I10 extended with a house fee account |
| 2026-08-27 | AMENDED | Sub-decision 4c resolved: single inbound stream; engine forwards non-order records. Remaining sub-decisions still not final. |

---

## 10. Amendment 2026-08-28 — Approach A confirmed

**The gateway owns risk state.** Approach B (engine owns money) and Approach C (database
transaction on the submit path) are rejected. This also answers §6.4: reservations live in
**gateway process memory**, not in Redis, even though Open Issue 003 locked S2 and Redis is
available.

**Confirmed consequences:**

- The matching engine stays money-blind. Open Issue 002's premise of a minimal engine holds.
- Reservations are lost if the gateway restarts, and are **rebuilt by replaying the outbound
  stream** — sub-decision 4b, the same mechanism the engine and every other consumer uses.
- Risk state is tied to a single gateway process. When Phase 2 adds gateways for fan-out
  capacity, this state must move or be sharded by user. Recorded as a known limitation.

**Still not confirmed** and awaiting the review pass: 4a (event stream authoritative, database
derived), 4b (checkpoint and replay for risk-state recovery), 4d (market orders as marketable
limit orders with a price band).

### 10.1 An observation that follows from S2

The stated advantage of gateway-memory reservations is that they add no round trip to the
submit path. Under S2 that advantage is smaller than it looks: **the gateway already makes a
Redis round trip on every submission** — the `XADD` to the inbound stream, plus the idempotency
claim from Open Issue 008 sub-decision 9c.

This does not change the decision. In-memory reservations are still the right choice, because
the reason is not latency but **ownership**: the gateway is the single-threaded sequencer, so a
sequencer that also owns reservation state is trivially a single writer over it. Moving that
state to Redis would reintroduce a read-modify-write across a network boundary, which is a race
unless scripted.

But it does open a worthwhile optimisation — see §10.2.

### 10.2 New sub-decision 4e — combine the idempotency claim and the append

Two separate Redis operations on the submit path have a failure window between them:

```
  SET client_order_id NX        ← key claimed
  ** gateway crashes here **
  XADD inbound ...              ← never happens
```

The key is claimed but no order exists. A retry now returns "in progress" (Open Issue 008 §9h)
**forever**, because nothing will ever produce an outcome. The client is stranded, and the only
recovery is TTL expiry an hour later.

**Proposed: perform the claim and the append in a single Lua script**, executed atomically by
Redis. Either the key is claimed *and* the record is appended, or neither happens.

| | Two operations | One Lua script |
|---|---|---|
| Round trips | 2 | **1** |
| Crash window between them | **Yes — client stranded until TTL** | None |
| Atomicity | None | Guaranteed — Redis executes scripts atomically |

Roughly 3 hours, and it removes a real failure mode rather than merely tidying the code.

### 10.3 Clarification to Open Issue 001 sub-decision 2b

Sub-decision 2b was written before the transport was settled and says the gateway *assigns
sequence numbers*. Under S2 that needs restating, because **`XADD` already returns a monotonic
stream ID.**

Maintaining a separate gateway-assigned sequence alongside the stream ID would mean two ordering
concepts that must be kept consistent — pointless work with a real chance of divergence.

**Proposed:** the Redis stream ID **is** the sequence number. Sub-decision 2b's substance is
unchanged and still holds — there is exactly one producer, so there is exactly one ordering —
but the gateway's role is more accurately described as **single producer** than as *sequencer*.
The ordering guarantee comes from there being one writer to one stream.

## 11. Decision log

| Date | Status | Note |
|---|---|---|
| 2026-08-27 | OPEN | Approach A proposed with sub-decisions 4a–4d |
| 2026-08-27 | AMENDED | Maker/taker fees adopted; invariant I10 extended with a house fee account |
| 2026-08-27 | AMENDED | Sub-decision 4c resolved: single inbound stream; engine forwards non-order records |
| 2026-08-28 | AMENDED | **Approach A confirmed** — gateway owns risk state in process memory, not Redis. 4e proposed (atomic claim-and-append). 2b restated: the stream ID is the sequence number. 4a, 4b, 4d still open. |

---

## 12. Sub-decision 4a unblocked 2026-08-28 — with a correction

Sub-decision 4a was marked "blocked on Open Issue 003." That issue is now resolved (S2, Redis
Streams locked), so 4a can be stated concretely — but two later decisions require a correction
to how it was originally worded.

**4a as written claims:** *"every rupee in the system is explainable by replaying events from
genesis."*

**That claim no longer holds as stated**, for two reasons decided after it was written:

1. **Redis Streams are trimmed** (Open Issue 003 §8.4). Older events leave the stream and live
   only in the archive.
2. **Invariant I12 was restated as a test property** (Open Issue 016 §10.1). Operationally,
   recovery replays from the last snapshot, not from genesis.

**Corrected statement of 4a:**

| Layer | Role |
|---|---|
| **Redis outbound stream** | Authoritative for money over the retained window |
| **Archive** | Authoritative for everything older; written by the archiver before trimming |
| **Relational database** | **Derived read model** — account pages, history, charts, warm-start. Never authoritative; rebuildable from stream plus archive |

On disagreement, stream-plus-archive wins and the database is rebuilt. The defensible claim
becomes: *every rupee is explainable by the recorded event history, and the replay logic that
interprets it is proven correct against a fixed dataset.* That is weaker than the original
wording and it is accurate, which the original was not.

**One consequence worth stating:** the archiver is now load-bearing for correctness, not merely
for backtesting convenience. If it falls behind trimming, history is lost permanently. The
retention rule must be that **a segment is trimmed only after the archiver's checkpoint is past
it** — the same discipline recorded for segment retention under S1, which applies equally here.

---

## 13. Core architecture confirmed 2026-08-28

The four load-bearing elements of this issue are confirmed as recorded. Nothing required
reconciliation.

| Element | Where decided |
|---|---|
| **Gateway owns risk state** | Approach A, §2–§3 |
| **Reservations live in gateway memory** (not Redis) | §10, answering §6.4 |
| **The C++ engine stays money-blind** | Approach A; preserves the minimal-engine premise of OI 002 |
| **The Redis stream ID provides ordering** | §10.3, restating OI 001 sub-decision 2b |

Together these give the property the issue exists to establish: **exactly one component writes
reservation state, and it is single-threaded**, so the double-spend race in §1 cannot occur —
not because it is caught, but because there is no interleaving in which it can happen.

Two consequences already recorded and now firm:

- Reservations are lost on gateway restart and **rebuilt by replaying the outbound stream**.
- Risk state is bound to one gateway process. Phase 2's multi-gateway work must move or shard
  it. Recorded as a known limitation, not a defect.

### 13.1 What remains open in this issue

| # | Proposal | Note |
|---|---|---|
| **4a** | Redis stream authoritative over the retained window; **archive** authoritative beyond it; relational database is a derived read model, never authoritative | Corrected in §12 — the original "replayable from genesis" claim no longer holds after trimming and the I12 restatement |
| **4b** | Gateway risk-state recovery by periodic checkpoint (offset + snapshot) then replay forward | Same mechanism as the engine; no new code path to test |
| **4d** | Market orders implemented as **marketable limit orders with a price band** | Makes the reservation computable and prevents a thin book being swept |
| **4e** | Idempotency claim and stream append fused into **one atomic Lua script** | Resolved in OI 008 §13.1; needs confirming here too |

Sub-decision 4e is also a prerequisite for Open Issue 008 sub-decision 9i, which specifies the
order of validate, reserve, and claim-and-append across both issues.

## 14. Decision log

| Date | Status | Note |
|---|---|---|
| 2026-08-27 | OPEN | Approach A proposed with sub-decisions 4a–4d |
| 2026-08-27 | AMENDED | Maker/taker fees; invariant I10 extended with a house fee account |
| 2026-08-27 | AMENDED | 4c resolved: single inbound stream; engine forwards non-order records |
| 2026-08-28 | AMENDED | Approach A confirmed; 4e proposed; 2b restated as stream-ID ordering |
| 2026-08-28 | AMENDED | 4a unblocked and corrected (§12) |
| 2026-08-28 | **CORE CONFIRMED** | Four load-bearing elements confirmed. 4a, 4b, 4d, 4e remain open. |

---

## Amendment 2026-08-28 — sub-decisions resolved (Open Issue 018 §13)

- **4a decided, simplified.** The Redis stream is the source of truth for money; the relational
  database is a derived read model, never authoritative. **The archive is not correctness-critical
  in Phase 1** — `MAXLEN` is set to ~2 M entries so the retained window exceeds any realistic
  session, and the archive exists to produce backtest data. This works because Phase 1 backtests
  run against real crypto history, not own-market history. §12 above is superseded on this point.
- **4b decided, simplified. No snapshots or checkpointing in Phase 1.** Every consumer rebuilds
  by replaying the retained stream from its start. Recovery time becomes an honest measured
  number (order of ten seconds), and checkpointing becomes a Phase 2 optimisation with a
  before/after. Saving ~12 h across engine and gateway.
- **4d decided as proposed.** Market orders are marketable limit orders with a price band.
- **4e already confirmed** via Open Issue 008 point 4.

Two operating rules follow from 4a and must hold: bots run at a modest idle rate (~10–50\norders/sec) on the demo deployment, and **benchmarks run on a scratch deployment** — a load test
at 20k/sec would otherwise consume the retained window in 100 seconds.
