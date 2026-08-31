"""The durable ordered log.

This is the spine. One producer, one stream, and ordering that is not computed by anything —
it emerges from there being exactly one writer (Open Issue 003).

**The Redis stream ID is the sequence number.** No counter is kept alongside it. A producer
cannot know its own ID before `XADD` returns, so records are written with `SEQ_UNASSIGNED` and
stamped by the reader via `contracts.with_seq()`; every replay re-derives the same value.

Three things live here:

- `StreamProducer` — batching `XADD`. Group commit, so `appendfsync always` costs one fsync per
  batch instead of one per order.
- `read_records` — the consumer helper over `XREAD COUNT n BLOCK`, shared by the ledger,
  fan-out and archiver.
- `HaltState` — Redis is on the critical path, and Open Issue 003 §8.5 requires that its
  absence fail *loudly* rather than time out silently or, worse, accept orders that cannot be
  durably recorded.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, NamedTuple

from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError, TimeoutError as RedisTimeoutError

from contracts.v1.generated.contracts import unpack_any, with_seq

#: The single field each stream entry carries: the packed fixed-width record. Redis stream
#: values are binary-safe, so the record crosses as-is with no encoding step to get wrong.
RECORD_FIELD = b"r"

#: Errors that mean "Redis is not reachable", as opposed to "this command was wrong".
UNREACHABLE = (RedisConnectionError, RedisTimeoutError, OSError)


class HaltReason:
    REDIS_UNREACHABLE = "redis_unreachable"


@dataclass
class HaltState:
    """Whether the exchange can durably record orders.

    A halt is not a flag set once on failure — Success Criterion 5 requires it to clear without
    a gateway restart, so a watchdog re-checks and lifts it on its own.
    """

    halted: bool = False
    reason: str | None = None
    since_ns: int | None = None
    detail: str | None = None

    def halt(self, reason: str, detail: str | None = None) -> None:
        if not self.halted:
            self.since_ns = time.time_ns()
        self.halted = True
        self.reason = reason
        self.detail = detail

    def clear(self) -> None:
        self.halted = False
        self.reason = None
        self.since_ns = None
        self.detail = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "halted": self.halted,
            "reason": self.reason,
            "since_ns": self.since_ns,
            "detail": self.detail,
        }


class StreamRecord(NamedTuple):
    """One entry, with its stream ID already applied to the record's seq fields."""

    stream_id: str
    record: Any


class _Pending(NamedTuple):
    stream: str
    payload: bytes
    future: asyncio.Future


class StreamProducer:
    """Batching `XADD`. The gateway is the single producer (Open Issue 007).

    Callers `await append(...)` and get the assigned stream ID back. Under concurrency the
    flusher drains everything that queued while the previous pipeline was in flight and writes
    it as one round trip — which is group commit, implemented by Redis rather than by hand
    (Open Issue 003 §8.2). That is what makes `appendfsync always` affordable.
    """

    def __init__(
        self,
        redis: Redis,
        halt: HaltState,
        *,
        maxlen: int,
        batch_max: int,
    ) -> None:
        self._redis = redis
        self._halt = halt
        self._maxlen = maxlen
        self._batch_max = batch_max
        self._queue: asyncio.Queue[_Pending] = asyncio.Queue()
        self._task: asyncio.Task | None = None
        self.batches_flushed = 0
        self.records_written = 0

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="stream-producer")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def append(self, stream: str, record) -> str:
        """Append one record; returns the Redis stream ID, which *is* its sequence number."""
        if self._halt.halted:
            raise ExchangeHalted(self._halt.reason or HaltReason.REDIS_UNREACHABLE)
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        await self._queue.put(_Pending(stream, record.pack(), future))
        return await future

    async def _run(self) -> None:
        while True:
            first = await self._queue.get()
            batch = [first]
            # Everything already queued goes in the same pipeline. Nothing is delayed waiting
            # for a batch to fill: the batch is whatever accumulated while the previous flush
            # was in flight, which is exactly the group-commit shape.
            while len(batch) < self._batch_max:
                try:
                    batch.append(self._queue.get_nowait())
                except asyncio.QueueEmpty:
                    break
            await self._flush(batch)

    async def _flush(self, batch: list[_Pending]) -> None:
        try:
            pipe = self._redis.pipeline(transaction=False)
            for item in batch:
                pipe.xadd(
                    item.stream,
                    {RECORD_FIELD: item.payload},
                    maxlen=self._maxlen,
                    approximate=True,
                )
            ids = await pipe.execute()
        except UNREACHABLE as exc:
            self._halt.halt(HaltReason.REDIS_UNREACHABLE, str(exc))
            for item in batch:
                if not item.future.done():
                    item.future.set_exception(ExchangeHalted(HaltReason.REDIS_UNREACHABLE))
            return
        except RedisError as exc:  # a real command error — not a halt
            for item in batch:
                if not item.future.done():
                    item.future.set_exception(exc)
            return

        self.batches_flushed += 1
        self.records_written += len(batch)
        for item, stream_id in zip(batch, ids):
            if not item.future.done():
                item.future.set_result(
                    stream_id.decode() if isinstance(stream_id, bytes) else stream_id
                )


class ExchangeHalted(Exception):
    """Raised when an order cannot be durably recorded. Never swallowed into a timeout."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


async def read_records(
    redis: Redis,
    stream: str,
    last_id: str = "0-0",
    *,
    count: int = 100,
    block_ms: int = 1000,
) -> list[StreamRecord]:
    """`XREAD COUNT n BLOCK` over one stream, decoded into contract records.

    Each record is stamped with the stream ID it was assigned, so `seq` is populated on read
    rather than authored on write — and re-derives identically on every replay.
    """
    response = await redis.xread({stream: last_id}, count=count, block=block_ms)
    out: list[StreamRecord] = []
    for _stream_name, entries in response or []:
        for raw_id, fields in entries:
            stream_id = raw_id.decode() if isinstance(raw_id, bytes) else raw_id
            payload = fields[RECORD_FIELD]
            out.append(StreamRecord(stream_id, with_seq(unpack_any(payload), stream_id)))
    return out


async def watch_health(
    redis: Redis, halt: HaltState, *, poll_ms: int, stop: asyncio.Event | None = None
) -> None:
    """Ping Redis on an interval, halting and un-halting as it goes away and comes back.

    Success Criterion 5 — "restarting Redis clears the halt state without a gateway restart" —
    is this loop. A halt set only when an order happens to fail would never lift on its own.
    """
    interval = poll_ms / 1000
    while stop is None or not stop.is_set():
        try:
            await redis.ping()
        except UNREACHABLE as exc:
            halt.halt(HaltReason.REDIS_UNREACHABLE, str(exc))
        except RedisError as exc:
            halt.halt(HaltReason.REDIS_UNREACHABLE, str(exc))
        else:
            if halt.halted:
                halt.clear()
        await asyncio.sleep(interval)
