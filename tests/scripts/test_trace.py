"""The `trace` tool — the claim that the event stream is already the trace, tested.

Open Issue 012 §5 rejects OpenTelemetry on the grounds that an order's path is a query over the
log rather than a second system. These tests are that claim held to account: given a log, does
the query actually reconstruct the path, and does it refuse to invent one when it cannot?

A hand-rolled in-memory Redis, in the style of `tests/ledger/test_consumer.py`, because the
three commands `trace` uses — `GET`, `XRANGE`, `XREVRANGE` — are small enough to model exactly
and a real Redis would make this a slow test of Redis.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from contracts.v1.generated.contracts import Fill, OrderAccepted, SubmitOrder
from services.gateway.idempotency import IDEMPOTENCY_PREFIX
from services.gateway.streams import RECORD_FIELD

REPO_ROOT = Path(__file__).resolve().parents[2]

#: `scripts/` is a directory of runnable tools, not a package, so the module is loaded by path
#: rather than imported. Adding an `__init__.py` purely to satisfy a test would change what the
#: directory is for.
_spec = importlib.util.spec_from_file_location("qa_trace", REPO_ROOT / "scripts" / "trace.py")
trace_mod = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
# Registered before exec: @dataclass resolves its own module through sys.modules, and a module
# that is not there yet fails at class-creation time rather than anywhere useful.
sys.modules["qa_trace"] = trace_mod
_spec.loader.exec_module(trace_mod)

pytestmark = pytest.mark.anyio

INBOUND = "qa.inbound"
OUTBOUND = "qa.outbound"


class Settings:
    stream_inbound = INBOUND
    stream_outbound = OUTBOUND


class FakeRedis:
    """`GET`, `XRANGE` and `XREVRANGE` over in-memory streams, with exclusive ranges."""

    def __init__(self) -> None:
        self.keys: dict[str, bytes] = {}
        self.streams: dict[str, list[tuple[str, dict[bytes, bytes]]]] = {}

    def add(self, stream: str, stream_id: str, record) -> None:
        self.streams.setdefault(stream, []).append(
            (stream_id, {RECORD_FIELD: record.pack()})
        )

    @staticmethod
    def _key(stream_id: str) -> tuple[int, int]:
        ms, _, ordinal = stream_id.partition("-")
        return int(ms), int(ordinal or 0)

    def _bound(self, token: str, *, default: tuple[int, int]) -> tuple[tuple[int, int], bool]:
        if token in ("-", "+"):
            return default, False
        exclusive = token.startswith("(")
        token = token.lstrip("(")
        if "-" not in token:
            token = f"{token}-0"
        return self._key(token), exclusive

    async def get(self, key):
        return self.keys.get(key)

    async def xrange(self, stream, min="-", max="+", count=None):
        low, low_excl = self._bound(min, default=(0, 0))
        high, _ = self._bound(max, default=(2**63, 2**63))
        out = []
        for stream_id, fields in self.streams.get(stream, []):
            key = self._key(stream_id)
            if key < low or (low_excl and key == low) or key > high:
                continue
            out.append((stream_id, fields))
        return out[:count] if count else out

    async def xrevrange(self, stream, max="+", min="-", count=None):
        entries = await self.xrange(stream, min=min, max=max)
        entries.reverse()
        return entries[:count] if count else entries

    async def aclose(self):
        return None


def submit(coid: int, user: int, ts_ns: int) -> SubmitOrder:
    return SubmitOrder.new(
        timestamp_ns=ts_ns,
        client_order_id=coid,
        user_id=user,
        price_ticks=100,
        qty=5,
        symbol_id=1,
        side=1,
        tif=1,
    )


def accepted(coid: int, user: int, order_id: int, ts_ns: int) -> OrderAccepted:
    return OrderAccepted.new(
        timestamp_ns=ts_ns,
        order_id=order_id,
        client_order_id=coid,
        user_id=user,
        price_ticks=100,
        qty=5,
        symbol_id=1,
        side=1,
        tif=1,
    )


async def run(redis, coid, user=None, back=10_000, forward=10_000):
    return await trace_mod.build_trace(redis, Settings(), coid, user, back, forward)


async def test_the_path_is_reconstructed_from_the_log_alone():
    """Gateway stamp, durable append, engine answer — in that order, from three records."""
    redis = FakeRedis()
    redis.add(INBOUND, "1000-0", submit(77, user=42, ts_ns=1_000_000_400))
    redis.add(OUTBOUND, "1002-0", accepted(77, user=42, order_id=900, ts_ns=1_000_000_400))

    result = await run(redis, 77, user=42)

    assert [hop.name for hop in result.hops] == [
        "gateway accepted",
        "inbound append",
        "engine output",
    ]
    assert result.inbound_id == "1000-0"
    assert result.order_id == 900
    # The gateway's stamp is the only nanosecond figure; both appends are millisecond buckets.
    assert [hop.coarse for hop in result.hops] == [False, True, True]


async def test_a_fill_is_joined_through_the_engine_assigned_order_id():
    """A Fill names order ids and never a client_order_id (Open Issue 008's two identifiers),
    so it is only reachable once the acknowledgement has handed over the engine's id."""
    redis = FakeRedis()
    redis.add(INBOUND, "1000-0", submit(77, user=42, ts_ns=1_000_000_000))
    redis.add(OUTBOUND, "1002-0", accepted(77, user=42, order_id=900, ts_ns=1_000_000_000))
    redis.add(
        OUTBOUND,
        "1003-0",
        Fill.new(
            timestamp_ns=1_000_000_000,
            maker_order_id=555,
            taker_order_id=900,
            maker_user_id=7,
            taker_user_id=42,
            price_ticks=100,
            qty=5,
            symbol_id=1,
            aggressor_side=1,
        ),
    )
    # A fill between two strangers must not be swept up just because it is nearby.
    redis.add(
        OUTBOUND,
        "1004-0",
        Fill.new(
            timestamp_ns=1_000_000_000,
            maker_order_id=111,
            taker_order_id=222,
            maker_user_id=7,
            taker_user_id=8,
            price_ticks=100,
            qty=1,
            symbol_id=1,
            aggressor_side=1,
        ),
    )

    result = await run(redis, 77, user=42)

    fills = [hop for hop in result.hops if hop.name == "fill"]
    assert len(fills) == 1
    assert "taker=900" in fills[0].detail


async def test_a_gateway_rejection_is_reported_rather_than_looked_for_in_the_stream():
    """The case the stream physically cannot answer. A refused order never becomes durable, so
    'absent from the log' and 'refused before the log' are the same observation without the
    gateway's own record of the outcome."""
    redis = FakeRedis()
    redis.keys[f"{IDEMPOTENCY_PREFIX}42:77"] = json.dumps(
        {"status": "rejected", "reason": "INSUFFICIENT_CASH"}
    ).encode()

    result = await run(redis, 77, user=42)

    assert result.hops == []
    assert result.gateway_outcome["reason"] == "INSUFFICIENT_CASH"
    assert any("never reaches the stream" in note for note in result.notes)


async def test_one_client_order_id_from_two_users_is_refused_not_guessed():
    """`client_order_id` is unique per user, not globally (Open Issue 008). Picking one would
    produce a confident trace of the wrong order, which is worse than no answer."""
    redis = FakeRedis()
    redis.add(INBOUND, "1000-0", submit(77, user=42, ts_ns=1_000_000_000))
    redis.add(INBOUND, "1001-0", submit(77, user=99, ts_ns=1_000_000_000))

    result = await run(redis, 77)

    assert result.hops == []
    assert {match["user_id"] for match in result.ambiguous} == {42, 99}
    assert any("--user" in note for note in result.notes)

    # With the user named, the ambiguity is gone and the right order is traced.
    narrowed = await run(redis, 77, user=99)
    assert narrowed.inbound_id == "1001-0"
    assert narrowed.ambiguous == []


async def test_an_order_outside_the_scanned_window_says_so():
    """A bounded scan that found nothing reports what it looked at. 'Not found in the last N'
    is a different claim from 'does not exist', and the tool may only make the first."""
    redis = FakeRedis()
    redis.add(INBOUND, "1000-0", submit(77, user=42, ts_ns=1_000_000_000))

    result = await run(redis, 424242)

    assert result.hops == []
    assert any("Scanned back" in note for note in result.notes)
    assert any("older than the scanned window" in note for note in result.notes)
