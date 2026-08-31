"""Shared settings.

Values come from the environment with defaults that match the local Docker stack. Task 1.4
takes this further — one config file, hashed, with the hash logged by every process. This is
deliberately just the values 1.3 needs.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _env_str(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return default if raw is None else int(raw)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    return default if raw is None else raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    """One frozen object rather than scattered os.environ reads."""

    redis_url: str = _env_str("QA_REDIS_URL", "redis://localhost:6379/0")
    database_url: str = _env_str(
        "QA_DATABASE_URL", "postgresql+psycopg://quant:quant@localhost:5432/quant_arena"
    )

    #: Virtual capital granted to a new account, in int64 ticks. Not specified anywhere in the
    #: plan or the open issues, so it lives here rather than being buried in the accounts code —
    #: the bots in 4.4 will need their own figure and this is where it will go.
    initial_cash_ticks: int = _env_int("QA_INITIAL_CASH_TICKS", 1_000_000)

    session_cookie_name: str = _env_str("QA_SESSION_COOKIE", "qa_session")
    session_ttl_seconds: int = _env_int("QA_SESSION_TTL_SECONDS", 12 * 60 * 60)

    #: Open Issue 015 requires Secure. It is settable only so that a browser can talk to a
    #: local http:// gateway during development — a browser silently drops a Secure cookie on
    #: a plain connection. It must be True anywhere real.
    session_cookie_secure: bool = _env_bool("QA_SESSION_COOKIE_SECURE", True)
    session_cookie_samesite: str = _env_str("QA_SESSION_COOKIE_SAMESITE", "strict")


settings = Settings()
