"""The four defects the week-3 audit found, each pinned by a test that fails without its fix.

Every one of these passed review and passed the suite as written; what they had in common is
that nothing exercised the *second* time through — a concurrent retry, a restarted process, a
fill at a better price. The tests below are deliberately written against observable behaviour
(what the stream holds, what the next order is allowed to do) rather than against internals,
so a future refactor of `RiskState` cannot quietly make them vacuous.
"""

from __future__ import annotations

import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from config.settings import Settings
from contracts.v1.generated.contracts import (
    OrderAccepted,
    Side,
    Tif,
    unpack_any,
)
from services.gateway.app import create_app
from services.gateway.risk import Reservation, RiskState

BUY, SELL = int(Side.BUY), int(Side.SELL)


def _order(coid: int, *, price: int, qty: int, side: int = BUY, symbol: int = 1) -> dict:
    return {
        "client_order_id": coid,
        "symbol_id": symbol,
        "side": side,
        "tif": int(Tif.GTC),
        "price_ticks": price,
        "qty": qty,
    }


def _await_grant(client: TestClient, settings: Settings, timeout: float = 5.0) -> None:
    """Block until the gateway's own watcher has applied the grant.

    Deliberately waits on the real asynchronous path instead of writing `settled_cash`
    directly — the point of the change is that the balance arrives from the stream, and a test
    that set it by hand would prove the opposite.
    """
    risk = client.app.state.risk
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if any(v == settings.initial_cash_ticks for v in risk.settled_cash.values()):
            return
        time.sleep(0.01)
    raise AssertionError(f"the grant never reached the risk state: {risk.settled_cash}")


def _run_matcher(settings: Settings) -> None:
    """Drain the inbound stream through the matcher, so the outbound stream holds the
    acknowledgements a restarted gateway rebuilds from."""
    from redis.asyncio import Redis

    from services.matcher.runner import Matcher

    async def drain() -> None:
        redis = Redis.from_url(settings.redis_url, decode_responses=False)
        matcher = Matcher(redis, settings)
        await matcher.recover()
        matcher.producer.start()
        while await matcher.step():
            pass
        await matcher.producer.stop()
        await redis.aclose()

    asyncio.run(drain())


def _inbound_count(settings: Settings, client_order_id: int) -> int:
    import redis as redis_sync

    r = redis_sync.Redis.from_url(settings.redis_url)
    try:
        return sum(
            1
            for _sid, fields in r.xrange(settings.stream_inbound)
            if getattr(unpack_any(fields[b"r"]), "client_order_id", None) == client_order_id
        )
    finally:
        r.close()


# ── Defect 1 · concurrent retries created real duplicate orders ────────────────────────────


def test_a_retry_while_the_original_is_in_flight_does_not_submit_a_second_order(
    logged_in: TestClient, settings: Settings
):
    """Success Criterion 2. The claim was always atomic; the route threw the answer away.

    Simulated rather than raced, because a race that reproduces only sometimes is not a
    regression test: the key is claimed first, so the second call is by construction the
    retry that arrives while the original is still in flight.
    """
    import redis as redis_sync

    client = logged_in
    # Plant the raw "in_progress" sentinel the Lua script writes: an original that has claimed
    # the key and has not yet recorded an outcome. Planting it is exact where racing would be
    # intermittent, and a flaky regression test is no regression test.
    r = redis_sync.Redis.from_url(settings.redis_url)
    r.set(f"qa.idempotent:1:4242", "in_progress", ex=3600)
    r.close()

    response = client.post("/orders", json=_order(4242, price=100, qty=1))

    assert response.status_code == 202
    assert response.json()["status"] == "in_progress"
    assert _inbound_count(settings, 4242) == 0, "the retry must not append a second order"


def test_only_one_caller_can_ever_win_the_claim(settings: Settings, _clean):
    """Eight genuinely concurrent claims on one key. This is the property the whole fix rests
    on, so it is raced for real rather than simulated — and it is deterministic in the
    direction that matters: more than one winner can never be correct."""
    from redis.asyncio import Redis

    from services.gateway.idempotency import IdempotencyStore

    async def race() -> list[bool]:
        redis = Redis.from_url(settings.redis_url, decode_responses=True)
        store = IdempotencyStore(redis, 3600)
        outcomes = await asyncio.gather(*(store.claim(1, 9999) for _ in range(8)))
        await redis.aclose()
        return [o.claimed for o in outcomes]

    won = asyncio.run(race())
    assert sum(won) == 1, f"exactly one caller may claim the key, got {sum(won)}"


def test_a_repeated_submission_still_returns_the_stored_outcome(logged_in: TestClient):
    """The sequential path was already correct and must stay that way."""
    client = logged_in
    first = client.post("/orders", json=_order(4243, price=100, qty=1))
    second = client.post("/orders", json=_order(4243, price=100, qty=1))

    assert first.status_code == second.status_code == 202
    assert first.json()["seq"] == second.json()["seq"]
    assert second.json()["status"] == "accepted"


