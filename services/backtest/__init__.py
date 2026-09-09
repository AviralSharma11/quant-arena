"""Task 7.1 — the backtester.

The research half of the project. Bars are fed one at a time so lookahead is impossible by
construction; fills are at the next bar's open with the taker fee; every run carries a manifest
that makes it reproducible. Engine-based fills are Phase 2 (Open Issue 018 section 3.2).

Run one:

    python -m services.backtest --symbol QAA --bar-minutes 5
"""
