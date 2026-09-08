"""Task 6.2's success criteria, proved over real Parquet files on disk.

| Criterion | Where |
|---|---|
| 1. A session produces readable Parquet files | `test_a_session_produces_readable_parquet` and the partition tests |
| 2. Bars reconcile exactly against the trade stream they were built from | `test_bars_reconcile_against_the_archived_trades` |
| 3. Restarting resumes with neither a gap nor a duplicate | `test_resume.py` |
| 4. Volume is in line with the ~345 MB/day estimate for ten symbols | `test_daily_volume_is_in_line_with_the_estimate` |

Criterion 2 is the one that needs care. Recomputing bars with the same code that wrote them would
prove only that a function is deterministic. So the reconciliation reads the **archived trades
table** and rebuilds OHLCV from it by hand — a few lines of `min`, `max`, first and last — and
compares that against the **archived bars table**. The two agree only if the bar aggregation
imported from `services/fanout/bars.py` really did fold in exactly the trades that were archived.
"""

from __future__ import annotations

import pyarrow.dataset as ds
import pytest

from services.archiver import schemas
from services.archiver.state import ArchiveState, parse_stream_id
from services.archiver.writer import ArchiveWriter
from services.fanout.bars import NS_PER_SECOND
from tests.archiver.conftest import ORIGIN_NS, session, stream_ids

FLUSH = 60


def archive(records, root, settings, *, flush_seconds: int = FLUSH) -> ArchiveWriter:
    """Run records through the state machine and write every unit, including the last."""
    state = ArchiveState(
        bar_widths=settings.bar_bucket_seconds,
        book_depth=settings.book_depth,
        flush_seconds=flush_seconds,
    )
    writer = ArchiveWriter(root, symbols=settings.symbols)
    for stream_id, record in stream_ids(records):
        state.apply(record, stream_id=stream_id)
        for unit in state.drain_ready():
            writer.write_unit(unit)
            writer.write_checkpoint(state.checkpoint())
    state.finish()
    for unit in state.drain_ready():
        writer.write_unit(unit)
        writer.write_checkpoint(state.checkpoint())
    return writer


def read(root, dataset):
    return ds.dataset(root / dataset, format="parquet", partitioning="hive").to_table()


# --- criterion 1: readable Parquet -------------------------------------------------------------


def test_a_session_produces_readable_parquet(archive_root, settings):
    writer = archive(session(seconds=130, symbols=(1, 2)), archive_root, settings)
    assert writer.files_written > 0

    for dataset in schemas.BY_DATASET:
        table = read(archive_root, dataset)
        assert table.num_rows > 0, dataset
        # Every declared column survives the round trip with its declared type. The partition
        # columns `symbol` and `date` are added by the hive layout on read, so the schema is a
        # superset rather than an equality.
        for field in schemas.BY_DATASET[dataset]:
            assert table.schema.field(field.name).type == field.type, (dataset, field.name)


def test_no_price_or_quantity_is_stored_as_a_float(archive_root, settings):
    """Ticks are int64 on the wire (Open Issue 001) and must be int64 on disk.

    pyarrow would happily infer a double from a Python int, and a float price in the backtester's
    input is a decimal error that nothing downstream can detect.
    """
    archive(session(seconds=130, symbols=(1,)), archive_root, settings)
    for dataset in schemas.BY_DATASET:
        table = read(archive_root, dataset)
        for name in table.schema.names:
            if name.endswith(("_ticks", "qty", "volume", "_ns")):
                assert str(table.schema.field(name).type).startswith("int"), (dataset, name)


def test_files_are_partitioned_by_symbol_and_day(archive_root, settings):
    archive(session(seconds=130, symbols=(1, 2)), archive_root, settings)
    for dataset in schemas.BY_DATASET:
        parts = sorted(p.name for p in (archive_root / dataset).iterdir())
        assert parts == ["symbol=QAA", "symbol=QAB"], dataset
        for part in parts:
            days = sorted(p.name for p in (archive_root / dataset / part).iterdir())
            assert days == ["date=2023-11-14"], (dataset, part)
            files = sorted(
                p.name for p in (archive_root / dataset / part / days[0]).iterdir()
            )
            # One file per flush unit, named by the unit's start in stream time. 130 seconds of
            # session spans three.
            assert files == ["221400.parquet", "221500.parquet", "221600.parquet"], dataset


def test_no_temporary_file_is_left_behind(archive_root, settings):
    """Every write is a temp file plus `os.replace`, so a reader never sees a truncated footer."""
    archive(session(seconds=130, symbols=(1,)), archive_root, settings)
    leftovers = [p for p in archive_root.rglob("*") if p.name.startswith(".")]
    assert leftovers == []


def test_a_symbol_missing_from_the_configuration_is_filed_visibly(archive_root, settings):
    """The stream outlives a configuration change, so an unknown symbol_id is a real case.

    It must not stop the archive, and it must not be filed under a name that looks real.
    """
    writer = ArchiveWriter(archive_root, symbols=settings.symbols)
    assert writer.symbol_name(1) == "QAA"
    assert writer.symbol_name(9_999) == "id=9999"


# --- criterion 2: bars reconcile against the trades --------------------------------------------


