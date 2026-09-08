"""Criterion 3: restarting the archiver resumes with neither a gap nor a duplicate.

This is the criterion the low-water checkpoint exists for, so it is tested the way it will
actually fail: a run is cut off at an arbitrary record, a **fresh** state machine and writer are
built over the same directory — no in-memory state survives, exactly as a restarted process has
none — and the resume point comes from the checkpoint file and nowhere else.

The assertion is set equality against the source of truth: the trades on disk after the
interrupted-then-resumed run must be exactly the `Fill` records of the session, each once. A gap
shows up as a missing element and a duplicate as a repeated one, so one comparison covers both
halves of the criterion.

Two cut points are exercised on purpose:

- mid-unit, the ordinary case;
- after files have been written but *before* the checkpoint moved, which is the window a crash
  actually leaves. That one is only safe because the file path is a pure function of the data, so
  the re-derived unit overwrites identical rows instead of appending them.
"""

from __future__ import annotations

import pyarrow.dataset as ds
import pytest

from contracts.v1.generated.contracts import Fill
from services.archiver.state import ArchiveState, parse_stream_id
from services.archiver.writer import ArchiveWriter
from tests.archiver.conftest import session, stream_ids

FLUSH = 60


def fresh(settings, root, *, flush_seconds: int = FLUSH):
    """A state machine and writer with nothing carried over — a restarted process."""
    state = ArchiveState(
        bar_widths=settings.bar_bucket_seconds,
        book_depth=settings.book_depth,
        flush_seconds=flush_seconds,
    )
    return state, ArchiveWriter(root, symbols=settings.symbols)


def pump(state, writer, paired, *, stop_after: int | None = None, finish: bool = False) -> int:
    """Apply records, writing each unit as it seals. Returns how many were applied."""
    applied = 0
    for stream_id, record in paired:
        state.apply(record, stream_id=stream_id)
        applied += 1
        for unit in state.drain_ready():
            writer.write_unit(unit)
            writer.write_checkpoint(state.checkpoint())
        if stop_after is not None and applied >= stop_after:
            return applied
    if finish:
        state.finish()
        for unit in state.drain_ready():
            writer.write_unit(unit)
            writer.write_checkpoint(state.checkpoint())
    return applied


def archived_trade_seqs(root) -> list[str]:
    table = ds.dataset(root / "trades", format="parquet", partitioning="hive").to_table()
    return table.column("seq").to_pylist()


def expected_trade_seqs(paired) -> list[str]:
    return [sid for sid, record in paired if isinstance(record, Fill)]


def after(paired, checkpoint: str):
    """The records a resumed reader would receive: strictly after the checkpoint.

    This is `read_records(last_id=checkpoint)` in list form, and using the same exclusive rule is
    the point — a test that re-fed the checkpoint record itself would hide the off-by-one that
    `Unit.resume_after` exists to prevent.
    """
    cut = parse_stream_id(checkpoint)
    return [(sid, rec) for sid, rec in paired if parse_stream_id(sid) > cut]


@pytest.mark.parametrize("stop_after", [37, 120, 260])
def test_a_restart_loses_no_trade_and_repeats_none(archive_root, settings, stop_after):
    paired = stream_ids(session(seconds=130, symbols=(1, 2)))
    assert stop_after < len(paired), "the cut must land inside the session"

    state, writer = fresh(settings, archive_root)
    pump(state, writer, paired, stop_after=stop_after)
    del state, writer                                    # the process dies here

    state, writer = fresh(settings, archive_root)
    resumed_from = writer.read_checkpoint() or "0-0"
    pump(state, writer, after(paired, resumed_from), finish=True)

    archived = archived_trade_seqs(archive_root)
    expected = expected_trade_seqs(paired)
    assert sorted(archived) == sorted(expected)          # no gap, no duplicate
    assert len(archived) == len(set(archived)), "a seq appearing twice is a duplicated trade"


def test_a_crash_after_writing_but_before_the_checkpoint_moves_is_idempotent(
    archive_root, settings
):
    """The one window the ordering in `Archiver.step` deliberately leaves open.

    Files are written, then the checkpoint moves. A crash in between means the next run re-derives
    units whose files already exist — and because the path is computed from the unit's own start
    time, it rewrites the same rows to the same path rather than adding a second file.
    """
    paired = stream_ids(session(seconds=130, symbols=(1, 2)))

    state, writer = fresh(settings, archive_root)
    # Write units but never record a checkpoint: the crash-shaped run.
    for stream_id, record in paired:
        state.apply(record, stream_id=stream_id)
        for unit in state.drain_ready():
            writer.write_unit(unit)
    files_before = sorted(p.name for p in archive_root.rglob("*.parquet"))
    assert files_before, "the interrupted run must have written something"
    assert writer.read_checkpoint() is None, "no checkpoint was recorded"

    state, writer = fresh(settings, archive_root)
    pump(state, writer, paired, finish=True)             # replays from the beginning

    archived = archived_trade_seqs(archive_root)
    assert sorted(archived) == sorted(expected_trade_seqs(paired))
    assert len(archived) == len(set(archived))


def test_resume_is_a_no_op_when_nothing_was_written(archive_root, settings):
    state, writer = fresh(settings, archive_root)
    assert writer.read_checkpoint() is None
    pump(state, writer, stream_ids(session(seconds=130, symbols=(1,))), finish=True)
    assert writer.read_checkpoint() is not None


def test_an_unreadable_checkpoint_replays_rather_than_guessing(archive_root, settings):
    """A malformed checkpoint means "replay from 0-0", never "resume from somewhere plausible".

    A wrong resume point is a gap, and a gap in this archive is silent — nothing downstream would
    ever report it. Replaying is merely slow.
    """
    _state, writer = fresh(settings, archive_root)
    archive_root.mkdir(parents=True, exist_ok=True)
    writer.checkpoint_path.write_text("{ not json")
    assert writer.read_checkpoint() is None

    writer.checkpoint_path.write_text('{"something_else": 1}')
    assert writer.read_checkpoint() is None


def test_the_written_checkpoint_is_an_exclusive_resume_point(archive_root, settings):
    """Everything at or before the recorded ID is on disk; the next read starts after it.

    Stated as its own test because it is the invariant the two restart tests silently depend on,
    and because getting it wrong costs exactly one record per restart — the kind of loss that
    never announces itself.
    """
    paired = stream_ids(session(seconds=130, symbols=(1, 2)))
    state, writer = fresh(settings, archive_root)
    # Two thirds of the way in, rather than a record count: a fixed count that happens to land
    # inside the first flush unit records no checkpoint at all and the test proves nothing.
    stop_after = len(paired) * 2 // 3
    pump(state, writer, paired, stop_after=stop_after)

    recorded = writer.read_checkpoint()
    assert recorded is not None, "a unit must have sealed for there to be a checkpoint"
    cut = parse_stream_id(recorded)

    on_disk = set(archived_trade_seqs(archive_root))
    assert on_disk, "something must have been written for this to mean anything"
    assert all(parse_stream_id(seq) <= cut for seq in on_disk)

    # And nothing after the checkpoint has reached a file yet.
    still_pending = {
        sid for sid, record in paired[:stop_after]
        if isinstance(record, Fill) and parse_stream_id(sid) > cut
    }
    assert not (still_pending & on_disk)
