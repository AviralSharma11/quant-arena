"""Redis stream runner for the C++ matching engine.

Redis remains outside the engine process so the existing durability, sequence stamping, and
recovery rules stay unchanged. The child process owns only deterministic matching state and speaks
length-prefixed packed contract records over stdin/stdout.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import struct
import time

from redis.asyncio import Redis

from config.settings import settings as default_settings
from config.startup import log_startup
from contracts.v1.generated.contracts import unpack_any
from services import checkpoint
from services.gateway.streams import HaltState, StreamProducer, read_records, watch_health
from services.matcher.checkpointing import (
    PROCESS,
    CheckpointSchedule,
    count_anchors_after,
    trim_streams,
)
from services.matcher.runner import is_anchor
from services.matcher.snapshot import REQUEST as SNAPSHOT_REQUEST

DEFAULT_ENGINE_PATH = "/usr/local/bin/quant-arena-engine"
FRAME_HEADER = struct.Struct("<I")


class CppEngineProcess:
    """Own the C++ worker and translate one contract record at a time."""

    def __init__(self, *, initial_cash_ticks: int) -> None:
        self.initial_cash_ticks = initial_cash_ticks
        self.process: asyncio.subprocess.Process | None = None

    async def start(self) -> None:
        if self.process is not None:
            if self.process.returncode is None:
                return
            self.process = None
        executable = os.environ.get("QA_CPP_ENGINE_PATH", DEFAULT_ENGINE_PATH)
        self.process = await asyncio.create_subprocess_exec(
            executable,
            "--initial-cash-ticks",
            str(self.initial_cash_ticks),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
        )

    async def stop(self) -> None:
        process = self.process
        self.process = None
        if process is None:
            return
        if process.returncode is None:
            process.terminate()
        await process.wait()

    async def _round_trip(self, payload: bytes) -> list[bytes]:
        process = self.process
        if process is None or process.stdin is None or process.stdout is None:
            raise RuntimeError("C++ engine process is not running")

        process.stdin.write(FRAME_HEADER.pack(len(payload)))
        process.stdin.write(payload)
        await process.stdin.drain()

        frames: list[bytes] = []
        while True:
            header = await process.stdout.readexactly(FRAME_HEADER.size)
            length = FRAME_HEADER.unpack(header)[0]
            if length == 0:
                return frames
            frames.append(await process.stdout.readexactly(length))

    async def apply(self, record) -> list:
        return [unpack_any(frame) for frame in await self._round_trip(record.pack())]

    async def snapshot(self) -> bytes:
        """The engine's state in the `snapshot.py` format (Open Issue 020)."""
        (frame,) = await self._round_trip(SNAPSHOT_REQUEST)
        return frame

    async def restore(self, snapshot: bytes) -> None:
        """Load a snapshot. The worker refuses unless it has applied nothing yet."""
        await self._round_trip(snapshot)


