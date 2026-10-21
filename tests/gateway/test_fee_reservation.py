"""A buy reserves its worst-case fee, so cash cannot be driven below zero.

The gateway reserved `price x qty` and the ledger then charged `notional + fee`, so an account
that spent its whole balance ended up to `TAKER_FEE_BPS` below zero — breaking Open Issue 004
§5's first invariant, `settled_cash - reserved >= 0`. These tests run `RiskState` alone: the
rule and the accounting that implements it are both in that class.
"""

from __future__ import annotations

import json

from hypothesis import example, given, settings
from hypothesis import strategies as st

from contracts.v1.generated.contracts import (
    CancelReason,
    Fill,
    OrderAccepted,
    OrderCancelled,
    Side,
    Tif,
)
from services.gateway.risk import RiskState, buy_cost

BUY, SELL = int(Side.BUY), int(Side.SELL)
BUYER, SELLER = 1, 2


def _state(cash: int) -> RiskState:
    state = RiskState()
    state.settled_cash[BUYER] = cash
    # The seller is a market maker so it may sell without inventory; only the buyer is under test.
    state.market_makers.add(SELLER)
    return state


def _accept(state: RiskState, order_id: int, price: int, qty: int) -> None:
    state.reserve(
        user_id=BUYER, client_order_id=order_id, symbol_id=1, side=BUY, price_ticks=price, qty=qty
    )
    state.apply(
        OrderAccepted.new(
            timestamp_ns=0, order_id=order_id, client_order_id=order_id, user_id=BUYER,
            price_ticks=price, qty=qty, symbol_id=1, side=BUY, tif=int(Tif.GTC),
        )
    )


def _fill(state: RiskState, order_id: int, *, price: int, qty: int, buyer_is_taker: bool) -> None:
    if buyer_is_taker:
        maker, taker, maker_user, taker_user, aggressor = 0, order_id, SELLER, BUYER, BUY
    else:
        maker, taker, maker_user, taker_user, aggressor = order_id, 0, BUYER, SELLER, SELL
    state.apply(
        Fill.new(
            timestamp_ns=0, maker_order_id=maker, taker_order_id=taker, maker_user_id=maker_user,
            taker_user_id=taker_user, price_ticks=price, qty=qty, symbol_id=1,
            aggressor_side=aggressor,
        )
    )


def _most_affordable(state: RiskState, price: int) -> int:
    """The largest quantity the gateway's own check accepts at `price`."""
    low, high = 0, state.available_cash(BUYER) // price
    while low < high:
        mid = (low + high + 1) // 2
        if state.reject_reason_for(
            user_id=BUYER, symbol_id=1, side=BUY, price_ticks=price, qty=mid
        ) is None:
            low = mid
        else:
            high = mid - 1
    return low


def _cancel(state: RiskState, order_id: int, remaining: int) -> None:
    state.apply(
        OrderCancelled.new(
            timestamp_ns=0, order_id=order_id, client_order_id=order_id, user_id=BUYER,
            remaining_qty=remaining, symbol_id=1, reason=int(CancelReason.USER_REQUESTED),
        )
    )


def test_a_buy_whose_fee_the_account_cannot_pay_is_refused():
    """The review's case: 10,000 ticks, buy 10 at 1,000. It used to fill and leave -10."""
    state = _state(10_000)
    reason = state.reject_reason_for(
        user_id=BUYER, symbol_id=1, side=BUY, price_ticks=1_000, qty=10
    )
    assert reason is not None and reason.name == "INSUFFICIENT_CASH"


def test_a_buy_sized_to_exactly_its_cost_fills_as_taker_and_stays_solvent():
    state = _state(buy_cost(1_000, 10))
    _accept(state, 1, 1_000, 10)
    assert state.available_cash(BUYER) == 0

    _fill(state, 1, price=1_000, qty=10, buyer_is_taker=True)

    assert state.settled_cash[BUYER] >= 0
    assert state.available_cash(BUYER) >= 0
    assert state.reserved.get(BUYER, 0) == 0


def test_cost_is_linear_so_partial_fills_release_exactly_what_was_reserved():
    assert buy_cost(999, 7) == 7 * buy_cost(999, 1)
    state = _state(10**9)
    _accept(state, 1, 999, 7)
    for _ in range(7):
        _fill(state, 1, price=999, qty=1, buyer_is_taker=True)
    assert state.reserved.get(BUYER, 0) == 0


def test_a_checkpoint_from_before_fees_were_reserved_is_restored_under_the_new_rule():
    """`reserved` is recomputed from the resting buys, never trusted from the dump: a total
    written under the notional-only rule would otherwise be released under this one."""
    live = _state(10**9)
    _accept(live, 1, 1_000, 10)
    dump = json.loads(live.dump_state())
    dump["reserved"] = [[BUYER, 1_000 * 10]]  # what the old rule would have saved

    restored = _state(0)
    restored.load_state(json.dumps(dump).encode())

    assert restored.reserved[BUYER] == buy_cost(1_000, 10)
    _cancel(restored, 1, 10)
    assert restored.reserved.get(BUYER, 0) == 0


_OPS = st.lists(
    st.one_of(
        st.tuples(st.just("buy"), st.integers(1, 50_000), st.integers(1, 40)),
        st.tuples(st.just("fill"), st.integers(0, 10**6), st.integers(1, 10**6), st.booleans(),
                  st.integers(0, 500)),
        st.tuples(st.just("cancel"), st.integers(0, 10**6)),
        # Spend everything at one price — the case that drove cash below zero.
        st.tuples(st.just("sweep"), st.integers(1_000, 50_000)),
    ),
    max_size=60,
)


@settings(max_examples=300, deadline=None)
@given(cash=st.integers(0, 3_000_000), ops=_OPS)
# Spend it all, fill it all as taker: the account the old rule left 3,000 ticks below zero.
@example(cash=3_000_000, ops=[("sweep", 1_000), ("fill", 0, 10**6, True, 0)])
def test_available_cash_never_goes_negative(cash: int, ops):
    """Any mix of buys, partial and price-improved fills (as taker or maker) and cancels keeps
    both settled and available cash at or above zero, and retiring every order releases every
    tick it reserved."""
    state = _state(cash)
    next_id = 1
    for op in ops:
        open_ids = sorted(state.open_orders)
        if op[0] == "buy":
            _, price, qty = op
            if state.reject_reason_for(
                user_id=BUYER, symbol_id=1, side=BUY, price_ticks=price, qty=qty
            ) is None:
                _accept(state, next_id, price, qty)
                next_id += 1
        elif op[0] == "sweep":
            qty = _most_affordable(state, op[1])
            if qty:
                _accept(state, next_id, op[1], qty)
                next_id += 1
        elif op[0] == "fill" and open_ids:
            _, pick, qty, buyer_is_taker, improvement = op
            order = state.open_orders[open_ids[pick % len(open_ids)]]
            fill_qty = min(qty, order.qty)
            fill_price = max(1, order.price_ticks - improvement)  # a buy never fills above limit
            _fill(state, order.order_id, price=fill_price, qty=fill_qty,
                  buyer_is_taker=buyer_is_taker)
        elif op[0] == "cancel" and open_ids:
            order = state.open_orders[open_ids[op[1] % len(open_ids)]]
            _cancel(state, order.order_id, order.qty)

        assert state.settled_cash.get(BUYER, 0) >= 0
        assert state.available_cash(BUYER) >= 0

    for order_id in sorted(state.open_orders):
        _cancel(state, order_id, state.open_orders[order_id].qty)
    assert state.reserved.get(BUYER, 0) == 0
