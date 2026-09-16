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
        # Resized 2026-09-08. Task 5.1 replaced a pair of ~1,000-tick random walks with ten real
        # instruments running to 8,208,718 ticks, and the week-1 grant of 1,000,000 could not buy
        # one unit of six of them — the designated market makers reported `two_sided_uptime: 0.0`
        # with `INSUFFICIENT_CASH` on every bid. Sized off the dataset's peak; see the config.
        ("initial_cash_ticks", 10_000_000_000, "decided during Task 1.3, resized at 5.1"),
    ],
)
def test_settled_values_match_their_decisions(attribute: str, expected: int, source: str):
    assert getattr(Settings.load(), attribute) == expected, source


#: A complete, valid configuration with one symbol, for the malformed-symbol tests below.
#: Everything except the `[[symbols.listed]]` block is the minimum `Settings.load` requires.
_MINIMAL_CONFIG = """
    [exchange]
    initial_cash_ticks = 1
    [session]
    ttl_seconds = 1
    cookie_name = "x"
    cookie_samesite = "strict"
    [limits]
    max_orders_per_second = 1
    market_order_band_bps = 500
    [idempotency]
    ttl_seconds = 3600
    [market_data]
    conflation_hz = 20
    book_depth = 10
    bar_bucket_seconds = [1, 60]
    [replay]
    real_seconds_per_simulated_minute = 1
    data_sha256 = "0000000000000000000000000000000000000000000000000000000000000000"
    [checkpoint]
    interval_ms = 1000
    trim_interval_ms = 1000
    inbound_readers = ["matcher"]
    outbound_readers = ["matcher"]
    [streams]
    inbound = "in"
    outbound = "out"
    batch_max = 10
    health_poll_ms = 500
    [[symbols.listed]]
    symbol_id = 1
    name = "QAA"
    tick_size_ticks = 1
    lot_size = 1
"""

#: A second entry reusing symbol_id 1.
_SECOND_SYMBOL_WITH_ID_ONE = """
    [[symbols.listed]]
    symbol_id = 1
    name = "QAB"
    tick_size_ticks = 1
    lot_size = 1
"""


def test_ten_symbols_are_listed():
    """Task 5.1 replaced the provisional QAA/QAB pair wholesale. Success Criterion 1, first half.

    This test previously asserted the *opposite* — that exactly two provisional symbols were
    listed — so that it would fail the moment 5.1 landed rather than letting a placeholder
    survive into the demonstration unnoticed. It has done its job and now asserts the real table.
    """
    symbols = Settings.load().symbols
    assert [s.symbol_id for s in symbols] == list(range(1, 11))
    assert [s.name for s in symbols] == [f"QA{letter}" for letter in "ABCDEFGHIJ"]


def test_no_symbol_is_named_after_a_real_instrument():
    """Open Issue 005 section 10 and Task 5.1's Boundaries both forbid it.

    The price paths behind these symbols *are* real, which is exactly why the names must not be:
    a placeholder that reads like a real ticker is the one most likely to survive into the
    demonstration by accident, and nobody should be able to believe they are trading the real
    instrument.
    """
    names = {s.name for s in Settings.load().symbols}
    real = {"BTC", "ETH", "BNB", "SOL", "XRP", "ADA", "DOGE", "AVAX", "LINK", "DOT",
            "BTCUSDT", "ETHUSDT", "USDT", "BITCOIN", "ETHEREUM"}
    assert not names & real


def test_the_ten_symbols_do_not_all_share_one_tick_size():
    """Success Criterion 1, second half — "distinct" is about scale, not just about count.

    The provisional block set every `tick_size_ticks` to 1 and said in a comment that this was
    "not a considered value". Ten symbols that all shared one tick size would assert that all ten
    trade on the same scale, which is the placeholder property this task exists to remove. The
    values here are the real increments of the instruments behind each symbol.
    """
    tick_sizes = {s.tick_size_ticks for s in Settings.load().symbols}
    assert len(tick_sizes) >= 4
    assert all(size > 0 for size in tick_sizes)


def test_every_symbol_has_a_designated_market_maker():
    """Open Issue 005 section 10.6: the market maker list is explicit, never a prefix rule.

    It grew from two names to ten with the symbol table. A symbol without one lists and quotes
    nothing — the bot runner logs `no_market_maker` and moves on — so this is the check that
    catches the two lists drifting apart.
    """
    settings = Settings.load()
    for symbol in settings.symbols:
        assert f"dmm_{symbol.name.lower()}" in settings.designated_market_maker_accounts


def test_a_symbol_missing_a_field_fails_loudly(tmp_path: Path):
    """Same rule as every other configuration value: no field may be defaulted.

    A symbol with a defaulted tick size would misprice the display silently, which is the
    class of failure `_require` exists to prevent.
    """
    config = _write_config(
        tmp_path,
        _MINIMAL_CONFIG.replace("    tick_size_ticks = 1\n", ""),
    )
    with pytest.raises(ConfigError, match="tick_size_ticks"):
        Settings.load(config)


def test_a_duplicate_symbol_id_fails_loudly(tmp_path: Path):
    """`symbol_id` is the key every consumer indexes by — the matcher holds one book per id,
    so two instruments sharing an id would share a book and trade against each other."""
    config = _write_config(tmp_path, _MINIMAL_CONFIG + _SECOND_SYMBOL_WITH_ID_ONE)
    with pytest.raises(ConfigError, match="duplicate symbol_id"):
        Settings.load(config)


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
        market_order_band_bps = 500
        [idempotency]
        ttl_seconds = 3600
        [market_data]
        conflation_hz = 20
        # book_depth deliberately missing
        [replay]
        real_seconds_per_simulated_minute = 1
        data_sha256 = "0000000000000000000000000000000000000000000000000000000000000000"
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
