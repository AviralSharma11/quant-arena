"""Redis stream runner for the C++ matching engine.

Redis remains outside the engine process so the existing durability, sequence stamping, and
recovery rules stay unchanged. The child process owns only deterministic matching state and speaks
length-prefixed packed contract records over stdin/stdout.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import struct
import time

from redis.asyncio import Redis

from config.settings import settings as default_settings
from config.startup import log_startup
from contracts.v1.generated.contracts import unpack_any
from services.gateway.streams import HaltState, StreamProducer, read_records, watch_health
from services.matcher.runner import is_anchor

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

    async def apply(self, record) -> list:
        process = self.process
        if process is None or process.stdin is None or process.stdout is None:
            raise RuntimeError("C++ engine process is not running")

        payload = record.pack()
        process.stdin.write(FRAME_HEADER.pack(len(payload)))
        process.stdin.write(payload)
        await process.stdin.drain()

        output: list = []
        while True:
            header = await process.stdout.readexactly(FRAME_HEADER.size)
            length = FRAME_HEADER.unpack(header)[0]
            if length == 0:
                return output
            packed = await process.stdout.readexactly(length)
            output.append(unpack_any(packed))


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
            maxlen=settings.stream_maxlen,
            batch_max=settings.stream_batch_max,
        )
        self._owns_producer = producer is None
        self.batch_size = batch_size
        self.poll_block_ms = poll_block_ms
        self.engine = CppEngineProcess(initial_cash_ticks=settings.initial_cash_ticks)
        self.last_inbound_id = "0-0"
        self.records_answered = 0
        self.last_recovery_seconds: float | None = None
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

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
        """Rebuild the C++ process from answered inbound records without appending outputs."""
        started = time.perf_counter()
        try:
            already_answered = await self._count_anchors()
            await self.engine.stop()
            self.engine = CppEngineProcess(initial_cash_ticks=self.settings.initial_cash_ticks)
            await self.engine.start()
            last_id, replayed = "0-0", 0
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
            self.records_answered = replayed
            return replayed
        finally:
            self.last_recovery_seconds = time.perf_counter() - started

    async def step(self) -> int:
        batch = await self._read(
            self.settings.stream_inbound,
            self.last_inbound_id,
            block_ms=self.poll_block_ms,
        )
        for item in batch:
            for output in await self.engine.apply(item.record):
                await self.producer.append(self.settings.stream_outbound, output)
            self.last_inbound_id = item.stream_id
            self.records_answered += 1
        return len(batch)

    async def run(self) -> None:
        while not self._stop.is_set():
            try:
                handled = await self.step()
                if not handled:
                    await asyncio.sleep(0.01)
            except asyncio.CancelledError:
                break
            except Exception:  # noqa: BLE001 -- a failed cycle leaves C++ state uncertain
                # A failure can occur after the child has applied an input but before its
                # anchor was durably appended. Retrying the live tail would then apply that
                # input twice. Reconcile from durable anchors before consuming again; this
                # also replaces a child that died without resetting order ids or the book.
                logging.getLogger(__name__).exception("cpp matcher step failed; recovering")
                while not self._stop.is_set():
                    try:
                        replayed = await self.recover()
                    except asyncio.CancelledError:
                        return
                    except Exception:  # noqa: BLE001 -- Redis may still be restarting
                        logging.getLogger(__name__).exception("cpp matcher recovery failed")
                        await asyncio.sleep(0.1)
                    else:
                        logging.getLogger(__name__).info(
                            "cpp matcher recovered %d inbound records; resuming at %s",
                            replayed,
                            self.last_inbound_id,
                        )
                        break

    async def start(self) -> None:
        if self._task is None:
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
        f"cpp-matcher: replayed {replayed} inbound records in "
        f"{matcher.last_recovery_seconds:.6f}s, resuming at {matcher.last_inbound_id}"
    )
    watchdog = asyncio.create_task(
        watch_health(redis, halt, poll_ms=settings.stream_health_poll_ms),
        name="cpp-matcher-halt-watchdog",
    )
    await matcher.start()
    try:
        await asyncio.Event().wait()
    finally:
        watchdog.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await watchdog
        await matcher.stop()
        await redis.aclose()


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
