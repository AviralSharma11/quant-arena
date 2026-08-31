"""Success Criterion 2 — the session survives a restart of the application process.

This is the criterion that constrains the design. Everything else in Task 1.3 would pass with
sessions held in a dictionary on the app object; only this fails.
"""

from __future__ import annotations

import dataclasses

import redis as redis_sync
from fastapi.testclient import TestClient

from config.settings import Settings
from services.gateway.app import create_app


def _login(client: TestClient, username: str = "trader_one",
           password: str = "correct-horse-battery") -> str:
    client.post("/auth/register", json={"username": username, "password": password})
    response = client.post("/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return response.cookies["qa_session"]


def _authenticated_probe(client: TestClient):
    """Any endpoint behind the session. POST /orders leaves no state we care about here."""
    return client.post(
        "/orders",
        json={"client_order_id": 1, "symbol_id": 1, "side": 1, "tif": 1,
              "price_ticks": 100, "qty": 1},
    )


# --- the restart test ------------------------------------------------------------------------


def test_session_survives_a_new_app_instance(settings: Settings):
    """Two app instances in sequence, sharing only Redis.

    **This is weaker than Success Criterion 2 and does not stand in for it.** Both apps live in
    one interpreter, so anything held at module or class scope would survive this and the test
    would pass while sessions were in memory. `test_restart.py` does the real thing with an
    actual uvicorn subprocess; this one catches per-instance state cheaply.
    """
    with TestClient(create_app(settings)) as first:
        cookie = _login(first)
        assert _authenticated_probe(first).status_code == 202

    # First gateway is gone. Now a brand-new one, as if the process had been restarted.
    with TestClient(create_app(settings)) as second:
        second.cookies.set("qa_session", cookie)
        response = _authenticated_probe(second)

    assert response.status_code == 202, (
        "the session did not survive the restart — it is being held in process memory, "
        f"not in Redis (got {response.status_code})"
    )


def test_the_session_really_is_a_key_in_redis(settings: Settings, client: TestClient):
    cookie = _login(client)
    r = redis_sync.Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        assert r.get(f"session:{cookie}") is not None
        assert 0 < r.ttl(f"session:{cookie}") <= settings.session_ttl_seconds
    finally:
        r.close()


def test_the_app_holds_no_session_state_of_its_own(settings: Settings):
    """A guard against the obvious regression: someone adds a dict 'just for speed'."""
    with TestClient(create_app(settings)) as client:
        _login(client)
        state = vars(client.app.state).get("_state", {})
        for name, value in state.items():
            assert not isinstance(value, dict) or not value, (
                f"app.state.{name} is a non-empty dict; session state must live in Redis"
            )


# --- cookie flags ----------------------------------------------------------------------------


def test_cookie_is_httponly_and_samesite(client: TestClient):
    client.post("/auth/register",
                json={"username": "flags_user", "password": "correct-horse-battery"})
    response = client.post("/auth/login",
                           json={"username": "flags_user", "password": "correct-horse-battery"})
    header = response.headers["set-cookie"].lower()
    assert "httponly" in header      # page JavaScript cannot read it
    assert "samesite=strict" in header
    assert "path=/" in header


def test_cookie_carries_secure_when_configured(settings: Settings):
    """Asserted against the raw header rather than the cookie jar, because the test client
    talks plain http and a jar would simply drop a Secure cookie."""
    secure_settings = dataclasses.replace(settings, session_cookie_secure=True)
    with TestClient(create_app(secure_settings)) as client:
        client.post("/auth/register",
                    json={"username": "secure_user", "password": "correct-horse-battery"})
        response = client.post(
            "/auth/login",
            json={"username": "secure_user", "password": "correct-horse-battery"},
        )
    assert "secure" in response.headers["set-cookie"].lower()


def test_no_jwt_anywhere_in_the_cookie(client: TestClient):
    """Open Issue 015 rejected JWT. A JWT is three base64 segments separated by dots."""
    cookie = _login(client)
    assert cookie.count(".") != 2 or not cookie.startswith("ey")


# --- revocation and rejection ------------------------------------------------------------------


def test_unauthenticated_request_is_401(client: TestClient):
    assert _authenticated_probe(client).status_code == 401


def test_a_made_up_cookie_is_401(client: TestClient):
    client.cookies.set("qa_session", "not-a-real-session-id")
    assert _authenticated_probe(client).status_code == 401


def test_logout_revokes_the_session_immediately(settings: Settings, client: TestClient):
    cookie = _login(client)
    assert client.post("/auth/logout").status_code == 204

    r = redis_sync.Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        assert r.get(f"session:{cookie}") is None, "logout left the session in Redis"
    finally:
        r.close()

    # Even a client that kept its own copy of the cookie is now locked out.
    client.cookies.set("qa_session", cookie)
    assert _authenticated_probe(client).status_code == 401


def test_logout_without_a_session_is_401(client: TestClient):
    assert client.post("/auth/logout").status_code == 401