def recompute_bars(trades, width_seconds: int) -> dict:
    """OHLCV per (symbol, bucket) from the archived trades, computed by hand.

    Trades are ordered by `(timestamp_ns, seq)` and not by row order: the open and the close are
    the *first* and *last* print in the bucket, so an unstable order would silently swap them on
    any bucket holding more than one trade at the same nanosecond.
    """
    width_ns = width_seconds * NS_PER_SECOND
    rows = sorted(
        zip(
            trades["symbol_id"].to_pylist(),
            trades["timestamp_ns"].to_pylist(),
            trades["price_ticks"].to_pylist(),
            trades["qty"].to_pylist(),
            trades["seq"].to_pylist(),
        ),
        key=lambda r: (r[1], parse_stream_id(r[4])),
    )
    out: dict = {}
    for symbol_id, timestamp_ns, price, qty, _seq in rows:
        key = (symbol_id, (timestamp_ns // width_ns) * width_ns)
        bar = out.get(key)
        if bar is None:
            out[key] = {
                "open_ticks": price, "high_ticks": price,
                "low_ticks": price, "close_ticks": price, "volume": qty,
            }
        else:
            bar["high_ticks"] = max(bar["high_ticks"], price)
            bar["low_ticks"] = min(bar["low_ticks"], price)
            bar["close_ticks"] = price
            bar["volume"] += qty
    return out


@pytest.mark.parametrize("width", [1, 60])
def test_bars_reconcile_against_the_archived_trades(archive_root, settings, width):
    archive(session(seconds=130, symbols=(1, 2)), archive_root, settings)
    trades = read(archive_root, "trades")
    bars = read(archive_root, "bars")

    expected = recompute_bars(trades, width)
    actual = {}
    for row in bars.to_pylist():
        if row["bucket_seconds"] != width:
            continue
        actual[(row["symbol_id"], row["bar_open_ns"])] = {
            k: row[k] for k in
            ("open_ticks", "high_ticks", "low_ticks", "close_ticks", "volume")
        }

    assert actual, f"no {width}s bars were archived"
    assert actual == expected


def test_every_archived_trade_falls_inside_the_bar_that_claims_it(archive_root, settings):
    """A bar filed under the wrong flush unit would still reconcile within that unit's rows.

    So the file's own partition is checked against the data in it: a bar's `bar_open_ns` must lie
    inside the unit whose file it was written to.
    """
    archive(session(seconds=130, symbols=(1,)), archive_root, settings)
    for path in (archive_root / "bars").rglob("*.parquet"):
        unit_label = path.stem                      # HHMMSS in UTC
        table = ds.dataset(path, format="parquet").to_table()
        import datetime as dt
        for bar_open_ns in table.column("bar_open_ns").to_pylist():
            moment = dt.datetime.fromtimestamp(bar_open_ns / NS_PER_SECOND, dt.UTC)
            unit_start = moment.replace(second=(moment.second // FLUSH) * FLUSH)
            assert unit_start.strftime("%H%M%S") == unit_label, (path, bar_open_ns)


# --- criterion 4: volume ----------------------------------------------------------------------


def test_daily_volume_is_in_line_with_the_estimate(archive_root, settings, capsys):
    """Volume is in line with — in fact comfortably under — the ~345 MB/day estimate.

    Open Issue 018 §11.1 sizes ten symbols at 1 Hz as ~345 MB/day, from ~400 B per snapshot. The
    real figure is well below that, and the assertion is written around the measurement rather than
    around the estimate:

        this test, 120 s synthetic, 10 symbols   →  113 MB/day
        live compose stack, 6.28 h of bot market →   55 MB/day  (14.5 MB total)

    Both are under the estimate for two reasons worth knowing: the estimate is for *uncompressed*
    snapshots at full ten-level depth, and this writes zstd over a long table where dictionary and
    run-length encoding remove nearly all the repeated `symbol_id` and `second_ns`; and the bot
    market quotes two or three levels a side, not ten. A busier book moves the figure up, which is
    what the upper bound is for.

    So the band is asymmetric on purpose. The two failures that matter are an archive an order of
    magnitude *larger* than planned — the disk filling during a Goal 5 benchmark run — and one an
    order of magnitude *smaller*, which means rows are being dropped. Neither bound is a claim that
    345 is the expected value.
    """
    seconds = 120
    symbols = tuple(symbol.symbol_id for symbol in settings.symbols)
    assert len(symbols) == 10, "the estimate is for ten symbols"

    writer = archive(session(seconds=seconds, symbols=symbols), archive_root, settings)
    measured = sum(p.stat().st_size for p in archive_root.rglob("*.parquet"))
    per_day = measured * (86_400 / seconds)
    mb_per_day = per_day / 1_000_000

    with capsys.disabled():
        print(
            f"\narchive volume: {measured / 1000:.1f} kB over {seconds}s of ten symbols "
            f"→ {mb_per_day:.0f} MB/day (estimate ~345, {writer.rows_written} rows)"
        )
    # Lower bound set below the 55 MB/day the live stack produces, so a real thin-book session
    # does not fail a test written against a synthetic one; upper bound at twice the estimate.
    assert 20 < mb_per_day < 690, f"{mb_per_day:.0f} MB/day is out of band against ~345"
