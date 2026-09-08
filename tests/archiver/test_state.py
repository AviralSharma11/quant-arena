"""The archiver's state machine: bucketing, sampling and the low-water checkpoint.

Unit level, no disk and no Redis. The criteria themselves are proved over real files in
`test_criteria.py`; what is checked here is the machinery those criteria rest on, and in
particular the checkpoint invariant, which is the one thing in this task that is easy to get
subtly wrong and impossible to notice afterwards:

> Every file on disk is complete for everything strictly before the checkpoint, and nothing at or
> after it has been written.
"""

from __future__ import annotations

import pytest

from contracts.v1.generated.contracts import Fill
from services.archiver.state import (
    ArchiveState,
    min_stream_id,
    parse_stream_id,
)
from services.fanout.bars import NS_PER_SECOND
from tests.archiver.conftest import ORIGIN_NS, session, stream_ids

FLUSH = 60


def drive(records, *, flush_seconds: int = FLUSH, **kwargs) -> ArchiveState:
    state = ArchiveState(flush_seconds=flush_seconds, **kwargs)
    for stream_id, record in stream_ids(records):
        state.apply(record, stream_id=stream_id)
    return state


# --- stream ID ordering -----------------------------------------------------------------------


def test_stream_ids_compare_numerically_not_lexicographically():
    assert parse_stream_id("1757337600123-4") == (1757337600123, 4)
    # The whole point: "10-0" sorts before "9-0" as text.
    assert min_stream_id(["10-0", "9-0"]) == "9-0"
    assert min_stream_id(["100-5", "100-2", "99-9"]) == "99-9"


def test_a_bare_millisecond_id_has_ordinal_zero():
    assert parse_stream_id("42") == (42, 0)


# --- construction guards ----------------------------------------------------------------------


def test_a_bar_width_that_does_not_divide_the_flush_unit_is_refused():
    """The invariant that makes a sealed unit complete is "no bucket is still open".

    A 7-second bar inside a 60-second unit breaks it: the bucket straddling the boundary is still
    taking trades when the unit is written. Refusing at construction turns a wrong file into a
    startup error.
    """
    with pytest.raises(ValueError, match="must divide the flush unit"):
        ArchiveState(bar_widths=(1, 7), flush_seconds=60)


def test_the_configured_widths_are_accepted(settings):
    """The real configuration must satisfy the constraint the archiver imposes."""
    ArchiveState(bar_widths=settings.bar_bucket_seconds, flush_seconds=FLUSH)


# --- bucketing --------------------------------------------------------------------------------


def test_units_are_cut_on_stream_time():
    state = ArchiveState(flush_seconds=FLUSH)
    assert state.unit_start(ORIGIN_NS) == ORIGIN_NS
    assert state.unit_start(ORIGIN_NS + 59 * NS_PER_SECOND) == ORIGIN_NS
    assert state.unit_start(ORIGIN_NS + 60 * NS_PER_SECOND) == ORIGIN_NS + 60 * NS_PER_SECOND


def test_a_unit_is_sealed_by_a_record_in_a_later_unit():
    state = drive(session(seconds=130, symbols=(1,)))
    ready = state.drain_ready()
    starts = [unit.start_ns for unit in ready]
    # 130 seconds spans three units; the third is still open, so two are sealed.
    assert starts == [ORIGIN_NS, ORIGIN_NS + 60 * NS_PER_SECOND]
    assert all(a < b for a, b in zip(starts, starts[1:])), "units must arrive ascending"


def test_nothing_is_sealed_while_only_one_unit_has_been_seen():
    state = drive(session(seconds=30, symbols=(1,)))
    assert state.drain_ready() == []
    # ...and `finish()` is what releases the final partial unit.
    state.finish()
    assert len(state.drain_ready()) == 1


def test_a_quiet_stretch_produces_no_unit_at_all():
    """A minute with no records is a minute with no file, not sixty identical snapshots."""
    records = session(seconds=1, symbols=(1,)) + session(
        seconds=1, symbols=(1,), start_ns=ORIGIN_NS + 600 * NS_PER_SECOND
    )
    state = drive(records)
    state.finish()
    starts = [unit.start_ns for unit in state.drain_ready()]
    assert starts == [ORIGIN_NS, ORIGIN_NS + 600 * NS_PER_SECOND]


# --- sampling ---------------------------------------------------------------------------------


def test_every_second_of_a_covered_unit_is_sampled_exactly_once():
    state = drive(session(seconds=130, symbols=(1,)))
    for unit in state.drain_ready():
        seconds = sorted(set(unit.rows["snapshots"]["second_ns"]))
        expected = [unit.start_ns + s * NS_PER_SECOND for s in range(FLUSH)]
        assert seconds == expected, unit.start_ns


