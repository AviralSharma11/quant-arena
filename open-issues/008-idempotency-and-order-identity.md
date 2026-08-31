# Open Issue 008 — Idempotency and Order Identity

**Status:** RESOLVED (2026-08-28) — all sub-decisions decided
**CURRENT STATE:** Two IDs · client_order_id mandatory · Redis holds idempotency state · claim
and XADD atomic · duplicate releases the reservation · rejections recorded · acknowledgement-
based submission · final events via the private stream.
**Opened:** 2026-08-27
**Addresses:** README.md Problem 4 (preventing duplicate processing)
**Depends on:** Open Issue 003 (Redis is now available as a store)
**Owner:** _unassigned_

---

## 1. The problem

`README.md` Problem 4 states it directly: a network failure may cause a client to send the
same request twice, and without protection the system creates two identical orders.

The failure is not exotic. It is the ordinary case:

```
  Client sends "BUY 50 @ ₹102"
  Gateway accepts, sequences it, engine fills it
  The HTTP response is lost — WiFi drops, the tab is closed, a timeout fires
  Client retries the same request
  Without protection: the user now owns 100 units and has spent twice
```

The client genuinely cannot tell the difference between *"the request never arrived"* and
*"the request succeeded but the response was lost."* Only the server can, and only if it was
designed to.

This matters more here than in a typical CRUD application, because the duplicate is not a
duplicate row that can be cleaned up later — **it is a real trade with a real counterparty
whose position also moved.** It cannot be undone without unwinding someone else's fill.

---

## 2. Sub-decision 9a — Who generates the identity?

### Option 1 — Server-generated order ID

The client posts an order; the server returns an ID it invented.

**Against:** this cannot work for deduplication. On a lost response the client never learns
the ID, so a retry carries no way to say "this is the same request as before." Server-side
IDs are necessary for *referring* to an order, but useless for *deduplicating* one.

### Option 2 — Client-generated idempotency key

The client generates a unique key (a UUID) *before* sending, and sends it with the request.
On retry it sends the same key. The server recognises the key and returns the original
outcome instead of acting again.

**For:** it is the only approach that works, because identity must exist before the first
attempt. It is also how Stripe, AWS, and every payments API handle this.

### Option 3 — Client order ID, in the exchange tradition

The same mechanism, but named and used the way exchanges use it. In FIX, `ClOrdID` is a
client-assigned identifier, unique per session, that the client uses to refer to its own
orders — including for cancellation, without ever needing to learn the exchange's ID.

**Proposed: Option 3.** It is Option 2 with a realistic name and a second use. It gives:

- **Deduplication** — a repeated `ClOrdID` is a retry.
- **Cancel-by-client-ID** — a client can cancel an order whose acknowledgement it never
  received, which Option 1 makes impossible and which is a real requirement, not a nicety.

The system therefore carries **two identifiers per order**, as real exchanges do:

| Identifier | Assigned by | Purpose |
|---|---|---|
| `client_order_id` | Client, before sending | Idempotency, and referring to one's own order |
| `order_id` | Engine, monotonic uint64 | Book internals, event stream, price-time priority |

---

## 3. Sub-decision 9b — What is stored, and what is returned on a duplicate?

Recording only "this key was seen" is not enough. A retry must receive **the same answer as
the original**, otherwise the client still cannot reconcile.

**Proposed:** store, keyed by `(user_id, client_order_id)`, the outcome of the first attempt
— accepted or rejected, the assigned `order_id`, the sequence number, and the rejection
reason if any. On a duplicate, return that stored outcome with an indication that it was a
replay.

Three cases must behave correctly:

| Case | Correct behaviour |
|---|---|
| Retry before the original is sequenced | Must not create a second order. Either wait for the first outcome or return "in progress" |
| Retry after acceptance | Return the original acceptance and `order_id` |
| Retry after rejection | Return the original rejection and its reason |

The first case is the one usually missed. It requires the key to be **claimed before
sequencing, not after** — the same discipline as the reservation in Open Issue 004, and for
the same reason.

---

## 4. Sub-decision 9c — Where does the deduplication store live?

| Option | For | Against |
|---|---|---|
| Gateway memory | Fastest; no dependency | Lost on restart, so a retry across a gateway restart duplicates |
| **Redis** | Already a required dependency (OI 003); survives gateway restart; native TTL for expiry; shared if gateways multiply later | One round trip on the submit path |
| Relational database | Durable and queryable | A write and a read on the hot path; the latency Open Issue 001 rejected |

