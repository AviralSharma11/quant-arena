from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from contracts.v1.generated.contracts import (
    CancelOrder,
    CreateAccount,
    CreditCash,
    Fill,
    OrderAccepted,
    OrderCancelled,
    Side,
    SubmitOrder,
    Tif,
)
from services.matcher.adapter import NaiveMatcher
from tests.cpp_worker import cpp_worker, run_cpp_worker

BUY = int(Side.BUY)
SELL = int(Side.SELL)
GTC = int(Tif.GTC)
IOC = int(Tif.IOC)
CORPUS_PATH = Path(__file__).parent / "data" / "task_4_1_corpus.json"


@st.composite
def order_flows(draw) -> list:
    records = []
    live_client_ids: list[int] = []
    next_order_id = 1
    length = draw(st.integers(min_value=1, max_value=40))

    for index in range(length):
        action = draw(
            st.sampled_from(
                ["submit", "cancel", "create_account", "credit_cash"]
                if live_client_ids
                else ["submit", "create_account", "credit_cash"]
            )
        )
        if action == "submit":
            client_order_id = next_order_id
            next_order_id += 1
            tif = draw(st.sampled_from([GTC, IOC]))
            records.append(
                SubmitOrder.new(
                    timestamp_ns=index + 1,
                    client_order_id=client_order_id,
                    user_id=draw(st.integers(min_value=1, max_value=4)),
                    symbol_id=draw(st.integers(min_value=1, max_value=3)),
                    side=draw(st.sampled_from([BUY, SELL])),
                    tif=tif,
                    price_ticks=draw(st.integers(min_value=1, max_value=120)),
                    qty=draw(st.integers(min_value=1, max_value=20)),
                )
            )
            if tif == GTC:
                live_client_ids.append(client_order_id)
            continue

        if action == "cancel":
            target = draw(st.sampled_from(live_client_ids))
            live_client_ids.remove(target)
            records.append(
                CancelOrder.new(
                    timestamp_ns=index + 1,
                    client_order_id=next_order_id,
                    user_id=draw(st.integers(min_value=1, max_value=4)),
                    target_client_order_id=target,
                )
            )
            next_order_id += 1
            continue

        user_id = draw(st.integers(min_value=1, max_value=4))
        if action == "create_account":
            records.append(
                CreateAccount.new(
                    timestamp_ns=index + 1,
                    client_order_id=next_order_id,
                    user_id=user_id,
                )
            )
        else:
            records.append(
                CreditCash.new(
                    timestamp_ns=index + 1,
                    client_order_id=next_order_id,
                    user_id=user_id,
                    amount_ticks=draw(st.integers(min_value=1, max_value=10_000)),
                )
            )
        next_order_id += 1

    return records


def _python_outputs(records: Sequence) -> tuple[list, NaiveMatcher]:
    matcher = NaiveMatcher(initial_cash_ticks=1_000_000)
    outputs = []
    for record in records:
        outputs.extend(matcher.apply(record))
        _assert_engine_invariants(matcher, outputs)
        _assert_output_invariants(outputs)
    return outputs, matcher


def _assert_engine_invariants(matcher: NaiveMatcher, outputs: Sequence) -> None:
    for book in matcher._books.values():
        if book.best_bid is not None and book.best_ask is not None:
            assert book.best_bid < book.best_ask
        assert all(order.remaining_quantity > 0 for order in book.orders_by_id.values())

    for record in outputs:
        if isinstance(record, Fill):
            assert record.qty > 0
        if isinstance(record, OrderCancelled):
            assert record.remaining_qty > 0


