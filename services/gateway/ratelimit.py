"""Per-user order rate limiting.

Open Issue 015 section 11.1 settles a **single tier of 1000 orders/sec for all accounts**,
replacing an earlier two-tier proposal, and records the consequence: with one tier this is no
longer denial-of-service protection, it is **exchange realism**. Real venues cap message rates
per participant, and Task 4.4's boundary — "do not special-case bots anywhere in the gateway" —
only means anything if there is a limit for a bot to be subject to.

`limits.max_orders_per_second` has been in the configuration since Task 1.4 and enforced
nowhere. It fell between 3.1 and 3.2 rather than being cut, and 4.4's fifth success criterion
("bots authenticate and are rate-limited exactly like any user") cannot pass without it.

## Why a token bucket, and why in process memory

A **token bucket** holds up to `rate` tokens and refills continuously at `rate` per second. Each
order spends one. It permits a short burst up to the bucket's capacity and then settles to the
sustained rate — which is what a rate limit should do, because real order flow is bursty and a
fixed window either rejects legitimate bursts or lets through twice the rate across a window
boundary.

State lives in gateway memory for the same reason `RiskState` does (Open Issue 004): this is on
the hot path of every order, a Redis round trip would dominate the check, and the gateway is
the single producer — so its memory is single-writer state needing no coordination. It is not
rebuilt on restart, and deliberately so: a restarted gateway granting a fresh burst is a
rounding error against a limit measured per second, and there is nothing here worth persisting.

Multiple gateway processes are Phase 2. With more than one, each would enforce the limit
separately and the effective cap would multiply — worth stating now, because it is invisible
until the day a second gateway is started.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass
class _Bucket:
    tokens: float
    last_refill: float


@dataclass
class RateLimiter:
    """A token bucket per user, refilled on read rather than by a background task.

    Refilling lazily means there is no timer, no sweep, and no task to keep alive — the elapsed
    time since a user's last order is exactly what determines how many tokens they have now.
    """

    rate_per_second: int
    #: Injectable so tests can advance time deliberately instead of sleeping. `monotonic` and
    #: not `time()`: a wall clock that steps backwards over NTP would hand out free tokens.
    clock: Callable[[], float] = time.monotonic
    _buckets: dict[int, _Bucket] = field(default_factory=dict)

    def allow(self, user_id: int) -> bool:
        """Spend one token. False means the caller is over its rate and nothing was spent."""
        now = self.clock()
        bucket = self._buckets.get(user_id)
        if bucket is None:
            # A user's first order finds a full bucket, then spends from it.
            bucket = _Bucket(tokens=float(self.rate_per_second), last_refill=now)
            self._buckets[user_id] = bucket
        else:
            elapsed = max(0.0, now - bucket.last_refill)
            bucket.tokens = min(
                float(self.rate_per_second),
                bucket.tokens + elapsed * self.rate_per_second,
            )
            bucket.last_refill = now

        if bucket.tokens < 1.0:
            return False
        bucket.tokens -= 1.0
        return True

    def retry_after_seconds(self, user_id: int) -> float:
        """How long until one more token exists. Reported to the client on a 429."""
        bucket = self._buckets.get(user_id)
        if bucket is None or bucket.tokens >= 1.0:
            return 0.0
        return (1.0 - bucket.tokens) / self.rate_per_second

    def forget(self, user_id: int) -> None:
        """Drop a user's bucket. Only for tests — nothing in the request path expires one."""
        self._buckets.pop(user_id, None)
