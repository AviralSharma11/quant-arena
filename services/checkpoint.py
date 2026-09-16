"""Checkpoints, the resume guard, and stream trimming (Open Issue 020).

Every process that rebuilds by replaying a stream saves, now and then, **its state together with
the stream ids that state reflects**. A restart loads that checkpoint and replays only what came
after it. Three rules make that safe, and all three live here so no process can apply one and
forget another:

1. **A checkpoint is one Redis hash, written by one `HSET`.** State and positions are never
   observed half-written, because they are never written separately.
2. **Resuming requires that nothing after the checkpoint was ever trimmed.** Trimming keeps each
   reader's own position record, so on a stream that has lost entries, a position older than the
   first retained entry proves records the process needs are gone, and it refuses to start
   (`CheckpointRefused`) rather than rebuild a different state — which is precisely how the engine
   forked on 2026-09-16. No checkpoint means position `0-0`, so a fresh process on a trimmed
   stream refuses too.
3. **Streams trim only below the oldest checkpoint among their readers.** A reader that has never
   checkpointed pins the stream untrimmed. Memory grows before correctness is lost.

A checkpoint that cannot be used — corrupt, or written under a different `config_hash` — is not
an error by itself: when the stream is still untrimmed the process falls back to replaying from
genesis, exactly as before checkpointing existed. It is refused only when that fallback would be a
partial replay.

State is the process's own business: each passes bytes in and gets bytes back. It is compressed
here with stdlib zlib, which is enough for id-dense tables and adds no dependency.
"""

from __future__ import annotations

import json
import logging
import time
import zlib
from dataclasses import dataclass

from redis.asyncio import Redis

logger = logging.getLogger(__name__)

KEY_PREFIX = "qa.checkpoint."


class CheckpointRefused(RuntimeError):
    """Resuming would rebuild from an incomplete stream. The process must not start."""


@dataclass(frozen=True)
class Checkpoint:
    #: stream name → the last id this state has applied (exclusive resume point).
    positions: dict[str, str]
    state: bytes
    config_hash: str
    written_at_ns: int


def key(process: str) -> str:
    return KEY_PREFIX + process


def parse_id(stream_id: str | bytes) -> tuple[int, int]:
    text = stream_id.decode() if isinstance(stream_id, bytes) else stream_id
    ms, _, seq = text.partition("-")
    return int(ms), int(seq or 0)


async def save(
    redis: Redis, process: str, *, positions: dict[str, str], state: bytes, config_hash: str
) -> None:
    await redis.hset(
        key(process),
        mapping={
            b"positions": json.dumps(positions, sort_keys=True).encode(),
            b"state": zlib.compress(state, 1),
            b"config_hash": config_hash.encode(),
            b"written_at_ns": str(time.time_ns()).encode(),
        },
    )


async def load(redis: Redis, process: str, *, config_hash: str) -> Checkpoint | None:
    """The process's checkpoint, or None if it has none it can use."""
    raw = await redis.hgetall(key(process))
    if not raw:
        return None
    try:
        written_under = raw[b"config_hash"].decode()
        if written_under != config_hash:
            logger.warning(
                "%s: checkpoint was written under config %s, running %s; not using it",
                process, written_under[:12], config_hash[:12],
            )
            return None
        return Checkpoint(
            positions=json.loads(raw[b"positions"]),
            state=zlib.decompress(raw[b"state"]),
            config_hash=written_under,
            written_at_ns=int(raw[b"written_at_ns"]),
        )
    except (KeyError, ValueError, zlib.error) as exc:
        logger.warning("%s: checkpoint is unreadable (%s); not using it", process, exc)
        return None


async def _stream_bounds(redis: Redis, stream: str) -> tuple[bool, str | None, str]:
    """(was anything ever removed, first retained id or None if empty, last generated id)."""
    if not await redis.exists(stream):
        return False, None, "0-0"
    info = {
        (k.decode() if isinstance(k, bytes) else k): v
        for k, v in (await redis.xinfo_stream(stream)).items()
    }

    def text(value) -> str:
        return value.decode() if isinstance(value, bytes) else str(value)

    # `max-deleted-entry-id` is updated by XDEL but NOT by XTRIM (checked on Redis 7.4), so the
    # count is what detects trimming: every entry ever added and no longer present was removed.
    removed = int(info["entries-added"]) > int(info["length"]) or text(
        info.get("max-deleted-entry-id", "0-0")
    ) != "0-0"
    first = info.get("first-entry")
    first_id = text(first[0]) if first else None
    return removed, first_id, text(info["last-generated-id"])


async def ensure_resumable(redis: Redis, process: str, stream: str, after_id: str) -> None:
    """Refuse unless every record newer than `after_id` is still on `stream`.

    `trim` never removes a reader's own position record, so on a stream that has lost entries a
    resume point is safe exactly when it is still at or after the first retained entry. A
    position that has itself been removed proves trimming went past it. Position `0-0` — no
    checkpoint — is therefore refused on any stream that has ever lost an entry.
    """
    removed, first_id, last_id = await _stream_bounds(redis, stream)
    if not removed:
        return
    floor = first_id if first_id is not None else last_id
    if parse_id(after_id) < parse_id(floor):
        raise CheckpointRefused(
            f"{process}: {stream} has been trimmed and now starts at {floor}, past this "
            f"process's resume point {after_id}. Replaying would rebuild from an incomplete "
            f"history and fork from every other process (Open Issue 020). Not starting."
        )


async def resume_positions(
    redis: Redis, process: str, streams: list[str], *, config_hash: str
) -> Checkpoint | None:
    """Load the checkpoint and apply the guard to every stream the process reads.

    Returns the checkpoint to resume from, or None to replay from genesis — and in the None case
    the guard has already confirmed that genesis is still there.
    """
    checkpoint = await load(redis, process, config_hash=config_hash)
    for stream in streams:
        position = checkpoint.positions.get(stream, "0-0") if checkpoint else "0-0"
        await ensure_resumable(redis, process, stream, position)
    return checkpoint


async def trim(redis: Redis, stream: str, readers: list[str]) -> str | None:
    """Trim `stream` below the oldest resume point among `readers`. Returns the MINID used.

    Nothing is trimmed while any reader lacks a checkpoint for this stream. The MINID itself is
    kept (`XTRIM MINID` removes strictly lower ids), which costs one record and means a reader
    positioned exactly there never sees its own position deleted.
    """
    oldest: tuple[int, int] | None = None
    for reader in readers:
        raw = await redis.hget(key(reader), b"positions")
        if raw is None:
            return None
        position = json.loads(raw).get(stream)
        if position is None:
            return None
        parsed = parse_id(position)
        oldest = parsed if oldest is None else min(oldest, parsed)
    if oldest is None or oldest == (0, 0):
        return None
    minid = f"{oldest[0]}-{oldest[1]}"
    await redis.xtrim(stream, minid=minid, approximate=True)
    return minid
