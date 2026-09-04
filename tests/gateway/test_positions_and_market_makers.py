"""Selling costs inventory, not cash — and only a market maker may run out of it.

Two defects, fixed together because either alone leaves the sell path incoherent:

- A sell reserved `limit x qty` in **cash**, which it does not need and would never release.
  Two sells of half the grant each exhausted an account that had spent nothing.
- Nothing checked the **position**. `RejectReason.INSUFFICIENT_POSITION` had been in the frozen
  contract since Task 1.1 and was raised by nothing, so every account could short without limit
  — violating Open Issue 004 section 5, invariant 2 in the one direction it exists to forbid.

The designated-market-maker exemption is Open Issue 005 section 10.6, confirmed, and Task 4.4's
Boundaries: retail accounts are cash accounts; a bot market maker may hold negative inventory in
exchange for quoting obligations.
"""

from __future__ import annotations

import dataclasses

import pytest
from fastapi.testclient import TestClient

from config.settings import Settings
from contracts.v1.generated.contracts import (
    AccountCreated,
    Fill,
    OrderAccepted,
    OrderCancelled,
    Side,
    Tif,
)
from services.gateway.app import create_app
from services.gateway.risk import Reservation, RiskState

BUY, SELL = int(Side.BUY), int(Side.SELL)
GRANT = 1_000_000


def _accepted(order_id: int, *, user_id: int, side: int, price: int, qty: int, symbol: int = 1):
    return OrderAccepted.new(
        timestamp_ns=0, order_id=order_id, client_order_id=order_id, user_id=user_id,
        price_ticks=price, qty=qty, symbol_id=symbol, side=side, tif=int(Tif.GTC),
    )


def _funded(*user_ids: int) -> RiskState:
    state = RiskState()
    for user_id in user_ids:
        state.apply(AccountCreated.new(
            timestamp_ns=0, client_order_id=0, user_id=user_id, initial_cash_ticks=GRANT,
        ))
    return state


# --- defect A: a sell reserved cash it never released -----------------------------------------


def test_a_sell_reserves_no_cash():
    """The defect, at its smallest. A sell delivers units and *receives* cash."""
    state = _funded(1)
    state.positions[(1, 1)] = 100

    state.reserve(user_id=1, client_order_id=1, symbol_id=1, side=SELL,
                  price_ticks=500_000, qty=1)

    assert state.available_cash(1) == GRANT, "a sell must not tie up buying power"


def test_two_large_sells_do_not_exhaust_an_account_that_spent_nothing():
    """The behaviour as observed on the live stack: two sells of half the grant each were
    accepted and the third was refused INSUFFICIENT_CASH, with settled cash untouched."""
    state = _funded(1)
    state.positions[(1, 1)] = 100

    for n in range(1, 4):
        state.reserve(user_id=1, client_order_id=n, symbol_id=1, side=SELL,
                      price_ticks=500_000, qty=1)

    assert state.available_cash(1) == GRANT
    assert state.available_position(1, 1) == 97


def test_a_sell_reserves_inventory_instead():
    """The mirror of the cash reservation: without it the same ten units could be offered on
    ten separate orders."""
    state = _funded(1)
    state.positions[(1, 1)] = 10

    state.reserve(user_id=1, client_order_id=1, symbol_id=1, side=SELL, price_ticks=100, qty=6)

    assert state.position(1, 1) == 10, "nothing has traded yet"
    assert state.available_position(1, 1) == 4
    assert state.reject_reason_for(
        user_id=1, symbol_id=1, side=SELL, price_ticks=100, qty=5
    ) is not None, "the reserved six are not available to sell again"


def test_a_cancelled_sell_returns_its_inventory():
    """A cancelled sell released nothing, so an account that quoted and cancelled all day
    slowly lost the ability to quote at all."""
    state = _funded(1)
    state.positions[(1, 1)] = 10
    state.reserve(user_id=1, client_order_id=1, symbol_id=1, side=SELL, price_ticks=100, qty=6)
    state.apply(_accepted(1, user_id=1, side=SELL, price=100, qty=6))
    assert state.available_position(1, 1) == 4

    state.apply(OrderCancelled.new(
        timestamp_ns=0, order_id=1, client_order_id=1, user_id=1,
        remaining_qty=6, symbol_id=1, reason=1,
    ))

    assert state.available_position(1, 1) == 10


def test_a_cancelled_buy_still_returns_its_cash():
    """The buy path must not have regressed while the sell path was being fixed."""
    state = _funded(1)
    state.reserve(user_id=1, client_order_id=1, symbol_id=1, side=BUY, price_ticks=1_000, qty=10)
    state.apply(_accepted(1, user_id=1, side=BUY, price=1_000, qty=10))
    assert state.available_cash(1) == GRANT - 10_000

    state.apply(OrderCancelled.new(
        timestamp_ns=0, order_id=1, client_order_id=1, user_id=1,
        remaining_qty=10, symbol_id=1, reason=1,
    ))

    assert state.available_cash(1) == GRANT


