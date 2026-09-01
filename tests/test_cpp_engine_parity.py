import subprocess
from pathlib import Path

import pytest

from engine.naive_model import Order, OrderBook, Side

ROOT = Path(__file__).resolve().parents[1]
CPP_DIR = ROOT / "engine" / "cpp"
CPP_EXE = CPP_DIR / "order_book_test.exe"


def _assert_same_price_fifo_sequence() -> None:
    book = OrderBook()
    book.add_order(Order(7, 40, "QA-TECH", Side.BUY, 100, 5, 1))
    book.add_order(Order(8, 41, "QA-TECH", Side.BUY, 100, 7, 2))
    book.add_order(Order(9, 42, "QA-TECH", Side.SELL, 100, 8, 3))

    fills = book.match()

    assert len(fills) == 2
    assert fills[0].buy_order_id == 7
    assert fills[1].buy_order_id == 8
    assert book.best_bid == 100
    assert book.best_ask is None
    assert book.orders_by_id[8].remaining_quantity == 4


def _assert_partial_fill_then_cancel() -> None:
    book = OrderBook()
    buy = Order(15, 70, "QA-TECH", Side.BUY, 100, 10, 1)
    sell = Order(16, 71, "QA-TECH", Side.SELL, 99, 8, 2)

    book.add_order(buy)
    book.add_order(sell)
    fills = book.match()

    assert len(fills) == 1
    assert fills[0].quantity == 8
    assert buy.remaining_quantity == 2
    book.remove_order(buy.order_id)
    assert book.best_bid is None
    assert book.best_ask is None


def test_python_reference_priority_and_cancel_logic() -> None:
    _assert_same_price_fifo_sequence()
    _assert_partial_fill_then_cancel()


def test_cpp_order_book_binary_runs_if_present() -> None:
    if not CPP_EXE.exists():
        pytest.skip("C++ engine binary is not present; build it with the VS 2022 toolchain before running parity validation.")

    result = subprocess.run([str(CPP_EXE)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "All C++ order book tests passed." in result.stdout
