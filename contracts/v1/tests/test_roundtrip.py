"""Success Criterion 2 — a record round-trips Python -> bytes -> Python with every field
identical.

Hypothesis rather than a handful of examples, because the failure this guards against is a
format-string character in the wrong place, which fixed sample values hide: 0 and 1 pack
identically under many wrong formats. Boundary values and sign do not.
"""

import struct

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from _schema import load_schema
from contracts.v1.generated import contracts

SCHEMA = load_schema()
HEADER_LEN = len(SCHEMA["header"]["fields"])

RANGES = {
    "u8": (0, 2**8 - 1),
    "u16": (0, 2**16 - 1),
    "i16": (-(2**15), 2**15 - 1),
    "u64": (0, 2**64 - 1),
    "i64": (-(2**63), 2**63 - 1),
}

TYPE_BY_FIELD = {
    record["name"]: {
        f["name"]: f["type"]
        for f in list(SCHEMA["header"]["fields"]) + list(record["fields"])
    }
    for record in SCHEMA["records"]
}


def strategy_for(record_name: str, cls):
    """Every field independently across its full integer range, including both boundaries."""
    types = TYPE_BY_FIELD[record_name]
    return st.fixed_dictionaries(
        {name: st.integers(*RANGES[types[name]]) for name in cls._fields}
    )


# Marked by hand, unlike every other generated test in the repository. The root conftest.py
# stamps `property` on anything Hypothesis has wrapped, but here `@given` wraps the *inner*
# `check`, so the collected function carries no Hypothesis attribute for that hook to find.
# 300 examples per record class is a generated test whichever function holds the decorator, and
# Task 3.3 keeps those out of the per-commit suite. `test_packed_length_is_exactly_size` and
# `test_extreme_values_survive` are deterministic and stay there, so per-commit still fails on
# a wrong width or signedness.
@pytest.mark.property
@pytest.mark.parametrize("cls", contracts.ALL_RECORDS, ids=lambda c: c.__name__)
def test_roundtrip_is_lossless(cls):
    @given(values=strategy_for(cls.__name__, cls))
    @settings(max_examples=300, deadline=None)
    def check(values):
        original = cls(**values)
        restored = cls.unpack(original.pack())
        assert restored == original
        for name in cls._fields:
            assert getattr(restored, name) == getattr(original, name), name

    check()


@pytest.mark.parametrize("cls", contracts.ALL_RECORDS, ids=lambda c: c.__name__)
def test_packed_length_is_exactly_size(cls):
    values = {name: 0 for name in cls._fields}
    assert len(cls(**values).pack()) == cls.SIZE
    assert struct.calcsize(cls.FORMAT) == cls.SIZE


@pytest.mark.parametrize("cls", contracts.ALL_RECORDS, ids=lambda c: c.__name__)
def test_extreme_values_survive(cls):
    """The boundaries specifically — a wrong width or signedness fails here even if the
    random search happened to miss it."""
    types = TYPE_BY_FIELD[cls.__name__]
    for pick in (min, max):
        values = {name: pick(RANGES[types[name]]) for name in cls._fields}
        original = cls(**values)
        assert cls.unpack(original.pack()) == original


@pytest.mark.parametrize("cls", contracts.ALL_RECORDS, ids=lambda c: c.__name__)
def test_out_of_range_values_are_refused_not_truncated(cls):
    """A silently truncated field in the money path is the whole reason this schema exists."""
    types = TYPE_BY_FIELD[cls.__name__]
    for name in cls._fields:
        low, high = RANGES[types[name]]
        values = {other: 0 for other in cls._fields}
        values[name] = high + 1
        with pytest.raises(struct.error):
            cls(**values).pack()
        values[name] = low - 1
        with pytest.raises(struct.error):
            cls(**values).pack()


@pytest.mark.parametrize("cls", contracts.ALL_RECORDS, ids=lambda c: c.__name__)
def test_unpack_any_dispatches_on_record_type(cls):
    record = cls.new(
        timestamp_ns=1_700_000_000_000_000_000,
        **{name: 1 for name in cls._fields[HEADER_LEN:]},
    )
    data = record.pack()
    assert contracts.peek_record_type(data) == cls.RECORD_TYPE
    assert contracts.unpack_any(data) == record


def test_unpack_any_refuses_an_unknown_record_type():
    data = bytearray(contracts.SubmitOrder.SIZE)
    struct.pack_into("<H", data, contracts.SubmitOrder.OFFSETS["record_type"], 9999)
    with pytest.raises(ValueError, match="unknown record_type"):
        contracts.unpack_any(bytes(data))


# --- seq is derived from the Redis stream id, never authored ---------------------------------


@pytest.mark.parametrize("cls", contracts.ALL_RECORDS, ids=lambda c: c.__name__)
def test_new_leaves_seq_unassigned(cls):
    record = cls.new(
        timestamp_ns=7, **{name: 0 for name in cls._fields[HEADER_LEN:]}
    )
    assert record.seq_ms == contracts.SEQ_UNASSIGNED
    assert record.seq_ord == contracts.SEQ_UNASSIGNED


@given(ms=st.integers(0, 2**64 - 1), ordinal=st.integers(0, 2**64 - 1))
def test_with_seq_applies_the_stream_id_and_changes_nothing_else(ms, ordinal):
    record = contracts.SubmitOrder.new(
        timestamp_ns=7, client_order_id=1, user_id=2, price_ticks=3, qty=4,
        symbol_id=5, side=contracts.Side.BUY, tif=contracts.Tif.GTC,
    )
    stamped = contracts.with_seq(record, f"{ms}-{ordinal}")
    assert (stamped.seq_ms, stamped.seq_ord) == (ms, ordinal)
    assert stamped._replace(seq_ms=0, seq_ord=0) == record
    assert contracts.SubmitOrder.unpack(stamped.pack()) == stamped


def test_with_seq_accepts_the_bytes_redis_actually_returns():
    record = contracts.BookChanged.new(
        timestamp_ns=1, price_ticks=100, qty_at_level=5, symbol_id=2,
        side=contracts.Side.SELL,
    )
    assert contracts.with_seq(record, b"1693526400000-3").seq_ord == 3
