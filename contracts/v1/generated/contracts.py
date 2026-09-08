# GENERATED FILE — DO NOT EDIT.
#
# Source:     contracts/v1/schema.toml
# Source sha: 2a6b0aa5c0e59e8e299ea6ef3265fd9536339ce4a5537b5886658c4b923079e1
# Regenerate: python contracts/v1/generate.py
#
# Hand-editing this file reintroduces exactly the C++/Python drift the generator exists to
# prevent. contracts/v1/tests/test_generated_is_current.py fails if you do.

"""Quant Arena wire records — generated from contracts/v1/schema.toml.

Standard library only, deliberately: every service depends on this module, so it must
not drag a dependency graph behind it.
"""

from __future__ import annotations

import struct
from enum import IntEnum
from typing import NamedTuple

SCHEMA_VERSION = 2

#: A record's seq is the Redis stream id, which does not exist until XADD returns.
#: Producers write SEQ_UNASSIGNED; consumers fill it in from the message id on read.
SEQ_UNASSIGNED = 0


class RecordType(IntEnum):
    """
    Record discriminator. Synthesised from the record list.
    """

    SUBMIT_ORDER = 1  # SubmitOrder
    CANCEL_ORDER = 2  # CancelOrder
    CREATE_ACCOUNT = 3  # CreateAccount
    CREDIT_CASH = 4  # CreditCash
    CONFIGURE_REPLAY = 5  # ConfigureReplay
    ORDER_ACCEPTED = 10  # OrderAccepted
    ORDER_REJECTED = 11  # OrderRejected
    FILL = 12  # Fill
    ORDER_CANCELLED = 13  # OrderCancelled
    BOOK_CHANGED = 14  # BookChanged
    ACCOUNT_CREATED = 15  # AccountCreated
    CASH_CREDITED = 16  # CashCredited
    REPLAY_CONFIGURED = 17  # ReplayConfigured


class Side(IntEnum):
    """
    Order side. Also used for Fill.aggressor_side.
    """

    BUY = 1
    SELL = 2


class Tif(IntEnum):
    """
    Time in force. GTC and IOC only — nothing else is meaningful without a session clock.
    """

    GTC = 1  # Good till cancelled.
    IOC = 2  # Immediate or cancel; any unfilled remainder is cancelled.


class CancelReason(IntEnum):
    """
    Why an order left the book. Distinguishes user-initiated from expiry.
    """

    USER_REQUESTED = 1
    IOC_EXPIRED = 2  # IOC remainder cancelled after aggressing.


class RejectReason(IntEnum):
    """
    Closed set. Adding a reason later is a schema change (Open Issue 016 section 10), so the
    full set is enumerated now even though the risk checks that raise most of them are week 3.
    """

    UNKNOWN_SYMBOL = 1
    INVALID_PRICE = 2
    INVALID_QUANTITY = 3
    INVALID_SIDE = 4
    INVALID_TIF = 5
    INSUFFICIENT_CASH = 6
    INSUFFICIENT_POSITION = 7
    UNKNOWN_ORDER = 8  # Cancel referenced a client_order_id with no live order.
    NOT_ORDER_OWNER = 9  # Cancel referenced another user's order.
    DUPLICATE_CLIENT_ORDER_ID = 10
    EXCHANGE_HALTED = 11
    ACCOUNT_NOT_FOUND = 12
    ACCOUNT_ALREADY_EXISTS = 13


