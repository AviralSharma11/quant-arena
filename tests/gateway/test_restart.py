"""Success Criterion 2, proven against a real process restart.

`test_sessions.py` has an in-process version of this using two `create_app()` calls. That check
is worth keeping but it is **weaker than the criterion**: two apps built in one interpreter
still share module and class state, so a session cache held in a global would survive it and
the test would pass while the property was false.

This file starts uvicorn as an actual subprocess, logs in over HTTP, kills the process, starts
a fresh one, and reuses the cookie. Nothing but Redis can carry the session across that.
"""

from __future__ import annotations

import contextlib
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

USER = {"username": "restart_user", "password": "correct-horse-battery"}
ORDER = {"client_order_id": 1, "symbol_id": 1, "side": 1, "tif": 1,
         "price_ticks": 100, "qty": 1}

STARTUP_TIMEOUT_S = 45


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextlib.contextmanager
def running_gateway(settings):
    """A real uvicorn process, torn down completely on exit."""
    port = _free_port()
    env = {
        **os.environ,
        "QA_DATABASE_URL": settings.database_url,
        "QA_REDIS_URL": settings.redis_url,
        "QA_SESSION_COOKIE_SECURE": "false",
        "QA_INITIAL_CASH_TICKS": str(settings.initial_cash_ticks),
        "PYTHONPATH": str(REPO_ROOT),
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "services.gateway.app:app",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=str(REPO_ROOT), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + STARTUP_TIMEOUT_S
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise RuntimeError(
                    "gateway exited during startup:\n"
                    + proc.stdout.read().decode(errors="replace")
                )
            try:
                if httpx.get(f"{base}/health", timeout=1.0).status_code == 200:
                    break
            except httpx.TransportError:
                time.sleep(0.15)
        else:
            raise RuntimeError("gateway did not become healthy in time")
        yield base
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:  # pragma: no cover
            proc.kill()
            proc.wait(timeout=10)


@pytest.mark.slow
def test_session_survives_a_real_process_restart(settings):
    with running_gateway(settings) as base:
        client = httpx.Client(base_url=base, timeout=10.0)
        assert client.post("/auth/register", json=USER).status_code == 201
        login = client.post("/auth/login", json=USER)
        assert login.status_code == 200, login.text
        cookie = login.cookies["qa_session"]
        assert client.post("/orders", json=ORDER).status_code == 202
        client.close()

    # The process that issued that cookie no longer exists. Nothing in its memory survived.
    with running_gateway(settings) as base:
        response = httpx.post(
            f"{base}/orders", json=ORDER, cookies={"qa_session": cookie}, timeout=10.0
        )

    assert response.status_code == 202, (
        "the session did not survive a real process restart — it was being held in the "
        f"gateway's own memory rather than in Redis (got {response.status_code})"
    )


@pytest.mark.slow
def test_a_session_created_before_the_restart_can_still_be_revoked_after_it(settings):
    """Logout must work on a session this process never issued — the state is not ours."""
    with running_gateway(settings) as base:
        client = httpx.Client(base_url=base, timeout=10.0)
        client.post("/auth/register", json=USER)
        cookie = client.post("/auth/login", json=USER).cookies["qa_session"]
        client.close()

    with running_gateway(settings) as base:
        cookies = {"qa_session": cookie}
        assert httpx.post(f"{base}/auth/logout", cookies=cookies, timeout=10.0).status_code == 204
        after = httpx.post(f"{base}/orders", json=ORDER, cookies=cookies, timeout=10.0)
    assert after.status_code == 401
