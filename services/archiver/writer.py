"""Parquet files on disk, and the checkpoint that says which ones exist.

The only module here that touches the filesystem. It knows two things: where a row belongs, and
how to put it there without leaving a half-written file behind.

## The path is a pure function of the data

    <root>/<dataset>/symbol=QAA/date=2026-09-08/<HHMM>.parquet

Partitioned by **symbol and day**, which the task asks for, in Hive layout so `pyarrow.dataset`
discovers the partition columns without being told. The leaf is the flush unit's start in stream
time.

Nothing in that path depends on when the file was written, only on what is in it. That is a
second line of defence behind the low-water checkpoint: if the process dies after writing a file
but before recording the checkpoint, the restart re-derives that unit from the stream and writes
the same rows to the same path. The window that would otherwise duplicate rows instead overwrites
identical ones.

Symbols are named, not numbered, because a human reads these directory names — and the mapping
comes from the configuration every process shares, so it cannot drift from the wire.

## Writes are atomic

Each file is written to `.<name>.tmp` in its final directory and then `os.replace`d into place.
`os.replace` is atomic on POSIX within a filesystem, so a reader — 7.1's backtester, or a second
run of this process — never opens a truncated Parquet footer. Same for the checkpoint, which is
the one file whose corruption would actually cost something: a lost checkpoint means re-deriving
history, a *wrong* one means a gap.

The checkpoint is JSON rather than Parquet: it is one small object, it is read before pyarrow is
needed, and it should be readable with `cat` when something has gone wrong at 2 a.m.

## Not in Phase 1

No manifest, no index, no query API over these files — 6.2's Boundaries forbid the last of those
outright, and the backtester reads the directory tree directly. Bulk history does not go into
PostgreSQL either: that is the derived read model (Open Issue 004), not a data warehouse.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from config.settings import Symbol
from services.archiver import schemas
from services.archiver.state import Unit
from services.fanout.bars import NS_PER_SECOND

LOGGER_NAME = "quant_arena.archiver"

#: Where the archive lives. **Infrastructure, not configuration** — it is a path on this machine,
#: it differs between a laptop, CI and the container, and it is exactly the class of value the
#: 2026-08-31 configuration/infrastructure split keeps out of the hashed TOML. A path inside the
#: hash would change `config_hash` when nothing about the configuration had.
ARCHIVE_DIR_ENV = "QA_ARCHIVE_DIR"
DEFAULT_ARCHIVE_DIR = "data/archive"

CHECKPOINT_FILENAME = "_checkpoint.json"

#: Zstandard: better ratio than snappy at a cost that does not matter for a background writer,
#: and readable by every current pyarrow. The volume estimate in Open Issue 018 §11.1
#: (~345 MB/day at ten symbols) is what this is measured against.
COMPRESSION = "zstd"


def archive_root(explicit: str | os.PathLike[str] | None = None) -> Path:
    return Path(explicit or os.environ.get(ARCHIVE_DIR_ENV) or DEFAULT_ARCHIVE_DIR)


def _unit_label(start_ns: int) -> tuple[str, str]:
    """`(date, leaf)` for a unit start, both in UTC.

    UTC and not local time: a partition boundary that moves with the reader's timezone would put
    the same second in two different days depending on who opened the file.
    """
    moment = dt.datetime.fromtimestamp(start_ns / NS_PER_SECOND, dt.UTC)
    return moment.strftime("%Y-%m-%d"), moment.strftime("%H%M%S")


class ArchiveWriter:
    """Turns sealed units into Parquet files, and records how far that has got."""

    def __init__(
        self,
        root: str | os.PathLike[str] | None = None,
        *,
        symbols: tuple[Symbol, ...] = (),
        compression: str = COMPRESSION,
    ) -> None:
        self.root = archive_root(root)
        self.compression = compression
        self._names = {symbol.symbol_id: symbol.name for symbol in symbols}
        self.files_written = 0
        self.rows_written = 0
        self.bytes_written = 0

    # -- naming ---------------------------------------------------------------------------------

    def symbol_name(self, symbol_id: int) -> str:
        """The configured name, or `id=<n>` for a symbol this process has never heard of.

        A symbol on the stream that is absent from the configuration is a real possibility — the
        stream outlives a configuration change — and it must not stop the archive. Filing it under
        a visibly odd name says so, where falling back to the number would look like a name.
        """
        return self._names.get(symbol_id) or f"id={symbol_id}"

    def path_for(self, dataset: str, symbol_id: int, start_ns: int) -> Path:
        date, leaf = _unit_label(start_ns)
        return (
            self.root
            / dataset
            / f"symbol={self.symbol_name(symbol_id)}"
            / f"date={date}"
            / f"{leaf}.parquet"
        )

    # -- writing --------------------------------------------------------------------------------

    def write_unit(self, unit: Unit) -> list[Path]:
        """Write one sealed unit. Returns the paths created, which may be empty."""
        written: list[Path] = []
        for dataset, schema in schemas.BY_DATASET.items():
            cols = unit.rows[dataset]
            if not cols["symbol_id"]:
                continue
            table = pa.Table.from_pydict(cols, schema=schema)
            # Split by symbol here rather than leaning on `write_to_dataset`: the partitioning is
            # two levels of a path this class already computes, and doing it by hand keeps the
            # atomic-replace guarantee, which the dataset writer does not offer.
            for symbol_id in sorted(set(cols["symbol_id"])):
                mask = pc.equal(table.column("symbol_id"), symbol_id)
                part = table.filter(mask)
                written.append(
                    self._write_table(part, self.path_for(dataset, symbol_id, unit.start_ns))
                )
        return written

    def _write_table(self, table: pa.Table, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.tmp")
        pq.write_table(table, tmp, compression=self.compression)
        os.replace(tmp, path)
        self.files_written += 1
        self.rows_written += table.num_rows
        self.bytes_written += path.stat().st_size
        return path

    # -- the checkpoint -------------------------------------------------------------------------

    @property
    def checkpoint_path(self) -> Path:
        return self.root / CHECKPOINT_FILENAME

    def read_checkpoint(self) -> str | None:
        """The recorded resume point, or None for a fresh archive.

        An unreadable checkpoint is treated as absent and said so loudly. Replaying the retained
        stream from the start is slow and correct; guessing a resume point is fast and wrong, and
        the wrong one is a gap nothing would ever report.
        """
        try:
            raw = self.checkpoint_path.read_text()
        except FileNotFoundError:
            return None
        except OSError:
            logging.getLogger(LOGGER_NAME).exception("checkpoint unreadable; replaying from 0-0")
            return None
        try:
            value = json.loads(raw)["stream_id"]
        except (ValueError, KeyError, TypeError):
            logging.getLogger(LOGGER_NAME).error(
                "checkpoint malformed (%r); replaying from 0-0", raw[:200]
            )
            return None
        return str(value)

    def write_checkpoint(self, stream_id: str, **extra: object) -> None:
        """Record the resume point. Called only *after* the files it describes are on disk."""
        self.root.mkdir(parents=True, exist_ok=True)
        payload = {"stream_id": stream_id, **extra}
        tmp = self.checkpoint_path.with_name(f".{CHECKPOINT_FILENAME}.tmp")
        tmp.write_text(json.dumps(payload, separators=(",", ":"), sort_keys=True) + "\n")
        os.replace(tmp, self.checkpoint_path)

    def stats(self) -> dict:
        return {
            "root": str(self.root),
            "files_written": self.files_written,
            "rows_written": self.rows_written,
            "bytes_written": self.bytes_written,
        }