class SubmitOrder(NamedTuple):
    """
    A new order. Matched by the engine.

    Direction: inbound. record_type = 1.
    """

    schema_version: int  # Version of this schema. Consumers accept the current version only.
    record_type: int  # Discriminator. Fixed offset across every record.
    seq_ms: int  # Redis stream id, millisecond part. 0 until assigned on read.
    seq_ord: int  # Redis stream id, ordinal part. 0 until assigned on read.
    timestamp_ns: int  # Gateway-assigned wall clock, nanoseconds.
    client_order_id: int  # Mandatory idempotency key, unique per user.
    user_id: int
    price_ticks: int
    qty: int
    symbol_id: int
    side: int
    tif: int

    FORMAT = "<HHQQqQQqqhBB"
    SIZE = 64
    RECORD_TYPE = 1
    OFFSETS = {
        "schema_version": 0,
        "record_type": 2,
        "seq_ms": 4,
        "seq_ord": 12,
        "timestamp_ns": 20,
        "client_order_id": 28,
        "user_id": 36,
        "price_ticks": 44,
        "qty": 52,
        "symbol_id": 60,
        "side": 62,
        "tif": 63,
    }

    @classmethod
    def new(cls, *, timestamp_ns: int, client_order_id: int, user_id: int, price_ticks: int, qty: int, symbol_id: int, side: int, tif: int) -> "SubmitOrder":
        """Build a record with the header filled in correctly and seq unassigned."""
        return cls(
            schema_version=SCHEMA_VERSION,
            record_type=1,
            seq_ms=SEQ_UNASSIGNED,
            seq_ord=SEQ_UNASSIGNED,
            timestamp_ns=timestamp_ns,
            client_order_id=client_order_id,
            user_id=user_id,
            price_ticks=price_ticks,
            qty=qty,
            symbol_id=symbol_id,
            side=side,
            tif=tif,
        )

    def pack(self) -> bytes:
        return _S_SubmitOrder.pack(*self)

    @classmethod
    def unpack(cls, data: bytes) -> "SubmitOrder":
        return cls(*_S_SubmitOrder.unpack(data))


_S_SubmitOrder = struct.Struct(SubmitOrder.FORMAT)
assert _S_SubmitOrder.size == SubmitOrder.SIZE, "SubmitOrder: format string and SIZE disagree"


class CancelOrder(NamedTuple):
    """
    Cancel by client order id — a client can cancel an order whose acknowledgement it never
    received.

    Direction: inbound. record_type = 2.
    """

    schema_version: int  # Version of this schema. Consumers accept the current version only.
    record_type: int  # Discriminator. Fixed offset across every record.
    seq_ms: int  # Redis stream id, millisecond part. 0 until assigned on read.
    seq_ord: int  # Redis stream id, ordinal part. 0 until assigned on read.
    timestamp_ns: int  # Gateway-assigned wall clock, nanoseconds.
    client_order_id: int  # Idempotency key of the cancel itself.
    user_id: int
    target_client_order_id: int  # The order being cancelled.

    FORMAT = "<HHQQqQQQ"
    SIZE = 52
    RECORD_TYPE = 2
    OFFSETS = {
        "schema_version": 0,
        "record_type": 2,
        "seq_ms": 4,
        "seq_ord": 12,
        "timestamp_ns": 20,
        "client_order_id": 28,
        "user_id": 36,
        "target_client_order_id": 44,
    }

    @classmethod
    def new(cls, *, timestamp_ns: int, client_order_id: int, user_id: int, target_client_order_id: int) -> "CancelOrder":
        """Build a record with the header filled in correctly and seq unassigned."""
        return cls(
            schema_version=SCHEMA_VERSION,
            record_type=2,
            seq_ms=SEQ_UNASSIGNED,
            seq_ord=SEQ_UNASSIGNED,
            timestamp_ns=timestamp_ns,
            client_order_id=client_order_id,
            user_id=user_id,
            target_client_order_id=target_client_order_id,
        )

    def pack(self) -> bytes:
        return _S_CancelOrder.pack(*self)

    @classmethod
    def unpack(cls, data: bytes) -> "CancelOrder":
        return cls(*_S_CancelOrder.unpack(data))


_S_CancelOrder = struct.Struct(CancelOrder.FORMAT)
assert _S_CancelOrder.size == CancelOrder.SIZE, "CancelOrder: format string and SIZE disagree"


class CreateAccount(NamedTuple):
    """
    Forwarded by the engine untouched — the engine is money-blind.

    Direction: inbound. record_type = 3.
    """

    schema_version: int  # Version of this schema. Consumers accept the current version only.
    record_type: int  # Discriminator. Fixed offset across every record.
    seq_ms: int  # Redis stream id, millisecond part. 0 until assigned on read.
    seq_ord: int  # Redis stream id, ordinal part. 0 until assigned on read.
    timestamp_ns: int  # Gateway-assigned wall clock, nanoseconds.
    client_order_id: int  # Idempotency key.
    user_id: int

    FORMAT = "<HHQQqQQ"
    SIZE = 44
    RECORD_TYPE = 3
    OFFSETS = {
        "schema_version": 0,
        "record_type": 2,
        "seq_ms": 4,
        "seq_ord": 12,
        "timestamp_ns": 20,
        "client_order_id": 28,
        "user_id": 36,
    }

    @classmethod
    def new(cls, *, timestamp_ns: int, client_order_id: int, user_id: int) -> "CreateAccount":
        """Build a record with the header filled in correctly and seq unassigned."""
        return cls(
            schema_version=SCHEMA_VERSION,
            record_type=3,
            seq_ms=SEQ_UNASSIGNED,
            seq_ord=SEQ_UNASSIGNED,
            timestamp_ns=timestamp_ns,
            client_order_id=client_order_id,
            user_id=user_id,
        )

    def pack(self) -> bytes:
        return _S_CreateAccount.pack(*self)

    @classmethod
    def unpack(cls, data: bytes) -> "CreateAccount":
        return cls(*_S_CreateAccount.unpack(data))


