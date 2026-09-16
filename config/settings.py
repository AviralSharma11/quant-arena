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
class Symbol:
    """One tradeable instrument, as `GET /symbols` serves it.

    `contracts/v1/rest_and_ws.md` section 2.3 makes this endpoint the **only** place symbol
    names and tick sizes are defined, and therefore the only thing that maps the `symbol_id`
    on the wire to something a human reads. So the definition lives in the shared configuration
    file — hashed into every process's startup line — rather than in a table the gateway owns
    and the matcher cannot see.

    `tick_size_ticks` is the divisor the presentation layer applies for display. It never
    travels back: a price that reaches the gateway is always integer ticks (Open Issue 016).
    """

    symbol_id: int
    name: str
    tick_size_ticks: int
    lot_size: int


def _symbols(raw: object) -> tuple[Symbol, ...]:
    """Parse `[[symbols.listed]]`, failing loudly on anything malformed.

    Every field is required for the same reason every other configuration value is: a symbol
    with a defaulted tick size would render prices wrongly and silently, which is precisely the
    class of failure `_require` exists to prevent.
    """
    if not isinstance(raw, list):
        raise ConfigError("symbols.listed must be an array of tables")

    parsed: list[Symbol] = []
    seen_ids: set[int] = set()
    seen_names: set[str] = set()
    for index, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise ConfigError(f"symbols.listed[{index}] must be a table")
        try:
            symbol = Symbol(
                symbol_id=entry["symbol_id"],
                name=entry["name"],
                tick_size_ticks=entry["tick_size_ticks"],
                lot_size=entry["lot_size"],
            )
        except KeyError as exc:
            raise ConfigError(
                f"symbols.listed[{index}] is missing {exc.args[0]}. Every field is required — "
                f"a defaulted tick size would misprice the display silently."
            ) from exc
        # `symbol_id` is an i16 on the wire (contracts/v1/schema.toml), and it is the key every
        # consumer indexes by — the matcher holds one book per id. A duplicate would give two
        # instruments one book; a duplicate name would make `GET /symbols` ambiguous to resolve.
        if symbol.symbol_id in seen_ids:
            raise ConfigError(f"symbols.listed: duplicate symbol_id {symbol.symbol_id}")
        if symbol.name in seen_names:
            raise ConfigError(f"symbols.listed: duplicate name {symbol.name!r}")
        if not 0 <= symbol.symbol_id <= 2**15 - 1:
            raise ConfigError(
                f"symbols.listed: symbol_id {symbol.symbol_id} is outside the contract's i16"
            )
        seen_ids.add(symbol.symbol_id)
        seen_names.add(symbol.name)
        parsed.append(symbol)
    return tuple(parsed)


@dataclass(frozen=True)
class ObligationLimits:
    """What a designated market maker is measured against (Open Issue 005 sub-decision 5h).

    A market maker is defined by its obligations, not its privileges. The inventory exemption
    in `designated_market_maker_accounts` is granted in exchange for these, so they are
    configuration rather than constants — the exchange sets them, the bot meets them.
    """

    max_spread_bps: int
    min_quote_size: int
    min_two_sided_uptime: float


@dataclass(frozen=True)
class NoiseSettings:
    count_per_symbol: int
    arrivals_per_second: float
    min_qty: int
    max_qty: int


@dataclass(frozen=True)
class BotSettings:
    """How the bots behave. Distinct from `designated_market_maker_accounts`, which stays a
    top-level setting because it is a *gateway* rule about who may hold negative inventory —
    the gateway reads that and never reads any of this."""

    seed: int
    quote_hz: int
    half_spread_bps: int
    quote_size: int
    inventory_skew_bps: int
    poll_interval_ms: int
    fair_value_start_ticks: int
    fair_value_volatility_ticks: int
    obligations: ObligationLimits
    noise: NoiseSettings