**Proposed: Redis**, with the key claimed atomically via `SET key value NX EX <ttl>`. A
successful set means "this is new, proceed"; a failed set means "this is a retry, return the
stored outcome."

This is a decision the Open Issue 003 outcome improved. Before Redis was adopted, gateway
memory would have been the only option without adding a dependency solely for this purpose.

---

## 5. Sub-decision 9d — How long are keys remembered?

Retention is a trade between memory and the width of the retry window.

| Window | Memory at ~500 orders/sec | Notes |
|---|---|---|
| 5 minutes | ~150k keys | Covers ordinary network retries |
| **1 hour** | ~1.8M keys, order of 200 MB | Covers a client offline for a while |
| 24 hours | ~43M keys | Expensive; protects against a scenario that does not occur |

**Proposed: a 1-hour TTL**, applied by Redis automatically. Beyond that window a retry is
treated as a new order, and that behaviour is documented rather than left implicit — an
hour-old retry is far more likely to be a genuine new intention than a network artifact.

---

## 6. Sub-decision 9e — Cancels and other requests

**Cancels are naturally idempotent** and need no key: cancelling an already-cancelled order
is a no-op that returns the same state. Cancelling a fully-filled order returns "too late"
either way. The correct response to a duplicate cancel is simply the current order state.

**Cash grants and account creation must be idempotent**, and are more dangerous than orders
because a duplicated grant creates money from nothing — breaking the cash-conservation
invariant in Open Issue 004 §5. They pass through the inbound stream (Open Issue 004
sub-decision 4c) and use the same mechanism.

---

## 7. Sub-decision 9f — Bots must exercise this path

The bots submit through the real API (Open Issue 005 §4), so they generate client order IDs
like any other participant. **Proposed:** the load-test harness deliberately injects
duplicate submissions at a configurable rate, so the deduplication path is exercised under
load rather than only in unit tests.

This costs almost nothing and converts an untested defensive mechanism into a measured one.
"Duplicates injected at 1% of order flow, zero duplicate fills observed across N million
orders" is a concrete claim; "we implemented idempotency" is not.

---

## 8. Cost summary

| Item | Hours |
|---|---|
| Client order ID plumbing through the API and event schema | 3 |
| Redis claim-and-store with TTL; three-case retry handling | 5 |
| Cancel-by-client-order-id | 2 |
| Duplicate injection in the load harness | 2 |
| **Total** | **12** |

Absorbed within the 35 hours budgeted for the gateway.

---

## 9. Questions to resolve

1. **Is the two-identifier scheme (`client_order_id` plus engine `order_id`) agreed?** It
   matches exchange practice and enables cancel-by-client-id, at the cost of carrying two
   identifiers through every layer.
2. **Retry-before-sequencing: wait, or return "in progress"?** Waiting is simpler for the
   client but holds a request open. Returning "in progress" is more honest but requires the
   client to poll or listen on its private stream.
3. **Is a 1-hour TTL right?** Shorter saves memory; longer widens the retry window.
4. **Should the idempotency key be required or optional on the API?** Requiring it is
   stricter and prevents a careless client from bypassing protection; optional is friendlier
   for someone experimenting with `curl`.

---

## 10. Decision log

| Date | Status | Note |
|---|---|---|
| 2026-08-27 | OPEN | Sub-decisions 9a–9f proposed. Nothing final. |

---

## 11. Amendment 2026-08-27 — answers recorded

- **9a — two identifiers confirmed.** `client_order_id` assigned by the client before
  sending, and `order_id` assigned by the engine. Both are carried through every layer and
  both appear in the event schema. Cancel-by-client-order-id is therefore supported.
- **9d/9g — the idempotency key is required**, not optional. A submission without a
  `client_order_id` is rejected with a 400 before any other processing. Stricter, and it
  removes the possibility of a careless client silently bypassing duplicate protection.
- **9b — a retry arriving before the original is sequenced returns "in progress"**, rather
  than holding the request open until the outcome is known.

### 9h — Consequence: the order API is acknowledgement-shaped, not result-shaped

Returning "in progress" is only coherent if the client already has somewhere to receive the
outcome. It does — the private user stream from Open Issue 006 sub-decision 7c. But it makes
explicit something that should be stated deliberately rather than discovered:

**Order submission returns an acknowledgement, not a result.**