_S_CreateAccount = struct.Struct(CreateAccount.FORMAT)
assert _S_CreateAccount.size == CreateAccount.SIZE, "CreateAccount: format string and SIZE disagree"


class CreditCash(NamedTuple):
    """
    Forwarded by the engine untouched.

    Direction: inbound. record_type = 4.
    """

    schema_version: int  # Version of this schema. Consumers accept the current version only.
    record_type: int  # Discriminator. Fixed offset across every record.
    seq_ms: int  # Redis stream id, millisecond part. 0 until assigned on read.
    seq_ord: int  # Redis stream id, ordinal part. 0 until assigned on read.
    timestamp_ns: int  # Gateway-assigned wall clock, nanoseconds.
    client_order_id: int  # Idempotency key.
    user_id: int
    amount_ticks: int

    FORMAT = "<HHQQqQQq"
    SIZE = 52
    RECORD_TYPE = 4
    OFFSETS = {
        "schema_version": 0,
        "record_type": 2,
        "seq_ms": 4,
        "seq_ord": 12,
        "timestamp_ns": 20,
        "client_order_id": 28,
        "user_id": 36,
        "amount_ticks": 44,
    }

    @classmethod
    def new(cls, *, timestamp_ns: int, client_order_id: int, user_id: int, amount_ticks: int) -> "CreditCash":
        """Build a record with the header filled in correctly and seq unassigned."""
        return cls(
            schema_version=SCHEMA_VERSION,
            record_type=4,
            seq_ms=SEQ_UNASSIGNED,
            seq_ord=SEQ_UNASSIGNED,
            timestamp_ns=timestamp_ns,
            client_order_id=client_order_id,
            user_id=user_id,
            amount_ticks=amount_ticks,
        )

    def pack(self) -> bytes:
        return _S_CreditCash.pack(*self)

    @classmethod
    def unpack(cls, data: bytes) -> "CreditCash":
        return cls(*_S_CreditCash.unpack(data))


_S_CreditCash = struct.Struct(CreditCash.FORMAT)
assert _S_CreditCash.size == CreditCash.SIZE, "CreditCash: format string and SIZE disagree"


class ConfigureReplay(NamedTuple):
    """
    The replay clock and configuration hash, written by the gateway at startup. Forwarded by the
    engine untouched so this configuration has a position in the total order and preserves the
    one-anchor-per-inbound-record recovery invariant.

    Direction: inbound. record_type = 5.
    """

    schema_version: int  # Version of this schema. Consumers accept the current version only.
    record_type: int  # Discriminator. Fixed offset across every record.
    seq_ms: int  # Redis stream id, millisecond part. 0 until assigned on read.
    seq_ord: int  # Redis stream id, ordinal part. 0 until assigned on read.
    timestamp_ns: int  # Gateway-assigned wall clock, nanoseconds.
    client_order_id: int  # Reserved startup idempotency key.
    real_seconds_per_simulated_minute: int
    config_hash_hi: int  # First 8 bytes of the SHA-256, big-endian.
    config_hash_lo: int  # Next 8 bytes of the SHA-256, big-endian.

    FORMAT = "<HHQQqQqQQ"
    SIZE = 60
    RECORD_TYPE = 5
    OFFSETS = {
        "schema_version": 0,
        "record_type": 2,
        "seq_ms": 4,
        "seq_ord": 12,
        "timestamp_ns": 20,
        "client_order_id": 28,
        "real_seconds_per_simulated_minute": 36,
        "config_hash_hi": 44,
        "config_hash_lo": 52,
    }

    @classmethod
    def new(cls, *, timestamp_ns: int, client_order_id: int, real_seconds_per_simulated_minute: int, config_hash_hi: int, config_hash_lo: int) -> "ConfigureReplay":
        """Build a record with the header filled in correctly and seq unassigned."""
        return cls(
            schema_version=SCHEMA_VERSION,
            record_type=5,
            seq_ms=SEQ_UNASSIGNED,
            seq_ord=SEQ_UNASSIGNED,
            timestamp_ns=timestamp_ns,
            client_order_id=client_order_id,
            real_seconds_per_simulated_minute=real_seconds_per_simulated_minute,
            config_hash_hi=config_hash_hi,
            config_hash_lo=config_hash_lo,
        )

    def pack(self) -> bytes:
        return _S_ConfigureReplay.pack(*self)

    @classmethod
    def unpack(cls, data: bytes) -> "ConfigureReplay":
        return cls(*_S_ConfigureReplay.unpack(data))


