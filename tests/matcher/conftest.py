"""Fixtures for the matcher tests.

Against a real Redis on logical database 15, like the gateway tests — the point of this task is
that records survive a real stream, and a fake would pass while the property was false.
"""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

import pytest
import redis as redis_sync

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from config.settings import Settings  # noqa: E402


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def settings() -> Settings:
    """The real configuration file, with only the Redis URL pointed at the test database."""
    return dataclasses.replace(Settings.load(), redis_url="redis://localhost:6379/15")


@pytest.fixture
def clean_redis(settings: Settings):
    """Not autouse: the adapter tests are pure and must run without a store."""
    r = redis_sync.Redis.from_url(settings.redis_url)
    r.flushdb()
    r.close()