```
POST /orders  { client_order_id, symbol, side, price, qty }
  → 202 Accepted { client_order_id, order_id, seq, status: "accepted" }

  ... fills, partial fills, and the final state arrive on the private stream
```

| Response | Meaning |
|---|---|
| `202 accepted` | Sequenced and durable. The outcome will arrive on the private stream |
| `202 in_progress` | A duplicate of a request still being processed. Await the private stream |
| `200 replay` | A duplicate of a completed request; the original outcome is returned |
| `400` | Malformed, or missing `client_order_id` |
| `409 rejected` | Failed validation or a risk check. Never reached the engine |
| `503 halted` | The exchange cannot durably record orders |

This matches how real exchanges behave — an order acknowledgement is not a fill, and the two
arrive separately over different channels. It also means the UI must be built around the
private stream from the beginning rather than around synchronous request/response, which is a
cheap decision now and an expensive one to retrofit.

## 12. Decision log

| Date | Status | Note |
|---|---|---|
| 2026-08-27 | OPEN | Sub-decisions 9a–9f proposed |
| 2026-08-27 | AMENDED | Two identifiers confirmed; key required; retry-before-sequencing returns "in progress"; 9h records the resulting acknowledgement-shaped API. Still not final. |

---

## 13. Sub-decision 9c resolved 2026-08-28, and a new problem: operation ordering

### 13.1 9c — the deduplication store

**Resolved: Redis, and the claim is fused with the stream append.** Open Issue 004 sub-decision
4e proposes performing the idempotency claim and the `XADD` in a **single Lua script**, executed
atomically by Redis. That resolves 9c and 4e together: the store is Redis, and it is written by
the same atomic operation that sequences the order.

Without fusing them there is a window in which the key is claimed but the record is never
appended — a crash between the two operations strands the client on `in_progress` until the TTL
expires an hour later, because nothing will ever produce an outcome.

### 13.2 Sub-decision 9i — the exact order of operations (new)

Specifying 9c and the reservation from Open Issue 004 together exposes an ordering problem
neither issue addressed alone. The submit path has three side-effecting steps:

- **V** — validate: bounds, tick grid, symbol (Open Issue 015 sub-decision 15e)
- **R** — risk check and reserve, in gateway memory (Open Issue 004, Approach A)
- **C+A** — claim the idempotency key and append to the stream, atomically (4e)

Two orderings are plausible and both are wrong in different ways:

| Ordering | Failure |
|---|---|
| V → C+A → R | If the risk check then rejects, the key is claimed but no order exists — the same stranding problem 4e was written to eliminate |
| V → R → C+A | If C+A detects a duplicate, the reservation taken in R has already been applied. **Every retry leaks a reservation**, and a client retrying a few times has its buying power silently consumed |

**Proposed: V → R → C+A, with an explicit release on duplicate detection.**

```
1. Validate. On failure → 400. Nothing else has happened.
2. Risk check and reserve in gateway memory.
   On failure → record the rejection (see 13.3) and return 409.
3. Claim the key and append, atomically (4e).
   - Claim succeeded  → 202 accepted.
   - Claim failed, outcome stored → RELEASE the reservation, return the stored outcome.
   - Claim failed, no outcome yet → RELEASE the reservation, return 202 in_progress.
```

The release is deterministic and entirely local — it touches only gateway memory and cannot
fail partway — so this ordering has no window of its own. It is the reverse-order compensation
that the other ordering cannot provide, because a claimed key in Redis cannot be un-claimed
safely once a crash is possible.

### 13.3 Consequence — risk rejections must also be recorded

Section 3 states that a retry after rejection returns the original rejection and its reason.
Under the ordering above, a risk-rejected order **never reaches step 3**, so nothing is stored
and a retry would be re-evaluated from scratch — possibly passing, if the balance has changed
in between. That contradicts §3.

**Proposed: record rejections too.** A risk rejection claims the key with the rejection as its
stored outcome. One extra Redis write on the rejection path, and it makes the contract in §3
true rather than aspirational: **the same `client_order_id` always yields the same answer,
whatever that answer is.**

The alternative — accepting that rejected orders are re-evaluated on retry — is defensible and
arguably friendlier, but it is a different contract and would require rewording §3. Predictable
is better than friendly here; a client that retries and gets a different verdict has no way to
reason about its own state.

### 13.4 Remaining open sub-decisions

