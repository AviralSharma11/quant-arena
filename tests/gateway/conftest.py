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
from services.gateway.app import create_app  # noqa: E402

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
        conn.execute(text("truncate table accounts, users restart identity cascade"))
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
def registered(client: TestClient) -> dict:
    body = {"username": "trader_one", "password": "correct-horse-battery"}
    response = client.post("/auth/register", json=body)
    assert response.status_code == 201, response.text
    return {**body, **response.json()}


@pytest.fixture
def logged_in(client: TestClient, registered: dict) -> TestClient:
    response = client.post(
        "/auth/login",
        json={"username": registered["username"], "password": registered["password"]},
    )
    assert response.status_code == 200, response.text
    return client
