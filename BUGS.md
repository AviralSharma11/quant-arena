# Bugs

Known defects that are not yet fixed. One section per bug. A fixed bug is deleted from this file;
the commit that fixed it is the record.

---

## BUG-001 — Cash is not denominated in one currency across symbols

**Found:** 2026-09-16, verifying PR #29 in the browser · **Severity:** high (money) · **Status:** open, needs a decision

### Symptom
The portfolio panel shows the same cash balance differently depending on the selected symbol:

| Selected | Cash shown |
|---|---|
| QAA (`tick_size_ticks = 100`) | ₹99,999,277.22 |
| QAJ (`tick_size_ticks = 1_000`) | ₹9,999,927.722 |

`PortfolioPanel` formats `cash_ticks` with the *selected* symbol's tick size
(`web/src/screens/Trading.tsx:146`, `cashSymbol={selected}`). Its doc comment says "Ticks are one
currency across the venue, so this is a display choice, not a conversion." That is not true.

### Cause
The display bug sits on top of an accounting one. Prices are integer ticks whose value
**differs per symbol**: `tick_size_ticks` is the display divisor, and the ten listed symbols use
four different values (100, 1,000, 10,000, 100,000; `config/quant_arena.toml`). The engine is
money-blind, and the gateway and ledger charge a fill's notional as `price_ticks × qty` in raw
ticks (`services/ledger/ledger.py`, `calculate_fees`; `services/gateway/risk.py`,
`reject_reason_for`). So one tick of cash spent on QAJ is not worth the same as one tick spent on
QAA.

Worked example: QAJ displays ₹0.910, which is 910 ticks at `tick_size_ticks = 1_000`. Buying one
unit reserves and debits **910 cash ticks**. Cash is displayed at a scale of 100, since the
₹100,000,000 grant is `initial_cash_ticks = 10_000_000_000`, so that purchase costs **₹9.10**:
ten times the price on screen. On a `100_000` symbol the error is 1,000×. Profit and loss, fees,
risk checks and the backtest comparison (7.1) are all affected wherever symbols with different tick
sizes are mixed.

### Why no display fix is correct
No single `cashSymbol` makes every symbol's costs line up with its displayed price, because the
stored balances are already inconsistent across instruments.

### Likely fix (needs a decision)
Define one venue-wide **cash scale** (for example 1 cash tick = ₹0.01, i.e. 100 per rupee) and
convert each fill's notional at the money boundary:
`notional_cash = price_ticks × qty × CASH_SCALE / tick_size_ticks`, with an explicit integer
rounding rule. The engine stays money-blind; the gateway (reservation, release, market-order
bands) and the ledger (cash, fees) must use the **same** function, and the frontend formats cash
with the cash scale, never a symbol's. Tick sizes that do not divide evenly need a rounding
decision that cannot mint or destroy money (a conservation property test).

Changing it alters every stored balance, so it is a `docker compose down -v` or a replay under
the new rule. Owner and schedule are not decided.

---

## BUG-002: an order the engine rejects keeps its cash reserved in the gateway

**Found:** 2026-09-16, while adding checkpoints to `RiskState` · **Severity:** low (rare path) · **Status:** open

`RiskState.reserve` records every submitted order in `pending_by_client` and adds its cost to
`reserved`. The entry is cleared only by `OrderAccepted` (`services/gateway/risk.py`, `apply`);
there is no `OrderRejected` branch. So when the gateway's own checks pass but the **engine** rejects
the order, its reservation is never released, and the account's available cash stays reduced until
the gateway restarts. The engine can reject for invalid price, quantity, side or TIF.

It is rare because the gateway validates the same fields first. It also no longer survives a
restart: a checkpoint excludes in-flight reservations (Open Issue 020). **Fix:** handle
`OrderRejected` in `apply` by popping `(user_id, client_order_id)` from `pending_by_client` and
releasing it, and add a test with an engine-only rejection.

---

## BUG-003: `tests/gateway/test_halt.py` fails, and it restarts the live Redis

**Found:** 2026-09-16 · **Severity:** medium (test hygiene) · **Status:** open

The test stops and starts the Compose `redis` service **of the running stack**, then expects a newly
registered user to have an order accepted. That last step fails ("recovered, but never accepted an
order"), with or without the checkpointing changes. Separately from the failure, running the gateway
test suite bounces the live market's Redis; it should run against a scratch Compose project.

---

## BUG-004: a published L2 snapshot is occasionally crossed for one tick

**Found:** 2026-09-16, verifying Open Issue 020 on the live stack · **Severity:** low (self-correcting) · **Status:** open

Over 30 s of all ten `book:*:l2` channels, 7 of 913 snapshots had best bid ≥ best ask, and never
two in a row for the same symbol: the next snapshot is always correct. This is not the restart fork
(a phantom order stays crossed until it is removed).

**Likely cause, not yet proven:** `FanOut.step` reads the outbound stream 100 records at a time, and
the conflation tick can run between two reads. An aggressing order's `OrderAccepted` puts it on the
book crossed; its `Fill` records, which remove the cross, may arrive in the next read. A snapshot
taken in between shows the momentary cross. **Fix direction:** do not publish a symbol's book
while an order accepted in the last read may still have fills pending, for example by holding a
symbol dirty until a read ends on a record that is not an `OrderAccepted` for it. Confirm the cause
first with a test that splits an accept and its fill across two reads.
