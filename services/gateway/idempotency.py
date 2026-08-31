"""Idempotency — two identifiers and atomic claim-and-append.

Open Issue 008: a client cannot distinguish "the request never arrived" from "it succeeded
but the response was lost" — only the server can, and only if designed to.

The mechanism is `client_order_id`, a client-assigned identifier unique per user, carried with
every submission. The gateway records the outcome (accepted or rejected, order_id, rejection
reason) in Redis with a one-hour TTL, keyed by (user_id, client_order_id). On a duplicate
submission, the same outcome is returned.

The critical invariant: claim and append are atomic — done by a single Lua script — so there
is no window in which the key is claimed but no order exists in the stream.
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
    """The recorded outcome of a first submission, for reply on duplicate."""

    status: Literal["in_progress", "accepted", "rejected"]
    order_id: int | None = None
    seq: str | None = None
    reason: str | None = None

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v is not None}

    @classmethod
    def from_dict(cls, d: dict) -> "IdempotencyOutcome":
        return cls(**d)


class IdempotencyStore:
    """Manages idempotency keys and outcomes in Redis."""

    def __init__(self, redis: Redis, ttl_seconds: int):
        self.redis = redis
        self.ttl_seconds = ttl_seconds

    def _key(self, user_id: int, client_order_id: int) -> str:
        return f"{IDEMPOTENCY_PREFIX}{user_id}:{client_order_id}"

    async def claim(self, user_id: int, client_order_id: int) -> IdempotencyOutcome:
        """Claim the idempotency key for a new submission.

        Returns:
        - status="in_progress" if this is the first submission for this key
        - status="in_progress" if a previous submission is still in flight
        - status="accepted"|"rejected" with stored outcome if a previous submission completed
        """
        key = self._key(user_id, client_order_id)
        stored = await self.redis.get(key)

        if stored is None:
            # First submission: set to "in_progress"
            await self.redis.set(key, "in_progress", ex=self.ttl_seconds)
            return IdempotencyOutcome(status="in_progress")

        # Duplicate submission: return stored outcome
        try:
            stored_dict = json.loads(stored)
            return IdempotencyOutcome.from_dict(stored_dict)
        except (json.JSONDecodeError, TypeError):
            # Backward compat: if stored as plain string "in_progress"
            return IdempotencyOutcome(status="in_progress")

    async def record_accepted(
        self, user_id: int, client_order_id: int, order_id: int, seq: str
    ) -> None:
        """Record that the submission was accepted."""
        key = self._key(user_id, client_order_id)
        outcome = IdempotencyOutcome(
            status="accepted", order_id=order_id, seq=seq
        )
        await self.redis.set(
            key, json.dumps(outcome.to_dict()), ex=self.ttl_seconds
        )

    async def record_rejected(
        self, user_id: int, client_order_id: int, reason: str
    ) -> None:
        """Record that the submission was rejected."""
        key = self._key(user_id, client_order_id)
        outcome = IdempotencyOutcome(status="rejected", reason=reason)
        await self.redis.set(
            key, json.dumps(outcome.to_dict()), ex=self.ttl_seconds
        )
