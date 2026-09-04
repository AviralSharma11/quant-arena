"""`GET /symbols` — the only source of symbol names, tick sizes and the enum tables.

`contracts/v1/rest_and_ws.md` section 2.3 is explicit that this endpoint is where a client
resolves a `symbol_id` into something a human reads, and where it learns that `side: 1` means
`BUY`. The tests below are mostly about the second half, because that is the part that can rot
silently: a hand-written enum table would keep serving `1` after `schema.toml` changed, and
nothing would fail.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from contracts.v1.generated.contracts import (
    SCHEMA_VERSION,
    CancelReason,
    RejectReason,
    Side,
    Tif,
)


@pytest.fixture
def symbols(client: TestClient) -> dict:
    response = client.get("/symbols")
    assert response.status_code == 200, response.text
    return response.json()


def test_it_needs_no_session(client: TestClient):
    """Static configuration, identical for every caller — and the login screen needs the enum
    tables before a session exists."""
    assert client.get("/symbols").status_code == 200


def test_every_configured_symbol_is_served(symbols: dict, settings):
    assert [s["symbol_id"] for s in symbols["symbols"]] == [
        s.symbol_id for s in settings.symbols
    ]
    assert [s["name"] for s in symbols["symbols"]] == [s.name for s in settings.symbols]


def test_each_symbol_carries_everything_a_client_needs_to_render_a_price(symbols: dict):
    for symbol in symbols["symbols"]:
        assert set(symbol) == {"symbol_id", "name", "tick_size_ticks", "lot_size"}
        # Integers, not floats. A float on this wire is a bug, not a rounding concern
        # (contracts/v1/rest_and_ws.md section 1).
        assert isinstance(symbol["tick_size_ticks"], int)
        assert isinstance(symbol["lot_size"], int)


@pytest.mark.parametrize(
    ("table", "enum"),
    [
        ("side", Side),
        ("tif", Tif),
        ("cancel_reason", CancelReason),
        ("reject_reason", RejectReason),
    ],
)
def test_the_enum_tables_are_the_schemas_own(symbols: dict, table: str, enum):
    """Derived from the generated module, never typed out by hand.

    This is the test that makes the endpoint worth having. `schema.toml` owns these integers;
    a second copy written out in the route would keep answering after a schema change, and the
    frontend would be confidently wrong rather than broken.
    """
    assert symbols["enums"][table] == {m.name: int(m) for m in enum}


def test_the_reject_reason_table_is_complete(symbols: dict):
    """All thirteen, not the three the contract's abbreviated example lists.

    A client that meets a reason it cannot name shows a bare integer to a user, which is the
    exact failure this endpoint exists to prevent.
    """
    assert len(symbols["enums"]["reject_reason"]) == len(RejectReason)
    assert "DUPLICATE_CLIENT_ORDER_ID" in symbols["enums"]["reject_reason"]


def test_the_schema_version_is_reported(symbols: dict):
    """Consumers accept the current version only (STATUS, 2026-08-31), so a client can check."""
    assert symbols["schema_version"] == SCHEMA_VERSION


# --- the registry is enforced, not merely published -------------------------------------------


def test_an_order_on_an_unlisted_symbol_is_rejected(logged_in: TestClient, settings):
    """Until week 4 `symbol_id` was range-checked against the contract's i16 and never looked
    up, so an order on a symbol nobody had listed rested happily on a book of its own."""
    unlisted = max(s.symbol_id for s in settings.symbols) + 1
    response = logged_in.post(
        "/orders",
        json={
            "client_order_id": 4001, "symbol_id": unlisted, "side": int(Side.BUY),
            "tif": int(Tif.GTC), "price_ticks": 100, "qty": 1,
        },
    )
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["reason"] == "UNKNOWN_SYMBOL"


def test_the_rejection_is_idempotent_like_any_other(logged_in: TestClient, settings):
    """A retry gets the identical answer, because the reason was recorded against the key."""
    unlisted = max(s.symbol_id for s in settings.symbols) + 1
    order = {
        "client_order_id": 4002, "symbol_id": unlisted, "side": int(Side.BUY),
        "tif": int(Tif.GTC), "price_ticks": 100, "qty": 1,
    }
    first = logged_in.post("/orders", json=order)
    second = logged_in.post("/orders", json=order)
    assert first.status_code == second.status_code == 409
    assert first.json() == second.json()


def test_an_order_on_a_listed_symbol_still_passes(logged_in: TestClient, settings):
    response = logged_in.post(
        "/orders",
        json={
            "client_order_id": 4003, "symbol_id": settings.symbols[0].symbol_id,
            "side": int(Side.BUY), "tif": int(Tif.GTC), "price_ticks": 100, "qty": 1,
        },
    )
    assert response.status_code == 202, response.text
