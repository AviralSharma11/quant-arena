import pytest

from engine.naive_model import Fill, Order, OrderBook, Side


def test_order_starts_with_full_quantity():
    order = Order(
        order_id=1,
        user_id=10,
        symbol="QA-TECH",
        side=Side.BUY,
        price=100,
        quantity=25,
        created_at=1,
    )

    assert order.remaining_quantity == 25
    assert order.is_open is True


def test_order_reduces_remaining_quantity():
    order = Order(
        order_id=2,
        user_id=11,
        symbol="QA-TECH",
        side=Side.SELL,
        price=101,
        quantity=10,
        created_at=2,
    )

    assert order.reduce(4) == 6
    assert order.remaining_quantity == 6
    assert order.is_open is True


def test_order_rejects_non_positive_quantity():
    with pytest.raises(ValueError):
        Order(
            order_id=3,
            user_id=12,
            symbol="QA-TECH",
            side=Side.BUY,
            price=99,
            quantity=0,
            created_at=3,
        )


def test_fill_requires_positive_quantity():
    with pytest.raises(ValueError):
        Fill(
            buy_order_id=1,
            sell_order_id=2,
            symbol="QA-TECH",
            price=100,
            quantity=0,
        )


def test_order_book_tracks_best_bid_and_ask():
    book = OrderBook()
    buy_order = Order(
        order_id=4,
        user_id=13,
        symbol="QA-TECH",
        side=Side.BUY,
        price=120,
        quantity=8,
        created_at=4,
    )
    sell_order = Order(
        order_id=5,
        user_id=14,
        symbol="QA-TECH",
        side=Side.SELL,
        price=115,
        quantity=3,
        created_at=5,
    )

    book.add_order(buy_order)
    book.add_order(sell_order)

    assert book.best_bid == 120
    assert book.best_ask == 115
    assert book.best_bid_order is buy_order
    assert book.best_ask_order is sell_order
    assert book.has_orders is True


def test_order_book_removes_orders_and_clears_empty_levels():
    book = OrderBook()
    buy_order = Order(
        order_id=6,
        user_id=15,
        symbol="QA-TECH",
        side=Side.BUY,
        price=125,
        quantity=10,
        created_at=6,
    )
    book.add_order(buy_order)

    assert book.remove_order(6) is buy_order
    assert book.best_bid is None
    assert book.best_ask is None
    assert book.has_orders is False
    assert book.level(Side.BUY, 125) is None


def test_matching_loop_executes_crossing_order():
    book = OrderBook()
    buy = Order(1, 10, "QA-TECH", Side.BUY, 100, 10, 1)
    sell = Order(2, 11, "QA-TECH", Side.SELL, 99, 6, 2)

    book.add_order(buy)
    book.add_order(sell)
    fills = book.match()

    assert len(fills) == 1
    assert fills[0].buy_order_id == 1
    assert fills[0].sell_order_id == 2
    assert fills[0].quantity == 6
    assert book.best_bid == 100
    assert book.best_ask is None
    assert buy.remaining_quantity == 4
    assert sell.remaining_quantity == 0


def test_matching_loop_handles_partial_fill_and_resting_order():
    book = OrderBook()
    buy = Order(10, 20, "QA-TECH", Side.BUY, 100, 20, 1)
    sell = Order(11, 21, "QA-TECH", Side.SELL, 99, 8, 2)

    book.add_order(buy)
    book.add_order(sell)
    fills = book.match()

    assert len(fills) == 1
    assert fills[0].quantity == 8
    assert buy.remaining_quantity == 12
    assert sell.remaining_quantity == 0
    assert book.best_bid == 100
    assert book.best_ask is None


def test_matching_loop_uses_resting_bid_price_when_seller_aggresses():
    book = OrderBook()
    buy = Order(12, 20, "QA-TECH", Side.BUY, 100, 5, 1)
    sell = Order(13, 21, "QA-TECH", Side.SELL, 90, 5, 2)

    book.add_order(buy)
    book.add_order(sell)
    fills = book.match()

    assert fills[0].price == 100


def test_matching_loop_does_nothing_when_no_crossing_order_exists():
    book = OrderBook()
    buy = Order(20, 30, "QA-TECH", Side.BUY, 98, 10, 1)
    sell = Order(21, 31, "QA-TECH", Side.SELL, 100, 5, 2)

    book.add_order(buy)
    book.add_order(sell)
    fills = book.match()

    assert fills == []
    assert buy.remaining_quantity == 10
    assert sell.remaining_quantity == 5
    assert book.best_bid == 98
    assert book.best_ask == 100
