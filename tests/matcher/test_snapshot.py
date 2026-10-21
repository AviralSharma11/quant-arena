"""Open Issue 020, engine side: a snapshot plus the tail reproduces an uninterrupted run exactly.

Three claims, each against both engines:

1. **Continuation.** Apply a flow, snapshot at an arbitrary cut, restore into a fresh engine,
   apply the rest: the outputs equal the uninterrupted run's tail, and the final snapshots match.
2. **Parity.** The C++ worker's snapshot bytes equal the naive matcher's at every cut, so the
   naive model is the oracle for the format as well as for matching.
3. **Refusal.** A malformed snapshot, or a restore into an engine that has already applied
   something, is an error rather than a quietly different engine.
"""

from __future__ import annotations

import struct
import subprocess
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from contracts.v1.generated.contracts import (
    CancelOrder,
    Side,
    SubmitOrder,
    Tif,
    unpack_any,
)
from services.matcher.adapter import NaiveMatcher
from services.matcher.snapshot import REQUEST, EngineSnapshot
from tests.cpp_worker import cpp_worker  # noqa: F401 — pytest fixture

FRAME = struct.Struct("<I")
CASH = 1_000_000


@st.composite
def flows(draw) -> list:
    """Submits and cancels over a small price grid, so crosses, partial fills and deep queues are
    common. Client ids are drawn from a small pool per user, so a duplicate `client_order_id` —
    the case the snapshot's `indexed` flag exists for — occurs regularly."""
    records = []
    for index in range(draw(st.integers(min_value=1, max_value=60))):
        user_id = draw(st.integers(min_value=1, max_value=3))
        client_order_id = draw(st.integers(min_value=1, max_value=12))
        if draw(st.integers(min_value=0, max_value=3)) == 0:
            records.append(CancelOrder.new(
                timestamp_ns=index + 1,
                client_order_id=1_000 + index,
                user_id=user_id,
                target_client_order_id=client_order_id,
            ))
        else:
            records.append(SubmitOrder.new(
                timestamp_ns=index + 1,
                client_order_id=client_order_id,
                user_id=user_id,
                symbol_id=draw(st.integers(min_value=1, max_value=2)),
                side=int(draw(st.sampled_from([Side.BUY, Side.SELL]))),
                tif=int(draw(st.sampled_from([Tif.GTC, Tif.GTC, Tif.IOC]))),
                price_ticks=draw(st.integers(min_value=95, max_value=105)),
                qty=draw(st.integers(min_value=1, max_value=15)),
            ))
    return records


def naive_run(records, matcher: NaiveMatcher | None = None):
    matcher = matcher or NaiveMatcher(initial_cash_ticks=CASH)
    outputs = [out for record in records for out in matcher.apply(record)]
    return outputs, matcher


def cpp_session(worker: Path, frames: list[bytes]) -> list[list[bytes]]:
    """Send raw frames to one worker process; return each frame's response frames."""
    stdin = b"".join(FRAME.pack(len(f)) + f for f in frames)
    result = subprocess.run(
        [str(worker), "--initial-cash-ticks", str(CASH)], input=stdin, capture_output=True
    )
    responses, offset, out = [], 0, result.stdout
    for _ in frames:
        frame_outputs = []
        while offset + FRAME.size <= len(out):
            (length,) = FRAME.unpack_from(out, offset)
            offset += FRAME.size
            if length == 0:
                break
            frame_outputs.append(out[offset:offset + length])
            offset += length
        else:
            raise AssertionError(result.stderr.decode(errors="replace") or "worker ended early")
        responses.append(frame_outputs)
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    return responses


def cpp_records(responses: list[list[bytes]]) -> list:
    return [unpack_any(payload) for frame in responses for payload in frame]


@given(flows(), st.data())
@settings(max_examples=150, deadline=None)
def test_naive_snapshot_then_tail_equals_uninterrupted_run(records, data):
    cut = data.draw(st.integers(min_value=0, max_value=len(records)))
    expected, whole = naive_run(records)

    head, first = naive_run(records[:cut])
    restored = NaiveMatcher.restore(first.snapshot(), initial_cash_ticks=CASH)
    tail, restored = naive_run(records[cut:], restored)

    assert head + tail == expected
    assert restored.snapshot() == whole.snapshot()
    assert restored.resting() == whole.resting()


@given(flows(), st.data())
@settings(max_examples=100, deadline=None)
def test_cpp_snapshot_bytes_match_naive_and_restore_continues_exactly(cpp_worker, records, data):  # noqa: F811
    cut = data.draw(st.integers(min_value=0, max_value=len(records)))
    expected, whole = naive_run(records)
    _, at_cut = naive_run(records[:cut])

    # The same process: head, snapshot, rest, snapshot.
    first = cpp_session(
        cpp_worker,
        [r.pack() for r in records[:cut]] + [REQUEST] + [r.pack() for r in records[cut:]] + [REQUEST],
    )
    snapshot_at_cut = first[cut][0]
    assert snapshot_at_cut == at_cut.snapshot()
    assert first[-1][0] == whole.snapshot()

    # A fresh process restored from that snapshot continues byte for byte.
    second = cpp_session(
        cpp_worker, [snapshot_at_cut] + [r.pack() for r in records[cut:]] + [REQUEST]
    )
    assert second[0] == []
    assert cpp_records(second[1:-1]) == expected[len(naive_run(records[:cut])[0]):]
    assert second[-1][0] == whole.snapshot()


def test_restore_refuses_an_engine_that_has_applied_records(cpp_worker):  # noqa: F811
    order = SubmitOrder.new(
        timestamp_ns=1, client_order_id=1, user_id=1, symbol_id=1,
        side=int(Side.BUY), tif=int(Tif.GTC), price_ticks=100, qty=1,
    )
    snapshot = naive_run([order])[1].snapshot()
    result = subprocess.run(
        [str(cpp_worker), "--initial-cash-ticks", str(CASH)],
        input=b"".join(FRAME.pack(len(f)) + f for f in (order.pack(), snapshot)),
        capture_output=True,
    )
    assert result.returncode != 0
    assert b"fresh engine" in result.stderr


@pytest.mark.parametrize("damage", ["truncate", "bad_magic", "unsorted"])
def test_malformed_snapshots_are_rejected(damage):
    orders = [
        SubmitOrder.new(timestamp_ns=i, client_order_id=i, user_id=1, symbol_id=1,
                        side=int(Side.BUY), tif=int(Tif.GTC), price_ticks=90 + i, qty=1)
        for i in (1, 2)
    ]
    data = bytearray(naive_run(orders)[1].snapshot())
    if damage == "truncate":
        data = data[:-1]
    elif damage == "bad_magic":
        data[:4] = b"XXXX"
    else:
        size = 52
        first, second = data[16:16 + size], data[16 + size:]
        data = data[:16] + second + first
    with pytest.raises(ValueError):
        EngineSnapshot.unpack(bytes(data))
