"""Fetch the crypto price history that the bots replay, once, and pin it.

**This script never runs at runtime.** Task 5.1's Boundaries make that a hard rule: fetching
live would destroy reproducibility, which is a stated project goal. It is a setup tool, run by
a developer, and its output — `data/market_history.parquet` plus a recorded SHA-256 — is
committed. `services/bots/fairvalue.py` reads the file and has no network code path at all,
which is what makes "the system runs fully offline from pinned data" provable rather than
asserted.

## What "anonymised" means here, precisely

The file contains no instrument names and no timestamps. Each real instrument becomes a
fictional symbol (`QAA`…`QAJ`), and each row is identified by a 0-based minute index rather than
a wall clock, so a row cannot be joined back to a moment in the real market without re-running
this script. The script itself names its sources, because reproducibility requires it — so the
honest claim is that the *dataset* is anonymised, not that the mapping is a secret.

The names are deliberately meaningless, for the reason `config/quant_arena.toml` already gives:
a placeholder that reads like a real ticker is the one most likely to survive into the
demonstration by accident. Task 5.1's Boundaries forbid naming a symbol after a real instrument.

## Prices are integer ticks from here on

Binance returns decimal strings. They are converted to integer ticks **once, here**, and
everything downstream is `int`. A float fair value is the one place a float could leak into a
price the gateway receives, and Open Issue 016 calls a float below the presentation layer a bug
rather than a rounding concern.

`ticks_per_unit` is the scale: `price_ticks = round(price * ticks_per_unit)`. It is chosen per
symbol so the quoted price keeps roughly the granularity the real instrument has — two decimal
places on a $60,000 instrument, five on a $0.15 one — and it is written into
`config/quant_arena.toml` as `tick_size_ticks`, which is what the frontend divides by to display.

Usage:

    python scripts/fetch_market_history.py                 # fetch, write, print the checksum
    python scripts/fetch_market_history.py --check         # verify the committed file only
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

import httpx
import pyarrow as pa
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / "data" / "market_history.parquet"
CONFIG_PATH = ROOT / "config" / "quant_arena.toml"
CHECKSUM_KEY = "data_sha256"

BINANCE_KLINES = "https://api.binance.com/api/v3/klines"
INTERVAL = "1m"
MINUTES = 10_080  # seven days. At 1 real second : 1 simulated minute, ~2.8 hours before looping.
PAGE = 1_000  # the endpoint's maximum per request.

#: Real instrument -> (fictional symbol, symbol_id, ticks per unit of price, lot size).
#:
#: `ticks_per_unit` is a power of ten chosen from the instrument's own price magnitude, so that
#: a tick is a sensible increment on each: 0.01 on the expensive ones, 0.00001 on the cheap ones.
#: Ten distinct values is the point — a table where every tick size was 1 would assert that all
#: ten symbols trade on the same scale, which is exactly the placeholder the provisional block
#: was flagged as.
INSTRUMENTS: tuple[tuple[str, str, int, int, int], ...] = (
    ("BTCUSDT",  "QAA", 1,    100,        1),
    ("ETHUSDT",  "QAB", 2,    100,        1),
    ("BNBUSDT",  "QAC", 3,    100,        1),
    ("SOLUSDT",  "QAD", 4,    100,        1),
    ("XRPUSDT",  "QAE", 5,    10_000,     10),
    ("ADAUSDT",  "QAF", 6,    10_000,     10),
    ("DOGEUSDT", "QAG", 7,    100_000,    100),
    ("AVAXUSDT", "QAH", 8,    1_000,      1),
    ("LINKUSDT", "QAI", 9,    1_000,      1),
    ("DOTUSDT",  "QAJ", 10,   1_000,      1),
)

CLOSE_INDEX = 4  # kline field order: open_time, open, high, low, close, volume, ...


def fetch_closes(client: httpx.Client, instrument: str, minutes: int) -> list[str]:
    """One instrument's closing prices, oldest first, as the decimal strings Binance sends."""
    closes: list[str] = []
    end_time: int | None = None
    while len(closes) < minutes:
        params = {
            "symbol": instrument,
            "interval": INTERVAL,
            "limit": min(PAGE, minutes - len(closes)),
        }
        if end_time is not None:
            params["endTime"] = end_time
        response = client.get(BINANCE_KLINES, params=params, timeout=30.0)
        response.raise_for_status()
        page = response.json()
        if not page:
            break
        closes = [row[CLOSE_INDEX] for row in page] + closes
        # Walk backwards. One millisecond before this page's first open_time.
        end_time = int(page[0][0]) - 1
    return closes[-minutes:]


