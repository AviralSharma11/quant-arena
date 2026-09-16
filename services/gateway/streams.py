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
import json
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
    #: The producer loop is not running, so nothing can be appended. Distinct from Redis being
    #: unreachable: the store may be perfectly healthy and the gateway still unable to record.
    PRODUCER_STOPPED = "producer_stopped"
    #: Nobody is publishing a halt state. Read by *other* processes, never set by the gateway
    #: on itself — a gateway that can run this line is by definition reachable.
    GATEWAY_UNREACHABLE = "gateway_unreachable"


#: Where the gateway publishes its halt state for other processes to read.
#:
#: The halt flag lives in gateway process memory (Open Issue 004: risk state is not in Redis),
#: which is right for the order path and useless to anyone else — fan-out is a separate process
#: and cannot see it. Task 5.2b needs it: `contracts/v1/rest_and_ws.md` §3.6 gives the browser a
#: `halted` frame, and until this key existed nothing could ever send one.
#:
#: Published rather than inferred, deliberately. Fan-out could ping Redis itself and call that a
#: halt, but "fan-out can reach Redis" is a different claim from "the gateway can durably record
#: orders" — they come apart in both directions, and the dangerous one is a store that is
#: readable but not writable, where the reads succeed while the exchange is halted.
HALT_KEY = "qa:halt"

#: The key is refreshed every poll and expires after this many intervals. An absent key
#: therefore means "no gateway is publishing", which is itself a halt — see
#: `services/fanout/halt.py`, which is what reads this.
HALT_KEY_TTL_INTERVALS = 3


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
        batch_max: int,
        maxlen: int | None = None,
    ) -> None:
        self._redis = redis
        self._halt = halt
        self._maxlen = maxlen
        self._batch_max = batch_max
        self._queue: asyncio.Queue[_Pending] = asyncio.Queue()
        self._task: asyncio.Task | None = None
        self.batches_flushed = 0
        self.records_written = 0
        self.max_batch = 0

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
        self._drain_and_fail(ExchangeHalted(HaltReason.PRODUCER_STOPPED))

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def append(self, stream: str, record) -> str:
        """Append one record; returns the Redis stream ID, which *is* its sequence number."""
        if self._halt.halted:
            raise ExchangeHalted(self._halt.reason or HaltReason.REDIS_UNREACHABLE)
        if not self.running:
            # Without this the caller would await a future nobody will ever resolve. A hung
            # request is worse than a failed one: the gateway looks alive and answers /health,
            # while every order silently never returns.
            self._halt.halt(HaltReason.PRODUCER_STOPPED, "stream producer is not running")
            raise ExchangeHalted(HaltReason.PRODUCER_STOPPED)
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        await self._queue.put(_Pending(stream, record.pack(), future))
        return await future

    @staticmethod
    def _fail(batch: list[_Pending], exc: BaseException) -> None:
        for item in batch:
            if not item.future.done():
                item.future.set_exception(exc)

    def _drain_and_fail(self, exc: BaseException) -> None:
        """Nothing may be left awaiting a future that will never be resolved."""
        while True:
            try:
                item = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            if not item.future.done():
                item.future.set_exception(exc)

    async def _run(self) -> None:
        try:
            await self._loop()
        finally:
            # However this loop ends — cancellation, or a bug nobody predicted — every queued
            # append is answered. See `append` for why a hang is the worse failure.
            self._halt.halt(HaltReason.PRODUCER_STOPPED, "stream producer stopped")
            self._drain_and_fail(ExchangeHalted(HaltReason.PRODUCER_STOPPED))

    async def _loop(self) -> None:
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
            try:
                await self._flush(batch)
            except asyncio.CancelledError:
                self._fail(batch, ExchangeHalted(HaltReason.PRODUCER_STOPPED))
                raise
            except BaseException as exc:  # noqa: BLE001 — a flush must never kill the loop
                self._fail(batch, exc)

    async def _flush(self, batch: list[_Pending]) -> None:
        try:
            pipe = self._redis.pipeline(transaction=False)
            for item in batch:
                # No MAXLEN in production: streams are trimmed below the oldest checkpoint by
                # `services.checkpoint.trim` (Open Issue 020). A length cap is kept for tests
                # and benchmarks that want a bounded scratch stream.
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
            self._fail(batch, exc)
            return

        self.batches_flushed += 1
        self.records_written += len(batch)
        self.max_batch = max(self.max_batch, len(batch))
        for item, stream_id in zip(batch, ids):
            if not item.future.done():
                item.future.set_result(
                    stream_id.decode() if isinstance(stream_id, bytes) else stream_id
                )
        # Defensive: a short reply must not leave anyone waiting forever.
        if len(ids) < len(batch):
            self._fail(
                batch[len(ids):],
                RedisError(f"pipeline returned {len(ids)} ids for {len(batch)} appends"),
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
    redis: Redis,
    halt: HaltState,
    *,
    poll_ms: int,
    stop: asyncio.Event | None = None,
    publish_key: str | None = HALT_KEY,
) -> None:
    """Ping Redis on an interval, halting and un-halting as it goes away and comes back.

    Success Criterion 5 — "restarting Redis clears the halt state without a gateway restart" —
    is this loop. A halt set only when an order happens to fail would never lift on its own.

    It also publishes the resulting state to `publish_key` so that processes outside the
    gateway can see it. This loop rather than a second one because the two would otherwise
    poll the same Redis on the same interval to answer the same question, and could disagree.

    The publish is best-effort by construction: if Redis is unreachable the write fails too,
    the key expires, and a reader sees the absence — which is the correct conclusion anyway.
    """
    interval = poll_ms / 1000
    ttl_ms = max(1, int(poll_ms * HALT_KEY_TTL_INTERVALS))
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
        if publish_key is not None:
            await publish_halt(redis, halt, key=publish_key, ttl_ms=ttl_ms)
        await asyncio.sleep(interval)


async def publish_halt(
    redis: Redis, halt: HaltState, *, key: str = HALT_KEY, ttl_ms: int
) -> bool:
    """Write the halt state where another process can read it. True if it landed.

    JSON, not a packed record: this is not a stream entry and it is not part of the frozen
    schema. It is one process telling another what it currently thinks, expiring on its own if
    that process stops thinking.
    """
    payload = json.dumps(halt.as_dict(), separators=(",", ":")).encode()
    try:
        await redis.set(key, payload, px=ttl_ms)
    except (RedisError, *UNREACHABLE):
        # Nothing to do and nothing to log at volume — this runs twice a second, and the
        # reader's timeout already says everything a log line would.
        return False
    return True