# --- defect B: nothing checked the position ---------------------------------------------------


def test_a_retail_account_cannot_sell_what_it_does_not_own():
    state = _funded(1)
    assert state.reject_reason_for(
        user_id=1, symbol_id=1, side=SELL, price_ticks=100, qty=1
    ).name == "INSUFFICIENT_POSITION"


def test_a_retail_account_may_sell_exactly_what_it_owns_and_no_more():
    state = _funded(1)
    state.positions[(1, 1)] = 5

    assert state.reject_reason_for(
        user_id=1, symbol_id=1, side=SELL, price_ticks=100, qty=5
    ) is None
    assert state.reject_reason_for(
        user_id=1, symbol_id=1, side=SELL, price_ticks=100, qty=6
    ).name == "INSUFFICIENT_POSITION"


def test_inventory_is_per_symbol():
    """Owning QAA is not permission to sell QAB. The matcher holds one book per symbol and the
    risk state has to agree, or an account could sell one instrument out of another's holding."""
    state = _funded(1)
    state.positions[(1, 1)] = 10

    assert state.reject_reason_for(
        user_id=1, symbol_id=1, side=SELL, price_ticks=100, qty=10
    ) is None
    assert state.reject_reason_for(
        user_id=1, symbol_id=2, side=SELL, price_ticks=100, qty=1
    ).name == "INSUFFICIENT_POSITION"


def test_a_fill_moves_inventory_for_both_counterparties():
    state = _funded(1, 2)
    state.positions[(1, 1)] = 10
    state.reserve(user_id=1, client_order_id=1, symbol_id=1, side=SELL, price_ticks=100, qty=10)
    state.apply(_accepted(1, user_id=1, side=SELL, price=100, qty=10))
    state.apply(_accepted(2, user_id=2, side=BUY, price=100, qty=10))

    state.apply(Fill.new(
        timestamp_ns=0, maker_order_id=1, taker_order_id=2, maker_user_id=1, taker_user_id=2,
        price_ticks=100, qty=10, symbol_id=1, aggressor_side=BUY,
    ))

    assert state.position(1, 1) == 0, "the seller delivered all ten"
    assert state.position(2, 1) == 10
    # Quantity conservation (Open Issue 004 section 5, invariant 4).
    assert state.position(1, 1) + state.position(2, 1) == 10


def test_a_filled_sell_releases_its_reserved_inventory():
    state = _funded(1, 2)
    state.positions[(1, 1)] = 10
    state.reserve(user_id=1, client_order_id=1, symbol_id=1, side=SELL, price_ticks=100, qty=4)
    state.apply(_accepted(1, user_id=1, side=SELL, price=100, qty=4))
    state.apply(_accepted(2, user_id=2, side=BUY, price=100, qty=4))
    state.apply(Fill.new(
        timestamp_ns=0, maker_order_id=1, taker_order_id=2, maker_user_id=1, taker_user_id=2,
        price_ticks=100, qty=4, symbol_id=1, aggressor_side=BUY,
    ))

    # Six units left, none of them still committed to the order that just completed.
    assert state.position(1, 1) == 6
    assert state.available_position(1, 1) == 6


# --- the designated market maker --------------------------------------------------------------


def test_a_market_maker_may_sell_inventory_it_does_not_have():
    """The whole exemption, in one assertion. A quoter that cannot offer without inventory has
    no ask side until it has bought something, and the book goes one-sided — the exact failure
    the market maker exists to prevent (Open Issue 005 section 5e)."""
    state = _funded(1)
    state.market_makers.add(1)

    assert state.reject_reason_for(
        user_id=1, symbol_id=1, side=SELL, price_ticks=100, qty=1_000
    ) is None


def test_a_market_maker_still_cannot_buy_beyond_its_cash():
    """The exemption is inventory-only. Nothing about being a market maker creates money."""
    state = _funded(1)
    state.market_makers.add(1)

    assert state.reject_reason_for(
        user_id=1, symbol_id=1, side=BUY, price_ticks=GRANT + 1, qty=1
    ).name == "INSUFFICIENT_CASH"


def test_the_privilege_comes_from_configuration_and_not_from_the_request(
    settings: Settings, client: TestClient, pump
):
    """A name on the configured list gets it; an unlisted name does not, however it registers.

    The list is explicit rather than a `dmm_*` prefix rule precisely so the exemption cannot be
    claimed by choosing a username.
    """
    listed = sorted(settings.designated_market_maker_accounts)[0]
    for username, expected in ((listed, True), ("dmm_impostor", False)):
        response = client.post(
            "/auth/register", json={"username": username, "password": "correct-horse-battery"}
        )
        assert response.status_code == 201, response.text
        user_id = response.json()["user_id"]
        assert client.app.state.risk.is_market_maker(user_id) is expected, username


