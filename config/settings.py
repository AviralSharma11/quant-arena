"""The configuration loader.

Two different kinds of value, kept apart on purpose:

**Configuration** — domain parameters every process must agree on. It lives in
`config/quant_arena.toml`, and its SHA-256 is what gets stamped into the startup log.

**Infrastructure** — where this process finds Redis and PostgreSQL, and whether cookies are
sent Secure. These differ between a laptop, CI, a Docker network and production while the
configuration is unchanged, so they come from the environment and are **not** hashed. Putting a
connection URL in the hashed file would change the hash when nothing about the configuration
had, and the hash's whole purpose is to answer "were these processes running the same setup?"

Task 1.4's boundary — "do not spread configuration across environment variables and code
defaults; one file is the point" — is satisfied in that sense: no domain parameter has a code
default, and none can be set from the environment. The file is the only source.
"""

from __future__ import annotations

import hashlib
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

CONFIG_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = CONFIG_DIR / "quant_arena.toml"

#: Which config file to read. The only environment variable that touches configuration, and it
#: selects the file rather than overriding anything inside it.
CONFIG_PATH_ENV = "QA_CONFIG_PATH"


class ConfigError(Exception):
    pass


def _require(table: dict, section: str, key: str):
    try:
        return table[section][key]
    except KeyError as exc:
        raise ConfigError(
            f"{section}.{key} is missing from the configuration file. Every parameter is "
            f"required — a missing one must fail loudly, not fall back to a code default."
        ) from exc


@dataclass(frozen=True)
class Settings:
    # --- configuration: from the file, covered by config_hash -------------------------------
    initial_cash_ticks: int
    session_ttl_seconds: int
    session_cookie_name: str
    session_cookie_samesite: str
    max_orders_per_second: int
    conflation_hz: int
    book_depth: int
    replay_real_seconds_per_simulated_minute: int
    symbols: tuple[str, ...]

    # --- infrastructure: from the environment, NOT covered by config_hash -------------------
    redis_url: str
    database_url: str
    session_cookie_secure: bool

    # --- provenance --------------------------------------------------------------------------
    config_path: str
    config_hash: str

    @classmethod
    def load(cls, config_path: Path | str | None = None) -> "Settings":
        path = Path(
            config_path or os.environ.get(CONFIG_PATH_ENV) or DEFAULT_CONFIG_PATH
        ).resolve()
        if not path.is_file():
            raise ConfigError(f"configuration file not found: {path}")

        raw = path.read_bytes()
        # Hash the bytes on disk, not the parsed result: a comment change is a configuration
        # change worth noticing, and it makes the hash trivially reproducible with sha256sum.
        config_hash = hashlib.sha256(raw).hexdigest()
        table = tomllib.loads(raw.decode("utf-8"))

        return cls(
            initial_cash_ticks=_require(table, "exchange", "initial_cash_ticks"),
            session_ttl_seconds=_require(table, "session", "ttl_seconds"),
            session_cookie_name=_require(table, "session", "cookie_name"),
            session_cookie_samesite=_require(table, "session", "cookie_samesite"),
            max_orders_per_second=_require(table, "limits", "max_orders_per_second"),
            conflation_hz=_require(table, "market_data", "conflation_hz"),
            book_depth=_require(table, "market_data", "book_depth"),
            replay_real_seconds_per_simulated_minute=_require(
                table, "replay", "real_seconds_per_simulated_minute"
            ),
            symbols=tuple(_require(table, "symbols", "listed")),
            redis_url=os.environ.get("QA_REDIS_URL", "redis://localhost:6379/0"),
            database_url=os.environ.get(
                "QA_DATABASE_URL",
                "postgresql+psycopg://quant:quant@localhost:5432/quant_arena",
            ),
            session_cookie_secure=os.environ.get(
                "QA_SESSION_COOKIE_SECURE", "true"
            ).strip().lower()
            in ("1", "true", "yes", "on"),
            config_path=str(path),
            config_hash=config_hash,
        )


settings = Settings.load()
