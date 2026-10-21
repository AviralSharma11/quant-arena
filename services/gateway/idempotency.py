"""Idempotency — two identifiers and atomic claim-and-append.

Open Issue 008: a client cannot distinguish "the request never arrived" from "it succeeded
but the response was lost" — only the server can, and only if designed to.

The mechanism is `client_order_id`, a client-assigned identifier unique per user, carried with
every submission. The gateway records the outcome (accepted or rejected, order_id, rejection
reason) in Redis with a one-hour TTL, keyed by (user_id, client_order_id). On a duplicate
submission, the same outcome is returned.

The order of operations is 008's: **validate → reserve → atomic claim-and-append → release on
duplicate.** The claim and the append are one Lua script
(`services.gateway.streams.CLAIM_AND_APPEND_SCRIPT`), run through the stream producer so
orders keep their group commit. There is therefore no window in which the key is claimed but
no order exists in the stream, nor one in which an order exists but the key does not — the
second is what let a retry after a lost reply append the same order twice.

This store is what is left around that script: the key format, a read-only lookup for the
cheap early answer, and rejections, which append nothing and so need no script.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal

from redis.asyncio import Redis

#: Redis key format for idempotency state. Prefix + user_id + client_order_id.
IDEMPOTENCY_PREFIX = "qa.idempotent:"


@dataclass
class IdempotencyOutcome:
    """The recorded outcome of a first submission, for reply on duplicate.

    `in_progress` is only ever read back from a sentinel a gateway older than the
    claim-and-append script left behind; this one never writes it.
    """

    status: Literal["in_progress", "accepted", "rejected"]
    order_id: int | None = None
    seq: str | None = None
    reason: str | None = None

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v is not None}

    @classmethod
    def from_dict(cls, d: dict) -> "IdempotencyOutcome":
        return cls(**d)

    @classmethod
    def parse(cls, raw: str) -> "IdempotencyOutcome":
        try:
            return cls.from_dict(json.loads(raw))
        except (json.JSONDecodeError, TypeError):
            return cls(status="in_progress")


class IdempotencyStore:
    """Idempotency keys and outcomes in Redis. Acceptances are written by the append script."""

    def __init__(self, redis: Redis, ttl_seconds: int):
        self.redis = redis
        #: How long a recorded outcome stays replayable. Open Issue 008's hour.
        self.ttl_seconds = ttl_seconds

    def key(self, user_id: int, client_order_id: int) -> str:
        return f"{IDEMPOTENCY_PREFIX}{user_id}:{client_order_id}"

    async def lookup(self, user_id: int, client_order_id: int) -> IdempotencyOutcome | None:
        """The stored outcome, or None. Read-only: nothing is claimed here.

        An early answer for a retry, before any validation or reservation. A miss proves
        nothing — two submissions can both miss — which is why the claim itself happens in the
        append script, where Redis decides the race.
        """
        raw = await self.redis.get(self.key(user_id, client_order_id))
        if raw is None:
            return None
        return IdempotencyOutcome.parse(raw.decode() if isinstance(raw, bytes) else raw)

    async def record_rejected(
        self, user_id: int, client_order_id: int, reason: str
    ) -> IdempotencyOutcome:
        """Record a rejection unless an outcome is already stored, and return what is stored.

        `NX`, because a rejection must never overwrite an acceptance: a submission that failed
        validation can race one with the same key that was appended, and the appended order is
        the one the client has to be told about.
        """
        key = self.key(user_id, client_order_id)
        outcome = IdempotencyOutcome(status="rejected", reason=reason)
        if await self.redis.set(key, json.dumps(outcome.to_dict()), ex=self.ttl_seconds, nx=True):
            return outcome
        return await self.lookup(user_id, client_order_id) or outcome
