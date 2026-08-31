"""Task 2.2 — Replay rebuild, database projection, and route-ordering tests.

Success Criteria:
3. Killing the ledger and restarting reproduces byte-identical balances by replay.
4. No balance is ever written to PostgreSQL from any source other than the stream.
5. GET /orders/open route ordering trap is avoided (does not shadow into DELETE /orders/{id}).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from contracts.v1.generated.contracts import (
    AccountCreated,
    CashCredited,
    Fill,
    OrderAccepted,
    OrderCancelled,
    Side,
    Tif,
)
from services.gateway.app import create_app
from services.gateway.deps import current_user_id, get_db, get_sessions
from services.ledger.ledger import Ledger


def test_killing_the_ledger_and_replaying_reproduces_byte_identical_state():
    """Criterion 3: State is completely reconstructible from an event sequence."""
    events = [
        AccountCreated.new(timestamp_ns=1, client_order_id=1, user_id=10, initial_cash_ticks=1_000_000),
        AccountCreated.new(timestamp_ns=2, client_order_id=2, user_id=20, initial_cash_ticks=1_000_000),
        CashCredited.new(timestamp_ns=3, client_order_id=3, user_id=10, amount_ticks=250_000),
        OrderAccepted.new(
            timestamp_ns=4, order_id=101, client_order_id=4, user_id=10, symbol_id=1,
            side=Side.BUY, price_ticks=50_000, qty=10, tif=Tif.GTC,
        ),
        OrderAccepted.new(
            timestamp_ns=5, order_id=102, client_order_id=5, user_id=20, symbol_id=1,
            side=Side.SELL, price_ticks=50_000, qty=4, tif=Tif.GTC,
        ),
        Fill.new(
            timestamp_ns=6, maker_order_id=101, taker_order_id=102, maker_user_id=10,
            taker_user_id=20, price_ticks=50_000, qty=4, symbol_id=1, aggressor_side=Side.SELL,
        ),
        OrderCancelled.new(
            timestamp_ns=7, order_id=101, client_order_id=6, user_id=10, symbol_id=1,
            remaining_qty=3, reason=1,
        ),
    ]

    # Original Ledger instance
    ledger1 = Ledger()
    for e in events:
        ledger1.apply(e)

    snapshot_balances1 = dict(ledger1.cash_balances)
    snapshot_positions1 = dict(ledger1.positions)
    snapshot_orders1 = {k: (o.remaining_qty, o.price_ticks) for k, o in ledger1.open_orders.items()}
    snapshot_fees1 = ledger1.house_fee_ticks

    # "Kill" ledger and create a fresh one from scratch
    ledger2 = Ledger()
    for e in events:
        ledger2.apply(e)

    # Assert byte-identical equality across all derived tables
    assert ledger2.cash_balances == snapshot_balances1
    assert ledger2.positions == snapshot_positions1
    assert {k: (o.remaining_qty, o.price_ticks) for k, o in ledger2.open_orders.items()} == snapshot_orders1
    assert ledger2.house_fee_ticks == snapshot_fees1
    assert ledger2.total_system_cash() == ledger1.total_system_cash()


def test_get_orders_open_does_not_collide_with_delete_order():
    """Task 2.2 Route-Ordering Trap: GET /orders/open must never return 405 Method Not Allowed."""
    app = create_app()
    app.dependency_overrides[current_user_id] = lambda: 10

    class DummyDb:
        async def exec(self, stmt):
            class Result:
                def all(self):
                    return []

                def first(self):
                    return None

            return Result()

    async def dummy_db():
        yield DummyDb()

    app.dependency_overrides[get_db] = dummy_db

    client = TestClient(app)
    response = client.get("/orders/open")
    assert response.status_code == 200, (
        f"Expected 200 from GET /orders/open, got {response.status_code}. "
        f"If 405, route ordering is broken and matches DELETE /orders/{{id}}!"
    )
    assert response.json() == []


def test_get_portfolio_route():
    """GET /portfolio returns settled cash and positions."""
    app = create_app()
    app.dependency_overrides[current_user_id] = lambda: 42

    class DummyDb:
        async def exec(self, stmt):
            class Result:
                def all(self):
                    return []

                def first(self):
                    return None

            return Result()

    async def dummy_db():
        yield DummyDb()

    app.dependency_overrides[get_db] = dummy_db

    client = TestClient(app)
    response = client.get("/portfolio")
    assert response.status_code == 200
    assert response.json() == {"user_id": 42, "cash_ticks": 0, "positions": []}


def test_unauthenticated_requests_to_open_orders_and_portfolio_are_401():
    """Authentication guard is verified on the new endpoints."""
    app = create_app()

    class DummySessions:
        async def user_id(self, sid):
            return None

    app.dependency_overrides[get_sessions] = lambda: DummySessions()

    client = TestClient(app)
    assert client.get("/orders/open").status_code == 401
    assert client.get("/portfolio").status_code == 401