class CppMatcher:
    """Tail Redis, delegate matching to C++, and append packed contract outputs."""

    def __init__(
        self,
        redis: Redis,
        settings,
        *,
        halt: HaltState | None = None,
        producer: StreamProducer | None = None,
        batch_size: int = 100,
        poll_block_ms: int = 500,
    ) -> None:
        self.redis = redis
        self.settings = settings
        self.halt = halt or HaltState()
        self.producer = producer or StreamProducer(
            redis,
            self.halt,
            batch_max=settings.stream_batch_max,
        )
        self._owns_producer = producer is None
        self.batch_size = batch_size
        self.poll_block_ms = poll_block_ms
        self.engine = CppEngineProcess(initial_cash_ticks=settings.initial_cash_ticks)
        self.last_inbound_id = "0-0"
        #: The id of the last outbound record this matcher has durably appended — or, straight
        #: after recovery, the last one on the stream. The checkpoint's second position.
        self.last_outbound_id = "0-0"
        self.records_answered = 0
        self.resumed_from_checkpoint = False
        self.schedule = CheckpointSchedule(
            interval_ms=settings.checkpoint_interval_ms,
            trim_interval_ms=settings.checkpoint_trim_interval_ms,
        )
        self.last_recovery_seconds: float | None = None
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    async def recover(self) -> int:
        """Rebuild the C++ process from its checkpoint and the tail, appending nothing.

        Raises `CheckpointRefused` when a stream has been trimmed past the resume point — the
        process must not start on a partial history (Open Issue 020).
        """
        started = time.perf_counter()
        try:
            saved = await checkpoint.resume_positions(
                self.redis,
                PROCESS,
                [self.settings.stream_inbound, self.settings.stream_outbound],
                config_hash=self.settings.config_hash,
            )
            inbound_from = saved.positions[self.settings.stream_inbound] if saved else "0-0"
            outbound_from = saved.positions[self.settings.stream_outbound] if saved else "0-0"

            await self.engine.stop()
            self.engine = CppEngineProcess(initial_cash_ticks=self.settings.initial_cash_ticks)
            await self.engine.start()
            if saved is not None:
                await self.engine.restore(saved.state)
            self.resumed_from_checkpoint = saved is not None

            already_answered, last_outbound = await count_anchors_after(
                self._read, self.settings.stream_outbound, outbound_from, is_anchor
            )
            last_id, replayed = inbound_from, 0
            while replayed < already_answered:
                batch = await self._read(self.settings.stream_inbound, last_id)
                if not batch:
                    raise RuntimeError(
                        "outbound anchors exceed retained inbound records; "
                        "cannot recover the C++ engine safely"
                    )
                for item in batch:
                    if replayed >= already_answered:
                        break
                    await self.engine.apply(item.record)
                    last_id = item.stream_id
                    replayed += 1
            self.last_inbound_id = last_id
            self.last_outbound_id = last_outbound
            self.records_answered = replayed
            return replayed
        finally:
            self.last_recovery_seconds = time.perf_counter() - started

    async def write_checkpoint(self) -> None:
        """Save the engine and both positions. Called only between inbound records, when every
        output of `last_inbound_id` is already durable — so the pair is always consistent."""
        await checkpoint.save(
            self.redis,
            PROCESS,
            positions={
                self.settings.stream_inbound: self.last_inbound_id,
                self.settings.stream_outbound: self.last_outbound_id,
            },
            state=await self.engine.snapshot(),
            config_hash=self.settings.config_hash,
        )
        self.schedule.checkpoint_written()

    async def step(self) -> int:
        process = self.engine.process
        if process is None or process.returncode is not None:
            # Never restart the worker here: a new worker is an *empty* engine, and matching on
            # it would fork from every consumer exactly as a partial replay does. The process
            # exits instead, and its restart recovers from the checkpoint (Open Issue 020).
            raise RuntimeError("C++ engine worker is not running; refusing to match on a fresh one")
        batch = await self._read(
            self.settings.stream_inbound,
            self.last_inbound_id,
            block_ms=self.poll_block_ms,
        )
        for item in batch:
            for output in await self.engine.apply(item.record):
                self.last_outbound_id = await self.producer.append(
                    self.settings.stream_outbound, output
                )
            self.last_inbound_id = item.stream_id
            self.records_answered += 1
        return len(batch)

    async def maintain(self) -> None:
        """Checkpoint and trim when due. Between batches, so the engine is never mid-record."""
        if self.schedule.checkpoint_due():
            await self.write_checkpoint()
        if self.schedule.trim_due():
            await trim_streams(self.redis, self.settings, self.halt)
            self.schedule.trimmed()

    async def run(self) -> None:
        while not self._stop.is_set():
            handled = await self.step()
            await self.maintain()
            if not handled:
                await asyncio.sleep(0.01)

    async def start(self) -> None:
        await self.engine.start()
        self.producer.start()
        self._stop.clear()
        self._task = asyncio.create_task(self.run(), name="cpp-matcher")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        if self._owns_producer:
            await self.producer.stop()
        await self.engine.stop()

    async def _read(self, stream: str, last_id: str, *, block_ms: int = 20):
        return await read_records(
            self.redis,
            stream,
            last_id=last_id,
            count=self.batch_size,
            block_ms=block_ms,
        )


async def main() -> None:
    settings = default_settings
    log_startup("cpp-matcher", settings)
    redis = Redis.from_url(settings.redis_url, decode_responses=False)
    halt = HaltState()
    matcher = CppMatcher(redis, settings, halt=halt)
    replayed = await matcher.recover()
    print(
        f"cpp-matcher: {'checkpoint' if matcher.resumed_from_checkpoint else 'genesis'} + "
        f"{replayed} inbound records in {matcher.last_recovery_seconds:.6f}s, "
        f"resuming at {matcher.last_inbound_id}"
    )
    # Straight away, so trimming is not pinned by this reader for a whole interval.
    await matcher.write_checkpoint()
    watchdog = asyncio.create_task(
        watch_health(redis, halt, poll_ms=settings.stream_health_poll_ms),
        name="cpp-matcher-halt-watchdog",
    )
    await matcher.start()
    try:
        # Awaiting the loop, not an Event: if matching stops for any reason the process exits
        # and is restarted into recovery, rather than staying up and answering nothing.
        await matcher._task
    finally:
        watchdog.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await watchdog
        await matcher.stop()
        await redis.aclose()


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
