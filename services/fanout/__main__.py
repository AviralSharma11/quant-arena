"""Run fan-out as its own process: `python -m services.fanout`.

Never inside the gateway. Open Issue 006 §7b is explicit: coupling fan-out to the sequencer
degrades order-acknowledgement latency under connection load, and the gateway must stay
answerable to HTTP while this tails a stream and rebuilds books.

**No compose service until Task 5.2b.** A container that maintains order books and serves them
to nobody is a container doing nothing observable, so the entry point exists to be run by hand
against the live stack — which is how this half is verified — and the service arrives with the
port it needs to expose.

    QA_REDIS_URL=redis://localhost:6379/0 python -m services.fanout
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import sys

from redis.asyncio import Redis

from config.settings import settings as default_settings
from config.startup import log_startup
from services.fanout.runner import FanOut, LOGGER_NAME

#: How often the book state is printed while running. Structured logs and offline analysis are
#: the whole of the observability story (Open Issue 012) — there is no Prometheus to scrape.
SNAPSHOT_INTERVAL_SECONDS = 5.0


def _configure_logging() -> None:
    root = logging.getLogger()
    if not root.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(message)s"))
        root.addHandler(handler)
    root.setLevel(logging.INFO)


async def _until_signalled() -> None:
    """SIGTERM as well as SIGINT — `docker compose stop` sends the first, and its default
    handler ends the process before any final summary can be written."""
    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(signum, stopping.set)
    await stopping.wait()


async def main() -> None:
    settings = default_settings
    _configure_logging()
    # Task 1.4 criterion 3: the configuration hash appears in every process's startup log, so a
    # recorded feed and the exchange that produced it are provably the same configuration.
    log_startup("fanout", settings)

    # decode_responses=False: stream entries carry packed fixed-width records, and decoding
    # them as text would corrupt the money path silently.
    redis = Redis.from_url(settings.redis_url, decode_responses=False)
    fanout = FanOut(redis, settings)

    replayed = await fanout.recover()
    logging.getLogger(LOGGER_NAME).info(
        '{"event":"fanout_recovered","records":%d,"resuming_at":"%s"}'
        % (replayed, fanout.state.last_seq)
    )

    fanout.start()
    reporter = asyncio.create_task(_report(fanout), name="fanout-snapshot")
    try:
        await _until_signalled()
    finally:
        reporter.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await reporter
        await fanout.stop()
        fanout.log_snapshot()
        await redis.aclose()


async def _report(fanout: FanOut) -> None:
    while True:
        await asyncio.sleep(SNAPSHOT_INTERVAL_SECONDS)
        fanout.log_snapshot()


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
