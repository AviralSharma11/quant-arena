"""The archiver against a real Redis stream.

The unit tests prove the state machine and the writer; this proves they are wired together and
that the resume path reads what the write path recorded. Against the real store rather than a
fake, for the same reason the fan-out and gateway suites are: the property under test is that
**the stream is sufficient**, and a fake would pass while that was false.
"""

from __future__ import annotations

import pyarrow.dataset as ds
import pytest
import redis as redis_sync
from redis.asyncio import Redis

from config.settings import Settings
from contracts.v1.generated.contracts import Fill
from services.archiver.runner import Archiver
from services.archiver.writer import ArchiveWriter
from services.gateway.streams import RECORD_FIELD
from tests.archiver.conftest import session

pytestmark = pytest.mark.anyio


def seed_outbound(settings: Settings, records: list) -> int:
    client = redis_sync.Redis.from_url(settings.redis_url)
    try:
        for record in records:
            client.xadd(settings.stream_outbound, {RECORD_FIELD: record.pack()})
    finally:
        client.close()
    return len(records)


def build(redis, settings, root) -> Archiver:
    return Archiver(redis, settings, ArchiveWriter(root, symbols=settings.symbols))


async def drain(archiver: Archiver) -> int:
    """Read until the stream is exhausted, then seal the final unit."""
    total = 0
    while True:
        handled = await archiver.step()
        if not handled:
            break
        total += handled
    archiver.finish()
    return total


def archived_seqs(root) -> list[str]:
    table = ds.dataset(root / "trades", format="parquet", partitioning="hive").to_table()
    return table.column("seq").to_pylist()


async def test_a_session_on_a_real_stream_reaches_disk(
    test_settings: Settings, clean_redis, archive_root
):
    records = session(seconds=130, symbols=(1, 2))
    written = seed_outbound(test_settings, records)

    redis = Redis.from_url(test_settings.redis_url, decode_responses=False)
    try:
        archiver = build(redis, test_settings, archive_root)
        assert archiver.resume() == "0-0", "a fresh archive has no checkpoint"
        handled = await drain(archiver)
    finally:
        await redis.aclose()

    assert handled == written
    fills = sum(1 for record in records if isinstance(record, Fill))
    assert len(archived_seqs(archive_root)) == fills
    assert archiver.writer.read_checkpoint() is not None


async def test_a_second_process_over_the_same_archive_adds_nothing(
    test_settings: Settings, clean_redis, archive_root
):
    """The idempotence that makes a restart safe, exercised through the process class.

    A second archiver resumes from the recorded checkpoint and sees only what came after it —
    which, with the stream exhausted, is nothing. The files must be untouched and no trade
    duplicated. If the checkpoint were inclusive-vs-exclusive wrong in either direction this test
    fails: one way loses the record at the boundary, the other archives it twice.
    """
    records = session(seconds=130, symbols=(1, 2))
    seed_outbound(test_settings, records)

    redis = Redis.from_url(test_settings.redis_url, decode_responses=False)
    try:
        first = build(redis, test_settings, archive_root)
        first.resume()
        await drain(first)
        before = archived_seqs(archive_root)
        recorded = first.writer.read_checkpoint()

        second = build(redis, test_settings, archive_root)
        assert second.resume() == recorded, "the resume point comes from the file, not from memory"
        assert await second.step() == 0, "nothing is left on the stream"
        second.finish()
    finally:
        await redis.aclose()

    after = archived_seqs(archive_root)
    assert sorted(after) == sorted(before)
    assert len(after) == len(set(after))


async def test_an_interrupted_process_resumes_from_its_checkpoint(
    test_settings: Settings, clean_redis, archive_root
):
    """Half the session, a restart, then the rest — the real shape of a redeploy mid-session."""
    records = session(seconds=130, symbols=(1, 2))
    half = len(records) // 2
    seed_outbound(test_settings, records[:half])

    redis = Redis.from_url(test_settings.redis_url, decode_responses=False)
    try:
        first = build(redis, test_settings, archive_root)
        first.resume()
        while await first.step():
            pass
        # No `finish()`: the process was killed, not stopped. Whatever had not sealed is lost
        # from memory and has to come back off the stream.

        seed_outbound(test_settings, records[half:])

        second = build(redis, test_settings, archive_root)
        second.resume()
        await drain(second)
    finally:
        await redis.aclose()

    fills = sum(1 for record in records if isinstance(record, Fill))
    archived = archived_seqs(archive_root)
    assert len(archived) == fills, "every fill is archived exactly once"
    assert len(archived) == len(set(archived))
