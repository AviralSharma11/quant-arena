"""Redis-backed sessions.

Open Issue 015 sub-decision 15a: session cookies, **no JWT**. The cookie carries an opaque
random id and nothing else; every piece of state sits in Redis.

That is the whole point, and it is what Success Criterion 2 tests: because no session data
lives in this process, restarting the gateway does not log anybody out. A dict on the app
object would pass every other test in this task and fail that one.
"""

from __future__ import annotations

import secrets

from redis.asyncio import Redis

#: 32 bytes from the OS CSPRNG. Long enough that guessing is not a threat model.
_SESSION_ID_BYTES = 32

_KEY_PREFIX = "session:"


class SessionStore:
    def __init__(self, redis: Redis, ttl_seconds: int) -> None:
        self._redis = redis
        self._ttl = ttl_seconds

    @staticmethod
    def _key(session_id: str) -> str:
        return f"{_KEY_PREFIX}{session_id}"

    async def create(self, user_id: int) -> str:
        session_id = secrets.token_urlsafe(_SESSION_ID_BYTES)
        await self._redis.set(self._key(session_id), str(user_id), ex=self._ttl)
        return session_id

    async def user_id(self, session_id: str) -> int | None:
        if not session_id:
            return None
        raw = await self._redis.get(self._key(session_id))
        if raw is None:
            return None
        return int(raw)

    async def destroy(self, session_id: str) -> None:
        if session_id:
            await self._redis.delete(self._key(session_id))
