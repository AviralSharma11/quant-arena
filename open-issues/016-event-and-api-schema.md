# Open Issue 016 — Event Schema and API Surface

**Status:** closed by user
**Opened:** 2026-08-27
**Depends on:** Open Issue 002 (integer-only numerics), Open Issue 004 (record types), Open Issue 008 (identifiers)
**Owner:** _unassigned_

---

## 1. Why this deserves its own decision

The event schema is two things at once:

1. **The contract between every component.** Gateway, engine, ledger, fan-out, archiver and
   backtester all read and write the same records.
2. **The contract between the two developers.** Open Issue 002 §6 makes the engine interface
   the seam that lets Dev A and Dev B work without blocking each other. That seam is this
   schema.

It is week-1 work, because everything else is written against it.

It also carries a constraint no ordinary API has: **the events are replayed.** A schema change
in week 4 must not make week-2 events unreadable, or recovery and reproducibility both break.

---

## 2. Sub-decision 16a — Schema evolution under replay

The naive framings both fail. "Never change the schema" is not achievable over seven weeks of
development. "Version every record and support all versions forever" is a real cost that grows
for the entire life of the project.

**The resolution is to notice that genesis replay is not actually an operational requirement.**

Recovery replays **from the last snapshot**, not from the beginning of time (Open Issue 002 §4,
Open Issue 004 sub-decision 4b). Snapshots are taken frequently. So schema compatibility only
has to hold **across one snapshot interval** — not forever.

| Consumer | What it must read | Compatibility needed |
|---|---|---|
| Engine recovery | Last snapshot onward | One snapshot interval |
| Gateway risk rebuild | Last checkpoint onward | One snapshot interval |
| Ledger | Live stream | Current version only |
| **Archive / backtester** | Arbitrarily old history | **Migrated offline when the schema changes** |

**Proposed:**

- Every record carries a `schema_version` field.
- Consumers accept the current version and the immediately preceding one — enough to cover a
  rolling deploy and one snapshot interval.
- On a breaking change during development, take a snapshot and truncate. Old events are not
  supported, because nothing needs them.
- The **archive** is migrated offline by a script when the schema changes. It is not on any
  hot path, so a batch rewrite is acceptable.

### Consequence for invariant I12

Open Issue 010 lists I12 as "replaying from genesis reproduces current balances exactly." Under
this decision that is no longer an *operational* property.

**Proposed restatement:** I12 becomes a **test** property over a fixed generated dataset —
replay this known sequence from its start, assert the resulting balances. Operationally,
recovery replays from the last snapshot, which is what real systems do. The test keeps the
guarantee that matters (the replay logic is correct) without requiring that every record ever
written stays readable forever.

---

## 3. Sub-decision 16b — One definition, two languages

The records cross a C++/Python boundary. Hand-writing the layout in both languages invites
silent drift — and a mismatch here does not raise an error, it misreads fields, which is the
worst possible failure mode in exactly the wrong component.

| Option | For | Against |
|---|---|---|
| Hand-write in both | No tooling | Drift is silent and catastrophic; and it drifts *between two developers* |
| Protobuf | Mature; versioning built in | Variable-width wire format, so records are not fixed-size POD; adds a build step |
| FlatBuffers / Cap'n Proto | Zero-copy | Heavier than the problem warrants |
| **Codegen from one definition file** | Fixed-width POD structs; deterministic sizes; ~200 lines of generator | Custom code, however small |

**Proposed: a single definition file, and a small generator** producing a C++ header of packed
structs and a Python module of `struct` format strings and named tuples. Roughly 6 hours.

The justification is specific rather than general: the records are already constrained to
fixed-width integers and small enums with no strings and no nesting (Open Issue 002 sub-decision
3c). That is precisely the case where a general serialisation framework earns nothing and a
50-line generator earns a great deal. It also makes the schema a **single reviewable artifact**
that both developers change together, which is the actual risk being managed.

---

## 4. Sub-decision 16c — Record types

### Inbound stream

| Record | Fields | Engine |
|---|---|---|
| `SubmitOrder` | `client_order_id`, `user_id`, `symbol_id`, `side`, `price_ticks`, `qty`, `tif` | Matched |
| `CancelOrder` | `client_order_id`, `user_id`, `target_client_order_id` | Removed |
| `CreateAccount` | `user_id`, `client_order_id` (as the idempotency key) | Forwarded |
| `CreditCash` | `user_id`, `amount_ticks`, `client_order_id` | Forwarded |

### Outbound stream

| Record | Notes |
|---|---|
| `OrderAccepted` | `order_id`, `seq`, echoing `client_order_id` |
| `OrderRejected` | With a reason code |
| `Fill` | Both sides, `price_ticks`, `qty`, **`aggressor_side`** (required for maker/taker fees, Open Issue 011 §11.2) |
| `OrderCancelled` | Distinguishes user-initiated from expiry |
| `BookChanged` | Consumed by fan-out for conflation |
| `AccountCreated`, `CashCredited` | Forwarded records, now sequenced |

