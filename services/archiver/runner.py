"""The archiver process — outbound stream in, Parquet out.

All the I/O. `state.py` decides what a row is; this decides when to read and when to write.

## Recovery resumes; it does not replay from genesis

Every other consumer in this system rebuilds from `0-0` on every start, because no snapshots and
no checkpoints is a settled Phase 1 decision (Open Issue 018 §13.1) and because each of them
holds *state* — a ledger, a book, a risk position — that a partial replay would leave wrong.

The archiver is the one process where that reasoning does not apply, and Task 6.2 asks for the
opposite in as many words: "checkpoint the consumer offset so restarts resume cleanly." The
distinction is worth stating plainly, because the two look alike and are not:

- A **state checkpoint** — what §13.1 rejects — lets a process skip deriving state it needs. Load
  it and you have believed a file instead of the log.
- A **consumer offset** — what this writes — records how far output has been produced. Nothing is
  ever *loaded* from it; the state machine here still starts empty and derives everything from
  the records it reads. Lose the checkpoint and you re-derive from `0-0`, which is exactly what a
  fresh archive does.

So the log remains the only source of truth, and the file is a bookmark, not a shortcut.

The safety of resuming rests entirely on the low-water mark in `state.py`: the checkpoint names
the oldest record not yet written, so re-reading from it re-derives the in-flight units and
duplicates nothing. `ArchiveState` starts with empty books, which means the first unit or two
after a resume can be missing orders that rested before the resume point — the same honest
incompleteness a trimmed stream gives fan-out, and the reason `Book.fill` is silent on an unknown
ID.

## The write cadence

A unit is written when the stream proves it is complete, not on a timer. That keeps the output a
pure function of the input: two runs over the same stream produce the same files.
"""

from __future__ import annotations

import asyncio
import json
import logging

from redis.asyncio import Redis

from config.settings import Settings
from services.archiver.state import DEFAULT_FLUSH_SECONDS, ArchiveState
from services.archiver.writer import ArchiveWriter
from services.gateway.streams import read_records

LOGGER_NAME = "quant_arena.archiver"


class Archiver:
    """Tails the outbound stream and keeps the Parquet archive current."""

    def __init__(
        self,
        redis: Redis,
        settings: Settings,
        writer: ArchiveWriter | None = None,
        *,
        state: ArchiveState | None = None,
        flush_seconds: int = DEFAULT_FLUSH_SECONDS,
        batch_size: int = 100,
        poll_block_ms: int = 200,
    ) -> None:
        self.redis = redis
        self.settings = settings
        self.writer = writer or ArchiveWriter(symbols=settings.symbols)
        self.state = state or ArchiveState(
            bar_widths=settings.bar_bucket_seconds,
            book_depth=settings.book_depth,
            flush_seconds=flush_seconds,
        )
        # 100 per read: measured at 8.32 µs per record against 159.11 µs at a count of one
        # (benchmarks/results/2.1-stream-durability.md). Every consumer in the system uses it.
        self.batch_size = batch_size
        self.poll_block_ms = poll_block_ms
        self.position: str = "0-0"
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    # -- lifecycle ------------------------------------------------------------------------------

    def resume(self) -> str:
        """Adopt the recorded checkpoint as the read position. Returns where reading starts."""
        recorded = self.writer.read_checkpoint()
        self.position = recorded or "0-0"
        return self.position

    async def step(self) -> int:
        """One read-apply-write cycle. Returns how many records were handled.

        The three phases are ordered so that the checkpoint can never claim more than is on disk:
        apply, then write the units that sealed, then record the position. A crash between the
        second and third re-derives those units on the next run and writes them to the same paths.
        """
        batch = await read_records(
            self.redis,
            self.settings.stream_outbound,
            last_id=self.position,
            count=self.batch_size,
            block_ms=self.poll_block_ms,
        )
        for item in batch:
            self.state.apply(item.record, stream_id=item.stream_id)
            self.position = item.stream_id

        self._write_ready()
        return len(batch)

    def _write_ready(self) -> None:
        units = self.state.drain_ready()
        if not units:
            return
        for unit in units:
            self.writer.write_unit(unit)
        # Only now, with the files in place, may the bookmark move.
        self.writer.write_checkpoint(
            self.state.checkpoint(),
            last_stream_id=self.state.last_stream_id,
            records_applied=self.state.records_applied,
        )

    def finish(self) -> None:
        """Seal and write the final partial unit. For a clean shutdown or the end of a replay."""
        self.state.finish()
        self._write_ready()

    async def run(self) -> None:
        while not self._stop.is_set():
            try:
                if not await self.step():
                    await asyncio.sleep(0.01)
            except asyncio.CancelledError:
                break
            except Exception:  # noqa: BLE001 — a bad record must not take the archiver down
                # The same reasoning fan-out uses, and the defect `HANDOFF.md` §3a reports in the
                # matcher: a long-running loop without this dies silently on the first Redis
                # restart and stays dead while its container reports healthy. Nothing is lost by
                # waiting — the position is `self.position` and the checkpoint is on disk.
                logging.getLogger(LOGGER_NAME).exception("archiver step failed")
                await asyncio.sleep(0.1)

    def start(self) -> None:
        if self._task is None:
            self._stop.clear()
            self._task = asyncio.create_task(self.run(), name="archiver")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    # -- introspection --------------------------------------------------------------------------

    def log_snapshot(self) -> None:
        logging.getLogger(LOGGER_NAME).info(
            json.dumps(
                {
                    "event": "archiver_snapshot",
                    "position": self.position,
                    **self.state.snapshot(),
                    **self.writer.stats(),
                },
                separators=(",", ":"),
            )
        )
