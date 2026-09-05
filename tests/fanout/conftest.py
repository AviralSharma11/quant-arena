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


@pytest.fixture(scope="session")
def run_node_script():
    """Run a TypeScript module against the real `web/src/stream/` sources, return its JSON.

    The same mechanism `tests/web/test_stream_client.py` uses, and duplicated rather than
    shared because a cross-package import in pytest costs more than nine lines. Node 24 strips
    types natively, so this needs no JavaScript test framework — the decision recorded on
    2026-08-31 that keeps the closed stack list one dependency wider rather than three.

    It exists here so that Task 5.2's fourth success criterion can be checked *across* the two
    languages: frames built by the Python server, fed to the TypeScript client that ships.
    """
    import json
    import shutil
    import subprocess

    web = REPO_ROOT / "web"

    def run(body: str) -> dict:
        node = shutil.which("node")
        if node is None:
            pytest.skip("node is not installed — the client half was NOT verified")
        script = web / "src" / "stream" / "__fanout_probe.mts"
        script.write_text(body)
        try:
            result = subprocess.run(
                [node, "--experimental-strip-types", str(script)],
                capture_output=True, text=True, cwd=web, timeout=60,
            )
        finally:
            script.unlink(missing_ok=True)
        if result.returncode != 0:
            pytest.fail(f"node exited {result.returncode}:\n{result.stdout}\n{result.stderr}")
        return json.loads(result.stdout.strip().splitlines()[-1])

    return run
