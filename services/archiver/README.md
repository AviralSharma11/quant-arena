# Archiver — Task 6.2

Outbound stream in, Parquet out. Three derivatives — trades, OHLCV bars, 1 Hz L2 book snapshots —
partitioned by symbol and day, resumable after a restart.

```
qa.outbound ──▶ ArchiveState ──▶ ArchiveWriter ──▶ <root>/<dataset>/symbol=QAA/date=…/HHMMSS.parquet
                (pure)             (disk)          <root>/_checkpoint.json
```

## 1. What this is not

**The archive is not correctness-critical in Phase 1.** `MAXLEN` is two million entries, more than
any realistic Phase 1 session, so the stream alone is authoritative and no consumer ever reads a
file this process wrote (Open Issue 018 §13.2, which corrected Open Issue 004's original 4a). It
becomes load-bearing in Phase 2, when the retention rule changes and a segment may only be trimmed
after the archiver's checkpoint has passed it.

Consequences worth knowing before changing anything here:

- Nothing on the order path waits for this process, and nothing depends on its container.
- Its failure mode is "no files", never "wrong market". Where a choice existed between dropping a
  row and risking a wrong one, the row is dropped and counted (`late_rows`).
- It may be stopped, its volume deleted, and the whole archive rebuilt from the retained stream.

## 2. The flush unit, and why the checkpoint is a low-water mark

Everything is bucketed on `timestamp_ns` — the stamp the gateway wrote, never a clock read — into
a **flush unit** of 60 seconds. A unit is written when the stream proves it is complete: the first
record of a later unit has arrived, so no further trade can land in it and every bar bucket inside
it has ended.

The **checkpoint is the last stream ID whose unit is fully written**, which is the ID immediately
preceding the oldest still-open unit's first record. Not the last record applied. That gives one
invariant, and Success Criterion 3 falls out of it:

> Everything at or before the checkpoint is on disk; nothing after it has been written.

A restart therefore re-reads from the checkpoint, re-derives the in-flight units, and writes each
file exactly once. No overwrite semantics, no dedup pass, no partial file to reconcile. The cost is
that the checkpoint lags one flush unit, so a restart re-reads up to 60 seconds of stream — at
8.32 µs per record, noise.

Two details that are easy to get wrong and expensive to notice:

- The checkpoint is **exclusive**, because `read_records(last_id=…)` and `XREAD` both return
  records *after* the ID given. A checkpoint naming a record still to be re-read would skip exactly
  that record on every restart. `Unit.resume_after` is what makes it exclusive.
- Stream IDs are compared as `(milliseconds, ordinal)` pairs, never as strings. `"10-0" < "9-0"` is
  true lexicographically, so a string comparison produces a checkpoint that walks backwards once a
  stream is ten milliseconds old.

### Why a consumer offset is not the checkpointing Phase 1 rejected

Open Issue 018 §13.1 removed snapshots and checkpointing, and Task 6.2 asks for "checkpoint the
consumer offset". Both are right, because they are different things:

| | What it is | Is it loaded? |
|---|---|---|
| **State checkpoint** — rejected | a saved copy of derived state, so a process can skip deriving it | yes, and that is the problem: you believe a file instead of the log |
| **Consumer offset** — this | a note of how far output has been produced | never. The state machine still starts empty and derives everything from records |

Lose `_checkpoint.json` and the archiver replays from `0-0`, which is what a fresh archive does.

## 3. Bar aggregation is imported, not reimplemented

6.2's Boundaries: "bar aggregation is shared with the live chart pipeline — build it once."
`ArchiveState` **composes** a `services.fanout.state.MarketState`, which already holds the books,
the tape and the bars, all derived from `OrderAccepted` / `Fill` / `OrderCancelled`.

Composed rather than moved to a shared package: fan-out is finished and tested, and editing it to
satisfy a naming preference here buys nothing. Revisit at a third consumer.

The payoff is in the test. Criterion 2 — "bars reconcile exactly against the trade stream they were
built from" — recomputes OHLCV from the **archived trades table** and compares it to the **archived
bars table**. Had this module aggregated its own way, that test would only prove two copies of one
bug agree.

## 4. Snapshots

Sampled at 1 Hz on stream time (Open Issue 011 §11b): the snapshot for second *N* is taken the
moment the first record of *N+1* arrives, which is the book as it stood at the end of *N*. A
wall-clock sampler would put a different number of snapshots in a replay than in the live run, and
an archive that cannot be reproduced is not evidence of anything.

Stored **long** — one row per price level, `(symbol_id, second_ns, side, level, price_ticks, qty)`
— rather than as a nested list. The backtester reconstructs a book at a timestamp (Open Issue 011
§11c), which a long table answers with a predicate on two columns; nested types are also where
Parquet readers disagree. Dictionary and run-length encoding remove almost all of the repetition
that costs.

Two behaviours follow from the design and are deliberate:

- **A unit with no records produces no file.** An hour with the stack switched off is an hour with
  no files, not 3,600 identical snapshots per symbol asserting a market nobody was running held its
  shape.
- **An empty book produces no rows** for that second. A side offering nothing is not a quote of
  zero — the same reasoning as `Book.best` returning `None`.

## 5. Layout

    <root>/trades/symbol=QAA/date=2026-09-08/143000.parquet
    <root>/bars/symbol=QAA/date=2026-09-08/143000.parquet      # bucket_seconds is a column
    <root>/snapshots/symbol=QAA/date=2026-09-08/143000.parquet
    <root>/_checkpoint.json

Hive layout, so `pyarrow.dataset` discovers `symbol` and `date` without being told. The leaf is the
flush unit's start, in UTC — a partition boundary that moved with the reader's timezone would put
one second in two different days depending on who opened the file.

**The path is a pure function of the data.** That is the second line of defence behind the
checkpoint: if the process dies after writing a file but before recording the checkpoint, the
restart re-derives that unit and writes the same rows to the same path. The window that would
otherwise duplicate rows overwrites identical ones instead. Every file is written to a temp name
and `os.replace`d into place, so a reader never opens a truncated footer.

`<root>` is `QA_ARCHIVE_DIR`, defaulting to `data/archive`. **Infrastructure, not configuration** —
it is a path on a machine, differs between a laptop, CI and the container, and a path inside
`config_hash` would change the hash when nothing about the configuration had (the 2026-08-31
configuration/infrastructure split).

## 6. Measured

Against the live compose stack — ten symbols, the bot market, 6.28 hours of history replayed from
`0-0` on 2026-09-08:

| | |
|---|---|
| Records applied | 702,432 |
| Files written | 4,666 |
| Total size | 14.5 MB — **55 MB/day** |
| trades / bars / snapshots | 5.03 MB (128,568 rows) / 5.31 MB (68,752 rows) / 4.16 MB (236,324 rows) |

Two things in that table are worth reading twice.

**55 MB/day against an estimate of ~345.** The estimate (Open Issue 018 §11.1) is uncompressed, at
full ten-level depth. This writes zstd over a long table, where dictionary and run-length encoding
remove nearly all the repeated `symbol_id` and `second_ns`; and the bot market quotes two or three
levels a side, not ten. A busier book raises it. Under budget rather than over is the good
direction, but the number is what it is, not what the estimate said.

**Bars occupy more bytes than snapshots while holding a third of the rows.** That is per-file
overhead, not data: 1,553 bar files with a median size of 3.4 kB, most of which is Parquet's footer
and schema. One file per flush unit per symbol per dataset is the price of the low-water invariant —
a longer flush unit means fewer, fatter files and a proportionally longer checkpoint lag. If the
file count ever becomes the problem, that trade is the dial, and it is one line.

The archiver also survived a Redis restart during this run — `tests/gateway/test_halt.py` stops and
starts Redis, the loop logged `archiver step failed` and carried on from its position. That is the
`except` in `runner.run`, and it is the exact defect `HANDOFF.md` §3a reports in the matcher, which
has no such guard and stays dead.

## 7. Not here, on purpose

- **No query API over the archive** — 6.2's Boundaries forbid it; the backtester reads the tree.
- **No bulk history in PostgreSQL** — that is the derived read model (Open Issue 004), not a
  warehouse.
- **No float column anywhere.** Ticks are int64 on the wire and int64 on disk; `pyarrow` would
  happily infer a double from a Python int, and a float price in 7.1's input is a decimal error
  nothing downstream can detect. A test asserts it.
- **No manifest or index.** One more file to fall out of step with the tree.
