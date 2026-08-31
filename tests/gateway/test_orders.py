"""POST /orders returns 202; malformed returns 400.

Task 1.3 criterion 3, updated for Task 2.1: the gateway is now the single producer and `XADD`s
to the inbound stream instead of calling an engine. The acknowledgement therefore carries a real
`seq` — the Redis stream ID — and a null `order_id`, because the engine assigns that and it
arrives on the private stream.
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient


VALID = {"client_order_id": 1, "symbol_id": 1, "side": 1, "tif": 1,
         "price_ticks": 100_000, "qty": 3}


def test_a_valid_order_is_accepted_and_sequenced(logged_in: TestClient):
    response = logged_in.post("/orders", json=VALID)
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["status"] == "accepted"
    assert body["client_order_id"] == VALID["client_order_id"]
    # A Redis stream ID: <milliseconds>-<ordinal>.
    assert re.fullmatch(r"\d+-\d+", body["seq"]), body["seq"]


def test_the_acknowledgement_is_not_a_result(logged_in: TestClient):
    """Open Issue 008 section 9h: 202 means sequenced, not filled. No fill data comes back."""
    body = logged_in.post("/orders", json=VALID).json()
    assert set(body) == {"client_order_id", "order_id", "seq", "status"}
    assert "fills" not in body and "price" not in body


def test_order_id_is_null_because_the_engine_assigns_it(logged_in: TestClient):
    """The gateway appends to the stream; it does not match. Reporting an order_id it does not
    have would be inventing one — and Open Issue 008 §9h makes this an acknowledgement."""
    assert logged_in.post("/orders", json=VALID).json()["order_id"] is None


def test_sequence_numbers_are_monotonic(logged_in: TestClient):
    """Task 2.1 Success Criterion 1. Ordering is not computed — it follows from there being
    exactly one writer to one stream."""
    seqs = [
        logged_in.post(
            "/orders",
            json={**VALID, "client_order_id": n, "price_ticks": 100_000, "qty": 1},
        ).json()["seq"]
        for n in range(1, 6)
    ]
    keys = [tuple(int(part) for part in s.split("-")) for s in seqs]
    assert keys == sorted(keys) and len(set(keys)) == len(keys)


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


def test_order_exceeding_available_cash_is_rejected(logged_in: TestClient):
    """Risk checks must happen before the gateway appends to the stream."""
    body = {**VALID, "client_order_id": 999, "qty": 200_000, "price_ticks": 6_412_500}
    response = logged_in.post("/orders", json=body)
    assert response.status_code == 409, response.text
    payload = response.json()
    assert payload["detail"]["status"] == "rejected"
    assert payload["detail"]["reason"] == "INSUFFICIENT_CASH"


def test_authentication_is_checked_before_the_body(client: TestClient):
    """A malformed order from a stranger must not reveal that it was malformed."""
    assert client.post("/orders", json={"nonsense": True}).status_code == 401


# --- cancel ------------------------------------------------------------------------------------


def test_cancel_by_client_order_id_is_sequenced(logged_in: TestClient):
    logged_in.post("/orders", json=VALID)
    response = logged_in.request(
        "DELETE", f"/orders/{VALID['client_order_id']}", json={"client_order_id": 99}
    )
    assert response.status_code == 202, response.text
    assert response.json()["status"] == "accepted"
    assert re.fullmatch(r"\d+-\d+", response.json()["seq"])


def test_the_gateway_no_longer_judges_whether_an_order_exists(logged_in: TestClient):
    """Before 2.1 an unknown cancel was a 409 from the in-process stub. The gateway does not
    hold the book any more, so it records the request and lets the engine answer — the reply
    arrives on the private stream. Pretending to know here would be the gateway inventing an
    outcome it cannot have."""
    response = logged_in.request("DELETE", "/orders/424242", json={"client_order_id": 99})
    assert response.status_code == 202
    assert response.json()["status"] == "accepted"