def to_ticks(price: str, ticks_per_unit: int) -> int:
    """Decimal string to integer ticks, rounded half-up, floored at one tick.

    A price of zero is not a price: the market maker quotes around this value and the gateway
    would reject the resulting order as INVALID_PRICE — a slow, confusing failure rather than a
    bounded one. Real close prices are never zero, so the floor is a guard, not a behaviour.
    """
    scaled = round(float(price) * ticks_per_unit)
    return max(1, int(scaled))


def build_table(closes_by_symbol: dict[str, list[int]]) -> pa.Table:
    """One row per (symbol, minute). No instrument name, no timestamp — see the module docstring."""
    symbols: list[str] = []
    minutes: list[int] = []
    prices: list[int] = []
    for symbol in sorted(closes_by_symbol):
        series = closes_by_symbol[symbol]
        symbols.extend([symbol] * len(series))
        minutes.extend(range(len(series)))
        prices.extend(series)
    return pa.table(
        {
            "symbol": pa.array(symbols, type=pa.string()),
            "minute_index": pa.array(minutes, type=pa.int64()),
            "close_ticks": pa.array(prices, type=pa.int64()),
        }
    )


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def recorded_checksum() -> str | None:
    """The checksum as `config/quant_arena.toml` records it.

    The configuration file is the single source of truth for it, deliberately: *which* dataset
    the market replays is a domain parameter every process must agree on, so it belongs in the
    hashed file. *Where the file sits on disk* is infrastructure and stays a code constant. A
    second copy in a `.sha256` file beside the data would be one more thing that can drift.
    """
    import tomllib

    table = tomllib.loads(CONFIG_PATH.read_text())
    return table.get("replay", {}).get(CHECKSUM_KEY) or None


def verify() -> int:
    if not DATA_PATH.exists():
        print(f"missing {DATA_PATH}", file=sys.stderr)
        return 1
    expected = recorded_checksum()
    if not expected:
        print(f"replay.{CHECKSUM_KEY} is not set in {CONFIG_PATH}", file=sys.stderr)
        return 1
    actual = sha256_of(DATA_PATH)
    if expected != actual:
        print(f"checksum mismatch\n  expected {expected}\n  actual   {actual}", file=sys.stderr)
        return 1
    print(f"ok — {DATA_PATH.name} matches replay.{CHECKSUM_KEY}")
    return 0


def fetch() -> int:
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    closes_by_symbol: dict[str, list[int]] = {}
    with httpx.Client() as client:
        for instrument, symbol, _symbol_id, ticks_per_unit, _lot in INSTRUMENTS:
            raw = fetch_closes(client, instrument, MINUTES)
            if len(raw) < MINUTES:
                print(
                    f"{instrument}: got {len(raw)} of {MINUTES} minutes", file=sys.stderr
                )
                return 1
            closes_by_symbol[symbol] = [to_ticks(price, ticks_per_unit) for price in raw]
            print(f"{symbol}: {len(raw)} minutes")

    # Deterministic settings, so re-running on identical data reproduces an identical file and
    # the checksum means what it claims to.
    pq.write_table(
        build_table(closes_by_symbol),
        DATA_PATH,
        compression="zstd",
        write_statistics=False,
        store_schema=True,
    )
    digest = sha256_of(DATA_PATH)
    size_kb = DATA_PATH.stat().st_size / 1024
    print(f"\nwrote {DATA_PATH} ({size_kb:.0f} KB)")
    if recorded_checksum() == digest:
        print(f"replay.{CHECKSUM_KEY} already matches — nothing to do")
        return 0
    # Deliberately not rewritten automatically. Editing the hashed configuration from a script
    # would change every process's config_hash as a side effect of running a fetch, and the one
    # question that hash answers is "did the configuration change?".
    print(
        f"\nNow set this in {CONFIG_PATH.relative_to(ROOT)} under [replay]:\n"
        f'\n    {CHECKSUM_KEY} = "{digest}"\n'
        "\nThat changes the config hash, which is correct: different prices are a different "
        "configuration."
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify the committed file against replay.data_sha256, and fetch nothing",
    )
    args = parser.parse_args()
    return verify() if args.check else fetch()


if __name__ == "__main__":
    raise SystemExit(main())
