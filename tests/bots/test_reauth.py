"""A bot whose session has expired logs in again and keeps trading.

`session.ttl_seconds` is absolute, not sliding — `SessionStore.create` sets the key with
`ex=ttl` and `SessionStore.user_id` reads it without renewing — so a bot quoting every second
loses its session on the twelfth hour exactly as an idle one would. Unhandled, the market
stops while every container stays healthy, which is how it was found: nine hours of
`POST /orders` → 401 behind a `(healthy)` gateway.

These tests run against `httpx.MockTransport` rather than a stack. What is being asserted is
the *protocol* — how many requests go out, and which `client_order_id` they carry — and a
transport is the only place both are visible. The live half of this fix is
`test_session_expiry.py`, which destroys a real session through `POST /auth/logout`.
"""

from __future__ import annotations

import contextlib
import json

import httpx
import pytest

from services.bots.client import BotClient

pytestmark = pytest.mark.anyio

BASE = "http://gateway"


@contextlib.asynccontextmanager
async def bot(handler):
    """A `BotClient` wired to a scripted transport instead of a socket.

    Not `BotClient.__aenter__`: that builds its own `AsyncClient` over a real socket and would
    overwrite the mock. Nothing in production changes to allow this — the transport is simply
    where a scripted gateway plugs in.
    """
    client = BotClient(BASE, "mm_qaa", "password")
    client._client = httpx.AsyncClient(
        base_url=BASE, transport=httpx.MockTransport(handler)
    )
    try:
        yield client
    finally:
        await client._client.aclose()


def login_ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"user_id": 7})


def unauthorised() -> httpx.Response:
    return httpx.Response(401, json={"detail": "not authenticated"})


