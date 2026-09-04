"""The fan-out process — outbound stream in, market state out.

All I/O lives here; `MarketState` is pure. The same split the matcher and the ledger use, and
for the same reason: the state machine stays replayable and directly testable, and nothing can
smuggle a clock reading into a derivation that has to reproduce identically.

## Recovery is a full replay, and it re-emits nothing

No snapshots, no checkpoints in Phase 1 (Open Issue 018 §13.1), so a restarted fan-out rebuilds
by replaying the retained stream from `0-0`.

Unlike the matcher, this needs no anchor counting and no care at all. The matcher *appends* to a
stream, so a naive replay would duplicate fills that moved real positions; fan-out only ever
derives a view, and rebuilding a view from the same inputs produces the same view. That
asymmetry is why `services/matcher/runner.py` is long and this file is short.

The trimmed stream is the real limit: at two million entries a replay legitimately begins
mid-history, so the book may be missing orders accepted before the window. `Book.fill` is silent
on an unknown id for exactly that reason.
"""

from __future__ import annotations

import asyncio
import json
import logging

from redis.asyncio import Redis

from config.settings import Settings
from services.fanout.state import MarketState
from services.gateway.streams import read_records

LOGGER_NAME = "quant_arena.fanout"


class FanOut:
    """Tails the outbound stream and keeps the market state current."""

    def __init__(
        self,
        redis: Redis,
        settings: Settings,
        *,
        state: MarketState | None = None,
        batch_size: int = 100,
        poll_block_ms: int = 200,
    ) -> None:
        self.redis = redis
        self.settings = settings
        self.state = state or MarketState(bar_widths=settings.bar_bucket_seconds)
        # 100 per read: measured at 8.32 µs per record against 159.11 µs at a count of one
        # (benchmarks/results/2.1-stream-durability.md). Every consumer in the system uses it.
        self.batch_size = batch_size
        self.poll_block_ms = poll_block_ms
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    async def recover(self) -> int:
        """Rebuild from genesis. Returns the number of records replayed."""
        last_id, replayed = "0-0", 0
        while True:
            batch = await read_records(
                self.redis,
                self.settings.stream_outbound,
                last_id=last_id,
                count=self.batch_size,
                block_ms=20,
            )
            if not batch:
                break
            for item in batch:
                self.state.apply(item.record, stream_id=item.stream_id)
                last_id = item.stream_id
            replayed += len(batch)
        return replayed

    async def step(self) -> int:
        """One read-and-apply cycle. Returns how many records were handled."""
        batch = await read_records(
            self.redis,
            self.settings.stream_outbound,
            last_id=self.state.last_seq,
            count=self.batch_size,
            block_ms=self.poll_block_ms,
        )
        for item in batch:
            self.state.apply(item.record, stream_id=item.stream_id)
        return len(batch)

    async def run(self) -> None:
        while not self._stop.is_set():
            try:
                if not await self.step():
                    await asyncio.sleep(0.01)
            except asyncio.CancelledError:
                break
            except Exception:  # noqa: BLE001 — a bad record must not take the feed down
                # Redis going away is the common case, and the market data is derived, so
                # there is nothing to lose by waiting: the position is `state.last_seq` and
                # whatever was missed is read when the store returns.
                logging.getLogger(LOGGER_NAME).exception("fan-out step failed")
                await asyncio.sleep(0.1)

    def start(self) -> None:
        if self._task is None:
            self._stop.clear()
            self._task = asyncio.create_task(self.run(), name="fanout")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    def log_snapshot(self) -> None:
        logging.getLogger(LOGGER_NAME).info(
            json.dumps(
                {"event": "fanout_snapshot", **self.state.snapshot()},
                separators=(",", ":"),
            )
        )
