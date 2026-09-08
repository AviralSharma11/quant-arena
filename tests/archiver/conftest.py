"""Fixtures for the archiver tests, and the session generator they share.

The state machine is pure, so almost everything here is provable without Redis and without a
running stack. Only the process test reaches for a store.

The one thing worth explaining is `session()`. Records are not hand-written: inbound orders are
driven through `NaiveMatcher`, the same executable specification the matcher runs (Open Issue
001), and its **outbound** records are what the archiver sees. So these tests exercise the real
record shapes, the real maker-price rule and the real `OrderAccepted`-before-`Fill` ordering
rather than a plausible imitation of them — which matters here because a `Fill` that named an
order the book had never heard of would silently produce an empty snapshot.
"""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from config.settings import Settings  # noqa: E402
from contracts.v1.generated.contracts import Side, SubmitOrder, Tif  # noqa: E402
from services.fanout.bars import NS_PER_SECOND  # noqa: E402
from services.matcher.adapter import NaiveMatcher  # noqa: E402

BUY, SELL = int(Side.BUY), int(Side.SELL)

#: An arbitrary but fixed wall-clock origin, **on a whole minute** so that a flush unit boundary
#: is obvious in a failure message: 2023-11-14T22:14:00Z, which is 60 × 28,333,334 seconds.
#: The rounder-looking 1_700_000_000 is not a multiple of 60 and put every unit boundary 20
#: seconds off, which is worth a comment because the failure it produced looked like a bucketing
#: bug in the code rather than a badly chosen constant in the test.
ORIGIN_NS = 1_700_000_040 * NS_PER_SECOND


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="session")
def settings() -> Settings:
    """The real configuration file, so a bad value in it fails the suite."""
    return Settings.load()


@pytest.fixture
def test_settings() -> Settings:
    """Infrastructure pointed at the test stores; every domain parameter left alone."""
    return dataclasses.replace(Settings.load(), redis_url="redis://localhost:6379/15")


@pytest.fixture
def archive_root(tmp_path: Path) -> Path:
    return tmp_path / "archive"


@pytest.fixture
def clean_redis(test_settings: Settings):
    import redis as redis_sync

    client = redis_sync.Redis.from_url(test_settings.redis_url)
    client.flushdb()
    client.close()


def submit(coid, user, side, price, qty, *, symbol=1, tif=Tif.GTC, ts=0):
    return SubmitOrder.new(
        timestamp_ns=ts,
        client_order_id=coid,
        user_id=user,
        symbol_id=symbol,
        side=int(side),
        tif=int(tif),
        price_ticks=price,
        qty=qty,
    )


def submit_through(matcher, coid, user, side, price, qty, symbol, ts) -> list:
    return list(matcher.apply(submit(coid, user, side, price, qty, symbol=symbol, ts=ts)))


def session(
    *,
    seconds: int = 130,
    symbols: tuple[int, ...] = (1, 2),
    trade_every: int = 1,
    start_ns: int = ORIGIN_NS,
) -> list:
    """Outbound records for a synthetic session, in stream order.

    One crossing pair per symbol per `trade_every` seconds, at a price that walks so the OHLCV
    has a shape rather than a flat line. `seconds` defaults to 130 — more than two 60-second
    flush units, which is the minimum that proves a unit is sealed by a *later* one and not by
    the end of the run.
    """
    matcher = NaiveMatcher(initial_cash_ticks=10_000_000_000)
    out: list = []
    coid = 0

    # Resting liquidity well away from where the trading happens, placed once and never filled —
    # a standing quote, as a designated market maker would leave. Without it every order in this
    # session crosses immediately and the book is empty at the end of every second, so the L2
    # snapshots are all zero rows and nothing about sampling is actually being tested.
    for symbol in symbols:
        base = 1_000 + symbol * 10
        coid += 1
        out.extend(submit_through(matcher, coid, 900 + symbol, BUY, base - 100, 7, symbol, start_ns))
        coid += 1
        out.extend(submit_through(matcher, coid, 910 + symbol, SELL, base + 100, 7, symbol, start_ns))

    for second in range(seconds):
        if second % trade_every:
            continue
        ts = start_ns + second * NS_PER_SECOND
        for symbol in symbols:
            price = 1_000 + symbol * 10 + (second % 7) * 3
            coid += 1
            out.extend(matcher.apply(submit(coid, 100 + symbol, BUY, price, 5, symbol=symbol, ts=ts)))
            coid += 1
            out.extend(matcher.apply(submit(coid, 200 + symbol, SELL, price, 5, symbol=symbol, ts=ts)))
    return out


def stream_ids(records: list, *, first_ms: int = 1_700_000_000_000) -> list[tuple[str, object]]:
    """Pair each record with a plausible ascending Redis stream ID.

    Ascending in the millisecond part rather than the ordinal, so that `parse_stream_id`'s
    ordering is genuinely exercised: at more than ten records the string and numeric orders
    disagree, which is the bug that comparison exists to prevent.
    """
    return [(f"{first_ms + i}-0", record) for i, record in enumerate(records)]
