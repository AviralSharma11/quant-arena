"""`POST /backtests` and `GET /backtests/{id}` — Task 7.2's server half.

Against the real gateway, the real Redis and a real session, like every other route test here.
The endpoints were in the frozen contract from week 1 (`rest_and_ws.md` §2), so nothing was
added to the REST surface — these fill it in.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from services.gateway.routes_backtests import MAX_DAY


def run(client: TestClient, **body):
    payload = {"symbol": "QAA", "bar_minutes": 5, "first_day": 1, "last_day": 2}
    payload.update(body)
    return client.post("/backtests", json=payload)


# -- the criterion: a user runs one and sees results ---------------------------------------

def test_a_backtest_runs_and_returns_every_metric(logged_in: TestClient):
    response = run(logged_in)
    assert response.status_code == 201, response.text

    body = response.json()
    metrics = body["metrics"]
    for name in (
        "pnl_ticks", "return_pct", "trade_count", "win_rate_pct", "max_drawdown_pct",
        "volatility_per_bar_pct", "sharpe_per_bar", "buy_and_hold_return_pct",
        "excess_return_pct",
    ):
        assert name in metrics, name
    assert body["tick_size_ticks"] == 100


def test_the_response_carries_the_limitation_so_the_page_cannot_invent_its_own(
    logged_in: TestClient,
):
    """Success Criterion 2 of this task. The text travels with the result rather than living in
    the frontend, so the page and the CLI report cannot drift into describing different fill
    models."""
    body = run(logged_in).json()
    assert "no partial fills, no slippage" in body["limitation"]
    assert body["manifest"]["fill_model"] == "next_bar_open"


# -- the id is the manifest hash -----------------------------------------------------------

def test_running_the_same_backtest_twice_returns_the_same_id(logged_in: TestClient):
    """Idempotent for free: Task 7.1 already guarantees byte-identical output under a content
    hash of every input, so two identical requests collapse onto one stored result."""
    first, second = run(logged_in).json(), run(logged_in).json()
    assert first["id"] == second["id"]
    assert first["metrics"] == second["metrics"]


def test_a_different_range_is_a_different_id_and_different_numbers(logged_in: TestClient):
    """Otherwise the id is decoration — two runs could share one and disagree."""
    two_days = run(logged_in, first_day=1, last_day=2).json()
    four_days = run(logged_in, first_day=1, last_day=4).json()
    assert two_days["id"] != four_days["id"]
    assert two_days["metrics"] != four_days["metrics"]


def test_a_stored_result_is_retrievable_by_its_id(logged_in: TestClient):
    created = run(logged_in).json()
    fetched = logged_in.get(f"/backtests/{created['id']}")
    assert fetched.status_code == 200
    assert fetched.json() == created


def test_an_unknown_id_is_a_404_that_explains_how_to_get_it_back(logged_in: TestClient):
    """Honest rather than alarming: the result is recomputable from its manifest, so nothing was
    lost that cannot be asked for again."""
    response = logged_in.get("/backtests/" + "0" * 16)
    assert response.status_code == 404
    assert "re-run the same manifest" in response.json()["detail"]["message"]


# -- the strategy list ---------------------------------------------------------------------

def test_the_strategy_list_is_served_not_hardcoded_in_the_frontend(logged_in: TestClient):
    """The 2026-09-07 decision deleted `STREAM_SYMBOLS` for exactly this: a list in two places
    has the same defect one listing later."""
    body = logged_in.get("/backtests/strategies").json()
    assert [s["id"] for s in body["strategies"]] == ["sma_crossover"]
    assert body["max_day"] == MAX_DAY == 7


# -- refusals ------------------------------------------------------------------------------

def test_an_unknown_symbol_is_a_400_naming_the_symbol(logged_in: TestClient):
    response = run(logged_in, symbol="NOPE")
    assert response.status_code == 400
    assert response.json()["detail"]["reason"] == "UNKNOWN_SYMBOL"


def test_an_unknown_strategy_is_a_400(logged_in: TestClient):
    response = run(logged_in, strategy="martingale")
    assert response.status_code == 400
    assert response.json()["detail"]["reason"] == "UNKNOWN_STRATEGY"


def test_an_inverted_day_range_is_a_400_rather_than_an_empty_result(logged_in: TestClient):
    response = run(logged_in, first_day=5, last_day=2)
    assert response.status_code == 400
    assert response.json()["detail"]["reason"] == "EMPTY_RANGE"


@pytest.mark.parametrize(
    "field, value",
    [("first_day", 0), ("last_day", MAX_DAY + 1), ("bar_minutes", 0), ("bar_minutes", 61)],
)
def test_out_of_range_inputs_are_refused_before_any_work_is_done(
    logged_in: TestClient, field: str, value: int
):
    """The range is capped because a backtest is CPU work inside the gateway, and the gateway
    is the single producer that must stay answerable to HTTP (Open Issue 007)."""
    assert run(logged_in, **{field: value}).status_code == 400


def test_a_range_too_short_to_hold_two_bars_is_a_400(logged_in: TestClient):
    """One bar to decide on and one to fill at. Fixable by widening the range, so the caller's
    problem and not a 500."""
    response = run(logged_in, first_day=1, last_day=1, bar_minutes=60)
    assert response.status_code in (201, 400)
    if response.status_code == 400:
        assert response.json()["detail"]["reason"] == "RANGE_TOO_SHORT"


# -- authentication ------------------------------------------------------------------------

def test_every_backtest_route_requires_a_session(client: TestClient):
    """Consistent with `/portfolio`. A backtest is CPU work, so an unauthenticated one is a
    free denial-of-service against the process that acknowledges orders."""
    assert client.post("/backtests", json={"symbol": "QAA"}).status_code == 401
    assert client.get("/backtests/abc").status_code == 401
    assert client.get("/backtests/strategies").status_code == 401