_S_ConfigureReplay = struct.Struct(ConfigureReplay.FORMAT)
assert _S_ConfigureReplay.size == ConfigureReplay.SIZE, "ConfigureReplay: format string and SIZE disagree"


class OrderAccepted(NamedTuple):
    """
    Order is live in the book. Carries price/qty/side/symbol because there are no snapshots
    (Open Issue 018 section 13.1): the ledger rebuilds open orders by replaying the OUTBOUND
    stream alone, and without these it would have to join against the inbound stream.

    Direction: outbound. record_type = 10.
    """

    schema_version: int  # Version of this schema. Consumers accept the current version only.
    record_type: int  # Discriminator. Fixed offset across every record.
    seq_ms: int  # Redis stream id, millisecond part. 0 until assigned on read.
    seq_ord: int  # Redis stream id, ordinal part. 0 until assigned on read.
    timestamp_ns: int  # Gateway-assigned wall clock, nanoseconds.
    order_id: int  # Engine-assigned.
    client_order_id: int
    user_id: int
    price_ticks: int
    qty: int  # Quantity accepted onto the book.
    symbol_id: int
    side: int
    tif: int

    FORMAT = "<HHQQqQQQqqhBB"
    SIZE = 72
    RECORD_TYPE = 10
    OFFSETS = {
        "schema_version": 0,
        "record_type": 2,
        "seq_ms": 4,
        "seq_ord": 12,
        "timestamp_ns": 20,
        "order_id": 28,
        "client_order_id": 36,
        "user_id": 44,
        "price_ticks": 52,
        "qty": 60,
        "symbol_id": 68,
        "side": 70,
        "tif": 71,
    }

    @classmethod
    def new(cls, *, timestamp_ns: int, order_id: int, client_order_id: int, user_id: int, price_ticks: int, qty: int, symbol_id: int, side: int, tif: int) -> "OrderAccepted":
        """Build a record with the header filled in correctly and seq unassigned."""
        return cls(
            schema_version=SCHEMA_VERSION,
            record_type=10,
            seq_ms=SEQ_UNASSIGNED,
            seq_ord=SEQ_UNASSIGNED,
            timestamp_ns=timestamp_ns,
            order_id=order_id,
            client_order_id=client_order_id,
            user_id=user_id,
            price_ticks=price_ticks,
            qty=qty,
            symbol_id=symbol_id,
            side=side,
            tif=tif,
        )

    def pack(self) -> bytes:
        return _S_OrderAccepted.pack(*self)

    @classmethod
    def unpack(cls, data: bytes) -> "OrderAccepted":
        return cls(*_S_OrderAccepted.unpack(data))


_S_OrderAccepted = struct.Struct(OrderAccepted.FORMAT)
assert _S_OrderAccepted.size == OrderAccepted.SIZE, "OrderAccepted: format string and SIZE disagree"


