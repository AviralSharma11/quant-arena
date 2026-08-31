"""Task 5.4a — the frontend scaffold builds and serves, with routing for the three screens.

No JavaScript test framework is used. Node 24 strips TypeScript natively, so the route table can
be imported and inspected directly, and the built app can be served and fetched over HTTP. That
covers both criteria without adding a dependency to a closed stack list.
"""

from __future__ import annotations

import contextlib
import json
import shutil
import socket
import subprocess
import time
from pathlib import Path

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
WEB = REPO_ROOT / "web"

#: Open Issue 014 sub-decision 14d. Three screens, fixed in advance.
EXPECTED_SCREENS = {"trading", "auth", "backtest"}


def _need(binary: str) -> str:
    path = shutil.which(binary)
    if path is None:
        pytest.skip(f"{binary} is not installed — the frontend scaffold was NOT verified")
    return path


@pytest.fixture(scope="module")
def routes() -> list[dict]:
    """The route table, read straight out of routes.ts by Node."""
    _need("node")
    result = subprocess.run(
        ["node", "--input-type=module", "-e",
         "import { ROUTES } from './src/routes.ts'; console.log(JSON.stringify(ROUTES));"],
        cwd=WEB, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


# --- criterion 2: routing in place for the three screens -------------------------------------


def test_there_are_exactly_three_screens(routes: list[dict]):
    """The three-screen limit is a scope commitment made in advance (Open Issue 014 §11.1):
    a fourth screen is a change to raise, not an addition to make. This test is where that
    commitment stops being a sentence in a document."""
    assert {r["id"] for r in routes} == EXPECTED_SCREENS
    assert len(routes) == 3


def test_every_route_has_a_distinct_path(routes: list[dict]):
    paths = [r["path"] for r in routes]
    assert len(set(paths)) == len(paths)
    assert all(p.startswith("/") for p in paths)


def test_every_route_names_the_task_that_will_build_it(routes: list[dict]):
    """So a placeholder cannot quietly become permanent."""
    for route in routes:
        assert route["builtBy"].startswith("Task "), route


def test_the_router_is_built_from_the_route_table_not_hardcoded():
    """If App.tsx listed the screens itself, the assertions above would prove nothing about
    what actually renders."""
    app = (WEB / "src" / "App.tsx").read_text()
    assert "ROUTES.map" in app
    assert 'path="/login"' not in app, "a route is hardcoded in App.tsx; drive it from ROUTES"


def test_an_unknown_path_does_not_grow_a_fourth_screen():
    app = (WEB / "src" / "App.tsx").read_text()
    assert 'path="*"' in app and "Navigate" in app


# --- criterion 1: builds and serves ------------------------------------------------------------


def test_the_stack_is_vite_typescript_and_react():
    package = json.loads((WEB / "package.json").read_text())
    deps = {**package.get("dependencies", {}), **package.get("devDependencies", {})}
    for required in ("react", "react-dom", "react-router-dom", "typescript", "vite"):
        assert required in deps, required


def test_build_artefacts_are_not_committed():
    ignored = (WEB / ".gitignore").read_text()
    assert "node_modules" in ignored
    assert "dist" in ignored


@pytest.mark.slow
def test_the_project_builds():
    """`npm run build` runs `tsc -b` first, so this also proves the TypeScript compiles."""
    _need("npm")
    if not (WEB / "node_modules").is_dir():
        pytest.skip("web/node_modules missing — run `npm install` in web/ first")
    result = subprocess.run(
        ["npm", "run", "build"], cwd=WEB, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (WEB / "dist" / "index.html").is_file()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextlib.contextmanager
def preview_server():
    port = _free_port()
    proc = subprocess.Popen(
        # --host 127.0.0.1 is required, not cosmetic: vite preview otherwise binds ::1 only,
        # and httpx connecting to 127.0.0.1 gets connection-refused with the server plainly
        # running and logging "http://localhost:<port>".
        ["npm", "run", "preview", "--",
         "--port", str(port), "--strictPort", "--host", "127.0.0.1"],
        cwd=WEB, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise RuntimeError(proc.stdout.read().decode(errors="replace"))
            try:
                if httpx.get(base, timeout=1.0).status_code == 200:
                    break
            except httpx.TransportError:
                time.sleep(0.2)
        else:
            raise RuntimeError("vite preview did not start in time")
        yield base
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:  # pragma: no cover
            proc.kill()
            proc.wait(timeout=10)


@pytest.mark.slow
def test_the_built_app_serves_every_screen(routes: list[dict]):
    """Served, not just built. A single-page app returns the same shell for each path — what
    this proves is that the server is configured for client-side routing, so a deep link or a
    page reload on /backtest does not 404."""
    _need("npm")
    if not (WEB / "dist" / "index.html").is_file():
        pytest.skip("web/dist missing — run `npm run build` in web/ first")
    with preview_server() as base:
        for route in routes:
            response = httpx.get(f"{base}{route['path']}", timeout=10.0)
            assert response.status_code == 200, route["path"]
            assert 'id="root"' in response.text, route["path"]


@pytest.mark.slow
def test_the_page_is_titled_quant_arena():
    assert "<title>Quant Arena</title>" in (WEB / "index.html").read_text()
