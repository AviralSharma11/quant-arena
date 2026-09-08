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

from config.settings import Settings


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


# --- idempotency -------------------------------------------------------------------------------


def test_same_client_order_id_twice_produces_one_order(logged_in: TestClient):
    """Task 3.2 Success Criterion 1. Submitting the same order twice with the same
    client_order_id should return the same order_id (null) and seq from the first submission."""
    order = {**VALID, "client_order_id": 2001}
    
    # First submission
    response1 = logged_in.post("/orders", json=order)
    assert response1.status_code == 202
    body1 = response1.json()
    assert body1["status"] == "accepted"
    seq1 = body1["seq"]
    
    # Retry with same client_order_id
    response2 = logged_in.post("/orders", json=order)
    assert response2.status_code == 202
    body2 = response2.json()
    assert body2["status"] == "accepted"
    
    # Should return the same seq (idempotent)
    assert body2["seq"] == seq1
    assert body2["client_order_id"] == order["client_order_id"]
    assert body2["order_id"] is None


def test_retry_after_rejection_returns_same_rejection(logged_in: TestClient):
    """Task 3.2 Success Criterion 2. If a submission was rejected, a retry with the same
    client_order_id should return the same rejection reason."""
    order = {**VALID, "client_order_id": 2002, "qty": 200_000, "price_ticks": 6_412_500}
    
    # First submission (invalid order, insufficient cash)
    response1 = logged_in.post("/orders", json=order)
    assert response1.status_code == 409
    body1 = response1.json()
    assert body1["detail"]["status"] == "rejected"
    reason1 = body1["detail"]["reason"]
    
    # Retry with same client_order_id
    response2 = logged_in.post("/orders", json=order)
    assert response2.status_code == 409
    body2 = response2.json()
    assert body2["detail"]["status"] == "rejected"
    
    # Should return the same rejection reason (idempotent)
    assert body2["detail"]["reason"] == reason1


def test_reserved_cash_does_not_leak_on_retry(logged_in: TestClient, settings: Settings):
    """Task 3.2 Success Criterion 3. A retried submission must not reserve twice.

    Sized as *fractions of the grant* rather than in absolute ticks. It was written against the
    week-1 figure of 1,000,000 and quietly stopped testing anything when Task 5.1's price scale
    forced the grant up to ten billion: the three orders no longer came close to exhausting the
    account, so the third one passed and the assertion that caught a double-reservation was
    asserting nothing. A test that hard-codes a configured value tests the value, not the
    behaviour.
    """
    tenth = settings.initial_cash_ticks // 10

    # Three tenths of the grant.
    order1 = {**VALID, "client_order_id": 2003, "price_ticks": tenth, "qty": 3}
    assert logged_in.post("/orders", json=order1).status_code == 202

    # The same order again. Idempotent, so it must reserve nothing further.
    assert logged_in.post("/orders", json=order1).status_code == 202

    # Four more tenths. Seven in total, which fits — but only if the retry above reserved
    # nothing. Had it double-reserved, six tenths would already be committed and this fails.
    order2 = {**VALID, "client_order_id": 2004, "price_ticks": tenth, "qty": 4}
    assert logged_in.post("/orders", json=order2).status_code == 202, (
        "the retry double-reserved: seven tenths of the grant should still fit"
    )

    # Four more would be eleven tenths. The check must still bite.
    order3 = {**VALID, "client_order_id": 2005, "price_ticks": tenth, "qty": 4}
    response3 = logged_in.post("/orders", json=order3)
    assert response3.status_code == 409, "eleven tenths of the grant must not be reservable"
    assert response3.json()["detail"]["reason"] == "INSUFFICIENT_CASH"


def test_cancel_retry_returns_same_seq(logged_in: TestClient):
    """Task 3.2 cancel idempotency. Retrying a cancel with the same client_order_id should
    return the same seq from the first cancel."""
    # Submit an order first
    order = {**VALID, "client_order_id": 2010}
    logged_in.post("/orders", json=order)
    
    # Cancel the order
    cancel_req = {"client_order_id": 3010}
    response1 = logged_in.request(
        "DELETE", f"/orders/{VALID['client_order_id']}", json=cancel_req
    )
    assert response1.status_code == 202
    body1 = response1.json()
    seq1 = body1["seq"]
    
    # Retry the same cancel (same client_order_id)
    response2 = logged_in.request(
        "DELETE", f"/orders/{VALID['client_order_id']}", json=cancel_req
    )
    assert response2.status_code == 202
    body2 = response2.json()
    
    # Should return the same seq (idempotent)
    assert body2["seq"] == seq1