def test_a_restart_resolves_the_privilege_again_from_the_configuration(
    settings: Settings, client: TestClient
):
    """`RiskState` lives in process memory, so the set has to be rebuilt on every boot. If it
    were not, a restarted gateway would refuse its own market maker's next quote."""
    listed = sorted(settings.designated_market_maker_accounts)[0]
    registered = client.post(
        "/auth/register", json={"username": listed, "password": "correct-horse-battery"}
    )
    user_id = registered.json()["user_id"]

    with TestClient(create_app(settings)) as restarted:
        assert restarted.app.state.risk.is_market_maker(user_id) is True


def test_removing_a_name_from_the_configuration_cannot_strand_a_short(
    settings: Settings, client: TestClient
):
    """Demotion is safe in one direction only, and that direction is the safe one.

    An account that already holds negative inventory becomes retail on the next restart. It
    cannot sell further — `available_position` is already below zero — so it can only buy back.
    The position heals rather than compounding.
    """
    demoted = dataclasses.replace(settings, designated_market_maker_accounts=frozenset())
    state = RiskState()
    state.settled_cash[1] = GRANT
    state.positions[(1, 1)] = -50  # left over from its market-making days
    assert not state.is_market_maker(1)

    assert state.reject_reason_for(
        user_id=1, symbol_id=1, side=SELL, price_ticks=100, qty=1
    ).name == "INSUFFICIENT_POSITION"
    assert state.reject_reason_for(
        user_id=1, symbol_id=1, side=BUY, price_ticks=100, qty=50
    ) is None, "buying back must stay available"
    assert demoted.designated_market_maker_accounts == frozenset()


# --- end to end, over HTTP --------------------------------------------------------------------


def test_a_retail_sell_without_inventory_is_refused_over_http(
    logged_in: TestClient, settings: Settings
):
    response = logged_in.post(
        "/orders",
        json={"client_order_id": 5001, "symbol_id": settings.symbols[0].symbol_id,
              "side": SELL, "tif": int(Tif.GTC), "price_ticks": 1_000, "qty": 1},
    )
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["reason"] == "INSUFFICIENT_POSITION"


def test_a_market_maker_may_quote_both_sides_from_a_standing_start(
    settings: Settings, client: TestClient, pump
):
    """Task 4.4 in miniature: a two-sided quote with zero inventory, which is the state every
    market maker starts a session in."""
    listed = sorted(settings.designated_market_maker_accounts)[0]
    credentials = {"username": listed, "password": "correct-horse-battery"}
    assert client.post("/auth/register", json=credentials).status_code == 201
    pump()
    assert client.post("/auth/login", json=credentials).status_code == 200

    symbol_id = settings.symbols[0].symbol_id
    bid = client.post("/orders", json={
        "client_order_id": 1, "symbol_id": symbol_id, "side": BUY,
        "tif": int(Tif.GTC), "price_ticks": 999, "qty": 100,
    })
    ask = client.post("/orders", json={
        "client_order_id": 2, "symbol_id": symbol_id, "side": SELL,
        "tif": int(Tif.GTC), "price_ticks": 1_001, "qty": 100,
    })

    assert bid.status_code == 202, bid.text
    assert ask.status_code == 202, ask.text


def test_buying_then_selling_works_for_a_retail_account_over_http(
    settings: Settings, client: TestClient, pump
):
    """The path a real user takes: buy, then sell what you bought — and no more."""
    symbol_id = settings.symbols[0].symbol_id
    maker = {"username": sorted(settings.designated_market_maker_accounts)[0],
             "password": "correct-horse-battery"}
    retail = {"username": "retail_one", "password": "correct-horse-battery"}
    for credentials in (maker, retail):
        assert client.post("/auth/register", json=credentials).status_code == 201
    pump()

    # The market maker offers ten units it does not own.
    client.post("/auth/login", json=maker)
    assert client.post("/orders", json={
        "client_order_id": 1, "symbol_id": symbol_id, "side": SELL,
        "tif": int(Tif.GTC), "price_ticks": 1_000, "qty": 10,
    }).status_code == 202
    pump()

    # The retail account lifts the offer, then sells five of the ten back.
    client.post("/auth/login", json=retail)
    assert client.post("/orders", json={
        "client_order_id": 1, "symbol_id": symbol_id, "side": BUY,
        "tif": int(Tif.GTC), "price_ticks": 1_000, "qty": 10,
    }).status_code == 202
    pump()

    assert client.post("/orders", json={
        "client_order_id": 2, "symbol_id": symbol_id, "side": SELL,
        "tif": int(Tif.GTC), "price_ticks": 1_100, "qty": 5,
    }).status_code == 202, "selling what it owns must work"

    over = client.post("/orders", json={
        "client_order_id": 3, "symbol_id": symbol_id, "side": SELL,
        "tif": int(Tif.GTC), "price_ticks": 1_100, "qty": 6,
    })
    assert over.status_code == 409, over.text
    assert over.json()["detail"]["reason"] == "INSUFFICIENT_POSITION"
