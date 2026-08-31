"""Task 2.2 — Ledger unit tests & cash conservation invariants.

Success Criteria:
1. A fill moves both counterparties' cash and positions correctly.
2. Fees land in the house account; user cash + fee account is strictly conserved.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from contracts.v1.generated.contracts import (
    AccountCreated,
    CashCredited,
    Fill,
    OrderAccepted,
    OrderCancelled,
    Side,
    Tif,
)
from services.ledger.ledger import (
    MAKER_FEE_BPS,
    TAKER_FEE_BPS,
    Ledger,
    calculate_fees,
)


def test_account_created_and_cash_credited():
    ledger = Ledger()
    assert ledger.get_balance(1) == 0

    ledger.apply(
        AccountCreated.new(
            timestamp_ns=1,
            client_order_id=1,
            user_id=1,
            initial_cash_ticks=1_000_000,
        )
    )
    assert ledger.get_balance(1) == 1_000_000
    assert ledger.total_system_cash() == 1_000_000

    ledger.apply(
        CashCredited.new(
            timestamp_ns=2,
            client_order_id=2,
            user_id=1,
            amount_ticks=500_000,
        )
    )
    assert ledger.get_balance(1) == 1_500_000
    assert ledger.total_system_cash() == 1_500_000


def test_order_accepted_and_cancelled():
    ledger = Ledger()
    ledger.apply(
        OrderAccepted.new(
            timestamp_ns=10,
            order_id=101,
            client_order_id=1,
            user_id=10,
            symbol_id=1,
            side=Side.BUY,
            price_ticks=100,
            qty=10,
            tif=Tif.GTC,
        )
    )
    orders = ledger.get_open_orders(10)
    assert len(orders) == 1
    assert orders[0].order_id == 101
    assert orders[0].remaining_qty == 10

    # Partial cancel
    ledger.apply(
        OrderCancelled.new(
            timestamp_ns=11,
            order_id=101,
            client_order_id=2,
            user_id=10,
            symbol_id=1,
            remaining_qty=4,
            reason=1,
        )
    )
    orders = ledger.get_open_orders(10)
    assert len(orders) == 1
    assert orders[0].remaining_qty == 6

    # Full cancel of remainder
    ledger.apply(
        OrderCancelled.new(
            timestamp_ns=12,
            order_id=101,
            client_order_id=3,
            user_id=10,
            symbol_id=1,
            remaining_qty=6,
            reason=1,
        )
    )
    assert ledger.get_open_orders(10) == []


def test_fill_moves_cash_and_positions_with_buy_aggressor():
    """Taker is buyer (aggressor_side=BUY), Maker is seller."""
    ledger = Ledger()
    # User 1 (buyer) has 1,000,000; User 2 (seller) has 1,000,000
    ledger.apply(AccountCreated.new(timestamp_ns=1, client_order_id=1, user_id=1, initial_cash_ticks=1_000_000))
    ledger.apply(AccountCreated.new(timestamp_ns=2, client_order_id=2, user_id=2, initial_cash_ticks=1_000_000))

    # Seller rests order on book
    ledger.apply(
        OrderAccepted.new(
            timestamp_ns=3,
            order_id=50,
            client_order_id=3,
            user_id=2,
            symbol_id=1,
            side=Side.SELL,
            price_ticks=100_000,
            qty=5,
            tif=Tif.GTC,
        )
    )

    # Trade: Buyer aggresses and buys 5 units @ 100,000
    # Notional = 500,000
    # Taker fee (buyer) = 500,000 * 10 / 10,000 = 500 ticks
    # Maker fee (seller) = 500,000 * 2 / 10,000 = 100 ticks
    # Total fee = 600 ticks
    ledger.apply(
        Fill.new(
            timestamp_ns=4,
            maker_order_id=50,
            taker_order_id=51,
            maker_user_id=2,
            taker_user_id=1,
            price_ticks=100_000,
            qty=5,
            symbol_id=1,
            aggressor_side=Side.BUY,
        )
    )

    # Buyer: cash -= 500,000 + 500 = -500,500 -> 499,500; position = +5
    assert ledger.get_balance(1) == 499_500
    assert ledger.get_position(1, symbol_id=1) == 5

    # Seller: cash += 500,000 - 100 = +499,900 -> 1,499,900; position = -5
    assert ledger.get_balance(2) == 1_499_900
    assert ledger.get_position(2, symbol_id=1) == -5

    # House fee account = 600
    assert ledger.house_fee_ticks == 600

    # Invariant: Total cash = 499,500 + 1,499,900 + 600 = 2,000,000
    assert ledger.total_system_cash() == 2_000_000

    # Maker order should be fully filled and removed
    assert ledger.get_open_orders(2) == []


def test_fill_moves_cash_and_positions_with_sell_aggressor():
    """Taker is seller (aggressor_side=SELL), Maker is buyer."""
    ledger = Ledger()
    ledger.apply(AccountCreated.new(timestamp_ns=1, client_order_id=1, user_id=1, initial_cash_ticks=1_000_000))
    ledger.apply(AccountCreated.new(timestamp_ns=2, client_order_id=2, user_id=2, initial_cash_ticks=1_000_000))

    # Buyer rests order on book
    ledger.apply(
        OrderAccepted.new(
            timestamp_ns=3,
            order_id=60,
            client_order_id=3,
            user_id=1,
            symbol_id=1,
            side=Side.BUY,
            price_ticks=200_000,
            qty=3,
            tif=Tif.GTC,
        )
    )

    # Trade: Seller aggresses and sells 2 units @ 200,000 (partial fill of maker)
    # Notional = 400,000
    # Taker fee (seller) = 400,000 * 10 / 10,000 = 400 ticks
    # Maker fee (buyer) = 400,000 * 2 / 10,000 = 80 ticks
    # Total fee = 480 ticks
    ledger.apply(
        Fill.new(
            timestamp_ns=4,
            maker_order_id=60,
            taker_order_id=61,
            maker_user_id=1,
            taker_user_id=2,
            price_ticks=200_000,
            qty=2,
            symbol_id=1,
            aggressor_side=Side.SELL,
        )
    )

    # Buyer (maker): cash -= 400,000 + 80 = -400,080 -> 599,920; pos = +2
    assert ledger.get_balance(1) == 599_920
    assert ledger.get_position(1, symbol_id=1) == 2

    # Seller (taker): cash += 400,000 - 400 = +399,600 -> 1,399,600; pos = -2
    assert ledger.get_balance(2) == 1_399_600
    assert ledger.get_position(2, symbol_id=1) == -2

    # House fees = 480
    assert ledger.house_fee_ticks == 480

    # Invariant: Total cash = 599,920 + 1,399,600 + 480 = 2,000,000
    assert ledger.total_system_cash() == 2_000_000

    # Maker order remaining qty should be 1
    orders = ledger.get_open_orders(1)
    assert len(orders) == 1
    assert orders[0].remaining_qty == 1


@given(
    price=st.integers(min_value=1, max_value=10_000_000),
    qty=st.integers(min_value=1, max_value=1_000_000),
    aggressor=st.sampled_from([Side.BUY, Side.SELL]),
)
def test_property_cash_and_unit_conservation(price: int, qty: int, aggressor: Side):
    """Property test: Across arbitrary trades, cash and units are strictly conserved."""
    ledger = Ledger()
    grant = 50_000_000_000_000  # large enough for any generated trade
    ledger.apply(AccountCreated.new(timestamp_ns=1, client_order_id=1, user_id=10, initial_cash_ticks=grant))
    ledger.apply(AccountCreated.new(timestamp_ns=2, client_order_id=2, user_id=20, initial_cash_ticks=grant))

    initial_total_cash = ledger.total_system_cash()
    assert initial_total_cash == 2 * grant

    ledger.apply(
        Fill.new(
            timestamp_ns=3,
            maker_order_id=1,
            taker_order_id=2,
            maker_user_id=10,
            taker_user_id=20,
            price_ticks=price,
            qty=qty,
            symbol_id=1,
            aggressor_side=aggressor,
        )
    )

    # Invariant: system cash is strictly conserved
    assert ledger.total_system_cash() == initial_total_cash

    # Invariant: net units of symbol across all users sum to 0
    total_units = sum(qty for (uid, sym), qty in ledger.positions.items() if sym == 1)
    assert total_units == 0
