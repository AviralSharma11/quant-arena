"""The shared configuration file, its loader, and the hash stamp.

Task 1.4 criterion 3 is that the configuration hash appears in every process's startup log.
The tests below cover the two halves of that: the hash is a real content hash of the file, and
the stamp carries it.
"""

from __future__ import annotations

import hashlib
import json
import logging
import textwrap
from pathlib import Path

import pytest

from config.settings import DEFAULT_CONFIG_PATH, ConfigError, Settings
from config.startup import CONFIG_HASH_KEY, log_startup, startup_record


def test_the_configuration_file_is_version_controlled_and_present():
    assert DEFAULT_CONFIG_PATH.is_file()


def test_the_hash_is_the_sha256_of_the_file_on_disk():
    """Reproducible with `sha256sum config/quant_arena.toml` — no bespoke digest."""
    settings = Settings.load()
    assert settings.config_hash == hashlib.sha256(DEFAULT_CONFIG_PATH.read_bytes()).hexdigest()
    assert len(settings.config_hash) == 64


def test_the_hash_changes_when_the_file_changes(tmp_path: Path):
    original = DEFAULT_CONFIG_PATH.read_text()
    a = tmp_path / "a.toml"
    b = tmp_path / "b.toml"
    a.write_text(original)
    b.write_text(original + "\n# a trailing comment\n")
    assert Settings.load(a).config_hash != Settings.load(b).config_hash


def test_the_hash_is_stable_across_loads():
    assert Settings.load().config_hash == Settings.load().config_hash


# --- values, each traceable to a decision ----------------------------------------------------


@pytest.mark.parametrize(
    "attribute,expected,source",
    [
        ("max_orders_per_second", 1000, "Open Issue 015 section 11.1"),
        ("conflation_hz", 20, "Open Issue 006"),
        ("book_depth", 10, "Open Issue 006"),
        ("replay_real_seconds_per_simulated_minute", 1, "Open Issue 005 sub-decision 5g"),
        ("initial_cash_ticks", 1_000_000, "decided during Task 1.3"),
    ],
)
def test_settled_values_match_their_decisions(attribute: str, expected: int, source: str):
    assert getattr(Settings.load(), attribute) == expected, source


def test_symbols_are_left_empty_until_task_5_1():
    """Ten symbols and their tick sizes are decided in 5.1, from replayed crypto history.
    An invented list here would be a settled-looking value nobody actually settled."""
    assert Settings.load().symbols == ()


# --- the boundary: one file, no code defaults ------------------------------------------------


def _write_config(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "quant_arena.toml"
    path.write_text(textwrap.dedent(body))
    return path


def test_a_missing_domain_parameter_fails_loudly(tmp_path: Path):
    """"Do not spread configuration across environment variables and code defaults" — so a
    missing value must be an error, never a silent fallback."""
    path = _write_config(tmp_path, """
        [exchange]
        initial_cash_ticks = 1
        [session]
        ttl_seconds = 1
        cookie_name = "x"
        cookie_samesite = "strict"
        [limits]
        max_orders_per_second = 1
        [idempotency]
        ttl_seconds = 1
        [market_data]
        conflation_hz = 20
        # book_depth deliberately missing
        [replay]
        real_seconds_per_simulated_minute = 1
        [symbols]
        listed = []
    """)
    with pytest.raises(ConfigError, match="market_data.book_depth"):
        Settings.load(path)


def test_a_missing_file_fails_loudly(tmp_path: Path):
    with pytest.raises(ConfigError, match="not found"):
        Settings.load(tmp_path / "nope.toml")


@pytest.mark.parametrize(
    "env_name",
    ["QA_INITIAL_CASH_TICKS", "QA_BOOK_DEPTH", "QA_CONFLATION_HZ",
     "QA_MAX_ORDERS_PER_SECOND", "QA_SESSION_TTL_SECONDS"],
)
def test_no_domain_parameter_can_be_set_from_the_environment(monkeypatch, env_name: str):
    """The file is the only source. An environment override would mean two processes could
    disagree while reporting the same configuration hash — the exact failure the hash exists
    to make impossible."""
    monkeypatch.setenv(env_name, "999999")
    before = Settings.load()
    monkeypatch.setenv(env_name, "111111")
    assert Settings.load() == before


def test_infrastructure_does_come_from_the_environment(monkeypatch):
    """Connection endpoints differ between a laptop, CI and Docker while the configuration is
    identical, so they are environment-driven and outside the hash."""
    monkeypatch.setenv("QA_REDIS_URL", "redis://elsewhere:6379/3")
    settings = Settings.load()
    assert settings.redis_url == "redis://elsewhere:6379/3"


def test_changing_infrastructure_does_not_change_the_configuration_hash(monkeypatch):
    baseline = Settings.load().config_hash
    monkeypatch.setenv("QA_REDIS_URL", "redis://somewhere-else:6379/9")
    monkeypatch.setenv("QA_DATABASE_URL", "postgresql+psycopg://u:p@other:5432/db")
    assert Settings.load().config_hash == baseline


# --- the startup stamp -------------------------------------------------------------------------


def test_the_startup_record_carries_the_configuration_hash():
    settings = Settings.load()
    record = startup_record("gateway", settings)
    assert record[CONFIG_HASH_KEY] == settings.config_hash
    assert record["process"] == "gateway"
    assert record["event"] == "startup"


def test_the_startup_line_is_one_json_object(caplog):
    settings = Settings.load()
    with caplog.at_level(logging.INFO, logger="quant_arena.startup"):
        log_startup("gateway", settings)
    lines = [r.getMessage() for r in caplog.records]
    assert len(lines) == 1
    assert json.loads(lines[0])[CONFIG_HASH_KEY] == settings.config_hash


def test_the_stamp_is_shared_so_every_process_emits_the_same_shape():
    """One helper, called by each process — which is how criterion 3 stays true as the engine,
    fan-out, bots and archiver arrive."""
    settings = Settings.load()
    shapes = {tuple(sorted(startup_record(name, settings))) for name in
              ("gateway", "engine", "fanout", "archiver")}
    assert len(shapes) == 1