class OrderRejected(NamedTuple):
    """
    Order never reached the book. No order_id was assigned.

    Direction: outbound. record_type = 11.
    """

    schema_version: int  # Version of this schema. Consumers accept the current version only.
    record_type: int  # Discriminator. Fixed offset across every record.
    seq_ms: int  # Redis stream id, millisecond part. 0 until assigned on read.
    seq_ord: int  # Redis stream id, ordinal part. 0 until assigned on read.
    timestamp_ns: int  # Gateway-assigned wall clock, nanoseconds.
    client_order_id: int
    user_id: int
    symbol_id: int
    reason: int

    FORMAT = "<HHQQqQQhH"
    SIZE = 48
    RECORD_TYPE = 11
    OFFSETS = {
        "schema_version": 0,
        "record_type": 2,
        "seq_ms": 4,
        "seq_ord": 12,
        "timestamp_ns": 20,
        "client_order_id": 28,
        "user_id": 36,
        "symbol_id": 44,
        "reason": 46,
    }

    @classmethod
    def new(cls, *, timestamp_ns: int, client_order_id: int, user_id: int, symbol_id: int, reason: int) -> "OrderRejected":
        """Build a record with the header filled in correctly and seq unassigned."""
        return cls(
            schema_version=SCHEMA_VERSION,
            record_type=11,
            seq_ms=SEQ_UNASSIGNED,
            seq_ord=SEQ_UNASSIGNED,
            timestamp_ns=timestamp_ns,
            client_order_id=client_order_id,
            user_id=user_id,
            symbol_id=symbol_id,
            reason=reason,
        )

    def pack(self) -> bytes:
        return _S_OrderRejected.pack(*self)

    @classmethod
    def unpack(cls, data: bytes) -> "OrderRejected":
        return cls(*_S_OrderRejected.unpack(data))


_S_OrderRejected = struct.Struct(OrderRejected.FORMAT)
assert _S_OrderRejected.size == OrderRejected.SIZE, "OrderRejected: format string and SIZE disagree"


class Fill(NamedTuple):
    """
    One trade. Both sides are named. aggressor_side is required for maker/taker fees (Open Issue
    011 section 11.2) and cannot be derived after the fact.

    Direction: outbound. record_type = 12.
    """

    schema_version: int  # Version of this schema. Consumers accept the current version only.
    record_type: int  # Discriminator. Fixed offset across every record.
    seq_ms: int  # Redis stream id, millisecond part. 0 until assigned on read.
    seq_ord: int  # Redis stream id, ordinal part. 0 until assigned on read.
    timestamp_ns: int  # Gateway-assigned wall clock, nanoseconds.
    maker_order_id: int
    taker_order_id: int
    maker_user_id: int
    taker_user_id: int
    price_ticks: int  # Always the resting (maker) price — price-time priority.
    qty: int
    symbol_id: int
    aggressor_side: int

    FORMAT = "<HHQQqQQQQqqhB"
    SIZE = 79
    RECORD_TYPE = 12
    OFFSETS = {
        "schema_version": 0,
        "record_type": 2,
        "seq_ms": 4,
        "seq_ord": 12,
        "timestamp_ns": 20,
        "maker_order_id": 28,
        "taker_order_id": 36,
        "maker_user_id": 44,
        "taker_user_id": 52,
        "price_ticks": 60,
        "qty": 68,
        "symbol_id": 76,
        "aggressor_side": 78,
    }

    @classmethod
    def new(cls, *, timestamp_ns: int, maker_order_id: int, taker_order_id: int, maker_user_id: int, taker_user_id: int, price_ticks: int, qty: int, symbol_id: int, aggressor_side: int) -> "Fill":
        """Build a record with the header filled in correctly and seq unassigned."""
        return cls(
            schema_version=SCHEMA_VERSION,
            record_type=12,
            seq_ms=SEQ_UNASSIGNED,
            seq_ord=SEQ_UNASSIGNED,
            timestamp_ns=timestamp_ns,
            maker_order_id=maker_order_id,
            taker_order_id=taker_order_id,
            maker_user_id=maker_user_id,
            taker_user_id=taker_user_id,
            price_ticks=price_ticks,
            qty=qty,
            symbol_id=symbol_id,
            aggressor_side=aggressor_side,
        )

    def pack(self) -> bytes:
        return _S_Fill.pack(*self)

    @classmethod
    def unpack(cls, data: bytes) -> "Fill":
        return cls(*_S_Fill.unpack(data))


_S_Fill = struct.Struct(Fill.FORMAT)
assert _S_Fill.size == Fill.SIZE, "Fill: format string and SIZE disagree"