# ── Defect 2 · a restart forgot every reservation ──────────────────────────────────────────


def test_replaying_an_accepted_order_restores_the_reservation():
    """Success Criterion 4.

    A replayed `OrderAccepted` has no pending reservation waiting for it — the process that
    reserved the cash is gone. Rebuilding `open_orders` without rebuilding `reserved` let the
    account commit the same ticks twice after a restart.
    """
    state = RiskState()
    state.settled_cash[1] = 1_000_000

    state.apply(
        OrderAccepted.new(
            timestamp_ns=1, order_id=7, client_order_id=1, user_id=1,
            price_ticks=100, qty=600, symbol_id=1, side=BUY, tif=int(Tif.GTC),
        )
    )

    assert state.reserved[1] == 60_000
    assert state.available_cash(1) == 940_000


def test_a_rebuilt_reservation_is_not_double_counted_when_it_was_observed_live():
    """The live path must stay as it was: reserve on submit, and the stream record that
    follows confirms it rather than reserving a second time."""
    state = RiskState()
    state.settled_cash[1] = 1_000_000
    state.reserve(user_id=1, client_order_id=1, symbol_id=1, side=BUY, price_ticks=100, qty=600)
    assert state.reserved[1] == 60_000

    state.apply(
        OrderAccepted.new(
            timestamp_ns=1, order_id=7, client_order_id=1, user_id=1,
            price_ticks=100, qty=600, symbol_id=1, side=BUY, tif=int(Tif.GTC),
        )
    )

    assert state.reserved[1] == 60_000, "the confirmation must not reserve again"


def test_a_restarted_gateway_still_refuses_an_order_it_has_no_cash_for(
    settings: Settings, client: TestClient
):
    """The whole defect, end to end: commit almost everything, restart, and check the
    commitment survived. A second `create_app` against the same stores is a real restart —
    nothing about the risk state lives on the app object."""
    body = {"username": "restart_risk", "password": "correct-horse-battery"}
    assert client.post("/auth/register", json=body).status_code == 201
    assert client.post("/auth/login", json=body).status_code == 200

    # From week 4 the grant is a `CreateAccount` on the inbound stream rather than a row the
    # gateway wrote, so the account has no cash until the matcher has forwarded it and the
    # gateway's watcher has read the `AccountCreated` back off the outbound stream.
    _run_matcher(settings)
    _await_grant(client, settings)

    grant = settings.initial_cash_ticks
    assert client.post("/orders", json=_order(1, price=grant - 1_000, qty=1)).status_code == 202
    # Only 1,000 ticks are left uncommitted.
    assert client.post("/orders", json=_order(2, price=2_000, qty=1)).status_code == 409

    # The matcher acknowledges the order, exactly as it does in the running stack. Until the
    # engine has answered, the order exists only on the inbound stream and the reservation is
    # genuinely unrecoverable — the outbound stream is what the gateway rebuilds from.
    _run_matcher(settings)

    with TestClient(create_app(settings)) as restarted:
        restarted.post("/auth/login", json=body)
        after = restarted.post("/orders", json=_order(3, price=2_000, qty=1))

    assert after.status_code == 409, (
        "after a restart the gateway forgot the reservation and let the account "
        "commit money it had already spent"
    )


# ── Defect 3 · price improvement stranded cash forever ─────────────────────────────────────


def test_a_fill_better_than_the_limit_releases_the_whole_reservation():
    """Success Criterion 2 of Task 3.1: reserve at the limit, settle at the fill.

    Reserved 10 x 1000, filled 10 x 900. Releasing at the fill price left the 1,000 of price
    improvement reserved for the life of the process.
    """
    state = RiskState()
    state.settled_cash[1] = 1_000_000
    state.open_orders[5] = Reservation(
        user_id=1, order_id=5, client_order_id=1, symbol_id=1,
        side=BUY, price_ticks=1_000, qty=10,
    )
    state.reserved[1] = 10_000

    state._release_fill(5, qty=10)

    assert state.reserved[1] == 0, "the price improvement must not stay reserved"
    assert 5 not in state.open_orders


def test_a_filled_sell_leaves_the_resting_book():
    """Sells tie up no cash, but they must still retire — `best_ask` reads this state."""
    state = RiskState()
    state.open_orders[6] = Reservation(
        user_id=2, order_id=6, client_order_id=1, symbol_id=1,
        side=SELL, price_ticks=900, qty=10,
    )

    state._release_fill(6, qty=10)

    assert 6 not in state.open_orders
    assert state.best_ask(1) is None


# ── Defect 4 · banded market orders were never built ───────────────────────────────────────


