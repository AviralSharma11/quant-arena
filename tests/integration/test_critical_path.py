"""T5, thinned — the critical path through the real API (Task 7.3).

Four tests. The plan's Boundaries say *"critical path only — this layer is deliberately thin"*
and *"do not duplicate what T1–T4 already cover"*, so nothing here re-checks matching
semantics, price-time priority, determinism or fee arithmetic. Those have owners. What is
checked here is only ever the thing that stops being true when two processes are wired
together wrongly:

| Test | The joint it holds |
|---|---|
| critical path | HTTP body -> record -> stream -> matcher -> ledger -> Postgres -> HTTP |
| cancellation | the cancel path, and that `GET /orders/open` still routes before `DELETE /orders/{id}` |
| duplicate | one `client_order_id` yields one order, through a real session and a real Redis |
| risk rejection | the gateway refuses *before* the stream, so nothing is appended at all |

Every one of them runs against a stack that is really up. There is no test client, no
dependency override and no in-process app: the point is the network and the database.
"""

from __future__ import annotations

import pytest

from contracts.v1.generated.contracts import Side, Tif
from tests.integration.conftest import (
    Account,
    LayerProbe,
    await_state,
    cancel_anchor,
    limit_order,
    market_order,
    order_anchor,
)

pytestmark = [pytest.mark.slow, pytest.mark.anyio]

#: A price deep enough below fair value that nothing will ever trade against it. The symbols
#: replay real crypto history and sit in the millions of ticks (Task 5.1), so an order down
#: here rests until it is cancelled. That is what makes the resting tests deterministic without
#: reaching into the book to arrange it.
RESTING_PRICE_TARGET = 1_000

#: The symbol every test uses. One is enough — this layer is about wiring, and ten symbols
#: would test the registry, which `GET /symbols` and Task 5.1 already own.
SYMBOL_ID = 1


