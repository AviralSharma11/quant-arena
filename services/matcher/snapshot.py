"""The engine snapshot — one byte format, produced identically by both engines (Open Issue 020).

A snapshot is everything a matcher needs to continue as if it had never stopped, and nothing
else. That turns out to be small, because of one property of the engine: **order ids are issued
in arrival order**. Time priority inside a price level, and which side of a cross set the trade
price, both follow arrival — so re-adding the resting orders in ascending `order_id` rebuilds
every queue exactly, and no internal structure (price levels, arrival counters) needs writing.

The C++ worker (`engine/cpp/stream_engine.cpp`) and `NaiveMatcher` both emit this format, and the
parity test asserts the bytes are equal. That makes the naive model the oracle for the snapshot
as well as for matching.

## Layout — little-endian, no padding

    magic          4s   b"QAS1"
    next_order_id  u64
    count          u32
    count × order:
      order_id         u64
      client_order_id  u64
      user_id          u64
      symbol_id        i16
      side             u8
      indexed          u8    1 if a cancel by (user_id, client_order_id) reaches this order
      price_ticks      i64
      remaining_qty    i64
      created_at_ns    i64

Orders are sorted by `order_id`. `indexed` exists for one edge: two live orders sharing a
`(user_id, client_order_id)` — the gateway's idempotency store prevents it, the engine does not —
leave only the newer one cancellable, and after that one leaves, neither. Recording the flag keeps
the restore exact rather than approximately right.

`initial_cash_ticks` is not in the snapshot. It is configuration, passed to the engine at start,
and a snapshot that carried it could disagree with the config hash the process announced.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

MAGIC = b"QAS1"
#: The control frame that asks a running engine for its snapshot.
REQUEST = b"QASN"

_HEAD = struct.Struct("<4sQI")
_ORDER = struct.Struct("<QQQhBBqqq")


@dataclass(frozen=True)
class SnapshotOrder:
    order_id: int
    client_order_id: int
    user_id: int
    symbol_id: int
    side: int
    indexed: bool
    price_ticks: int
    remaining_qty: int
    created_at_ns: int


@dataclass(frozen=True)
class EngineSnapshot:
    next_order_id: int
    orders: tuple[SnapshotOrder, ...]

    def pack(self) -> bytes:
        ordered = sorted(self.orders, key=lambda o: o.order_id)
        parts = [_HEAD.pack(MAGIC, self.next_order_id, len(ordered))]
        parts.extend(
            _ORDER.pack(
                o.order_id, o.client_order_id, o.user_id, o.symbol_id, o.side,
                1 if o.indexed else 0, o.price_ticks, o.remaining_qty, o.created_at_ns,
            )
            for o in ordered
        )
        return b"".join(parts)

    @classmethod
    def unpack(cls, data: bytes) -> "EngineSnapshot":
        if len(data) < _HEAD.size:
            raise ValueError("engine snapshot is shorter than its header")
        magic, next_order_id, count = _HEAD.unpack_from(data, 0)
        if magic != MAGIC:
            raise ValueError(f"not an engine snapshot (magic {magic!r})")
        if len(data) != _HEAD.size + count * _ORDER.size:
            raise ValueError("engine snapshot length does not match its order count")
        orders = []
        previous = 0
        for index in range(count):
            fields = _ORDER.unpack_from(data, _HEAD.size + index * _ORDER.size)
            order = SnapshotOrder(*fields[:5], bool(fields[5]), *fields[6:])
            if order.order_id <= previous or order.order_id >= next_order_id:
                raise ValueError("engine snapshot orders are not ascending below next_order_id")
            previous = order.order_id
            orders.append(order)
        return cls(next_order_id=next_order_id, orders=tuple(orders))
