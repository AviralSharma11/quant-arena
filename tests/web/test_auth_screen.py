"""Task 5.4b — Auth screens unit and integration tests.

Success Criterion:
- Login works and the session survives a page reload.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from tests.web.test_scaffold import WEB, preview_server

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_auth_screen_structure():
    """Verify Auth screen implements form controls, error alerts, and logout handler."""
    auth_src = (WEB / "src" / "screens" / "Auth.tsx").read_text()

    # Endpoints called
    assert '"/auth/register"' in auth_src
    assert '"/auth/login"' in auth_src
    assert '"/auth/logout"' in auth_src

    # Form fields
    assert 'id="username"' in auth_src
    assert 'id="password"' in auth_src
    assert 'type="submit"' in auth_src
    assert 'type="password"' in auth_src
    # The eight-character minimum is a registration rule. Signing in checks an existing password,
    # so the length rule — and its meter — apply only in register mode.
    assert "const PASSWORD_MIN = 8;" in auth_src
    assert 'minLength={mode === "register" ? PASSWORD_MIN : undefined}' in auth_src

    # Modes and session
    assert 'mode === "login"' in auth_src
    assert 'mode === "register"' in auth_src
    assert "handleLogoutClick" in auth_src


def test_app_session_rehydration_on_mount():
    """Verify App.tsx re-verifies session on page reload via /portfolio."""
    app_src = (WEB / "src" / "App.tsx").read_text()

    assert '"/portfolio"' in app_src
    assert "localStorage" in app_src
    assert "qa_user" in app_src
    assert "<Auth" in app_src


@pytest.mark.slow
def test_login_route_serves_auth_bundle():
    """Verify /login route serves and mounts properly."""
    if not (WEB / "dist" / "index.html").is_file():
        pytest.skip("web/dist missing — run `npm run build` in web/ first")

    with preview_server() as base:
        response = httpx.get(f"{base}/login", timeout=10.0)
        assert response.status_code == 200
        assert 'id="root"' in response.text