def _bots(table: dict) -> BotSettings:
    return BotSettings(
        seed=_require(table, "bots", "seed"),
        quote_hz=_require(table, "bots", "quote_hz"),
        half_spread_bps=_require(table, "bots", "half_spread_bps"),
        quote_size=_require(table, "bots", "quote_size"),
        inventory_skew_bps=_require(table, "bots", "inventory_skew_bps"),
        poll_interval_ms=_require(table, "bots", "poll_interval_ms"),
        fair_value_start_ticks=_require(table, "bots", "fair_value_start_ticks"),
        fair_value_volatility_ticks=_require(table, "bots", "fair_value_volatility_ticks"),
        obligations=ObligationLimits(
            max_spread_bps=_require(table["bots"], "obligations", "max_spread_bps"),
            min_quote_size=_require(table["bots"], "obligations", "min_quote_size"),
            min_two_sided_uptime=_require(
                table["bots"], "obligations", "min_two_sided_uptime"
            ),
        ),
        noise=NoiseSettings(
            count_per_symbol=_require(table["bots"], "noise", "count_per_symbol"),
            arrivals_per_second=_require(table["bots"], "noise", "arrivals_per_second"),
            min_qty=_require(table["bots"], "noise", "min_qty"),
            max_qty=_require(table["bots"], "noise", "max_qty"),
        ),
    )


@dataclass(frozen=True)
class Settings:
    # --- configuration: from the file, covered by config_hash -------------------------------
    initial_cash_ticks: int
    session_ttl_seconds: int
    session_cookie_name: str
    session_cookie_samesite: str
    idempotency_ttl_seconds: int
    max_orders_per_second: int
    market_order_band_bps: int
    conflation_hz: int
    book_depth: int
    #: Bar widths in seconds of stream time. See the config file — 5.1 revisits what a
    #: second means once the replay clock exists.
    bar_bucket_seconds: tuple[int, ...]
    replay_real_seconds_per_simulated_minute: int
    #: SHA-256 of the pinned price history. The dataset's *identity* is a domain parameter — it
    #: determines every price in the market — so it is hashed with the rest of the file. Its
    #: path is infrastructure and lives in `services/bots/fairvalue.py`.
    replay_data_sha256: str
    stream_inbound: str
    stream_outbound: str
    checkpoint_interval_ms: int
    checkpoint_trim_interval_ms: int
    checkpoint_inbound_readers: tuple[str, ...]
    checkpoint_outbound_readers: tuple[str, ...]
    stream_batch_max: int
    stream_health_poll_ms: int
    symbols: tuple[Symbol, ...]
    #: Usernames permitted to hold negative inventory. See the config file.
    designated_market_maker_accounts: frozenset[str]
    #: How the bots behave. Only the bot runner reads this.
    bots: BotSettings

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
            idempotency_ttl_seconds=_require(table, "idempotency", "ttl_seconds"),
            max_orders_per_second=_require(table, "limits", "max_orders_per_second"),
            market_order_band_bps=_require(table, "limits", "market_order_band_bps"),
            conflation_hz=_require(table, "market_data", "conflation_hz"),
            book_depth=_require(table, "market_data", "book_depth"),
            bar_bucket_seconds=tuple(
                _require(table, "market_data", "bar_bucket_seconds")
            ),
            replay_real_seconds_per_simulated_minute=_require(
                table, "replay", "real_seconds_per_simulated_minute"
            ),
            replay_data_sha256=_require(table, "replay", "data_sha256"),
            stream_inbound=_require(table, "streams", "inbound"),
            stream_outbound=_require(table, "streams", "outbound"),
            checkpoint_interval_ms=_require(table, "checkpoint", "interval_ms"),
            checkpoint_trim_interval_ms=_require(table, "checkpoint", "trim_interval_ms"),
            checkpoint_inbound_readers=tuple(_require(table, "checkpoint", "inbound_readers")),
            checkpoint_outbound_readers=tuple(_require(table, "checkpoint", "outbound_readers")),
            stream_batch_max=_require(table, "streams", "batch_max"),
            stream_health_poll_ms=_require(table, "streams", "health_poll_ms"),
            symbols=_symbols(_require(table, "symbols", "listed")),
            designated_market_maker_accounts=frozenset(
                _require(table, "bots", "designated_market_maker_accounts")
            ),
            bots=_bots(table),
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
