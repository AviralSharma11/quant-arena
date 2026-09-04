"""Per-user order rate limiting.

Open Issue 015 section 11.1: a single tier of 1000 orders/sec for every account, which with one
tier is exchange realism rather than denial-of-service protection. Task 4.4's fifth success
criterion — "bots authenticate and are rate-limited exactly like any user" — is what forced it
to be built: `limits.max_orders_per_second` had been in the configuration since Task 1.4 and
enforced nowhere.

Two halves, tested separately. The bucket arithmetic is pure and needs no stores; the HTTP
behaviour needs a gateway, and gets one configured down to a rate a test can actually reach.
"""

from __future__ import annotations

import dataclasses

import pytest
from fastapi.testclient import TestClient

from config.settings import Settings
from contracts.v1.generated.contracts import Side, Tif
from services.gateway.app import create_app
from services.gateway.ratelimit import RateLimiter

# --- the bucket ------------------------------------------------------------------------------


class _Clock:
    """A hand-wound monotonic clock, so the tests assert on arithmetic rather than on sleep."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_a_burst_up_to_the_rate_is_allowed():
    """A token bucket permits a burst up to capacity and then settles to the sustained rate.
    Real order flow is bursty; a fixed window would reject a legitimate burst."""
    limiter = RateLimiter(rate_per_second=5, clock=_Clock())
    assert [limiter.allow(1) for _ in range(5)] == [True] * 5
    assert limiter.allow(1) is False


def test_tokens_refill_continuously_rather_than_on_a_window_boundary():
    """Half a second of a five-per-second rate is two whole tokens, not zero and not five."""
    clock = _Clock()
    limiter = RateLimiter(rate_per_second=5, clock=clock)
    for _ in range(5):
        limiter.allow(1)

    clock.advance(0.5)
    assert [limiter.allow(1) for _ in range(3)] == [True, True, False]


def test_the_bucket_never_fills_past_its_capacity():
    """An idle hour does not buy an hour's worth of burst."""
    clock = _Clock()
    limiter = RateLimiter(rate_per_second=5, clock=clock)
    limiter.allow(1)
    clock.advance(3600)
    assert [limiter.allow(1) for _ in range(6)] == [True] * 5 + [False]


def test_one_user_cannot_spend_anothers_tokens():
    limiter = RateLimiter(rate_per_second=2, clock=_Clock())
    assert [limiter.allow(1) for _ in range(3)] == [True, True, False]
    assert limiter.allow(2) is True


def test_a_refused_call_spends_nothing():
    """Otherwise a client hammering the endpoint would hold its own bucket permanently empty."""
    clock = _Clock()
    limiter = RateLimiter(rate_per_second=1, clock=clock)
    assert limiter.allow(1) is True
    for _ in range(50):
        assert limiter.allow(1) is False

    clock.advance(1.0)
    assert limiter.allow(1) is True


def test_the_clock_is_monotonic_not_wall_clock():
    """A wall clock stepping backwards over NTP would hand out free tokens; `monotonic` cannot
    step backwards, and a negative elapsed is floored at zero regardless."""
    clock = _Clock()
    limiter = RateLimiter(rate_per_second=1, clock=clock)
    limiter.allow(1)
    clock.advance(-100.0)
    assert limiter.allow(1) is False


# --- the endpoint ----------------------------------------------------------------------------


@pytest.fixture
def slow_client(settings: Settings):
    """A gateway that allows two orders a second, so the limit is reachable in a test.

    Only this one domain parameter is overridden, and only in the fixture — the real
    configuration ships 1000/sec and `tests/config/test_settings.py` asserts that figure.
    """
    limited = dataclasses.replace(settings, max_orders_per_second=2)
    with TestClient(create_app(limited)) as client:
        yield client


@pytest.fixture
def slow_logged_in(slow_client: TestClient, pump_for) -> TestClient:
    body = {"username": "rate_limited", "password": "correct-horse-battery"}
    assert slow_client.post("/auth/register", json=body).status_code == 201
    pump_for(slow_client)()
    assert slow_client.post("/auth/login", json=body).status_code == 200
    return slow_client


def _order(coid: int, symbol_id: int) -> dict:
    return {
        "client_order_id": coid, "symbol_id": symbol_id, "side": int(Side.BUY),
        "tif": int(Tif.GTC), "price_ticks": 100, "qty": 1,
    }


def test_the_third_order_in_a_second_is_refused(slow_logged_in: TestClient, settings):
    symbol_id = settings.symbols[0].symbol_id
    codes = [
        slow_logged_in.post("/orders", json=_order(n, symbol_id)).status_code
        for n in range(1, 4)
    ]
    assert codes == [202, 202, 429]


def test_a_refusal_names_when_to_come_back(slow_logged_in: TestClient, settings):
    symbol_id = settings.symbols[0].symbol_id
    for n in range(1, 3):
        slow_logged_in.post("/orders", json=_order(n, symbol_id))
    response = slow_logged_in.post("/orders", json=_order(3, symbol_id))

    assert response.status_code == 429
    assert response.json()["detail"]["reason"] == "MAX_ORDERS_PER_SECOND"
    assert int(response.headers["Retry-After"]) >= 1


def test_a_refusal_does_not_poison_the_idempotency_key(
    slow_logged_in: TestClient, settings
):
    """The most important test here.

    A 429 means "not processed, ask again". If the limiter ran *after* the idempotency claim,
    that `client_order_id` would be answered "rejected" for the whole one-hour TTL, and the
    client's correct retry of the very same intent would keep getting the refusal back — a
    transient limit turned into a permanently dead order id.
    """
    symbol_id = settings.symbols[0].symbol_id
    for n in range(1, 3):
        slow_logged_in.post("/orders", json=_order(n, symbol_id))

    refused = slow_logged_in.post("/orders", json=_order(99, symbol_id))
    assert refused.status_code == 429

    # Wait out the bucket, then retry the identical request.
    import time

    time.sleep(1.1)
    retried = slow_logged_in.post("/orders", json=_order(99, symbol_id))
    assert retried.status_code == 202, retried.text


def test_a_bot_is_limited_exactly_like_a_browser(slow_client: TestClient, pump_for, settings):
    """Task 4.4's boundary: bots go through the real API and are not special-cased anywhere in
    the gateway. Nothing in the request identifies a client as one, which is the point — so
    this test is really the assertion that no such thing exists to special-case on.
    """
    symbol_id = settings.symbols[0].symbol_id
    pump = pump_for(slow_client)
    for name in ("a_browser", "a_bot"):
        body = {"username": name, "password": "correct-horse-battery"}
        assert slow_client.post("/auth/register", json=body).status_code == 201
        pump()
        assert slow_client.post("/auth/login", json=body).status_code == 200
        codes = [
            slow_client.post("/orders", json=_order(n, symbol_id)).status_code
            for n in range(1, 4)
        ]
        assert codes == [202, 202, 429], name