Every record carries: `seq`, `timestamp_ns`, `schema_version`, `record_type`.

---

## 5. Sub-decision 16d — REST surface

Deliberately small. Streaming carries everything continuous; REST carries actions and
re-synchronisation.

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/auth/register`, `/auth/login`, `/auth/logout` | Sessions (Open Issue 015) |
| `POST` | `/orders` | Submit — returns an acknowledgement, not a result (Open Issue 008 §9h) |
| `DELETE` | `/orders/{client_order_id}` | Cancel by client order id |
| `GET` | `/orders/open` | Re-synchronisation after a stream gap |
| `GET` | `/portfolio` | Re-synchronisation after a stream gap |
| `GET` | `/symbols` | Static configuration |
| `POST` | `/backtests`, `GET /backtests/{id}` | Run and retrieve |
| `WS` | `/stream` | Market data and the private user stream |

The two `GET` endpoints exist **solely** for re-synchronisation after a detected sequence gap
(Open Issue 014 sub-decision 14e). They are not the normal path, and the UI must not poll them.

---

## 6. Sub-decision 16e — Conventions

Fixed once, so that neither developer has to ask:

| Convention | Rule |
|---|---|
| Money | `int64` ticks. Never floating point below the presentation layer |
| Quantities | `int64` units |
| Timestamps | `int64` nanoseconds, **assigned by the gateway**, never read inside the engine |
| Order ids | `uint64`, monotonic, engine-assigned |
| Client order ids | Opaque, client-assigned, unique per user |
| Symbols | `int16` id on the wire; the string name appears only at the presentation layer |
| Enums | Small integers with named constants; never strings |
| Field naming | `snake_case`, with units in the name (`price_ticks`, `timestamp_ns`) |

Putting units in field names is not stylistic. `price` invites the question of whether it is
ticks or rupees; `price_ticks` does not, and that ambiguity is exactly how a floating-point
value or a unit-conversion error reaches the ledger.

---

## 7. Cost summary

| Item | Hours |
|---|---|
| Schema definition file | 3 |
| Generator for C++ header and Python module | 6 |
| REST endpoint definitions and validation | 4 |
| **Total** | **13** |

Absorbed within the gateway budget. **This is week-1 work** — everything else is written
against it.

---

## 8. Questions to resolve

1. **Is codegen worth 6 hours**, against hand-writing the structs in both languages? The
   argument is that the failure mode of drift is silent misreads in the money path, and that
   two developers editing two copies is where drift comes from.
2. **Is restating I12 as a test property acceptable?** It is what real systems do, but the
   claim weakens from "we can replay everything from genesis" to "our replay logic is proven
   correct on a fixed dataset."
3. **`tif` (time in force) in Phase 1?** Only `GTC` and `IOC` are meaningful without a session
   clock. Including the field now costs nothing and avoids a schema change later.
4. **Is the REST surface complete?** Anything missing becomes a schema change during the build.

---

## 9. Decision log

| Date | Status | Note |
|---|---|---|
| 2026-08-27 | OPEN | Sub-decisions 16a–16e proposed; I12 restatement raised. Nothing final. |

---

## 10. Amendment 2026-08-27 — answers recorded

- **16b — codegen confirmed.** One definition file; a generator produces the C++ packed-struct
  header and the Python module. Hand-writing the layout twice is rejected.
- **16a — I12 is restated as a test property.** See §10.1.
- **16c — `tif` (time in force) is included now.** `GTC` and `IOC` only; the field costs nothing
  today and avoids a schema change later.
- **16d — the REST surface is confirmed complete.** Additions during the build are scope
  changes, to be raised rather than absorbed.

### 10.1 Invariant I12 restated

**Previously:** replaying from genesis reproduces current balances exactly — an operational
property.

**Now:** replaying a fixed generated dataset from its start reproduces the expected balances
exactly — a **test** property, running in the per-commit suite (Open Issue 010 §10a).

Operationally, recovery replays from the last snapshot, which is what production systems do and
what makes bounded schema compatibility viable at all.

**The weakened claim, stated plainly so it is not overstated later:** the project can say *"our
replay logic is proven correct against a fixed dataset"* rather than *"every event ever written
can be replayed from genesis."* The guarantee that matters — that replay is correct — is
retained; the guarantee that is expensive and unnecessary — that every historical record stays
readable forever — is not.

The archive retains full history for backtesting and is migrated offline when the schema
changes, so nothing is actually lost; it simply is not replayable through the live path.

## 11. Decision log

| Date | Status | Note |
|---|---|---|
| 2026-08-27 | OPEN | Sub-decisions 16a–16e proposed |
| 2026-08-27 | AMENDED | Codegen confirmed; I12 restated as a test property; `tif` included; REST surface confirmed complete. Still not final. |
