"""Reading the exchange's halt state from outside the gateway.

`contracts/v1/rest_and_ws.md` §3.6 gives the browser a `halted` frame, and `web/src/stream/
client.ts` renders it as its own connection state — deliberately not "connected", because
showing a confident green indicator over prices nobody can trade on is the one thing Open
Issue 014 §14e says a trading interface must never do.

Nothing could send that frame before Task 5.2b. The halt flag is a plain object in the
gateway's memory (Open Issue 004 keeps risk state out of Redis), and fan-out is a different
process. So the gateway now *publishes* it — `services/gateway/streams.py` `HALT_KEY` — and this
reads it.

## Why published rather than inferred

Fan-out already talks to Redis constantly, so the cheap thing would be to ping Redis here and
call a failed ping a halt. That substitutes one claim for another:

| What the browser reads | What a local ping would actually mean |
|---|---|
| the gateway cannot durably record orders | fan-out cannot reach Redis |

They usually coincide and come apart in both directions. A hiccup on fan-out's own connection
would show HALTED over a healthy exchange; a store that is *readable but not writable* — disk
full, an fsync failing under `appendfsync always` — would read fine here while every order was
being refused. The second is precisely the case a halt exists to cover, so the signal has to
come from the process making the claim.

## An absent key is a halt

The gateway refreshes the key every `stream_health_poll_ms` with a TTL of three intervals. If it
stops — crashed, killed, not started yet — the key expires and this reports `gateway_unreachable`.
That is true rather than defensive: with nothing publishing, no order can be recorded, and the
state clears on its own within one poll of the gateway returning.
"""

from __future__ import annotations

import json

from redis.asyncio import Redis
from redis.exceptions import RedisError

from services.gateway.streams import HALT_KEY, UNREACHABLE, HaltReason


class HaltView:
    """The last halt state read from Redis, and whether it changed.

    Deliberately not a `HaltState`: this process does not *own* a halt and must never set one.
    It observes somebody else's, which is a different type of thing even though the fields
    match, and keeping them distinct is what stops fan-out growing an opinion of its own.
    """

    def __init__(self, *, key: str = HALT_KEY) -> None:
        self.key = key
        self.halted = False
        self.reason: str | None = None
        self.detail: str | None = None
        #: Bumped on every transition, so the tick can send one frame per change rather than
        #: one per tick. Twenty identical `halted` frames a second would be its own outage.
        self.transitions = 0

    async def refresh(self, redis: Redis) -> bool:
        """Read the key once. Returns True if the state changed."""
        try:
            raw = await redis.get(self.key)
        except (RedisError, *UNREACHABLE) as exc:
            # Redis is gone, so the gateway cannot be appending to it either.
            return self._set(True, HaltReason.REDIS_UNREACHABLE, str(exc))

        if raw is None:
            return self._set(
                True,
                HaltReason.GATEWAY_UNREACHABLE,
                "no gateway is publishing a halt state",
            )
        try:
            state = json.loads(raw)
        except (ValueError, TypeError):
            # Something else owns this key. Report it rather than guessing at the contents:
            # a fan-out that silently ignored a malformed key would report a healthy exchange
            # on the strength of a value it could not read.
            return self._set(True, HaltReason.GATEWAY_UNREACHABLE, "halt key is not readable")

        return self._set(
            bool(state.get("halted")), state.get("reason"), state.get("detail")
        )

    def _set(self, halted: bool, reason: str | None, detail: str | None) -> bool:
        if halted == self.halted and reason == self.reason:
            # `detail` alone is not a transition: it carries the exception text, which varies
            # between two readings of the same outage.
            self.detail = detail
            return False
        self.halted, self.reason, self.detail = halted, reason, detail
        self.transitions += 1
        return True
