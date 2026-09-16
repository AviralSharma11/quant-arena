"""What both matcher runners share for Open Issue 020: resume points, anchor counting, trimming.

The matcher's checkpoint is the engine snapshot (`snapshot.py`) plus **two** positions:

- `inbound` — the last inbound record whose outputs are all durably on the outbound stream;
- `outbound` — the id of the last of those outputs.

Restart restores the snapshot, then counts anchors on the outbound stream *after* `outbound`.
Exactly one anchor is emitted per inbound record (`runner.is_anchor`), so that count is how many
inbound records *after* `inbound` were already answered — they are replayed into the engine
silently, and matching resumes at the first one nobody answered. This is the genesis-replay logic
the matcher always had, started from the checkpoint instead of from `0-0`.

The matcher is also the process that trims. It is the one place already reading both streams
continuously, and trimming is a pure function of checkpoints (`services.checkpoint.trim`), so it
needs no coordination with anyone. The gateway stays the single *producer* of inbound records;
`XTRIM` appends nothing.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

from redis.asyncio import Redis

from services import checkpoint
from services.gateway.streams import HaltState

logger = logging.getLogger(__name__)

PROCESS = "matcher"


async def count_anchors_after(
    read: Callable[[str, str], Awaitable[list]],
    stream: str,
    after_id: str,
    is_anchor: Callable[[object], bool],
) -> tuple[int, str]:
    """Anchors on `stream` strictly after `after_id`, and the last id seen (or `after_id`)."""
    last_id, anchors = after_id, 0
    while True:
        batch = await read(stream, last_id)
        if not batch:
            return anchors, last_id
        for item in batch:
            anchors += is_anchor(item.record)
            last_id = item.stream_id


class CheckpointSchedule:
    """Decides when to write a checkpoint and when to trim, off a monotonic clock.

    The clock is read here, in the runner's I/O layer — never in the engine, which stays
    deterministic.
    """

    def __init__(self, *, interval_ms: int, trim_interval_ms: int) -> None:
        self.interval = interval_ms / 1000
        self.trim_interval = trim_interval_ms / 1000
        self._last_checkpoint = time.monotonic()
        self._last_trim = time.monotonic()

    def checkpoint_due(self) -> bool:
        return time.monotonic() - self._last_checkpoint >= self.interval

    def checkpoint_written(self) -> None:
        self._last_checkpoint = time.monotonic()

    def trim_due(self) -> bool:
        return time.monotonic() - self._last_trim >= self.trim_interval

    def trimmed(self) -> None:
        self._last_trim = time.monotonic()


async def trim_streams(redis: Redis, settings, halt: HaltState) -> None:
    """Trim both streams below their readers' oldest checkpoints. Never while halted."""
    if halt.halted:
        return
    for stream, readers in (
        (settings.stream_inbound, settings.checkpoint_inbound_readers),
        (settings.stream_outbound, settings.checkpoint_outbound_readers),
    ):
        try:
            minid = await checkpoint.trim(redis, stream, list(readers))
            if minid is not None:
                logger.debug("trimmed %s below %s", stream, minid)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — a failed trim costs memory, never correctness
            logger.exception("trimming %s failed; will retry", stream)
