"""Fixtures for the fan-out tests.

Almost nothing is needed: the state machine is pure, so criteria 1 to 4 are provable without a
store. Only the replay-against-real-Redis test reaches for one.
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


@pytest.fixture(scope="session")
def settings() -> Settings:
    """The real configuration file, so a bad value in it fails the suite."""
    return Settings.load()


@pytest.fixture
def test_settings() -> Settings:
    """Infrastructure pointed at the test stores; every domain parameter left alone."""
    return dataclasses.replace(Settings.load(), redis_url="redis://localhost:6379/15")


@pytest.fixture
def clean_redis(test_settings: Settings):
    r = redis_sync.Redis.from_url(test_settings.redis_url)
    r.flushdb()
    r.close()