class OrderCancelled(NamedTuple):
    """
    Order left the book without being fully filled.

    Direction: outbound. record_type = 13.
    """

    schema_version: int  # Version of this schema. Consumers accept the current version only.
    record_type: int  # Discriminator. Fixed offset across every record.
    seq_ms: int  # Redis stream id, millisecond part. 0 until assigned on read.
    seq_ord: int  # Redis stream id, ordinal part. 0 until assigned on read.
    timestamp_ns: int  # Gateway-assigned wall clock, nanoseconds.
    order_id: int
    client_order_id: int
    user_id: int
    remaining_qty: int  # Quantity removed from the book.
    symbol_id: int
    reason: int

    FORMAT = "<HHQQqQQQqhB"
    SIZE = 63
    RECORD_TYPE = 13
    OFFSETS = {
        "schema_version": 0,
        "record_type": 2,
        "seq_ms": 4,
        "seq_ord": 12,
        "timestamp_ns": 20,
        "order_id": 28,
        "client_order_id": 36,
        "user_id": 44,
        "remaining_qty": 52,
        "symbol_id": 60,
        "reason": 62,
    }

    @classmethod
    def new(cls, *, timestamp_ns: int, order_id: int, client_order_id: int, user_id: int, remaining_qty: int, symbol_id: int, reason: int) -> "OrderCancelled":
        """Build a record with the header filled in correctly and seq unassigned."""
        return cls(
            schema_version=SCHEMA_VERSION,
            record_type=13,
            seq_ms=SEQ_UNASSIGNED,
            seq_ord=SEQ_UNASSIGNED,
            timestamp_ns=timestamp_ns,
            order_id=order_id,
            client_order_id=client_order_id,
            user_id=user_id,
            remaining_qty=remaining_qty,
            symbol_id=symbol_id,
            reason=reason,
        )

    def pack(self) -> bytes:
        return _S_OrderCancelled.pack(*self)

    @classmethod
    def unpack(cls, data: bytes) -> "OrderCancelled":
        return cls(*_S_OrderCancelled.unpack(data))


_S_OrderCancelled = struct.Struct(OrderCancelled.FORMAT)
assert _S_OrderCancelled.size == OrderCancelled.SIZE, "OrderCancelled: format string and SIZE disagree"


class BookChanged(NamedTuple):
    """
    One aggregated price level, after the change. A fixed-size POD record cannot carry a
    variable list of levels, so the engine emits per-level updates and fan-out maintains its own
    book from them; the complete-L2-snapshot rule of Open Issue 006 governs the browser wire,
    not this record. qty_at_level = 0 means the level is now empty.

    Direction: outbound. record_type = 14.
    """

    schema_version: int  # Version of this schema. Consumers accept the current version only.
    record_type: int  # Discriminator. Fixed offset across every record.
    seq_ms: int  # Redis stream id, millisecond part. 0 until assigned on read.
    seq_ord: int  # Redis stream id, ordinal part. 0 until assigned on read.
    timestamp_ns: int  # Gateway-assigned wall clock, nanoseconds.
    price_ticks: int
    qty_at_level: int  # Aggregate resting quantity at this price after the change. 0 = level removed.
    symbol_id: int
    side: int

    FORMAT = "<HHQQqqqhB"
    SIZE = 47
    RECORD_TYPE = 14
    OFFSETS = {
        "schema_version": 0,
        "record_type": 2,
        "seq_ms": 4,
        "seq_ord": 12,
        "timestamp_ns": 20,
        "price_ticks": 28,
        "qty_at_level": 36,
        "symbol_id": 44,
        "side": 46,
    }

    @classmethod
    def new(cls, *, timestamp_ns: int, price_ticks: int, qty_at_level: int, symbol_id: int, side: int) -> "BookChanged":
        """Build a record with the header filled in correctly and seq unassigned."""
        return cls(
            schema_version=SCHEMA_VERSION,
            record_type=14,
            seq_ms=SEQ_UNASSIGNED,
            seq_ord=SEQ_UNASSIGNED,
            timestamp_ns=timestamp_ns,
            price_ticks=price_ticks,
            qty_at_level=qty_at_level,
            symbol_id=symbol_id,
            side=side,
        )

    def pack(self) -> bytes:
        return _S_BookChanged.pack(*self)

    @classmethod
    def unpack(cls, data: bytes) -> "BookChanged":
        return cls(*_S_BookChanged.unpack(data))


_S_BookChanged = struct.Struct(BookChanged.FORMAT)
assert _S_BookChanged.size == BookChanged.SIZE, "BookChanged: format string and SIZE disagree"