| # | Proposal |
|---|---|
| **9d** | **1-hour TTL.** ~1.8 M keys, order of 200 MB at target volumes. Beyond the window a retry is treated as a new order, documented rather than implicit — an hour-old retry is far more likely genuine intent than a network artifact |
| **9e** | **Cancels carry a key for uniformity, but deduplication is not load-bearing for them** — cancelling an already-cancelled order is a no-op returning the same state, and cancelling a filled order returns "too late" either way. **`CreateAccount` and `CreditCash` are the dangerous cases**: a duplicated grant creates money from nothing and breaks invariant I10. They travel the same path and use the same mechanism |
| **9f** | **The load harness injects duplicate submissions at a configurable rate**, so the path is exercised under load rather than only in unit tests. ~2 hours, and it converts an untested defensive mechanism into a measured claim: *"duplicates injected at 1% of order flow, zero duplicate fills across N million orders"* |

### 13.5 Revised cost

| Item | Hours |
|---|---|
| Client order ID through the API and event schema | 3 |
| Atomic claim-and-append Lua script (4e) | 3 |
| Three-case retry handling and the 9i ordering | 4 |
| Recording rejections (13.3) | 1 |
| Cancel-by-client-order-id | 2 |
| Duplicate injection in the load harness | 2 |
| **Total** | **15** |

Against 12 previously. The increase is 4e plus rejection recording; both remove real failure
modes.

## 14. Decision log

| Date | Status | Note |
|---|---|---|
| 2026-08-27 | OPEN | Sub-decisions 9a–9f proposed |
| 2026-08-27 | AMENDED | Two identifiers; key required; retry-before-sequencing returns "in progress"; 9h acknowledgement-shaped API |
| 2026-08-28 | AMENDED | 9c resolved as Redis fused with the append via 4e; **9i added** (operation ordering, with release-on-duplicate); 13.3 requires recording rejections. 9d, 9e, 9f still open. |

---

## 15. Confirmed 2026-08-28

| # | Confirmed | Sub-decision |
|---|---|---|
| 1 | Two identifiers — `client_order_id` + `order_id` | 9a |
| 2 | `client_order_id` is mandatory | 9g |
| 3 | Redis holds idempotency state | 9c |
| 4 | Claim and `XADD` happen atomically | 9c / OI 004 4e |
| 5 | A duplicate releases the reservation | 9i |
| 6 | Rejected requests are also recorded | §13.3 |
| 7 | Order submission is acknowledgement-based | 9h |
| 8 | Final order events arrive on the private stream | 9h |

Points 4, 5 and 6 together close the three failure modes this issue exists to eliminate: a
claimed key with no order behind it, a reservation leaked on every retry, and the same
`client_order_id` producing different answers on different attempts.

**Open Issue 004 sub-decision 4e is confirmed by point 4** and can be closed there.

### 15.1 Still open

| # | Proposal |
|---|---|
| **9d** | **1-hour TTL** on idempotency keys — roughly 1.8 M keys, order of 200 MB at target volumes. Beyond the window a retry is treated as a new order, documented rather than implicit |
| **9e** | Cancels carry a key for uniformity, but deduplication is not load-bearing for them. **`CreateAccount` and `CreditCash` are the dangerous cases** — a duplicated grant creates money from nothing and breaks invariant I10 |
| **9f** | The load harness injects duplicate submissions at a configurable rate (~2 h), converting a defensive mechanism into a measured claim |

## 16. Decision log

| Date | Status | Note |
|---|---|---|
| 2026-08-27 | OPEN | 9a–9f proposed |
| 2026-08-27 | AMENDED | Two identifiers; key required; "in progress" on early retry; 9h |
| 2026-08-28 | AMENDED | 9c resolved via 4e; 9i added; rejections recorded |
| 2026-08-28 | **CONFIRMED** | Eight-point core mechanism agreed. 9d, 9e, 9f remain open. |

---

## Amendment 2026-08-28 — sub-decisions resolved (Open Issue 018 §13)

- **9d confirmed.** 1-hour TTL on idempotency keys; a configuration value.
- **9e confirmed.** Cancels carry a key for uniformity though deduplication is not load-bearing
  for them. `CreateAccount` and `CreditCash` use the same mechanism and are the dangerous cases,
  since a duplicated grant creates money from nothing and breaks invariant I10.
- **9f confirmed.** The load harness injects duplicate submissions at a configurable rate (~2 h).
  This carries the highest interview value per hour of anything remaining: it converts an
  untested defensive mechanism into a measured claim.