def test_a_snapshot_reports_the_book_at_the_end_of_its_second():
    """Sampling happens before the arriving record is applied, which is what makes this true.

    A resting bid is placed in second 0 and nothing crosses it, so the snapshot for second 0 must
    show it — a sampler that ran *after* applying the next second's record would show the same
    thing, so the case that separates them is the one below: the bid is cancelled in second 1 and
    second 0 must still carry it.
    """
    from contracts.v1.generated.contracts import CancelOrder
    from services.matcher.adapter import NaiveMatcher
    from tests.archiver.conftest import BUY, submit

    matcher = NaiveMatcher(initial_cash_ticks=10_000_000_000)
    records = list(matcher.apply(submit(1, 10, BUY, 1_000, 5, ts=ORIGIN_NS)))
    records += list(
        matcher.apply(
            CancelOrder.new(
                timestamp_ns=ORIGIN_NS + NS_PER_SECOND,
                client_order_id=2,
                user_id=10,
                target_client_order_id=1,
            )
        )
    )
    state = drive(records)
    state.finish()
    unit = state.drain_ready()[0]
    rows = list(
        zip(unit.rows["snapshots"]["second_ns"], unit.rows["snapshots"]["qty"])
    )
    first_second = [qty for second, qty in rows if second == ORIGIN_NS]
    second_second = [qty for second, qty in rows if second == ORIGIN_NS + NS_PER_SECOND]
    assert first_second == [5], "the bid rested for the whole of second 0"
    assert second_second == [], "and was gone by the end of second 1"


# --- the checkpoint ---------------------------------------------------------------------------


def test_the_checkpoint_covers_every_sealed_record_and_no_open_one():
    records = session(seconds=130, symbols=(1,))
    paired = stream_ids(records)
    state = ArchiveState(flush_seconds=FLUSH)
    for stream_id, record in paired:
        state.apply(record, stream_id=stream_id)

    ready = state.drain_ready()
    assert ready, "two units should have sealed"

    # Every record in a sealed unit is at or before the checkpoint; every record in a unit still
    # open is strictly after it. That is the invariant, stated as an assertion. The checkpoint is
    # exclusive, so "at or before" is the correct comparison for the sealed side.
    checkpoint = parse_stream_id(state.checkpoint())
    sealed_ids = {seq for unit in ready for seq in unit.rows["trades"]["seq"]}
    assert sealed_ids, "the sealed units must contain trades to make this meaningful"
    assert all(parse_stream_id(i) <= checkpoint for i in sealed_ids)
    assert all(parse_stream_id(u.first_stream_id) > checkpoint for u in state._open.values())


def test_the_checkpoint_does_not_advance_past_an_open_unit():
    state = drive(session(seconds=130, symbols=(1,)))
    before = state.checkpoint()
    state.drain_ready()
    after = state.checkpoint()
    assert before == after, "draining sealed units must not move the low-water mark"
    assert parse_stream_id(after) < parse_stream_id(state.last_stream_id)


def test_with_nothing_open_the_checkpoint_is_the_last_record_applied():
    state = drive(session(seconds=130, symbols=(1,)))
    state.finish()
    state.drain_ready()
    assert state.checkpoint() == state.last_stream_id


def test_the_checkpoint_never_walks_backwards():
    state = ArchiveState(flush_seconds=FLUSH)
    seen = ["0-0"]
    for stream_id, record in stream_ids(session(seconds=200, symbols=(1, 2))):
        state.apply(record, stream_id=stream_id)
        if state.drain_ready():
            seen.append(state.checkpoint())
    assert seen == sorted(seen, key=parse_stream_id), seen


# --- records without a timestamp --------------------------------------------------------------


def test_a_record_with_no_timestamp_opens_no_unit_but_advances_the_id(settings):
    """`ConfigureReplay` is the first record on every session's inbound stream after Amendment 2.

    It carries no `timestamp_ns`, so it cannot be bucketed. It must not open a unit — a unit whose
    first ID is that record would pin the checkpoint before any market data existed — and it must
    still move `last_stream_id`, or a stream of nothing else would leave the resume point at
    `0-0` for ever.
    """
    class Stampless:
        pass

    state = ArchiveState(flush_seconds=FLUSH)
    state.apply(Stampless(), stream_id="1700000000000-0")
    assert state.drain_ready() == []
    assert state.checkpoint() == "1700000000000-0"
    assert state.records_applied == 1


# --- late arrivals ----------------------------------------------------------------------------


def test_a_row_for_a_written_unit_is_counted_not_misfiled():
    """Timestamps are expected non-decreasing; a stream that goes backwards is an anomaly.

    Filing the row into whichever unit happens to be open would put a trade under the wrong
    minute, which is a wrong archive that nothing would ever report. Dropping and counting it is
    visible in the snapshot line.
    """
    state = drive(session(seconds=130, symbols=(1,)))
    state.drain_ready()
    assert state.late_rows == 0

    # One record stamped an hour in the past, after its unit is long gone.
    late = session(seconds=1, symbols=(1,), start_ns=ORIGIN_NS)
    fills = [r for r in late if isinstance(r, Fill)]
    assert fills, "the generator must produce a fill for this to test anything"
    for record in late:
        state.apply(record, stream_id="1700000999999-0")
    assert state.late_rows > 0
