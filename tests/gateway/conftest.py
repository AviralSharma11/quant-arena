"""Fixtures for the gateway tests.

Runs against the real Redis and the real PostgreSQL from the Docker stack — not a fake. Success
Criterion 2 is specifically that a session survives a process restart *because it lives in
Redis*, and a fake would pass that test while the property was false.

Isolation: a dedicated `quant_arena_test` database and Redis logical database 15, both wiped
between tests, so the development data is never touched.
"""

from __future__ import annotations

import dataclasses
import sys
import time
from pathlib import Path

import psycopg
import pytest
import redis as redis_sync
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlmodel import SQLModel

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from config.settings import Settings  # noqa: E402
from services.gateway import models  # noqa: E402,F401  — registers the tables
from contracts.v1.generated.contracts import unpack_any, with_seq  # noqa: E402
from services.gateway.app import create_app  # noqa: E402
from services.gateway.streams import RECORD_FIELD  # noqa: E402
from services.matcher.adapter import NaiveMatcher  # noqa: E402

ADMIN_DSN = "postgresql://quant:quant@localhost:5432/postgres"
TEST_DB = "quant_arena_test"
SYNC_TEST_DSN = f"postgresql+psycopg://quant:quant@localhost:5432/{TEST_DB}"


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    """Async tests run on anyio's pytest plugin, which ships with FastAPI's own dependency on
    anyio — so no test framework is added to the closed stack list for this."""
    return "asyncio"


@pytest.fixture(scope="session")
def settings() -> Settings:
    """The real configuration file, with only *infrastructure* pointed at the test stores.

    Domain parameters are never overridden here — the tests assert against the same
    config/quant_arena.toml the gateway ships with, so a bad value in it fails the suite.
    """
    return dataclasses.replace(
        Settings.load(),
        database_url=f"postgresql+psycopg://quant:quant@localhost:5432/{TEST_DB}",
        redis_url="redis://localhost:6379/15",
        # False only so the httpx cookie jar carries the cookie over plain http in tests.
        # The Secure flag itself is asserted directly against the Set-Cookie header in
        # test_sessions.py, which is the honest way to check it.
        session_cookie_secure=False,
    )


@pytest.fixture(scope="session", autouse=True)
def _database(settings: Settings):
    with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
        exists = conn.execute(
            "select 1 from pg_database where datname = %s", (TEST_DB,)
        ).fetchone()
        if not exists:
            conn.execute(f'create database "{TEST_DB}"')

    engine = create_engine(SYNC_TEST_DSN)
    SQLModel.metadata.create_all(engine)
    yield
    engine.dispose()


@pytest.fixture(autouse=True)
def _clean(settings: Settings, _database):
    """Wipe both stores before every test, so no test can pass on another's leftovers."""
    engine = create_engine(SYNC_TEST_DSN)
    with engine.begin() as conn:
        conn.execute(
            text(
                "truncate table accounts, users, positions, open_orders, house_fees "
                "restart identity cascade"
            )
        )
    engine.dispose()

    # flushdb clears sessions AND the streams, so no test inherits another's stream entries.
    r = redis_sync.Redis.from_url(settings.redis_url)
    r.flushdb()
    r.close()


@pytest.fixture
def client(settings: Settings):
    """A running gateway. Entering the context manager runs the lifespan."""
    with TestClient(create_app(settings)) as test_client:
        yield test_client


@pytest.fixture
def pump_for(settings: Settings):
    """Build a pump bound to a particular gateway. See `pump` for what a pump is.

    A factory because some tests build their own app — `test_ratelimit.py` needs one configured
    down to a reachable rate — and a pump has to wait on *that* app's risk watcher.
    """

    def _for(client: TestClient):
        return _make_pump(client, settings)

    return _for


@pytest.fixture
def pump(client: TestClient, settings: Settings):
    """Run the matcher by hand, then wait for the gateway to have seen the result.

    From week 4 the cash grant is an event, not a row: `POST /auth/register` appends
    `CreateAccount` to the inbound stream and nothing else (Open Issue 004 — PostgreSQL is a
    derived read model and may not be the source of truth for a balance). The grant becomes
    real when the matcher forwards it as `AccountCreated` and the gateway's `RiskState` reads
    that off the outbound stream.

    The gateway suite runs no matcher process, so this fixture is one: it drains whatever the
    gateway has appended since the last call, feeds it through the same `NaiveMatcher` the real
    process uses, and appends the results. It then **waits for the gateway's own background
    watcher** to consume them rather than reaching into `RiskState` directly — the asynchronous
    path is the thing under test, and a test that wrote the state itself would prove nothing.

    One matcher instance per test, held across calls, so `order_id`s stay monotonic and the
    books persist exactly as they do in the real process.
    """
    yield from _pump_impl(client, settings)


def _make_pump(client: TestClient, settings: Settings):
    """The non-fixture form, for `pump_for`. Its Redis client is closed with the test."""
    gen = _pump_impl(client, settings)
    pump = next(gen)
    _OPEN_PUMPS.append(gen)
    return pump


#: Generators handed out by `_make_pump`, closed by the autouse fixture below so no test leaks
#: a Redis connection.
_OPEN_PUMPS: list = []


@pytest.fixture(autouse=True)
def _close_pumps():
    yield
    while _OPEN_PUMPS:
        gen = _OPEN_PUMPS.pop()
        gen.close()


def _pump_impl(client: TestClient, settings: Settings):
    matcher = NaiveMatcher(initial_cash_ticks=settings.initial_cash_ticks)
    r = redis_sync.Redis.from_url(settings.redis_url)
    cursor = {"inbound": None}

    def _pump(timeout: float = 5.0) -> int:
        start = "-" if cursor["inbound"] is None else f"({cursor['inbound']}"
        entries = r.xrange(settings.stream_inbound, start, "+")
        last_out_id = None
        for raw_id, fields in entries:
            record = with_seq(unpack_any(fields[RECORD_FIELD]), raw_id)
            for out in matcher.apply(record):
                last_out_id = r.xadd(
                    settings.stream_outbound,
                    {RECORD_FIELD: out.pack()},
                    maxlen=settings.stream_maxlen,
                    approximate=True,
                )
            cursor["inbound"] = raw_id.decode()

        if last_out_id is None:
            return 0
        # The gateway's watcher blocks on XREAD, so this normally returns on the first pass.
        # The deadline exists to fail a genuinely stuck watcher loudly rather than hang.
        want = last_out_id.decode()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if client.app.state.risk.last_seq == want:
                return len(entries)
            time.sleep(0.01)
        raise AssertionError(
            f"the gateway's risk watcher never reached {want} "
            f"(stopped at {client.app.state.risk.last_seq})"
        )

    yield _pump
    r.close()


@pytest.fixture
def registered(client: TestClient, pump) -> dict:
    body = {"username": "trader_one", "password": "correct-horse-battery"}
    response = client.post("/auth/register", json=body)
    assert response.status_code == 201, response.text
    # Registration is acknowledgement-shaped now: the response says the grant was recorded,
    # not that it has been applied. Pumping is what applies it, and every test downstream of
    # this fixture expects a funded account.
    pump()
    return {**body, **response.json()}


@pytest.fixture
def logged_in(client: TestClient, registered: dict) -> TestClient:
    response = client.post(
        "/auth/login",
        json={"username": registered["username"], "password": registered["password"]},
    )
    assert response.status_code == 200, response.text
    return client
