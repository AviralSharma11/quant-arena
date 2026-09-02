"""The last link: what the matcher emits is what the ledger can spend.

Task 2.2 proved the ledger moves money correctly from a hand-written list of records. This
proves the matcher's *actual* outbound stream is that list — the two halves were written a week
apart by the same person against the same frozen contract, and never once run end to end.

    POST /orders ──► qa.inbound ──► matcher ──► qa.outbound ──► ledger

The ledger is driven in memory here rather than through PostgreSQL. The read-model projection
already has its own tests in `tests/ledger/`; what is unproven, and proven here, is the handoff.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from config.settings import Settings  # noqa: E402
from contracts.v1.generated.contracts import AccountCreated, Fill  # noqa: E402
from services.ledger.ledger import Ledger, calculate_fees  # noqa: E402
from services.matcher.runner import Matcher  # noqa: E402
from tests.matcher.test_integration import (  # noqa: E402
    BUY,
    SELL,
    drain,
    feed,
    outbound,
    submit,
)  # the same helpers, so the two files cannot drift apart

pytestmark = pytest.mark.anyio


async def test_a_fill_the_matcher_produced_moves_both_counterparties(
    settings: Settings, clean_redis
):
    """Criterion 2: the loop closes all the way to money.

    Buyer and seller both start with the configured grant. One trade later the buyer is down
    the notional plus a fee and holds the position; the seller is up the notional less a fee.
    """
    grant = settings.initial_cash_ticks
    redis = await feed(settings, [
        # A notional large enough that the basis-point fees do not truncate to zero.
        submit(coid=1, user=10, side=BUY, price=20_000, qty=5, ts=1),
        submit(coid=2, user=20, side=SELL, price=20_000, qty=5, ts=2),
    ])
    matcher = Matcher(redis, settings)
    matcher.producer.start()
    await drain(matcher)
    await matcher.producer.stop()

    records = await outbound(redis, settings)
    await redis.aclose()

    ledger = Ledger()
    # The accounts themselves are the gateway's, not the engine's — the engine is money-blind
    # (Open Issue 001) and never emits an account record. Seeded here exactly as 2.2's own
    # replay test does.
    for user_id in (10, 20):
        ledger.apply(
            AccountCreated.new(
                timestamp_ns=0, client_order_id=0, user_id=user_id, initial_cash_ticks=grant
            )
        )
    for record in records:
        ledger.apply(record)

    fill = next(r for r in records if isinstance(r, Fill))
    notional = fill.price_ticks * fill.qty

    # The buyer rested first, so the buyer is the maker and pays the smaller fee.
    fees = calculate_fees(fill.price_ticks, fill.qty)
    maker_fee, taker_fee = fees.maker_fee_ticks, fees.taker_fee_ticks
    assert maker_fee > 0 and taker_fee > maker_fee
    assert ledger.cash_balances[10] == grant - notional - maker_fee
    assert ledger.cash_balances[20] == grant + notional - taker_fee
    assert ledger.house_fee_ticks == maker_fee + taker_fee
    assert ledger.positions[(10, 1)] == 5
    assert ledger.positions[(20, 1)] == -5

    # Conservation: every tick is either in a user's cash or in the fee account.
    total = sum(ledger.cash_balances.values()) + ledger.house_fee_ticks
    assert total == 2 * grant

    # And the fully filled orders left the book on both sides.
    assert ledger.open_orders == {}


async def test_the_aggressor_side_the_matcher_stamps_is_the_one_the_ledger_bills(
    settings: Settings, clean_redis
):
    """`aggressor_side` "cannot be derived after the fact" (`schema.toml`), and the ledger's
    maker/taker fee split is the only consumer that can prove the matcher got it right."""
    redis = await feed(settings, [
        # A notional large enough that the basis-point fees do not truncate to zero.
        submit(coid=1, user=10, side=BUY, price=20_000, qty=5, ts=1),
        submit(coid=2, user=20, side=SELL, price=20_000, qty=5, ts=2),
    ])
    matcher = Matcher(redis, settings)
    matcher.producer.start()
    await drain(matcher)
    await matcher.producer.stop()
    records = await outbound(redis, settings)
    await redis.aclose()

    fill = next(r for r in records if isinstance(r, Fill))
    # The seller arrived second and crossed a resting bid, so the seller is the taker.
    assert int(fill.aggressor_side) == SELL
    assert fill.maker_user_id == 10 and fill.taker_user_id == 20

    ledger = Ledger()
    for user_id in (10, 20):
        ledger.apply(
            AccountCreated.new(
                timestamp_ns=0, client_order_id=0, user_id=user_id,
                initial_cash_ticks=settings.initial_cash_ticks,
            )
        )
    for record in records:
        ledger.apply(record)

    notional = fill.price_ticks * fill.qty
    buyer_paid = settings.initial_cash_ticks - ledger.cash_balances[10]
    seller_got = ledger.cash_balances[20] - settings.initial_cash_ticks
    # The maker (buyer) pays the smaller fee, so the buyer's outlay is nearer the notional
    # than the seller's proceeds are.
    assert buyer_paid - notional < notional - seller_got
