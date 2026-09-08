"""The compose stack's shape.

Parsed with `docker compose config --format json`, so these assertions run against what Docker
itself resolves — variable substitution, defaults and all — rather than against a hand-rolled
reading of the YAML. That also means the file is proven to be valid, not merely well-formed.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVICES = ("redis", "postgres", "gateway", "matcher", "ledger", "fanout", "archiver")

#: Environment keys the gateway container is allowed to receive. Everything else belongs in
#: config/quant_arena.toml — see the boundary in Task 1.4.
INFRASTRUCTURE_KEYS = {
    "QA_REDIS_URL", "QA_DATABASE_URL", "QA_SESSION_COOKIE_SECURE", "QA_CONFIG_PATH",
}


@pytest.fixture(scope="module")
def compose() -> dict:
    if shutil.which("docker") is None:
        pytest.skip("docker is not installed — the compose stack was NOT verified")
    result = subprocess.run(
        ["docker", "compose", "config", "--format", "json"],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    if result.returncode != 0:
        pytest.fail(f"docker compose config failed:\n{result.stderr}")
    return json.loads(result.stdout)


def test_the_stack_is_the_stores_and_the_four_python_processes(compose: dict):
    """One producer, one matcher, one projection, one feed — and nothing that crept in.

    The ledger became a service in week 4. `LedgerConsumer` had existed since Task 2.2 and
    was started by nothing, so the read model was proven correct and never actually run.
    Fan-out arrived in 5.2b, when it finally had a port to expose — 5.2a deliberately shipped
    no service, on the grounds that a container serving nobody does nothing observable.
    """
    assert set(compose["services"]) == set(SERVICES)


@pytest.mark.parametrize("service", SERVICES)
def test_every_service_has_a_health_check(compose: dict, service: str):
    assert compose["services"][service].get("healthcheck"), service


@pytest.mark.parametrize("service", SERVICES)
def test_every_service_has_a_restart_policy(compose: dict, service: str):
    assert compose["services"][service].get("restart") == "unless-stopped", service


def test_the_gateway_waits_for_healthy_dependencies_not_merely_started(compose: dict):
    """`service_started` would let the gateway boot against a PostgreSQL that is running but
    not yet accepting connections, and it creates its tables on startup."""
    depends = compose["services"]["gateway"]["depends_on"]
    assert set(depends) == {"redis", "postgres"}
    for name, spec in depends.items():
        assert spec["condition"] == "service_healthy", name


def test_the_gateway_receives_infrastructure_only(compose: dict):
    """The boundary, enforced: no domain parameter reaches the container as an environment
    variable. If one did, two processes could disagree while reporting the same config hash."""
    supplied = set(compose["services"]["gateway"].get("environment", {}))
    assert supplied <= INFRASTRUCTURE_KEYS, (
        f"non-infrastructure environment on the gateway: {sorted(supplied - INFRASTRUCTURE_KEYS)}"
    )


def test_the_gateway_points_at_the_compose_network_not_localhost(compose: dict):
    env = compose["services"]["gateway"]["environment"]
    assert "@postgres:" in env["QA_DATABASE_URL"]
    assert "redis:6379" in env["QA_REDIS_URL"]


def test_postgres_keeps_its_data_in_a_volume(compose: dict):
    mounts = compose["services"]["postgres"].get("volumes", [])
    assert any("/var/lib/postgresql/data" in m.get("target", "") for m in mounts)


def test_the_image_carries_the_configuration_file():
    """The hash in the container's log has to describe the config the container actually read,
    so config/ must be copied into the image."""
    dockerfile = (REPO_ROOT / "Dockerfile").read_text()
    assert "COPY config/" in dockerfile


def test_the_image_does_not_buffer_stdout():
    """Without PYTHONUNBUFFERED the startup line carrying the config hash sits in a buffer
    instead of reaching `docker compose logs` — which is where criterion 3 is checked."""
    assert "PYTHONUNBUFFERED=1" in (REPO_ROOT / "Dockerfile").read_text()


def test_the_image_does_not_run_as_root():
    assert "USER quant" in (REPO_ROOT / "Dockerfile").read_text()


def test_the_matcher_is_its_own_process_and_never_reaches_postgresql(compose: dict):
    """Open Issue 007: matching is a single-writer loop with no request attached to it, so it
    does not live inside the gateway. And the engine is money-blind (Open Issue 001) — the read
    model belongs to the ledger, so the matcher is given no database URL at all."""
    matcher = compose["services"]["matcher"]
    assert matcher["command"] == ["python", "-m", "services.matcher.cpp_runner"]
    assert set(matcher["environment"]) == {"QA_REDIS_URL", "QA_CPP_ENGINE_PATH"}
    assert set(matcher["depends_on"]) == {"redis"}
