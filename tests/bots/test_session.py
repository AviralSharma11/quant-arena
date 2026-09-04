"""A real bot session against a running exchange — Success Criteria 1, 2 and 3.

These three cannot be proven in isolation. "The book is never empty and trades print
continuously" is a claim about a gateway, a matcher and a ledger running together with bots
talking HTTP to them, and a test with the exchange stubbed out would assert that the stub
behaves, which is nothing.

So this runs against the compose stack and **skips loudly** when it is not up. A green suite
carrying that skip has not verified 4.4's first three criteria — the same caveat `test_sizes.py`
carries for the C++ layout check, and the same trap `test_halt.py` fell into for weeks.

    docker compose up -d --build

Bots are started here rather than by the `bots` compose profile, so the test controls the seed
and can read the obligation meters directly when the session ends.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import sys
import time
import uuid
from pathlib import Path

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from config.settings import Settings  # noqa: E402
from contracts.v1.generated.contracts import Fill, unpack_any  # noqa: E402
from services.bots.runner import BotRunner  # noqa: E402
from services.gateway.streams import RECORD_FIELD  # noqa: E402

pytestmark = pytest.mark.slow

GATEWAY = "http://localhost:8000"

#: The same variable `services/bots/__main__.py` reads, so a stack whose bot accounts were
#: created by `docker compose --profile bots up` is usable here without resetting it. Bot
#: accounts persist in PostgreSQL, and a test with a hardcoded password would fail against any
#: stack the profile had already touched — a failure about the environment, wearing the
#: costume of a failure about the code.
BOT_PASSWORD = os.environ.get("QA_BOT_PASSWORD") or "correct-horse-battery-bots"

#: Long enough for the maker to quote several times at 2 Hz and for Poisson arrivals at 0.5/s
#: to actually arrive. Short enough to stay a test rather than a benchmark.
SESSION_SECONDS = 12.0

#: Consecutive successful writes required before the session is allowed to start.
WARMUP_WRITES = 8


@pytest.fixture(scope="module")
def running_stack():
    if shutil.which("docker") is None:
        pytest.skip("docker is not installed — a live bot session was NOT verified")
    try:
        health = httpx.get(f"{GATEWAY}/health", timeout=2)
    except httpx.TransportError:
        pytest.skip(
            "the compose stack is not running — a live bot session was NOT verified. "
            "Start it with: docker compose up -d --build"
        )
    if health.status_code != 200 or health.json()["halted"]:
        pytest.skip("the exchange is halted — a live bot session was NOT verified")
    _warm_the_write_path()


def _warm_the_write_path(attempts: int = 40, delay: float = 0.25) -> None:
    """Wait until the exchange actually accepts a write, not merely until `/health` says ok.

    `/health` reports the halt watchdog's view, which lags the producer's. After a Redis
    restart the first append can still fail on a pooled connection that died with the server,
    and a single failed flush halts the whole exchange until the watchdog's next ping — up to
    `streams.health_poll_ms` — failing every concurrent append in that window.

    That is a real property of the gateway and it is measured elsewhere. It is not what this
    module is for: running a bot session into it measures the outage, not the market maker,
    and produced a uptime figure that failed a 0.95 obligation the bot was otherwise meeting
    perfectly. Registering a throwaway account is a write, so this proves the path is open
    before the session that will be judged on it begins.

    Several *consecutive* writes, not one. The gateway's Redis pool holds more than one
    connection, and after a restart each dead one halts the exchange again the first time it
    is used — so a single success proves only that one of them was replaced.
    """
    consecutive = 0
    for attempt in range(attempts):
        response = httpx.post(
            f"{GATEWAY}/auth/register",
            json={"username": f"warmup_{uuid.uuid4().hex[:12]}", "password": BOT_PASSWORD},
            timeout=5,
        )
        consecutive = consecutive + 1 if response.status_code == 201 else 0
        if consecutive >= WARMUP_WRITES:
            return
        if attempt < attempts - 1:
            time.sleep(delay)
    pytest.skip("the exchange never settled — a live bot session was NOT verified")


@pytest.fixture(scope="module")
def live_settings() -> Settings:
    return Settings.load()


def _outbound_fills(settings: Settings, since_id: str) -> list:
    """Every trade printed after `since_id`, read straight off the durable log.

    The stream is the record, not the read model: a fill that reached PostgreSQL also reached
    here, and reading here removes any question of projection lag from the assertion.
    """
    import redis as redis_sync

    client = redis_sync.Redis.from_url("redis://localhost:6379/0")
    try:
        entries = client.xrange(settings.stream_outbound, f"({since_id}", "+")
    finally:
        client.close()
    return [
        record
        for _id, fields in entries
        if isinstance(record := unpack_any(fields[RECORD_FIELD]), Fill)
    ]


def _stream_tip(settings: Settings) -> str:
    import redis as redis_sync

    client = redis_sync.Redis.from_url("redis://localhost:6379/0")
    try:
        last = client.xrevrange(settings.stream_outbound, "+", "-", count=1)
    finally:
        client.close()
    return last[0][0].decode() if last else "0-0"


async def _run_session(settings: Settings, seconds: float) -> BotRunner:
    runner = BotRunner(settings, base_url=GATEWAY, password=BOT_PASSWORD)
    async with contextlib.AsyncExitStack() as stack:
        await runner.build(stack)
        await runner.sign_in()
        runner.start()
        await asyncio.sleep(seconds)
        await runner.stop()
    return runner


@pytest.fixture(scope="module")
def session(running_stack, live_settings: Settings):
    """**One** bot session, shared by every assertion in this module.

    Module scope, not function scope. Each of these criteria is a different question about the
    same session, and a per-test fixture ran nine twelve-second sessions instead of one — nine
    times the wall clock for no extra evidence, and nine restarts of the same bot accounts,
    which is how the `client_order_id` reuse bug in `BotClient` was found.
    """
    start_id = _stream_tip(live_settings)
    started = time.monotonic()
    runner = asyncio.run(_run_session(live_settings, SESSION_SECONDS))
    return runner, start_id, time.monotonic() - started


#: Rejections that mean the *exchange* was unavailable, not that the bot misbehaved.
OUTAGE_REASONS = {"EXCHANGE_HALTED", "redis_unreachable"}


def _assume_no_outage(maker) -> None:
    """Skip loudly when the exchange halted during the session.

    An obligation measured across an outage is a measurement of the outage. A market maker
    cannot quote into an exchange that will not record its orders, and counting that against
    its uptime blames the bot for the venue being down.

    This is a skip and not a pass: the criterion is genuinely unverified when it fires, exactly
    as `test_sizes.py` is unverified without a compiler. It is narrow on purpose — only these
    two reasons, and every other assertion in this module still runs.

    In practice it fires after a Redis restart. A single stale pooled connection halts the
    whole gateway until the halt watchdog's next ping, and a restart leaves several, so the
    exchange halts intermittently for far longer than it is actually unreachable. That is a
    gateway property worth its own attention; it is not this module's to assert on.
    """
    halted = {r: n for r, n in maker.rejections.items() if r in OUTAGE_REASONS}
    if halted:
        pytest.skip(
            f"the exchange halted {sum(halted.values())} times during the session "
            f"({halted}) — the market maker's obligations were NOT verified"
        )


# --- Success Criterion 1: the book is never empty and trades print continuously ---------------


def test_the_market_maker_keeps_a_two_sided_market_up(session):
    """The book is never empty *because* something is obliged to quote into it.

    Measured over the session, not at the instant it stopped. Requoting pulls a side and
    replaces it, so there is a real window in which one side is legitimately down — and an
    assertion on the final instant fails whenever the session happens to end inside it, which
    it intermittently did. The obligation meter samples after each complete tick, which is the
    honest place to ask this; `test_the_market_maker_meets_its_uptime_obligation` holds the
    criterion itself.
    """
    runner, _, _ = session
    assert runner.makers, "no market maker was configured for any listed symbol"
    for maker in runner.makers:
        summary = maker.summary()
        assert maker.quotes_placed > 0, summary
        assert maker.meter.two_sided_samples > 0, (
            f"the maker never had both sides up at once: {summary}"
        )


def test_trades_print_continuously(session, live_settings: Settings):
    """Not merely "a trade happened" — a rate. A single print would satisfy a weaker
    assertion while the market was dead for the rest of the session."""
    runner, start_id, elapsed = session
    fills = _outbound_fills(live_settings, start_id)

    assert fills, "no trade printed at all during the session"
    per_second = len(fills) / elapsed
    assert per_second > 0.1, (
        f"only {len(fills)} trades in {elapsed:.1f}s — the tape is effectively dead"
    )


def test_the_noise_traders_actually_reached_the_book(session):
    runner, _, _ = session
    sent = sum(runner.orders_sent for runner in runner.noise)
    assert sent > 0, "no noise order was accepted; nothing was there to cross the spread"


# --- Success Criterion 2: obligations are met, and breaches are recorded ----------------------


def test_the_market_maker_meets_its_uptime_obligation(session):
    runner, _, _ = session
    for maker in runner.makers:
        _assume_no_outage(maker)
        summary = maker.summary()
        assert summary["samples"] > 0, "the meter never sampled — nothing was measured"
        assert summary["meets_uptime"], summary


def test_every_breach_is_recorded_rather_than_merely_counted(session):
    """A breach has to be an assertable event, not a subjective judgement (Open Issue 005
    sub-decision 5h). Whether any occurred is not the point; that each is retrievable is."""
    runner, _, _ = session
    for maker in runner.makers:
        assert len(maker.meter.breaches) == maker.meter.summary()["total_breaches"]
        for breach in maker.meter.breaches:
            assert breach.kind and breach.detail


def test_the_maker_never_breached_its_own_spread_or_size_obligations(session):
    """It quotes from parameters that satisfy them by construction, so a breach here means the
    configuration and the obligations have drifted apart."""
    runner, _, _ = session
    for maker in runner.makers:
        breaches = maker.meter.summary()["breaches"]
        assert "spread_too_wide" not in breaches, maker.summary()
        assert "size_too_small" not in breaches, maker.summary()


# --- Success Criterion 3: inventory stays bounded ---------------------------------------------


def test_market_maker_inventory_stays_bounded(session, live_settings: Settings):
    """Not "small" — *bounded*. The claim inventory skew supports is that the position does not
    ratchet one way until the maker cannot quote, so the bound is stated against the quote size
    it is willing to show rather than against zero.
    """
    runner, _, _ = session
    limit = live_settings.bots.quote_size * 20
    for maker in runner.makers:
        assert abs(maker.inventory) < limit, (
            f"inventory drifted to {maker.inventory}, beyond {limit}: {maker.summary()}"
        )


def test_the_market_maker_can_still_quote_at_the_end_of_the_session(session):
    """The failure inventory skew exists to prevent: a maker that has accumulated so far in one
    direction that it can no longer show a market (Open Issue 005 sub-decision 5b)."""
    runner, _, _ = session
    for maker in runner.makers:
        _assume_no_outage(maker)
        rejections = maker.summary()["rejections"]
        assert "INSUFFICIENT_CASH" not in rejections, maker.summary()
        assert "INSUFFICIENT_POSITION" not in rejections, (
            "a designated market maker must never be refused for inventory — it is exempt"
        )


# --- Success Criterion 5: bots are ordinary users ---------------------------------------------


def test_bots_are_ordinary_authenticated_accounts(session):
    """No special path, no privileged endpoint. Every bot has a user id because it registered
    and logged in like anyone (Task 4.4's Boundaries: never special-cased in the gateway)."""
    runner, _, _ = session
    for client in runner.clients:
        assert client.user_id is not None, client.username
        assert client.cash_ticks > 0, f"{client.username} was never funded"
