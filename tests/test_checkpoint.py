"""Open Issue 020: the checkpoint store, the resume guard, and trimming — against a real Redis.

Uses database 13 and flushes it, so it never touches the live stack's data in database 0.
"""

from __future__ import annotations

import pytest
from redis.asyncio import Redis

from services import checkpoint
from services.checkpoint import CheckpointRefused

HASH = "a" * 64

pytestmark = pytest.mark.anyio


@pytest.fixture(scope="module")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def redis():
    client = Redis.from_url("redis://localhost:6379/13", decode_responses=False)
    try:
        await client.ping()
    except Exception:  # noqa: BLE001
        pytest.skip("Redis is not reachable on localhost:6379")
    await client.flushdb()
    yield client
    await client.flushdb()
    await client.aclose()


async def _fill(redis, stream: str, n: int) -> list[str]:
    return [(await redis.xadd(stream, {b"r": b"x"})).decode() for _ in range(n)]


async def test_save_and_load_round_trip(redis):
    await checkpoint.save(redis, "ledger", positions={"s": "5-0"}, state=b"state" * 1000, config_hash=HASH)
    loaded = await checkpoint.load(redis, "ledger", config_hash=HASH)
    assert loaded is not None
    assert loaded.positions == {"s": "5-0"}
    assert loaded.state == b"state" * 1000


async def test_a_checkpoint_from_another_config_is_not_used(redis):
    await checkpoint.save(redis, "ledger", positions={"s": "5-0"}, state=b"x", config_hash=HASH)
    assert await checkpoint.load(redis, "ledger", config_hash="b" * 64) is None


async def test_untrimmed_stream_resumes_from_genesis_without_a_checkpoint(redis):
    await _fill(redis, "s", 10)
    assert await checkpoint.resume_positions(redis, "ledger", ["s"], config_hash=HASH) is None


async def test_trimmed_stream_without_a_checkpoint_is_refused(redis):
    await _fill(redis, "s", 10)
    await redis.xtrim("s", maxlen=5, approximate=False)
    with pytest.raises(CheckpointRefused, match="trimmed"):
        await checkpoint.resume_positions(redis, "ledger", ["s"], config_hash=HASH)


async def test_checkpoint_behind_the_trim_point_is_refused_and_ahead_of_it_resumes(redis):
    ids = await _fill(redis, "s", 10)
    await redis.xtrim("s", minid=ids[5], approximate=False)  # deletes ids[0..4]

    await checkpoint.save(redis, "ledger", positions={"s": ids[3]}, state=b"", config_hash=HASH)
    with pytest.raises(CheckpointRefused):
        await checkpoint.resume_positions(redis, "ledger", ["s"], config_hash=HASH)

    # A position that is itself gone was passed by the trim, so it is refused too.
    await checkpoint.save(redis, "ledger", positions={"s": ids[4]}, state=b"", config_hash=HASH)
    with pytest.raises(CheckpointRefused):
        await checkpoint.resume_positions(redis, "ledger", ["s"], config_hash=HASH)

    await checkpoint.save(redis, "ledger", positions={"s": ids[5]}, state=b"", config_hash=HASH)
    resumed = await checkpoint.resume_positions(redis, "ledger", ["s"], config_hash=HASH)
    assert resumed is not None and resumed.positions["s"] == ids[5]


async def test_trim_waits_for_every_reader_then_keeps_the_oldest_position(redis):
    ids = await _fill(redis, "s", 10)
    await checkpoint.save(redis, "a", positions={"s": ids[7]}, state=b"", config_hash=HASH)
    assert await checkpoint.trim(redis, "s", ["a", "b"]) is None  # b has no checkpoint
    assert await redis.xlen("s") == 10

    await checkpoint.save(redis, "b", positions={"s": ids[2]}, state=b"", config_hash=HASH)
    await redis.xadd("s", {b"r": b"x"}, id="*")
    assert await checkpoint.trim(redis, "s", ["a", "b"]) == ids[2]
    remaining = [entry[0].decode() for entry in await redis.xrange("s")]
    assert ids[2] in remaining  # approximate trimming may keep more, never less
    # Whatever it removed, both readers can still resume.
    for reader in ("a", "b"):
        await checkpoint.resume_positions(redis, reader, ["s"], config_hash=HASH)
