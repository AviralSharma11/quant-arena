"""Task 2.2 — LedgerConsumer stream tailing and PostgreSQL projection tests.

Success Criteria:
3. Killing the ledger and restarting reproduces byte-identical balances by replay.
4. No balance is ever written to PostgreSQL from any source other than the stream.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from contracts.v1.generated.contracts import (
    AccountCreated,
    CancelReason,
    OrderCancelled,
    CashCredited,
    Fill,
    OrderAccepted,
    OrderCancelled,
    Side,
    Tif,
)
from services.ledger.consumer import LedgerConsumer
from services.ledger.ledger import Ledger

pytestmark = pytest.mark.anyio


class MockRedisStream:
    """In-memory Redis stream mock for testing stream replay."""

    def __init__(self) -> None:
        self.streams: dict[str, list[tuple[str, dict[bytes, bytes]]]] = {}
        self._counter = 1000

    async def xadd(
        self, stream: str, fields: dict[bytes, bytes], maxlen: int | None = None, approximate: bool = True
    ) -> str:
        self._counter += 1
        stream_id = f"{self._counter}-0"
        if stream not in self.streams:
            self.streams[stream] = []
        self.streams[stream].append((stream_id, fields))
        return stream_id

    async def xread(
        self, streams: dict[str, str], count: int = 100, block: int = 1000
    ) -> list[tuple[str, list[tuple[str, dict[bytes, bytes]]]]]:
        out = []
        for stream_name, last_id in streams.items():
            entries = self.streams.get(stream_name, [])
            if last_id == "0-0":
                filtered = entries[:count]
            else:
                last_ms = int(last_id.split("-")[0])
                filtered = [e for e in entries if int(e[0].split("-")[0]) > last_ms][:count]
            if filtered:
                out.append((stream_name, filtered))
        return out


class MockAsyncDbConnection:
    def __init__(self) -> None:
        self.executed: list[tuple[str, dict[str, Any] | None]] = []

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> None:
        self.executed.append((str(stmt), params))


class MockAsyncDbEngine:
    def __init__(self) -> None:
        self.conn = MockAsyncDbConnection()

    def begin(self):
        class _Context:
            def __init__(self, conn):
                self.conn = conn

            async def __aenter__(self):
                return self.conn

            async def __aexit__(self, exc_type, exc_val, exc_tb):
                pass

        return _Context(self.conn)


async def test_ledger_consumer_replay_and_postgres_flush():
    """Test full replay recovery reading stream and executing bulk SQL statements."""
    mock_db = MockAsyncDbEngine()
    mock_redis = MockRedisStream()
    stream_name = "qa.outbound"

    # Seed events into stream
    events = [
        AccountCreated.new(timestamp_ns=1, client_order_id=1, user_id=1, initial_cash_ticks=1_000_000),
        AccountCreated.new(timestamp_ns=2, client_order_id=2, user_id=2, initial_cash_ticks=2_000_000),
        CashCredited.new(timestamp_ns=3, client_order_id=3, user_id=1, amount_ticks=500_000),
        OrderAccepted.new(
            timestamp_ns=4, order_id=10, client_order_id=4, user_id=1, symbol_id=1,
            side=Side.BUY, price_ticks=10_000, qty=10, tif=Tif.GTC,
        ),
        Fill.new(
            timestamp_ns=5, maker_order_id=10, taker_order_id=11, maker_user_id=1,
            taker_user_id=2, price_ticks=10_000, qty=4, symbol_id=1, aggressor_side=Side.SELL,
        ),
    ]

    for e in events:
        await mock_redis.xadd(stream_name, {b"r": e.pack()})

    consumer = LedgerConsumer(mock_redis, mock_db, stream_name, batch_size=10)
    replayed = await consumer.replay_from_genesis()
    assert replayed == 5

    # Check that in-memory ledger state derived everything correctly
    # User 1: 1.5M - notional(40k) - maker_fee(8) = 1,459,992
    assert consumer.ledger.get_balance(1) == 1_459_992
    # User 2: 2.0M + notional(40k) - taker_fee(40) = 2,039,960
    assert consumer.ledger.get_balance(2) == 2_039_960
    assert consumer.ledger.get_position(1, symbol_id=1) == 4
    assert consumer.ledger.get_position(2, symbol_id=1) == -4
    assert consumer.ledger.open_orders[10].remaining_qty == 6
    assert consumer.ledger.house_fee_ticks == 48

    # Verify SQL statements were executed in bulk
    assert len(mock_db.conn.executed) > 0
    executed_statements = [stmt for stmt, _ in mock_db.conn.executed]
    assert any("INSERT INTO accounts" in s for s in executed_statements)
    assert any("INSERT INTO positions" in s for s in executed_statements)
    assert any("INSERT INTO open_orders" in s for s in executed_statements)
    assert any("INSERT INTO house_fees" in s for s in executed_statements)


async def test_live_tailing_updates_ledger_and_database():
    """Test live background stream tailing loop."""
    mock_db = MockAsyncDbEngine()
    mock_redis = MockRedisStream()
    stream_name = "qa.outbound"

    consumer = LedgerConsumer(mock_redis, mock_db, stream_name, poll_block_ms=10)
    consumer.start()
    try:
        await mock_redis.xadd(
            stream_name,
            {b"r": AccountCreated.new(timestamp_ns=1, client_order_id=1, user_id=99, initial_cash_ticks=500_000).pack()},
        )
        # Give consumer loop a moment to process the batch
        await asyncio.sleep(0.05)
        assert consumer.ledger.get_balance(99) == 500_000
    finally:
        await consumer.stop()


async def test_live_flush_writes_only_what_the_batch_touched():
    """Regression: a live flush rewrote every open order after every batch, so with a large
    resting book the ledger fell hours behind and new accounts never reached PostgreSQL."""
    mock_db = MockAsyncDbEngine()
    mock_redis = MockRedisStream()
    stream_name = "qa.outbound"

    resting = [
        OrderAccepted.new(
            timestamp_ns=i, order_id=1_000 + i, client_order_id=i, user_id=1, symbol_id=1,
            side=Side.BUY, price_ticks=1, qty=1, tif=Tif.GTC,
        )
        for i in range(500)
    ]
    for e in resting:
        await mock_redis.xadd(stream_name, {b"r": e.pack()})

    consumer = LedgerConsumer(mock_redis, mock_db, stream_name, batch_size=1_000, poll_block_ms=10)
    await consumer.replay_from_genesis()
    mock_db.conn.executed.clear()

    consumer.start()
    try:
        await mock_redis.xadd(
            stream_name,
            {b"r": AccountCreated.new(timestamp_ns=9_999, client_order_id=1, user_id=80, initial_cash_ticks=500).pack()},
        )
        await mock_redis.xadd(stream_name, {b"r": OrderCancelled.new(
            timestamp_ns=10_000, order_id=1_000, client_order_id=0, user_id=1, remaining_qty=1, symbol_id=1,
            reason=CancelReason.USER_REQUESTED,
        ).pack()})
        await asyncio.sleep(0.05)
    finally:
        await consumer.stop()

    statements = mock_db.conn.executed
    account_rows = [p for s, p in statements if "INSERT INTO accounts" in s]
    assert account_rows == [[{"user_id": 80, "cash_ticks": 500, "now_ns": account_rows[0][0]["now_ns"]}]]
    # The 499 orders nothing touched are neither deleted nor rewritten.
    assert not any(s.strip() == "DELETE FROM open_orders" for s, _ in statements)
    assert not any("INSERT INTO open_orders" in s for s, _ in statements)
    assert [p for s, p in statements if "DELETE FROM open_orders WHERE" in s] == [[{"order_id": 1_000}]]
