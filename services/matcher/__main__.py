"""Run the matcher as its own process: `python -m services.matcher`.

Separate from the gateway on purpose. The gateway is the single *producer* of the inbound
stream (Open Issue 007) and must stay answerable to HTTP; matching is a single-writer loop with
no request attached to it. Coupling them would make a slow match a slow API.
"""

from __future__ import annotations

import asyncio
import contextlib

from redis.asyncio import Redis

from config.settings import settings as default_settings
from config.startup import log_startup
from services.gateway.streams import HaltState, watch_health
from services.matcher.runner import Matcher


async def main() -> None:
    settings = default_settings
    # Task 1.4 criterion 3: the configuration hash appears in every process's startup log, so
    # two processes disagreeing about the configuration announce it rather than diverging.
    log_startup("matcher", settings)

    # decode_responses=False: stream entries carry packed fixed-width records, and decoding
    # them as text would corrupt the money path silently.
    redis = Redis.from_url(settings.redis_url, decode_responses=False)
    halt = HaltState()
    matcher = Matcher(redis, settings, halt=halt)

    replayed = await matcher.recover()
    print(f"matcher: replayed {replayed} inbound records, resuming at {matcher.last_inbound_id}")

    watchdog = asyncio.create_task(
        watch_health(redis, halt, poll_ms=settings.stream_health_poll_ms),
        name="matcher-halt-watchdog",
    )
    matcher.start()
    try:
        await asyncio.Event().wait()
    finally:
        watchdog.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await watchdog
        await matcher.stop()
        await redis.aclose()


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
