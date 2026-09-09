"""The live half of the re-authentication fix, against a running exchange.

`test_reauth.py` proves the protocol against a scripted transport — how many requests go out
and which key they carry. This proves the thing that actually broke: a real session, really
destroyed on the server, and a bot that keeps trading through it.

`POST /auth/logout` is the instrument. It deletes the Redis session key, which is exactly what
`ex=session.ttl_seconds` does twelve hours in — same server state, same 401, without waiting
half a day for it. Nothing here reaches into Redis; the whole test runs through the public API,
which is also the point of Task 4.4's Boundaries.
"""

from __future__ import annotations

import json
import logging
import shutil
import sys
import uuid
from pathlib import Path

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from contracts.v1.generated.contracts import Side  # noqa: E402
from services.bots.client import BotClient  # noqa: E402

pytestmark = [pytest.mark.slow, pytest.mark.anyio]

GATEWAY = "http://localhost:8000"

#: Roughly how far below the market the order sits. Far enough that it rests instead of
#: crossing: the assertion is about the session surviving, not about a fill, and a resting
#: order needs no counterparty — so this passes on a stack whose bots are stopped.
RESTING_TICKS_TARGET = 1_000


def resting_price(symbol: dict) -> int:
    """The target, rounded down to a whole number of this symbol's ticks, never zero."""
    tick = symbol["tick_size_ticks"]
    return max(tick, (RESTING_TICKS_TARGET // tick) * tick)


@pytest.fixture(scope="module")
def running_stack():
    if shutil.which("docker") is None:
        pytest.skip("docker is not installed — session recovery was NOT verified")
    try:
        health = httpx.get(f"{GATEWAY}/health", timeout=2)
    except httpx.TransportError:
        pytest.skip(
            "the compose stack is not running — session recovery was NOT verified. "
            "Start it with: docker compose up -d --build"
        )
    if health.status_code != 200 or health.json()["halted"]:
        pytest.skip("the exchange is halted — session recovery was NOT verified")


@pytest.fixture
def symbol(running_stack) -> dict:
    """The first listed symbol, read from the exchange rather than hardcoded.

    `GET /symbols` is the only source of names and scales (the 2026-09-07 decision that deleted
    `STREAM_SYMBOLS`), and the resting price below has to be a whole number of *this* symbol's
    ticks or the gateway refuses it for a reason that has nothing to do with sessions.
    """
    symbols = httpx.get(f"{GATEWAY}/symbols", timeout=5).json()["symbols"]
    assert symbols, "the exchange lists no symbols"
    return symbols[0]


async def test_a_bot_trades_on_through_a_destroyed_session(symbol, caplog):
    """The nine-hour outage, reproduced in a second and then survived.

    Without the fix the submit below returns 401 and the bot quotes nothing ever again, while
    every container continues to report healthy — which is precisely how this was missed.
    """
    username = f"reauth_{uuid.uuid4().hex[:12]}"
    async with BotClient(GATEWAY, username, "correct-horse-battery-bots") as client:
        await client.sign_in()
        await client.await_funding()

        # Prove the account works before anything is taken away from it, so a failure after
        # the logout cannot be blamed on funding or on the symbol.
        first = await client.submit_limit(
            symbol_id=symbol["symbol_id"], side=int(Side.BUY),
            price_ticks=resting_price(symbol), qty=symbol["lot_size"],
        )
        assert first.accepted, f"the account could not trade before the logout: {first.reason}"

        # Destroy the session server-side. This is what the TTL does at twelve hours.
        logout = await client.http.post("/auth/logout")
        assert logout.status_code == 204

        with caplog.at_level(logging.WARNING, logger="quant_arena.bots"):
            second = await client.submit_limit(
                symbol_id=symbol["symbol_id"], side=int(Side.BUY),
                price_ticks=resting_price(symbol), qty=symbol["lot_size"],
            )

    assert second.accepted, f"the bot did not recover its session: {second.reason}"
    assert second.seq, "the recovered order was acknowledged without a sequence number"

    reauth = [
        json.loads(record.message)
        for record in caplog.records
        if record.message.startswith("{") and '"session_reauth"' in record.message
    ]
    assert len(reauth) == 1, "the renewal happened silently"
    assert reauth[0]["username"] == username


async def test_a_read_recovers_too(symbol):
    """`refresh()` swallowed every non-200 in silence, so an expired session left a bot
    quoting off a balance frozen at whatever it last saw."""
    username = f"reauth_{uuid.uuid4().hex[:12]}"
    async with BotClient(GATEWAY, username, "correct-horse-battery-bots") as client:
        await client.sign_in()
        funded = await client.await_funding()

        assert (await client.http.post("/auth/logout")).status_code == 204

        client.cash_ticks = 0
        await client.refresh()

    assert client.cash_ticks == funded