def resting_price(settings, symbol_id: int) -> int:
    """`RESTING_PRICE_TARGET`, rounded down to a whole number of this symbol's ticks."""
    symbol = next(s for s in settings.symbols if s.symbol_id == symbol_id)
    tick = symbol.tick_size_ticks
    return max(tick, (RESTING_PRICE_TARGET // tick) * tick)


async def open_orders(account: Account) -> list[dict]:
    response = await account.client.get("/orders/open")
    assert response.status_code == 200, f"GET /orders/open: {response.text}"
    return response.json()


async def portfolio(account: Account) -> dict:
    response = await account.client.get("/portfolio")
    assert response.status_code == 200, f"GET /portfolio: {response.text}"
    return response.json()


# --- 1. the critical path -------------------------------------------------------------------


async def test_the_critical_path_reaches_the_read_model(
    account: Account, probe: LayerProbe, settings
):
    """register -> capital -> order -> fill -> the portfolio reflects it.

    The `account` fixture has already proven the first two hops: it does not yield until the
    grant has crossed register -> `CreateAccount` -> matcher -> `AccountCreated` -> ledger ->
    Postgres and surfaced on `GET /portfolio`. What is left is the order.

    A **market** order is used because a fill needs a counterparty and the resting book is the
    only one available. The gateway turns it into a marketable limit banded off the opposing
    side and forces IOC (Task 3.1), so it either trades on arrival or is cancelled — it cannot
    rest and leave this test waiting for a fill that was never going to come.
    """
    before = await portfolio(account)
    assert before["cash_ticks"] == settings.initial_cash_ticks
    assert before["positions"] == [], "a fresh account is not holding anything"

    coid = account.coid()
    since = await probe.position()
    response = await account.client.post(
        "/orders",
        json=market_order(
            client_order_id=coid, symbol_id=SYMBOL_ID, side=Side.BUY, qty=1
        ),
    )

    if response.status_code == 409 and response.json()["detail"]["reason"] == "INVALID_PRICE":
        # Nothing is resting on the ask, so there is no reference price and no band. This is
        # the gateway behaving correctly on an empty book, not a wiring fault — but it means
        # the criterion cannot be verified on this stack. Skipping loudly beats passing
        # quietly, the same way `test_sizes.py` skips without a C++20 compiler.
        pytest.skip(
            "no liquidity: the ask side of symbol "
            f"{SYMBOL_ID} is empty, so a market order has no counterparty and the fill half of "
            "the critical path cannot be exercised. Start the market makers with "
            "`QA_BOT_PASSWORD=... docker compose --profile bots up -d --build` and re-run."
        )

    assert response.status_code == 202, (
        f"the gateway refused the order: {response.status_code} {response.text}"
    )
    body = response.json()
    assert body["status"] == "accepted"
    seq = body["seq"]
    assert seq, "a 202 with no seq — the acknowledgement is not backed by a stream id"

    # The acknowledgement says durable. This is the assertion that checks it, and it is only
    # possible because the Redis stream id *is* the sequence number (Open Issue 003) — there is
    # no parallel counter that could agree with the response while the stream disagreed.
    assert await probe.contains(settings.stream_inbound, seq), (
        f"the gateway returned 202 with seq {seq} but that record is not on "
        f"{settings.stream_inbound}. The acknowledgement is lying: the producer or the "
        f"claim-and-append script is at fault, not the matcher."
    )

    settled = await await_state(
        probe,
        lambda: portfolio(account),
        lambda observed: any(
            p["symbol_id"] == SYMBOL_ID and p["qty"] == 1 for p in observed["positions"]
        ),
        what=f"the fill for seq {seq} to reach GET /portfolio as a position of 1",
        seq=seq,
        anchor=order_anchor(coid),
        since=since,
    )

    # Cash fell by more than nothing, and the position is real. The exact amount is the fee
    # arithmetic Task 2.2 owns; asserting it again here would be the duplication the Boundaries
    # forbid. What this layer is entitled to say is that the two moved together.
    assert settled["cash_ticks"] < before["cash_ticks"], (
        "a position appeared without cash moving — the ledger wrote one side of the fill"
    )


# --- 2. cancellation ------------------------------------------------------------------------


async def test_a_resting_order_can_be_cancelled_through_the_api(
    account: Account, probe: LayerProbe, settings
):
    """The cancel path, end to end, on an order that will not fill underneath it.

    Also standing proof of the route-ordering trap `routes_orders.py` carries a comment about:
    `GET /orders/open` must be registered before `DELETE /orders/{target_client_order_id}`, or
    FastAPI reads "open" as the path parameter and answers 405. Nothing else in the suite calls
    both against a running server.
    """
    price = resting_price(settings, SYMBOL_ID)
    coid = account.coid()

    since = await probe.position()
    placed = await account.client.post(
        "/orders",
        json=limit_order(
            client_order_id=coid,
            symbol_id=SYMBOL_ID,
            side=Side.BUY,
            price_ticks=price,
            qty=1,
            tif=Tif.GTC,
        ),
    )
    assert placed.status_code == 202, f"the gateway refused the order: {placed.text}"
    seq = placed.json()["seq"]

    resting = await await_state(
        probe,
        lambda: open_orders(account),
        lambda observed: any(o["client_order_id"] == coid for o in observed),
        what=f"the resting order {coid} to appear on GET /orders/open",
        seq=seq,
        anchor=order_anchor(coid),
        since=since,
    )
    assert len(resting) == 1, f"one order was placed, {len(resting)} are resting"

    cancel_coid = account.coid()
    since_cancel = await probe.position()
    cancelled = await account.client.request(
        "DELETE", f"/orders/{coid}", json={"client_order_id": cancel_coid}
    )
    assert cancelled.status_code == 202, (
        f"the cancel was refused: {cancelled.status_code} {cancelled.text}. A 405 here means "
        f"GET /orders/open is registered after DELETE /orders/{{id}} and 'open' is being read "
        f"as the path parameter."
    )

    await await_state(
        probe,
        lambda: open_orders(account),
        lambda observed: all(o["client_order_id"] != coid for o in observed),
        what=f"the cancelled order {coid} to leave GET /orders/open",
        seq=cancelled.json()["seq"] or None,
        anchor=cancel_anchor(cancel_coid),
        since=since_cancel,
    )


# --- 3. duplicate submission ----------------------------------------------------------------


async def test_a_duplicate_client_order_id_yields_exactly_one_order(
    account: Account, probe: LayerProbe, settings
):
    """The same `client_order_id` twice is one order, not two.

    Task 3.2 proved this against the Lua script. What it could not prove is the same property
    through a real HTTP request, a real session and a real Redis — the path a retrying client
    actually takes. A duplicate that got through here is a real trade against a real
    counterparty whose position also moved, and it cannot be undone (Open Issue 008).
    """
    price = resting_price(settings, SYMBOL_ID)
    coid = account.coid()
    body = limit_order(
        client_order_id=coid,
        symbol_id=SYMBOL_ID,
        side=Side.BUY,
        price_ticks=price,
        qty=1,
        tif=Tif.GTC,
    )

    since = await probe.position()
    first = await account.client.post("/orders", json=body)
    assert first.status_code == 202, f"the gateway refused the order: {first.text}"
    first_seq = first.json()["seq"]
    assert first_seq

    second = await account.client.post("/orders", json=body)
    assert second.status_code == 202, (
        f"a duplicate was answered {second.status_code}, not 202: {second.text}"
    )
    assert second.json()["seq"] == first_seq, (
        "the retry was given a different seq, which means a second record was appended — "
        "two orders exist where the client sent one"
    )

    await await_state(
        probe,
        lambda: open_orders(account),
        lambda observed: any(o["client_order_id"] == coid for o in observed),
        what=f"the order {coid} to appear on GET /orders/open",
        seq=first_seq,
        anchor=order_anchor(coid),
        since=since,
    )
    resting = await open_orders(account)
    assert len(resting) == 1, (
        f"one client_order_id was sent twice and {len(resting)} orders are resting: {resting}"
    )

    await account.client.request(
        "DELETE", f"/orders/{coid}", json={"client_order_id": account.coid()}
    )


# --- 4. risk rejection ----------------------------------------------------------------------


async def test_an_unaffordable_order_is_refused_before_the_stream(
    account: Account, probe: LayerProbe, settings
):
    """The rejection, and the half of it that only an integration test can see.

    Task 3.1's contract is not merely that an unaffordable order is refused — it is that the
    gateway refuses it **before** appending, so the stream never carries a record the exchange
    would have to reason about. A unit test sees the 409; only this layer can see that nothing
    was written.

    The negative is proved with a barrier rather than a sleep: a *valid* order is sent
    afterwards and waited for. Once it has crossed the whole path and surfaced on
    `GET /orders/open`, everything appended before it has also been processed — so if the
    rejected order had reached the stream, it would be resting here too.
    """
    price = resting_price(settings, SYMBOL_ID)

    # Priced so that price x qty is a hundred times the grant. Nothing about the book matters:
    # the check is cash against reservation, and it happens before any of it.
    unaffordable = await account.client.post(
        "/orders",
        json=limit_order(
            client_order_id=account.coid(),
            symbol_id=SYMBOL_ID,
            side=Side.BUY,
            price_ticks=price,
            qty=(settings.initial_cash_ticks // price) * 100,
            tif=Tif.GTC,
        ),
    )
    assert unaffordable.status_code == 409, (
        f"an order worth a hundred grants was answered {unaffordable.status_code}: "
        f"{unaffordable.text}"
    )
    detail = unaffordable.json()["detail"]
    assert detail["status"] == "rejected"
    assert detail["reason"] == "INSUFFICIENT_CASH", (
        f"refused for the wrong reason: {detail['reason']}"
    )

    barrier_coid = account.coid()
    since = await probe.position()
    barrier = await account.client.post(
        "/orders",
        json=limit_order(
            client_order_id=barrier_coid,
            symbol_id=SYMBOL_ID,
            side=Side.BUY,
            price_ticks=price,
            qty=1,
            tif=Tif.GTC,
        ),
    )
    assert barrier.status_code == 202, f"the barrier order was refused: {barrier.text}"

    await await_state(
        probe,
        lambda: open_orders(account),
        lambda observed: any(o["client_order_id"] == barrier_coid for o in observed),
        what=f"the barrier order {barrier_coid} to appear on GET /orders/open",
        seq=barrier.json()["seq"],
        anchor=order_anchor(barrier_coid),
        since=since,
    )

    resting = await open_orders(account)
    assert len(resting) == 1, (
        f"the rejected order reached the book: {len(resting)} orders are resting where only "
        f"the barrier should be. {resting}"
    )
    assert resting[0]["client_order_id"] == barrier_coid

    # The reservation was released too, so only the barrier's own reservation is outstanding.
    # Cash is settled cash, so it is untouched by a resting buy.
    assert (await portfolio(account))["cash_ticks"] == settings.initial_cash_ticks

    await account.client.request(
        "DELETE", f"/orders/{barrier_coid}", json={"client_order_id": account.coid()}
    )
