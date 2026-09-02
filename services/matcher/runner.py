"""The matcher process — inbound stream in, outbound stream out.

This closes the loop that Task 2.1 left open at both ends:

    POST /orders ──► qa.inbound ──► matcher ──► qa.outbound ──► ledger ──► /portfolio
      (gateway)       (Redis)      (this)       (Redis)        (2.2)

All I/O lives here; `NaiveMatcher` is pure. That split is what makes the matcher testable
without Redis and, more importantly, what keeps the model deterministic — the runner never
passes a clock reading or an iteration order into it.

## Recovery is a full replay, and it re-emits nothing

There are no snapshots and no checkpoints in Phase 1 (Open Issue 018 §13.1), so a restarted
matcher rebuilds by replaying the retained stream from `0-0`. The obvious hazard is that a
naive replay would *re-append* every outbound record it had already written, duplicating fills
that moved real positions.

The matcher avoids that without a checkpoint, and without adding a field to a frozen schema, by
counting **anchors**. Every inbound record produces exactly one anchor:

| Inbound | Anchor |
|---|---|
| `SubmitOrder` | `OrderAccepted` or `OrderRejected` |
| `CancelOrder` | `OrderCancelled` with `USER_REQUESTED`, or `OrderRejected` |
| `CreateAccount` · `CreditCash` | the forwarded `AccountCreated` · `CashCredited` |

`Fill`, and the `OrderCancelled` that retires an IOC remainder, are not anchors — an IOC is
distinguishable by its `reason`, which is why that enum earned its two values. So the number of
anchors on the outbound stream is exactly the number of inbound records already answered.
Replay feeds that many inbound records through the matcher **silently**, rebuilding the book
and the `order_id` counter, then starts appending from the first record that was never
answered. Same code path as normal operation, which is the property Task 4.2 will need too.
"""

from __future__ import annotations

import asyncio

from redis.asyncio import Redis

from config.settings import Settings
from contracts.v1.generated.contracts import (
    AccountCreated,
    CancelReason,
    CashCredited,
    OrderAccepted,
    OrderCancelled,
    OrderRejected,
)
from services.gateway.streams import HaltState, StreamProducer, read_records
from services.matcher.adapter import NaiveMatcher


def is_anchor(record) -> bool:
    """Exactly one of these is emitted per inbound record. See the module docstring."""
    if isinstance(record, (OrderAccepted, OrderRejected, AccountCreated, CashCredited)):
        return True
    if isinstance(record, OrderCancelled):
        return int(record.reason) == int(CancelReason.USER_REQUESTED)
    return False


class Matcher:
    """Tails the inbound stream, matches, appends to the outbound stream."""

    def __init__(
        self,
        redis: Redis,
        settings: Settings,
        *,
        halt: HaltState | None = None,
        producer: StreamProducer | None = None,
        batch_size: int = 100,
        poll_block_ms: int = 500,
    ) -> None:
        self.redis = redis
        self.settings = settings
        self.matcher = self._new_matcher()
        self.halt = halt or HaltState()
        self.producer = producer or StreamProducer(
            redis,
            self.halt,
            maxlen=settings.stream_maxlen,
            batch_max=settings.stream_batch_max,
        )
        self._owns_producer = producer is None
        self.batch_size = batch_size
        self.poll_block_ms = poll_block_ms
        #: Where the inbound tail resumes. Set by `recover()`, advanced by `run()`.
        self.last_inbound_id = "0-0"
        self.records_answered = 0
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    def _new_matcher(self) -> NaiveMatcher:
        return NaiveMatcher(initial_cash_ticks=self.settings.initial_cash_ticks)

    # -- recovery ------------------------------------------------------------------------------

    async def _count_anchors(self) -> int:
        last_id, anchors = "0-0", 0
        while True:
            batch = await self._read(self.settings.stream_outbound, last_id)
            if not batch:
                return anchors
            for item in batch:
                anchors += is_anchor(item.record)
                last_id = item.stream_id

    async def recover(self) -> int:
        """Rebuild the book by replaying inbound. Returns the number of records replayed.

        Nothing is appended: the outbound records for these inputs are already on the stream.
        """
        already_answered = await self._count_anchors()
        self.matcher = self._new_matcher()
        self.records_answered = 0
        last_id, replayed = "0-0", 0

        while replayed < already_answered:
            batch = await self._read(self.settings.stream_inbound, last_id)
            if not batch:
                # The outbound stream claims more answers than the inbound stream has records.
                # Only trimming can do that, and rebuilding past a trim point is not something
                # to paper over silently — Phase 1 replays the whole retained stream or fails.
                break
            for item in batch:
                if replayed >= already_answered:
                    break
                self.matcher.apply(item.record)
                last_id = item.stream_id
                replayed += 1

        self.last_inbound_id = last_id
        self.records_answered = replayed
        return replayed

    # -- the live loop -------------------------------------------------------------------------

    async def step(self) -> int:
        """One read-match-append cycle. Returns the number of inbound records handled."""
        batch = await self._read(
            self.settings.stream_inbound, self.last_inbound_id, block_ms=self.poll_block_ms
        )
        for item in batch:
            for out in self.matcher.apply(item.record):
                await self.producer.append(self.settings.stream_outbound, out)
            # Advanced only after every outbound record is durable. A crash mid-record replays
            # that record on restart, and the anchor count is what makes that safe.
            self.last_inbound_id = item.stream_id
            self.records_answered += 1
        return len(batch)

    async def run(self) -> None:
        while not self._stop.is_set():
            try:
                if not await self.step():
                    await asyncio.sleep(0.01)
            except asyncio.CancelledError:
                break
            except Exception:  # noqa: BLE001 — a bad record must not kill the exchange
                await asyncio.sleep(0.1)

    def start(self) -> None:
        if self._task is None:
            self._stop.clear()
            self.producer.start()
            self._task = asyncio.create_task(self.run(), name="matcher")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        if self._owns_producer:
            await self.producer.stop()

    async def _read(self, stream: str, last_id: str, *, block_ms: int = 20):
        return await read_records(
            self.redis, stream, last_id=last_id, count=self.batch_size, block_ms=block_ms
        )
