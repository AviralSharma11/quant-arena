"""The public deployment's compose override (Task 3.3, `deploy/docker-compose.prod.yml`).

Resolved by `docker compose config`, as `test_compose.py` does for the development stack, so the
assertions are about what Docker would actually run. Each one is a property that, if it quietly
regressed, would put something on the internet that must not be there.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
APP_SERVICES = ("gateway", "matcher", "ledger", "fanout", "archiver", "bots")
SECRETS = {
    "QA_IMAGE_TAG": "0123456789abcdef0123456789abcdef01234567",
    "QA_POSTGRES_PASSWORD": "test-postgres-password",
    "QA_BOT_PASSWORD": "test-bot-password",
    "QA_DOMAIN": "quant-arena.example.cloudapp.azure.com",
}


def _config(env: dict[str, str]) -> subprocess.CompletedProcess:
    if shutil.which("docker") is None:
        pytest.skip("docker is not installed — the production override was NOT verified")
    # A clean environment apart from PATH/HOME, and an empty env file, so neither the developer's
    # shell nor a stray .env can supply a secret the test means to leave out.
    base = {k: os.environ[k] for k in ("PATH", "HOME") if k in os.environ}
    return subprocess.run(
        ["docker", "compose", "--env-file", os.devnull, "-f", "docker-compose.yml",
         "-f", "deploy/docker-compose.prod.yml", "--profile", "bots",
         "config", "--format", "json"],
        cwd=REPO_ROOT, capture_output=True, text=True, env={**base, **env},
    )


@pytest.fixture(scope="module")
def prod() -> dict:
    result = _config(SECRETS)
    if result.returncode != 0:
        pytest.fail(f"docker compose config failed:\n{result.stderr}")
    return json.loads(result.stdout)


def test_only_the_tls_entry_point_is_published(prod: dict):
    published = {
        name: sorted({int(p["published"]) for p in service.get("ports", [])})
        for name, service in prod["services"].items()
        if service.get("ports")
    }
    assert published == {"web": [80, 443]}, (
        "only Caddy may be reachable from outside; PostgreSQL, Redis, the gateway and fan-out "
        f"must stay on the compose network, got {published}"
    )


@pytest.mark.parametrize("service", APP_SERVICES)
def test_every_app_service_runs_the_published_image_at_the_deployed_commit(prod: dict, service: str):
    spec = prod["services"][service]
    assert spec["image"] == f"ghcr.io/aviralsharma11/quant-arena-gateway:{SECRETS['QA_IMAGE_TAG']}"


def test_the_entry_point_image_is_pinned_to_the_same_commit(prod: dict):
    assert prod["services"]["web"]["image"] == (
        f"ghcr.io/aviralsharma11/quant-arena-web:{SECRETS['QA_IMAGE_TAG']}"
    )


def test_the_session_cookie_is_secure_behind_tls(prod: dict):
    assert prod["services"]["gateway"]["environment"]["QA_SESSION_COOKIE_SECURE"] == "true"


def test_no_service_falls_back_to_the_development_database_password(prod: dict):
    for name in ("gateway", "ledger"):
        url = prod["services"][name]["environment"]["QA_DATABASE_URL"]
        assert f":{SECRETS['QA_POSTGRES_PASSWORD']}@" in url, name
    assert prod["services"]["postgres"]["environment"]["POSTGRES_PASSWORD"] == (
        SECRETS["QA_POSTGRES_PASSWORD"]
    )


@pytest.mark.parametrize("missing", sorted(SECRETS))
def test_a_missing_secret_stops_the_deployment(missing: str):
    """`:?` rather than a default: without it, a forgotten `.env` line would start the public
    stack on the development password, or on whatever image `latest` happens to be."""
    result = _config({k: v for k, v in SECRETS.items() if k != missing})
    assert result.returncode != 0
    assert missing in result.stderr