def test_a_market_buy_becomes_a_limit_at_the_banded_best_ask(settings: Settings):
    state = RiskState()
    state.open_orders[1] = Reservation(
        user_id=2, order_id=1, client_order_id=1, symbol_id=1,
        side=SELL, price_ticks=1_000, qty=5,
    )

    banded = state.banded_market_price(symbol_id=1, side=BUY, band_bps=500)

    assert state.best_ask(1) == 1_000
    assert banded == 1_050, "best_ask x 1.05, as Task 3.1 specifies"


def test_a_market_order_into_an_empty_book_has_no_price_and_is_refused(logged_in: TestClient):
    """Success Criterion 6. With nothing resting on the other side there is no reference
    price, so there is no band — and a market order with no band is the thing the criterion
    forbids."""
    response = logged_in.post(
        "/orders",
        json={
            "client_order_id": 77,
            # A *listed* symbol with an empty book. Symbol 3 was used here until week 4, when
            # the gateway gained a registry and began rejecting it as UNKNOWN_SYMBOL — which
            # would have passed this test for entirely the wrong reason.
            "symbol_id": 2,
            "side": BUY,
            "tif": int(Tif.GTC),
            "qty": 1,
            "order_type": "market",
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"]["reason"] == "INVALID_PRICE"


def test_a_market_order_may_not_carry_its_own_price(logged_in: TestClient):
    response = logged_in.post(
        "/orders",
        json={
            "client_order_id": 78, "symbol_id": 1, "side": BUY, "tif": int(Tif.GTC),
            "qty": 1, "price_ticks": 100, "order_type": "market",
        },
    )
    assert response.status_code == 400


def test_a_limit_order_must_carry_a_price(logged_in: TestClient):
    response = logged_in.post(
        "/orders",
        json={
            "client_order_id": 79, "symbol_id": 1, "side": BUY,
            "tif": int(Tif.GTC), "qty": 1,
        },
    )
    assert response.status_code == 400


def test_the_band_is_configuration_not_a_constant(settings: Settings):
    assert settings.market_order_band_bps == 500

    state = RiskState()
    state.open_orders[1] = Reservation(
        user_id=2, order_id=1, client_order_id=1, symbol_id=4,
        side=BUY, price_ticks=1_000, qty=5,
    )
    # A market sell floors at best_bid x (1 - band).
    assert state.banded_market_price(symbol_id=4, side=SELL, band_bps=500) == 950
    assert state.banded_market_price(symbol_id=4, side=SELL, band_bps=1_000) == 900


# --- the idempotency key's two lifetimes ---------------------------------------------------------


@pytest.fixture
def idempotency_store(settings: Settings):
    """An `IdempotencyStore` on the test Redis, driven directly.

    Directly rather than through the app, because what is under test is the TTL Redis actually
    holds on the key — which no HTTP response reveals.
    """
    from redis.asyncio import Redis

    from services.gateway.idempotency import IdempotencyStore

    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    yield IdempotencyStore(redis, settings.idempotency_ttl_seconds)


@pytest.mark.anyio
async def test_an_abandoned_claim_expires_in_seconds_not_an_hour(idempotency_store):
    """A gateway that dies between the claim and the record must not lock the key for an hour.

    One Redis key served two very different lifetimes: "a request is in flight" (milliseconds)
    and "this answer is replayable" (Open Issue 008's hour). Writing the sentinel at the hour
    meant a crash inside the claim-to-record window left the key holding `in_progress` with no
    outcome — so every retry read the sentinel, got `claimed=False`, and was answered
    `202 in_progress` for the full hour. A client following the contract exactly ("await the
    private stream, then retry") never gets an answer and the order is neither placed nor
    refused.
    """
    store = idempotency_store
    key = store._key(7, 4242)

    claimed = await store.claim(7, 4242)
    assert claimed.claimed is True

    ttl = await store.redis.ttl(key)
    assert 0 < ttl <= store.in_flight_ttl_seconds, (
        f"the in-flight sentinel is holding a {ttl}s TTL; it must expire in seconds so an "
        f"abandoned claim costs the client one retry rather than an hour"
    )
    assert store.in_flight_ttl_seconds < store.ttl_seconds, (
        "the sentinel must be shorter-lived than the outcome it stands in for"
    )
    await store.redis.aclose()


@pytest.mark.anyio
async def test_a_recorded_outcome_still_keeps_the_full_replay_ttl(idempotency_store):
    """The short TTL is for the *claim* only. Once an answer exists it must stay replayable for
    the window Open Issue 008 specifies, or a legitimate retry would be processed twice."""
    store = idempotency_store
    key = store._key(8, 5150)

    await store.claim(8, 5150)
    await store.record_accepted(8, 5150, None, "1693526400000-4")

    ttl = await store.redis.ttl(key)
    assert ttl > store.in_flight_ttl_seconds
    assert ttl <= store.ttl_seconds

    # And it replays as the same answer, which is the whole point of keeping it.
    outcome = await store.claim(8, 5150)
    assert outcome.status == "accepted"
    assert outcome.seq == "1693526400000-4"
    assert outcome.claimed is False
    await store.redis.aclose()
