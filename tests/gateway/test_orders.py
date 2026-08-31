"""Success Criterion 3 — POST /orders returns 202 with an order id; malformed returns 400."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from contracts.v1.generated.contracts import RejectReason

VALID = {"client_order_id": 1, "symbol_id": 1, "side": 1, "tif": 1,
         "price_ticks": 6_412_500, "qty": 3}


def test_a_valid_order_is_accepted_with_an_order_id(logged_in: TestClient):
    response = logged_in.post("/orders", json=VALID)
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["status"] == "accepted"
    assert body["client_order_id"] == VALID["client_order_id"]
    assert isinstance(body["order_id"], int) and body["order_id"] > 0


def test_the_acknowledgement_is_not_a_result(logged_in: TestClient):
    """Open Issue 008 section 9h: 202 means sequenced, not filled. No fill data comes back."""
    body = logged_in.post("/orders", json=VALID).json()
    assert set(body) == {"client_order_id", "order_id", "seq", "status"}
    assert "fills" not in body and "price" not in body


def test_seq_is_null_until_there_is_a_stream(logged_in: TestClient):
    """The Redis stream id IS the sequence number (Open Issue 003), and there is no stream
    until Task 2.1. Null is honest; a locally invented counter would not be."""
    assert logged_in.post("/orders", json=VALID).json()["seq"] is None


def test_order_ids_are_monotonic(logged_in: TestClient):
    ids = [
        logged_in.post("/orders", json={**VALID, "client_order_id": n}).json()["order_id"]
        for n in range(1, 6)
    ]
    assert ids == sorted(ids) and len(set(ids)) == len(ids)


@pytest.mark.parametrize(
    "override,why",
    [
        ({"client_order_id": None}, "client_order_id missing"),
        ({"client_order_id": -1}, "client_order_id negative"),
        ({"client_order_id": 2**64}, "client_order_id above uint64"),
        ({"qty": 0}, "zero quantity"),
        ({"qty": -5}, "negative quantity"),
        ({"qty": 2**63}, "quantity above int64"),
        ({"price_ticks": 0}, "zero price"),
        ({"price_ticks": -100}, "negative price"),
        ({"side": 3}, "side outside the enum"),
        ({"side": 0}, "side zero"),
        ({"tif": 9}, "tif outside GTC/IOC"),
        ({"symbol_id": 2**15}, "symbol_id above int16"),
        ({"symbol_id": -1}, "symbol_id negative"),
        ({"price_ticks": 1.5}, "fractional price — no floats below the presentation layer"),
        ({"qty": "three"}, "quantity not a number"),
    ],
)
def test_malformed_orders_are_400(logged_in: TestClient, override: dict, why: str):
    body = {**VALID, **override}
    body = {k: v for k, v in body.items() if v is not None}
    assert logged_in.post("/orders", json=body).status_code == 400, why


def test_an_order_without_a_session_is_401(client: TestClient):
    assert client.post("/orders", json=VALID).status_code == 401


def test_authentication_is_checked_before_the_body(client: TestClient):
    """A malformed order from a stranger must not reveal that it was malformed."""
    assert client.post("/orders", json={"nonsense": True}).status_code == 401


# --- cancel ------------------------------------------------------------------------------------


def test_cancel_by_client_order_id(logged_in: TestClient):
    logged_in.post("/orders", json=VALID)
    response = logged_in.request(
        "DELETE", f"/orders/{VALID['client_order_id']}", json={"client_order_id": 99}
    )
    assert response.status_code == 202, response.text
    assert response.json()["status"] == "cancelled"


def test_cancelling_an_unknown_order_is_409_with_a_reason(logged_in: TestClient):
    """Never a 404 — a cancel answers the same shape as a submit
    (contracts/v1/rest_and_ws.md section 2.2)."""
    response = logged_in.request("DELETE", "/orders/424242", json={"client_order_id": 99})
    assert response.status_code == 409
    assert response.json()["detail"]["reason"] == int(RejectReason.UNKNOWN_ORDER)


def test_one_user_cannot_cancel_another_users_order(logged_in: TestClient, client: TestClient):
    logged_in.post("/orders", json=VALID)
    logged_in.post("/auth/logout")

    client.post("/auth/register", json={"username": "intruder", "password": "correct-horse-bat"})
    client.post("/auth/login", json={"username": "intruder", "password": "correct-horse-bat"})
    response = client.request(
        "DELETE", f"/orders/{VALID['client_order_id']}", json={"client_order_id": 1}
    )
    assert response.status_code == 409
    assert response.json()["detail"]["reason"] == int(RejectReason.UNKNOWN_ORDER)