class AccountCreated(NamedTuple):
    """
    Forwarded record, now sequenced.

    Direction: outbound. record_type = 15.
    """

    schema_version: int  # Version of this schema. Consumers accept the current version only.
    record_type: int  # Discriminator. Fixed offset across every record.
    seq_ms: int  # Redis stream id, millisecond part. 0 until assigned on read.
    seq_ord: int  # Redis stream id, ordinal part. 0 until assigned on read.
    timestamp_ns: int  # Gateway-assigned wall clock, nanoseconds.
    client_order_id: int
    user_id: int
    initial_cash_ticks: int

    FORMAT = "<HHQQqQQq"
    SIZE = 52
    RECORD_TYPE = 15
    OFFSETS = {
        "schema_version": 0,
        "record_type": 2,
        "seq_ms": 4,
        "seq_ord": 12,
        "timestamp_ns": 20,
        "client_order_id": 28,
        "user_id": 36,
        "initial_cash_ticks": 44,
    }

    @classmethod
    def new(cls, *, timestamp_ns: int, client_order_id: int, user_id: int, initial_cash_ticks: int) -> "AccountCreated":
        """Build a record with the header filled in correctly and seq unassigned."""
        return cls(
            schema_version=SCHEMA_VERSION,
            record_type=15,
            seq_ms=SEQ_UNASSIGNED,
            seq_ord=SEQ_UNASSIGNED,
            timestamp_ns=timestamp_ns,
            client_order_id=client_order_id,
            user_id=user_id,
            initial_cash_ticks=initial_cash_ticks,
        )

    def pack(self) -> bytes:
        return _S_AccountCreated.pack(*self)

    @classmethod
    def unpack(cls, data: bytes) -> "AccountCreated":
        return cls(*_S_AccountCreated.unpack(data))


_S_AccountCreated = struct.Struct(AccountCreated.FORMAT)
assert _S_AccountCreated.size == AccountCreated.SIZE, "AccountCreated: format string and SIZE disagree"


class CashCredited(NamedTuple):
    """
    Forwarded record, now sequenced. The engine is money-blind and does not track balances.

    Direction: outbound. record_type = 16.
    """

    schema_version: int  # Version of this schema. Consumers accept the current version only.
    record_type: int  # Discriminator. Fixed offset across every record.
    seq_ms: int  # Redis stream id, millisecond part. 0 until assigned on read.
    seq_ord: int  # Redis stream id, ordinal part. 0 until assigned on read.
    timestamp_ns: int  # Gateway-assigned wall clock, nanoseconds.
    client_order_id: int
    user_id: int
    amount_ticks: int

    FORMAT = "<HHQQqQQq"
    SIZE = 52
    RECORD_TYPE = 16
    OFFSETS = {
        "schema_version": 0,
        "record_type": 2,
        "seq_ms": 4,
        "seq_ord": 12,
        "timestamp_ns": 20,
        "client_order_id": 28,
        "user_id": 36,
        "amount_ticks": 44,
    }

    @classmethod
    def new(cls, *, timestamp_ns: int, client_order_id: int, user_id: int, amount_ticks: int) -> "CashCredited":
        """Build a record with the header filled in correctly and seq unassigned."""
        return cls(
            schema_version=SCHEMA_VERSION,
            record_type=16,
            seq_ms=SEQ_UNASSIGNED,
            seq_ord=SEQ_UNASSIGNED,
            timestamp_ns=timestamp_ns,
            client_order_id=client_order_id,
            user_id=user_id,
            amount_ticks=amount_ticks,
        )

    def pack(self) -> bytes:
        return _S_CashCredited.pack(*self)

    @classmethod
    def unpack(cls, data: bytes) -> "CashCredited":
        return cls(*_S_CashCredited.unpack(data))


_S_CashCredited = struct.Struct(CashCredited.FORMAT)
assert _S_CashCredited.size == CashCredited.SIZE, "CashCredited: format string and SIZE disagree"


