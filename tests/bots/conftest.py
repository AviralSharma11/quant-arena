"""Fixtures for the bot tests.

The decision tests need no stores at all — that is the point of keeping the decisions pure.
Only `settings` is shared, and only so the shipped configuration itself can be asserted on.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from config.settings import Settings  # noqa: E402


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="session")
def settings() -> Settings:
    """The real configuration file, unmodified — so a bad value in it fails the suite."""
    return Settings.load()
