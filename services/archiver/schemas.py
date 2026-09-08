"""The Parquet schemas, and the only place that knows what a column is called.

Three datasets, one schema each. Every price and quantity is an **int64 tick count** — the same
rule the wire has (Open Issue 001: the engine is money-blind and nothing in this system carries
a float price). A Parquet file with a `double` price column would be a decimal error waiting for
whoever reads it in 7.1, and `pyarrow` would happily write one.

## Why the snapshot table is long rather than nested

A book snapshot is naturally `{bids: [[price, qty], ...], asks: [...]}`, and Parquet can store
that as a list of structs. It is stored as one row per level instead:

    symbol_id | second_ns | side | level | price_ticks | qty

- The backtester reconstructs a book at a timestamp (Open Issue 011 §11c). A long table answers
  "the book for symbol 3 at second N" with a predicate pushdown on two columns; a nested column
  has to be read and unpacked whole.
- Nested types are where Parquet readers disagree. This table is readable by anything.
- `level` is kept explicitly rather than left to row order, because row order within a Parquet
  row group is not something a reader is obliged to preserve.

The cost is repetition of `symbol_id` and `second_ns`, which dictionary encoding and run-length
encoding both remove almost entirely — the reason the 1 Hz volume estimate (~345 MB/day for ten
symbols, Open Issue 018 §11.1) survives the choice.
"""

from __future__ import annotations

import pyarrow as pa

#: One print, as the tape carried it. `seq` is the Redis stream ID of the `Fill` — a string,
#: because that is what the ID is (Open Issue 003: the stream ID *is* the sequence number, and
#: it is `<milliseconds>-<counter>`, not an integer).
TRADES = pa.schema([
    pa.field("symbol_id", pa.int32(), nullable=False),
    pa.field("price_ticks", pa.int64(), nullable=False),
    pa.field("qty", pa.int64(), nullable=False),
    pa.field("aggressor_side", pa.int8(), nullable=False),
    pa.field("timestamp_ns", pa.int64(), nullable=False),
    pa.field("seq", pa.string(), nullable=False),
])

#: OHLCV. Field names are the frozen contract's (`rest_and_ws.md` §3.3) so that a bar read off
#: disk and a bar read off the WebSocket have the same shape — one vocabulary, not two.
#: `bucket_seconds` is a column rather than a partition: both widths are usually wanted together
#: and there are only two of them.
BARS = pa.schema([
    pa.field("symbol_id", pa.int32(), nullable=False),
    pa.field("bucket_seconds", pa.int32(), nullable=False),
    pa.field("bar_open_ns", pa.int64(), nullable=False),
    pa.field("open_ticks", pa.int64(), nullable=False),
    pa.field("high_ticks", pa.int64(), nullable=False),
    pa.field("low_ticks", pa.int64(), nullable=False),
    pa.field("close_ticks", pa.int64(), nullable=False),
    pa.field("volume", pa.int64(), nullable=False),
])

#: L2, one row per price level. `second_ns` is the **start** of the sampled second, and the book
#: is as it stood at that second's *end* — after the last record inside it. Naming the bucket by
#: its start is the same convention `bar_open_ns` uses, so the two tables join without an
#: off-by-one. `level` is 0 for the most aggressive price on that side,
#: matching `Book.levels`, so a reader takes the top of book without knowing which side it asked
#: for.
SNAPSHOTS = pa.schema([
    pa.field("symbol_id", pa.int32(), nullable=False),
    pa.field("second_ns", pa.int64(), nullable=False),
    pa.field("side", pa.int8(), nullable=False),
    pa.field("level", pa.int16(), nullable=False),
    pa.field("price_ticks", pa.int64(), nullable=False),
    pa.field("qty", pa.int64(), nullable=False),
])

#: Dataset name → schema. The writer iterates this, so adding a dataset is one entry plus the
#: rows to fill it.
BY_DATASET: dict[str, pa.Schema] = {
    "trades": TRADES,
    "bars": BARS,
    "snapshots": SNAPSHOTS,
}


def empty(dataset: str) -> dict[str, list]:
    """A column-oriented, empty row set for one dataset.

    Rows are accumulated column-wise because that is what `pa.Table.from_pydict` wants; building
    a list of dicts and transposing it later would allocate twice for no gain.
    """
    return {field.name: [] for field in BY_DATASET[dataset]}