async def test_an_expired_session_is_renewed_and_the_order_resent():
    """The whole fix, in one assertion: the order that met a 401 is placed after a re-login."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/auth/login":
            return login_ok(request)
        # The first order meets a dead session; the second, after the login, succeeds.
        orders = [r for r in seen if r.url.path == "/orders"]
        if len(orders) == 1:
            return unauthorised()
        return httpx.Response(202, json={"seq": "1788885539965-0"})

    async with bot(handler) as client:
        outcome = await client.submit_limit(
            symbol_id=1, side=0, price_ticks=100, qty=5
        )

    assert outcome.accepted
    assert outcome.seq == "1788885539965-0"
    assert [r.url.path for r in seen] == ["/orders", "/auth/login", "/orders"]


async def test_the_resent_order_carries_the_same_client_order_id():
    """Open Issue 008: one id per *decision*, held fixed across every retry of it.

    A retry that allocated a fresh key would be a new order wearing a retry's clothes — and
    since a 401 is raised by the `CurrentUser` dependency, before the rate limiter and before
    `idempotency.claim`, nothing was recorded against the first key. Resending it is the
    protocol working, not a duplicate.
    """
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/auth/login":
            return login_ok(request)
        bodies.append(json.loads(request.content))
        return unauthorised() if len(bodies) == 1 else httpx.Response(202, json={})

    async with bot(handler) as client:
        outcome = await client.submit_limit(
            symbol_id=3, side=1, price_ticks=8_208_718, qty=2
        )

    assert len(bodies) == 2
    assert bodies[0] == bodies[1]
    assert bodies[0]["client_order_id"] == outcome.client_order_id


async def test_a_persistent_401_retries_exactly_once_and_then_gives_up():
    """One retry, never a loop. A gateway answering 401 forever must not spin a bot."""
    orders = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal orders
        if request.url.path == "/auth/login":
            return login_ok(request)
        orders += 1
        return unauthorised()

    async with bot(handler) as client:
        outcome = await client.submit_limit(
            symbol_id=1, side=0, price_ticks=100, qty=5
        )

    assert orders == 2
    assert not outcome.accepted
    assert not outcome.rate_limited and not outcome.halted


async def test_a_failed_relogin_returns_the_original_refusal():
    """If the login itself is refused there is nothing to retry with, and the caller is told."""
    attempts = {"orders": 0, "login": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/auth/login":
            attempts["login"] += 1
            return httpx.Response(503, json={"detail": "halted"})
        attempts["orders"] += 1
        return unauthorised()

    async with bot(handler) as client:
        outcome = await client.submit_limit(
            symbol_id=1, side=0, price_ticks=100, qty=5
        )

    assert attempts == {"orders": 1, "login": 1}
    assert not outcome.accepted


@pytest.mark.parametrize(
    "status, field",
    [(429, "rate_limited"), (503, "halted")],
)
async def test_a_non_401_failure_triggers_no_relogin(status: int, field: str):
    """Rate limiting and a halt are answers, not authentication problems.

    They already have meanings the bots act on — back off, and wait for the producer — and a
    login in the middle of either would be a request spent on the wrong diagnosis.
    """
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(status, json={"detail": {"reason": "NOPE"}})

    async with bot(handler) as client:
        outcome = await client.submit_limit(
            symbol_id=1, side=0, price_ticks=100, qty=5
        )

    assert paths == ["/orders"]
    assert not outcome.accepted
    assert getattr(outcome, field)


async def test_the_reads_recover_too_not_only_the_writes():
    """`refresh()` swallowed every non-200 silently, which is part of why nine hours of 401s
    produced no bot-side signal at all. It goes through the same door as the writes now."""
    portfolio = {
        "cash_ticks": 9_999,
        "positions": [{"symbol_id": 1, "qty": 4}],
    }
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path == "/auth/login":
            return login_ok(request)
        if seen.count("/portfolio") == 1:
            return unauthorised()
        return httpx.Response(200, json=portfolio)

    async with bot(handler) as client:
        await client.refresh()

    assert client.cash_ticks == 9_999
    assert client.position(1) == 4
    assert seen == ["/portfolio", "/auth/login", "/portfolio"]


async def test_a_cancel_recovers_and_keeps_its_own_key():
    """Two identifiers (Open Issue 008): the cancel is a request in its own right, and its own
    `client_order_id` must survive the retry just as a submit's does."""
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/auth/login":
            return login_ok(request)
        bodies.append(json.loads(request.content))
        return unauthorised() if len(bodies) == 1 else httpx.Response(202, json={})

    async with bot(handler) as client:
        outcome = await client.cancel(target_client_order_id=1_788_885_539_965)

    assert len(bodies) == 2 and bodies[0] == bodies[1]
    assert bodies[0]["client_order_id"] == outcome.client_order_id
    assert outcome.accepted


async def test_the_renewal_is_logged_rather_than_silent(caplog):
    """Four faults in this project have had the same signature — a healthy container doing
    nothing. A self-heal nobody can see is the next one."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/auth/login":
            return login_ok(request)
        return unauthorised() if request.url.path == "/portfolio" else httpx.Response(200)

    with caplog.at_level("WARNING", logger="quant_arena.bots"):
        async with bot(handler) as client:
            await client.refresh()

    events = [
        json.loads(r.message)
        for r in caplog.records
        if r.message.startswith("{")
    ]
    reauth = [e for e in events if e.get("event") == "session_reauth"]
    assert len(reauth) == 1
    assert reauth[0]["username"] == "mm_qaa"
    assert reauth[0]["path"] == "/portfolio"


async def test_sign_in_still_names_a_wrong_password_rather_than_retrying_it():
    """The regression this refactor could most easily cause. `sign_in`'s login must stay off
    `_request`: a 401 there is a stale `QA_BOT_PASSWORD`, which is not transient and must be
    said out loud instead of disappearing behind another login attempt."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/auth/register":
            return httpx.Response(409, json={"detail": "exists"})
        return unauthorised()

    async with bot(handler) as client:
        with pytest.raises(RuntimeError, match="different credentials"):
            await client.sign_in(attempts=2, delay=0)
