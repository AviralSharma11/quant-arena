"""Task 2.1 Success Criteria 4 and 5 — the halt state, against a real Redis outage.

Redis is on the critical path: if it is unreachable no order can be accepted. Open Issue 003
§8.5 requires that this **fail loudly** — an explicit halt with a clear reason — rather than
time out silently or, worst of all, acknowledge an order that was never durably recorded.

This stops the actual Redis container. A mock would prove nothing here: the interesting
behaviour is what a real connection failure does to a running gateway, including on code paths
nobody was thinking about (the session lookup runs before the order handler and reads Redis
too — that is what turned an early version of this into a 500 rather than a 503).

Runs against the compose stack, not a TestClient, because criterion 5 is about a *process*
recovering without being restarted.
"""

from __future__ import annotations

import itertools
import shutil
import subprocess
import time

import httpx
import pytest

GATEWAY = "http://localhost:8000"
COMPOSE_SERVICE = "redis"
CONTAINER = "quant-arena-gateway-1"

pytestmark = pytest.mark.slow


def _compose(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", "compose", *args], capture_output=True, text=True)


def _gateway_started_at() -> str:
    result = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.StartedAt}}", CONTAINER],
        capture_output=True, text=True,
    )
    return result.stdout.strip()


def _wait_for(predicate, timeout: float = 30.0, interval: float = 0.25):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            last = predicate()
            if last:
                return last
        except httpx.TransportError:
            last = None
        time.sleep(interval)
    return last


@pytest.fixture
def running_stack():
    if shutil.which("docker") is None:
        pytest.skip("docker not installed — the halt state was NOT verified")
    try:
        if httpx.get(f"{GATEWAY}/health", timeout=2).status_code != 200:
            raise httpx.TransportError("unhealthy")
    except httpx.TransportError:
        pytest.skip(
            "the compose stack is not running — the halt state was NOT verified. "
            "Start it with: docker compose up -d --build"
        )
    try:
        yield
    finally:
        # Whatever happened, leave Redis running for every other test in the suite.
        _compose("start", COMPOSE_SERVICE)
        _wait_for(lambda: httpx.get(f"{GATEWAY}/health", timeout=2).json()["halted"] is False)


def test_stopping_redis_halts_the_exchange_and_restarting_clears_it(running_stack):
    started_before = _gateway_started_at()

    session = httpx.Client(base_url=GATEWAY, timeout=10.0)
    session.post("/auth/register", json={"username": "halt_probe", "password": "correct-horse-b"})
    login = session.post("/auth/login", json={"username": "halt_probe", "password": "correct-horse-b"})
    assert login.status_code == 200, login.text
    # A price the grant can actually afford. This was 6,412,500 against a 1,000,000 grant,
    # which no account could ever have bought — the test simply never met the risk checks,
    # because it skips whenever the compose stack is down and the stack was usually down.
    order = {"client_order_id": 90001, "symbol_id": 1, "side": 1, "tif": 1,
             "price_ticks": 1_000, "qty": 1}
    # The grant is an event now, so it arrives when the matcher has forwarded it and the
    # gateway has read it back off the outbound stream. Retry rather than sleep a guessed
    # interval — and with a **fresh `client_order_id` each attempt**, because the first
    # rejection is recorded against its key and every retry of that key would correctly get
    # the identical rejection back for the whole idempotency TTL.
    attempts = itertools.count(90001)
    accepted = _wait_for(
        lambda: (
            r := session.post(
                "/orders", json={**order, "client_order_id": next(attempts)}
            )
        ).status_code == 202 and r
    )
    assert accepted, "the grant never reached the gateway's risk state"

    # --- criterion 4 -------------------------------------------------------------------------
    _compose("stop", COMPOSE_SERVICE)

    health = _wait_for(lambda: (h := httpx.get(f"{GATEWAY}/health", timeout=2).json())["halted"] and h)
    assert health and health["halted"], "the gateway never noticed Redis was gone"
    assert health["reason"] == "redis_unreachable"
    assert health["since_ns"] is not None, "a halt must record when it started"

    rejected = session.post("/orders", json={**order, "client_order_id": 90002})
    assert rejected.status_code == 503, (
        f"a halted exchange must reject with 503 and a reason, got {rejected.status_code}: "
        f"{rejected.text}"
    )
    assert rejected.json()["detail"]["reason"] == "redis_unreachable"

    # --- criterion 5 -------------------------------------------------------------------------
    _compose("start", COMPOSE_SERVICE)

    recovered = _wait_for(
        lambda: (h := httpx.get(f"{GATEWAY}/health", timeout=2).json())["halted"] is False and h
    )
    assert recovered and recovered["halted"] is False, "the halt never cleared on its own"
    assert recovered["reason"] is None and recovered["since_ns"] is None

    # A brand-new client, registering and trading for the first time after the outage — so
    # this proves auth, the grant and sequencing all work again, not merely that an existing
    # session survived.
    #
    # It retries, because the *first* write after Redis returns can still fail. redis-py's
    # pool may hand out a connection that died with the server, and the producer treats that
    # as unreachable and refuses the append rather than retrying it. That refusal is correct
    # and deliberate: the pipeline is not transactional, so a flush that errored part-way may
    # already have written some entries, and retrying it inside the producer could duplicate
    # an order. Open Issue 003 §8.5 says fail loudly instead — which puts the retry where it
    # belongs, in the client. A real client retries; a test that gives up after one attempt
    # is asserting that clients never have to.
    fresh = httpx.Client(base_url=GATEWAY, timeout=10.0)
    credentials = {"username": "after_halt", "password": "correct-horse-b"}

    def _registered_and_logged_in() -> bool:
        registered = fresh.post("/auth/register", json=credentials)
        # 409 means a previous attempt already created the row and only the grant failed.
        if registered.status_code not in (201, 409):
            return False
        return fresh.post("/auth/login", json=credentials).status_code == 200

    assert _wait_for(_registered_and_logged_in), "registration never succeeded after recovery"
    # Same asynchronous grant, same retry with a fresh key each attempt. This user registered
    # *during* the halt window's tail, so its `CreateAccount` may still be in flight.
    after = itertools.count(90003)
    accepted = _wait_for(
        lambda: (
            r := fresh.post(
                "/orders", json={**order, "client_order_id": next(after)}
            )
        ).status_code == 202 and r
    )
    assert accepted, "recovered, but never accepted an order"
    assert accepted.json()["seq"], "recovered, but not sequencing"

    # The whole point of criterion 5: nothing was restarted to achieve any of that.
    assert _gateway_started_at() == started_before, (
        "the gateway process restarted — the halt must clear without one"
    )
