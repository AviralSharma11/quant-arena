"""Run the ledger as its own process: `python -m services.ledger`.

`LedgerConsumer` was written in Task 2.2 and, until week 4, was started by nothing. Its tests
drove it directly, so the projection was proven correct and never actually run — with the
visible consequence that `/portfolio` and `/orders/open` never moved after a fill in the
deployed stack. This module is the missing entry point.

Its own process, for the same reason the matcher has one (Open Issue 007). The gateway must
stay answerable to HTTP, and projecting the whole outbound stream into PostgreSQL is a
long-running loop with no request attached to it. It also makes the ledger the **single writer**
of `accounts`, `positions`, `open_orders` and `house_fees` — which is what Open Issue 004 means
by PostgreSQL being a derived read model. Two writers and it would not be derived from anything.

## Startup is a full replay, every time

There are no snapshots and no checkpoints in Phase 1 (Open Issue 018 §13.1), so the ledger
rebuilds from `0-0` on every start and rewrites the read model from what it computed. Slow
recovery is the deliberate, measured Phase 2 baseline rather than an omission.

Replaying is safe to repeat in a way the matcher's replay is not: the matcher *appends* to a
stream, so a naive replay would duplicate fills, which is why it counts anchors. The ledger only
ever writes a projection, and rewriting a projection with the same inputs produces the same
output. That asymmetry is why this file is short and `services/matcher/runner.py` is not.
"""

from __future__ import annotations

import asyncio
import contextlib

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import create_async_engine

from config.settings import settings as default_settings
from config.startup import log_startup
from services.ledger.consumer import LedgerConsumer


async def main() -> None:
    settings = default_settings
    # Task 1.4 criterion 3: the configuration hash appears in every process's startup log.
    log_startup("ledger", settings)

    # decode_responses=False: stream entries carry packed fixed-width records, and decoding
    # them as text would corrupt the money path silently.
    redis = Redis.from_url(settings.redis_url, decode_responses=False)
    db = create_async_engine(settings.database_url, pool_pre_ping=True)

    consumer = LedgerConsumer(redis, db, settings.stream_outbound)
    replayed = await consumer.replay_from_genesis()
    print(
        f"ledger: replayed {replayed} outbound records, "
        f"resuming at {consumer.ledger.last_seq}"
    )

    consumer.start()
    try:
        await asyncio.Event().wait()
    finally:
        await consumer.stop()
        await db.dispose()
        await redis.aclose()


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