class ReplayConfigured(NamedTuple):
    """
    Forwarded replay configuration, now sequenced.

    Direction: outbound. record_type = 17.
    """

    schema_version: int  # Version of this schema. Consumers accept the current version only.
    record_type: int  # Discriminator. Fixed offset across every record.
    seq_ms: int  # Redis stream id, millisecond part. 0 until assigned on read.
    seq_ord: int  # Redis stream id, ordinal part. 0 until assigned on read.
    timestamp_ns: int  # Gateway-assigned wall clock, nanoseconds.
    client_order_id: int
    real_seconds_per_simulated_minute: int
    config_hash_hi: int
    config_hash_lo: int

    FORMAT = "<HHQQqQqQQ"
    SIZE = 60
    RECORD_TYPE = 17
    OFFSETS = {
        "schema_version": 0,
        "record_type": 2,
        "seq_ms": 4,
        "seq_ord": 12,
        "timestamp_ns": 20,
        "client_order_id": 28,
        "real_seconds_per_simulated_minute": 36,
        "config_hash_hi": 44,
        "config_hash_lo": 52,
    }

    @classmethod
    def new(cls, *, timestamp_ns: int, client_order_id: int, real_seconds_per_simulated_minute: int, config_hash_hi: int, config_hash_lo: int) -> "ReplayConfigured":
        """Build a record with the header filled in correctly and seq unassigned."""
        return cls(
            schema_version=SCHEMA_VERSION,
            record_type=17,
            seq_ms=SEQ_UNASSIGNED,
            seq_ord=SEQ_UNASSIGNED,
            timestamp_ns=timestamp_ns,
            client_order_id=client_order_id,
            real_seconds_per_simulated_minute=real_seconds_per_simulated_minute,
            config_hash_hi=config_hash_hi,
            config_hash_lo=config_hash_lo,
        )

    def pack(self) -> bytes:
        return _S_ReplayConfigured.pack(*self)

    @classmethod
    def unpack(cls, data: bytes) -> "ReplayConfigured":
        return cls(*_S_ReplayConfigured.unpack(data))


_S_ReplayConfigured = struct.Struct(ReplayConfigured.FORMAT)
assert _S_ReplayConfigured.size == ReplayConfigured.SIZE, "ReplayConfigured: format string and SIZE disagree"


#: record_type -> record class.
RECORD_BY_TYPE = {
    1: SubmitOrder,
    2: CancelOrder,
    3: CreateAccount,
    4: CreditCash,
    5: ConfigureReplay,
    10: OrderAccepted,
    11: OrderRejected,
    12: Fill,
    13: OrderCancelled,
    14: BookChanged,
    15: AccountCreated,
    16: CashCredited,
    17: ReplayConfigured,
}

ALL_RECORDS = tuple(RECORD_BY_TYPE.values())

_RECORD_TYPE_OFFSET = 2
_SCHEMA_VERSION_OFFSET = 0


def peek_schema_version(data: bytes) -> int:
    """Read the schema version before dispatching a packed record."""
    return struct.unpack_from("<H", data, _SCHEMA_VERSION_OFFSET)[0]


def peek_record_type(data: bytes) -> int:
    """Read the discriminator without knowing which record this is."""
    return struct.unpack_from("<H", data, _RECORD_TYPE_OFFSET)[0]


def unpack_any(data: bytes):
    """Unpack a current-version record of any type, dispatching on record_type."""
    schema_version = peek_schema_version(data)
    if schema_version != SCHEMA_VERSION:
        raise ValueError(
            f"unsupported schema_version {schema_version}; expected {SCHEMA_VERSION}"
        )
    record_type = peek_record_type(data)
    cls = RECORD_BY_TYPE.get(record_type)
    if cls is None:
        raise ValueError(f"unknown record_type {record_type}")
    return cls.unpack(data)


def parse_stream_id(stream_id: str | bytes) -> tuple[int, int]:
    """Split a Redis stream id `<ms>-<ord>` into its two halves."""
    if isinstance(stream_id, bytes):
        stream_id = stream_id.decode("ascii")
    ms, _, ordinal = stream_id.partition("-")
    return int(ms), int(ordinal)


def with_seq(record, stream_id: str | bytes):
    """Stamp a record with the sequence number it was assigned on append.

    Open Issue 003 fixes the Redis stream id AS the sequence number, and forbids a
    parallel counter. A producer cannot know its own id before XADD returns, so the
    number is applied here, on read, and re-derived identically on every replay.
    """
    seq_ms, seq_ord = parse_stream_id(stream_id)
    return record._replace(seq_ms=seq_ms, seq_ord=seq_ord)
