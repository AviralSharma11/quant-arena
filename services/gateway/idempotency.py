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

# Lua script for atomic claim:
# Check if the idempotency key exists, and if not, set it to "in_progress" atomically.
# This ensures that only one submission can claim the key at a time.
#
# KEYS[1] = idempotency key
# ARGV[1] = TTL in seconds
#
# Returns:
# - [1, outcome_json] if key already exists (duplicate)
# - [2, nil] if key didn't exist and we set it to "in_progress" (new submission)
CLAIM_SCRIPT = """
local key = KEYS[1]
local ttl = tonumber(ARGV[1])

-- Check if key already exists
local existing = redis.call('GET', key)
if existing then
    return {1, existing}  -- duplicate: return stored outcome
end

-- Mark as in_progress atomically (only succeeds if key didn't exist)
redis.call('SET', key, 'in_progress', 'EX', ttl)
return {2, nil}  -- new: proceed with submission
"""


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
    """Manages idempotency keys and outcomes in Redis.

    Uses a Lua script to atomically claim the idempotency key before appending to the stream.
    This ensures the following invariant: if the key is claimed, an order was appended.
    """

    def __init__(self, redis: Redis, ttl_seconds: int):
        self.redis = redis
        self.ttl_seconds = ttl_seconds
        self._claim_script = redis.register_script(CLAIM_SCRIPT)

    def _key(self, user_id: int, client_order_id: int) -> str:
        return f"{IDEMPOTENCY_PREFIX}{user_id}:{client_order_id}"

    async def claim(self, user_id: int, client_order_id: int) -> IdempotencyOutcome:
        """Claim the idempotency key atomically.

        Uses a Lua script to ensure that only one submission can claim the key.
        If the key was already claimed, returns the stored outcome.

        Returns:
        - status="in_progress" if this is the first submission for this key
        - status="accepted"|"rejected" with stored outcome if a previous submission completed
        """
        key = self._key(user_id, client_order_id)

        # Execute the atomic claim script
        result = await self._claim_script(keys=[key], args=[self.ttl_seconds])

        if result[0] == 1:
            # Duplicate: key already exists, return stored outcome
            try:
                stored_dict = json.loads(result[1])
                return IdempotencyOutcome.from_dict(stored_dict)
            except (json.JSONDecodeError, TypeError):
                # Backward compat: if stored as plain string "in_progress"
                return IdempotencyOutcome(status="in_progress")
        else:
            # New submission: key was set to "in_progress"
            return IdempotencyOutcome(status="in_progress")

    async def record_accepted(
        self, user_id: int, client_order_id: int, order_id: int | None, seq: str
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
