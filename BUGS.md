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