def _assert_output_invariants(outputs: Sequence) -> None:
    """Audit the event log independently of the model's final book state."""
    live: dict[int, dict[str, int]] = {}
    cancelled: set[int] = set()
    arrival = 0

    for record in outputs:
        if isinstance(record, OrderAccepted):
            live[record.order_id] = {
                "symbol_id": record.symbol_id,
                "side": record.side,
                "price_ticks": record.price_ticks,
                "remaining_qty": record.qty,
                "arrival": arrival,
            }
            arrival += 1
            continue

        if isinstance(record, Fill):
            assert record.maker_order_id in live
            assert record.taker_order_id in live
            maker = live[record.maker_order_id]
            taker = live[record.taker_order_id]
            assert record.maker_order_id not in cancelled
            assert record.taker_order_id not in cancelled
            assert maker["symbol_id"] == taker["symbol_id"] == record.symbol_id
            assert maker["side"] != taker["side"]
            assert maker["price_ticks"] == record.price_ticks
            assert 0 < record.qty <= maker["remaining_qty"]
            assert record.qty <= taker["remaining_qty"]

            opposite = [
                (order_id, order)
                for order_id, order in live.items()
                if order["symbol_id"] == taker["symbol_id"]
                and order["side"] != taker["side"]
                and order["remaining_qty"] > 0
                and (
                    (
                        taker["side"] == BUY
                        and order["price_ticks"] <= taker["price_ticks"]
                    )
                    or (
                        taker["side"] == SELL
                        and order["price_ticks"] >= taker["price_ticks"]
                    )
                )
            ]
            if taker["side"] == BUY:
                expected = min(opposite, key=lambda item: (item[1]["price_ticks"], item[1]["arrival"]))
            else:
                expected = max(opposite, key=lambda item: (item[1]["price_ticks"], -item[1]["arrival"]))
            assert expected[0] == record.maker_order_id

            maker["remaining_qty"] -= record.qty
            taker["remaining_qty"] -= record.qty
            if maker["remaining_qty"] == 0:
                del live[record.maker_order_id]
            if taker["remaining_qty"] == 0:
                del live[record.taker_order_id]
            continue

        if isinstance(record, OrderCancelled):
            assert record.order_id in live
            assert record.remaining_qty == live[record.order_id]["remaining_qty"]
            assert record.remaining_qty > 0
            del live[record.order_id]
            cancelled.add(record.order_id)


def _corpus_records(case: dict) -> list:
    records = []
    for item in case["records"]:
        if item["type"] == "submit":
            records.append(
                SubmitOrder.new(
                    timestamp_ns=item["timestamp_ns"],
                    client_order_id=item["client_order_id"],
                    user_id=item["user_id"],
                    symbol_id=item["symbol_id"],
                    side=int(Side[item["side"]]),
                    tif=int(Tif[item["tif"]]),
                    price_ticks=item["price_ticks"],
                    qty=item["qty"],
                )
            )
        elif item["type"] == "cancel":
            records.append(
                CancelOrder.new(
                    timestamp_ns=item["timestamp_ns"],
                    client_order_id=item["client_order_id"],
                    user_id=item["user_id"],
                    target_client_order_id=item["target_client_order_id"],
                )
            )
        else:
            raise AssertionError(f"unknown regression corpus record type: {item['type']}")
    return records


def test_fixed_flow_matches_python_and_is_byte_deterministic(cpp_worker):
    corpus = json.loads(CORPUS_PATH.read_text())
    assert corpus["version"] == 1
    for case in corpus["flows"]:
        records = _corpus_records(case)
        expected, _ = _python_outputs(records)
        actual, first_bytes = run_cpp_worker(cpp_worker, records)
        repeated, second_bytes = run_cpp_worker(cpp_worker, records)

        assert actual == expected, case["name"]
        assert second_bytes == first_bytes, case["name"]
        assert repeated == actual, case["name"]


def test_replaying_a_split_log_rebuilds_identical_cpp_output(cpp_worker):
    records = [
        SubmitOrder.new(
            timestamp_ns=1, client_order_id=1, user_id=10, symbol_id=1,
            side=Side.BUY, tif=Tif.GTC, price_ticks=100, qty=5,
        ),
        SubmitOrder.new(
            timestamp_ns=2, client_order_id=2, user_id=20, symbol_id=1,
            side=Side.SELL, tif=Tif.GTC, price_ticks=90, qty=2,
        ),
        SubmitOrder.new(
            timestamp_ns=3, client_order_id=3, user_id=11, symbol_id=2,
            side=Side.BUY, tif=Tif.IOC, price_ticks=50, qty=3,
        ),
    ]
    prefix = records[:2]
    suffix = records[2:]
    uninterrupted, uninterrupted_bytes = run_cpp_worker(cpp_worker, records)
    replayed, replayed_bytes = run_cpp_worker(cpp_worker, prefix + suffix)

    assert replayed == uninterrupted
    assert replayed_bytes == uninterrupted_bytes


@given(order_flows())
@settings(max_examples=100, deadline=None)
def test_generated_order_flows_match_python_reference(cpp_worker, records):
    expected, _ = _python_outputs(records)
    actual, _ = run_cpp_worker(cpp_worker, records)
    assert actual == expected


@given(order_flows())
@settings(max_examples=100, deadline=None)
def test_generated_order_flows_are_deterministic(cpp_worker, records):
    _, first_bytes = run_cpp_worker(cpp_worker, records)
    _, second_bytes = run_cpp_worker(cpp_worker, records)
    assert second_bytes == first_bytes
