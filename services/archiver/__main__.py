"""Run the archiver as its own process: `python -m services.archiver`.

Its own process for the same reason the matcher, ledger and fan-out have one (Open Issue 007):
the gateway must stay answerable to HTTP, and this is a long-running loop with no request
attached to it. Unlike those three it is also the only process whose absence costs nothing in
Phase 1 — the archive is not correctness-critical (Open Issue 018 §13.2) — so it takes no part in
anyone else's healthcheck ordering.

No PostgreSQL: bulk history is not written to the read model (6.2's Boundaries), and this process
has nothing to say to it.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sys

from redis.asyncio import Redis

from config.settings import settings as default_settings
from config.startup import log_startup
from services.archiver.runner import LOGGER_NAME, Archiver

#: How often the process reports what it has written. Structured logs are the observability story
#: (Open Issue 012: no Prometheus, no Grafana), so this line is the only place a running archive
#: announces its size.
SNAPSHOT_INTERVAL_SECONDS = 60


async def main() -> None:
    settings = default_settings
    logging.basicConfig(stream=sys.stdout, level=logging.INFO, format="%(message)s")
    # Task 1.4 criterion 3: the configuration hash appears in every process's startup log.
    log_startup("archiver", settings)

    # decode_responses=False: stream entries carry packed fixed-width records, and decoding them
    # as text would corrupt them silently.
    redis = Redis.from_url(settings.redis_url, decode_responses=False)
    archiver = Archiver(redis, settings)

    resumed_from = archiver.resume()
    await archiver.ensure_resumable()
    await archiver.publish_position()
    logging.getLogger(LOGGER_NAME).info(
        '{"event":"archiver_resume","from":"%s","root":"%s"}',
        resumed_from,
        archiver.writer.root,
    )

    archiver.start()
    try:
        while True:
            await asyncio.sleep(SNAPSHOT_INTERVAL_SECONDS)
            archiver.log_snapshot()
    finally:
        await archiver.stop()
        # The final partial unit is still the truth about what happened. A SIGTERM from
        # `docker compose down` lands here, so a stopped stack leaves a complete archive rather
        # than one missing its last minute.
        archiver.finish()
        archiver.log_snapshot()
        await redis.aclose()


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
