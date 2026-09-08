"""The archiver — outbound stream in, Parquet out.

Task 6.2. It produces the three derivatives the backtester (7.1) consumes — trades, OHLCV bars
and 1 Hz L2 book snapshots — partitioned by symbol and day, and it resumes cleanly after a
restart.

## What it is not

**The archive is not correctness-critical in Phase 1**, and the task says so twice. `MAXLEN` is
two million entries, which is more than any realistic Phase 1 session, so the stream alone is
authoritative and no consumer ever needs to read a file this process wrote (Open Issue 018
§13.2, correcting Open Issue 004's original 4a). It becomes load-bearing in Phase 2, when the
retention rule changes. Nothing here is on the order path, and nothing on the order path waits
for it.

That is why this process may be stopped, deleted and rebuilt from the stream at any time, and
why its failure mode is "no files", never "wrong market".

## The three modules

| Module | What is in it | Pure? |
|---|---|---|
| `state.py` | bucketing, the low-water mark, pending rows | yes — no clock, no Redis, no disk |
| `writer.py` | Parquet paths, atomic writes, the checkpoint file | disk only |
| `runner.py` | the Redis loop, recovery, shutdown | all the I/O |

The same split the matcher, ledger and fan-out use, and for the same reason: the part that
decides *what* a bar or a snapshot is stays replayable and directly testable, and nothing can
smuggle a clock reading into a derivation that has to reproduce identically.
"""
