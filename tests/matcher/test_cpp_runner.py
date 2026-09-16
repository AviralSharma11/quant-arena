from __future__ import annotations

import os
from pathlib import Path

import pytest
from redis.asyncio import Redis

from contracts.v1.generated.contracts import (
    AccountCreated,
    CancelOrder,
    CashCredited,
    ConfigureReplay,
    CreateAccount,
    CreditCash,
    Fill,
    OrderCancelled,
    OrderRejected,
    RejectReason,
    ReplayConfigured,
    Side,
    SubmitOrder,
    Tif,
)
from services.gateway.streams import HaltState, StreamProducer, read_records
import dataclasses

from services.checkpoint import CheckpointRefused
from services.matcher.adapter import NaiveMatcher
from services.matcher.checkpointing import trim_streams
from services.matcher.cpp_runner import CppEngineProcess, CppMatcher

pytestmark = pytest.mark.anyio

ROOT = Path(__file__).resolve().parents[2]


def _engine_path() -> Path | None:
    configured = os.environ.get("QA_CPP_ENGINE_PATH")
    if configured:
        path = Path(configured)
        return path if path.exists() else None
    for candidate in (
        ROOT / "engine" / "cpp" / "quant_arena_engine.exe",
        ROOT / "engine" / "cpp" / "quant_arena_engine",
    ):
        if candidate.exists():
            return candidate
    return None


async def test_cpp_worker_matches_at_maker_price_and_cancels_remainder(monkeypatch):
    executable = _engine_path()
    if executable is None:
        pytest.skip("build engine/cpp/quant_arena_engine before running C++ worker tests")
    monkeypatch.setenv("QA_CPP_ENGINE_PATH", str(executable))

    engine = CppEngineProcess(initial_cash_ticks=1_000_000)
    await engine.start()
    try:
        buy = SubmitOrder.new(
            timestamp_ns=1,
            client_order_id=1,
            user_id=10,
            symbol_id=1,
            side=Side.BUY,
            tif=Tif.GTC,
            price_ticks=100,
            qty=10,
        )
        sell = SubmitOrder.new(
            timestamp_ns=2,
            client_order_id=2,
            user_id=20,
            symbol_id=1,
            side=Side.SELL,
            tif=Tif.GTC,
            price_ticks=90,
            qty=5,
        )
        cancel = CancelOrder.new(
            timestamp_ns=3,
            client_order_id=3,
            user_id=10,
            target_client_order_id=1,
        )

        accepted_buy = await engine.apply(buy)
        accepted_sell = await engine.apply(sell)
        cancelled = await engine.apply(cancel)
    finally:
        await engine.stop()

    assert [type(record).__name__ for record in accepted_buy] == ["OrderAccepted"]
    assert [type(record).__name__ for record in accepted_sell] == ["OrderAccepted", "Fill"]
    assert accepted_sell[1].price_ticks == 100
    assert [type(record).__name__ for record in cancelled] == ["OrderCancelled"]
    assert cancelled[0].remaining_qty == 5


async def test_cpp_worker_rejects_invalid_orders_and_unknown_cancels(monkeypatch):
    executable = _engine_path()
    if executable is None:
        pytest.skip("build engine/cpp/quant_arena_engine before running C++ worker tests")
    monkeypatch.setenv("QA_CPP_ENGINE_PATH", str(executable))

    engine = CppEngineProcess(initial_cash_ticks=1_000_000)
    await engine.start()
    try:
        invalid = SubmitOrder.new(
            timestamp_ns=1,
            client_order_id=1,
            user_id=10,
            symbol_id=1,
            side=Side.BUY,
            tif=Tif.GTC,
            price_ticks=0,
            qty=1,
        )
        unknown_cancel = CancelOrder.new(
            timestamp_ns=2,
            client_order_id=2,
            user_id=10,
            target_client_order_id=999,
        )
        invalid_output = await engine.apply(invalid)
        cancel_output = await engine.apply(unknown_cancel)
    finally:
        await engine.stop()

    assert isinstance(invalid_output[0], OrderRejected)
    assert invalid_output[0].reason == int(RejectReason.INVALID_PRICE)
    assert isinstance(cancel_output[0], OrderRejected)
    assert cancel_output[0].reason == int(RejectReason.UNKNOWN_ORDER)
    assert cancel_output[0].symbol_id == 0


async def test_cpp_worker_preserves_fifo_ioc_and_symbol_isolation(monkeypatch):
    executable = _engine_path()
    if executable is None:
        pytest.skip("build engine/cpp/quant_arena_engine before running C++ worker tests")
    monkeypatch.setenv("QA_CPP_ENGINE_PATH", str(executable))

    engine = CppEngineProcess(initial_cash_ticks=1_000_000)
    await engine.start()
    try:
        buy_one = SubmitOrder.new(
            timestamp_ns=1, client_order_id=1, user_id=10, symbol_id=1,
            side=Side.BUY, tif=Tif.GTC, price_ticks=100, qty=2,
        )
        buy_two = SubmitOrder.new(
            timestamp_ns=2, client_order_id=2, user_id=11, symbol_id=1,
            side=Side.BUY, tif=Tif.GTC, price_ticks=100, qty=2,
        )
        sell = SubmitOrder.new(
            timestamp_ns=3, client_order_id=3, user_id=20, symbol_id=1,
            side=Side.SELL, tif=Tif.IOC, price_ticks=90, qty=5,
        )
        other_symbol_sell = SubmitOrder.new(
            timestamp_ns=4, client_order_id=4, user_id=21, symbol_id=2,
            side=Side.SELL, tif=Tif.GTC, price_ticks=90, qty=1,
        )
        outputs = [
            await engine.apply(buy_one),
            await engine.apply(buy_two),
            await engine.apply(sell),
            await engine.apply(other_symbol_sell),
        ]
    finally:
        await engine.stop()

    fills = [record for record in outputs[2] if isinstance(record, Fill)]
    assert [(fill.maker_order_id, fill.qty) for fill in fills] == [(1, 2), (2, 2)]
    assert isinstance(outputs[2][-1], OrderCancelled)
    assert outputs[2][-1].remaining_qty == 1
    assert [type(record).__name__ for record in outputs[3]] == ["OrderAccepted"]


async def test_cpp_worker_forwards_account_and_cash_records(monkeypatch):
    executable = _engine_path()
    if executable is None:
        pytest.skip("build engine/cpp/quant_arena_engine before running C++ worker tests")
    monkeypatch.setenv("QA_CPP_ENGINE_PATH", str(executable))

    engine = CppEngineProcess(initial_cash_ticks=1234)
    await engine.start()
    try:
        created = await engine.apply(CreateAccount.new(timestamp_ns=1, client_order_id=1, user_id=7))
        credited = await engine.apply(
            CreditCash.new(timestamp_ns=2, client_order_id=2, user_id=7, amount_ticks=55)
        )
    finally:
        await engine.stop()

    assert isinstance(created[0], AccountCreated)
    assert created[0].initial_cash_ticks == 1234
    assert isinstance(credited[0], CashCredited)
    assert credited[0].amount_ticks == 55


async def test_cpp_worker_forwards_replay_configuration(monkeypatch):
    executable = _engine_path()
    if executable is None:
        pytest.skip("build engine/cpp/quant_arena_engine before running C++ worker tests")
    monkeypatch.setenv("QA_CPP_ENGINE_PATH", str(executable))

    engine = CppEngineProcess(initial_cash_ticks=1_000_000)
    await engine.start()
    try:
        configured = await engine.apply(
            ConfigureReplay.new(
                timestamp_ns=4,
                client_order_id=0,
                real_seconds_per_simulated_minute=1,
                config_hash_hi=11,
                config_hash_lo=22,
            )
        )
    finally:
        await engine.stop()

    assert len(configured) == 1
    assert isinstance(configured[0], ReplayConfigured)
    assert configured[0].timestamp_ns == 4
    assert configured[0].real_seconds_per_simulated_minute == 1
    assert configured[0].config_hash_hi == 11
    assert configured[0].config_hash_lo == 22


async def test_cpp_matcher_recovers_without_duplicate_outbound_records(
    settings, clean_redis, monkeypatch
):
    executable = _engine_path()
    if executable is None:
        pytest.skip("build engine/cpp/quant_arena_engine before running C++ worker tests")
    monkeypatch.setenv("QA_CPP_ENGINE_PATH", str(executable))
    redis = Redis.from_url(settings.redis_url, decode_responses=False)
    inbound = [
        ConfigureReplay.new(
            timestamp_ns=0,
            client_order_id=0,
            real_seconds_per_simulated_minute=1,
            config_hash_hi=11,
            config_hash_lo=22,
        ),
        SubmitOrder.new(
            timestamp_ns=1, client_order_id=1, user_id=10, symbol_id=1,
            side=Side.BUY, tif=Tif.GTC, price_ticks=100, qty=5,
        ),
        SubmitOrder.new(
            timestamp_ns=2, client_order_id=2, user_id=20, symbol_id=1,
            side=Side.SELL, tif=Tif.GTC, price_ticks=100, qty=5,
        ),
    ]
    input_producer = StreamProducer(
        redis, HaltState(), batch_max=settings.stream_batch_max
    )
    input_producer.start()
    for record in inbound:
        await input_producer.append(settings.stream_inbound, record)
    await input_producer.stop()

    first = CppMatcher(redis, settings)
    await first.engine.start()
    first.producer.start()
    while await first.step():
        pass
    await first.stop()

    async def outbound() -> list:
        records, last_id = [], "0-0"
        while True:
            batch = await read_records(
                redis, settings.stream_outbound, last_id=last_id, count=100, block_ms=20
            )
            if not batch:
                return records
            records.extend(item.record for item in batch)
            last_id = batch[-1].stream_id

    before = await outbound()
    second = CppMatcher(redis, settings)
    assert await second.recover() == 3
    assert second.last_recovery_seconds is not None
    assert second.last_recovery_seconds >= 0
    assert await outbound() == before
    second.producer.start()
    assert await second.step() == 0
    await second.stop()
    await redis.aclose()

    assert [type(record).__name__ for record in before] == [
        "ReplayConfigured", "OrderAccepted", "OrderAccepted", "Fill"
    ]


# --- Open Issue 020: checkpoint, trim, restart --------------------------------------------------


def _flow(start: int, count: int) -> list:
    """Crossing limit orders on one symbol: fills, partial fills and a resting book."""
    return [
        SubmitOrder.new(
            timestamp_ns=i, client_order_id=i, user_id=10 + i % 3, symbol_id=1,
            side=Side.BUY if i % 2 else Side.SELL, tif=Tif.GTC,
            price_ticks=95 + (i * 7) % 11, qty=1 + i % 4,
        )
        for i in range(start, start + count)
    ]


async def _outbound(redis, settings, after: str = "0-0") -> list:
    records, last_id = [], after
    while True:
        batch = await read_records(redis, settings.stream_outbound, last_id=last_id, count=500, block_ms=20)
        if not batch:
            return records
        records.extend(item.record for item in batch)
        last_id = batch[-1].stream_id


async def _append(redis, settings, records) -> None:
    producer = StreamProducer(redis, HaltState(), batch_max=settings.stream_batch_max)
    producer.start()
    for record in records:
        await producer.append(settings.stream_inbound, record)
    await producer.stop()


def _only_matcher_reads(settings):
    return dataclasses.replace(
        settings, checkpoint_inbound_readers=("matcher",), checkpoint_outbound_readers=("matcher",)
    )


async def test_cpp_matcher_resumes_from_a_checkpoint_after_both_streams_are_trimmed(
    settings, clean_redis, monkeypatch
):
    """The 2026-09-16 failure, reversed: trim below the checkpoint, restart, and the engine
    continues exactly as an uninterrupted one would — ids, fills and resting book included."""
    executable = _engine_path()
    if executable is None:
        pytest.skip("build engine/cpp/quant_arena_engine before running C++ worker tests")
    monkeypatch.setenv("QA_CPP_ENGINE_PATH", str(executable))
    settings = _only_matcher_reads(settings)
    redis = Redis.from_url(settings.redis_url, decode_responses=False)
    head, answered_after, unanswered = _flow(1, 60), _flow(61, 20), _flow(81, 20)

    first = CppMatcher(redis, settings)
    await first.engine.start()
    first.producer.start()
    await _append(redis, settings, head)
    while await first.step():
        pass
    await first.write_checkpoint()
    await _append(redis, settings, answered_after)
    while await first.step():  # answered, but after the checkpoint
        pass
    await first.stop()

    await trim_streams(redis, settings, HaltState())
    for stream in (settings.stream_inbound, settings.stream_outbound):
        info = await redis.xinfo_stream(stream)
        assert info["entries-added"] > info["length"], f"{stream} was not trimmed"

    await _append(redis, settings, unanswered)
    second = CppMatcher(redis, settings)
    assert await second.recover() == len(answered_after)
    assert second.resumed_from_checkpoint
    second.producer.start()
    while await second.step():
        pass
    final_snapshot = await second.engine.snapshot()
    await second.stop()

    reference = NaiveMatcher(initial_cash_ticks=settings.initial_cash_ticks)
    expected = [out for record in head + answered_after + unanswered for out in reference.apply(record)]
    # Read-back records carry the stream id in seq_ms/seq_ord; the reference never went through
    # a stream. The ids are the stream's business, so they are cleared before comparing.
    everything = [
        record._replace(seq_ms=0, seq_ord=0) for record in await _outbound(redis, settings)
    ]
    # The retained outbound stream is exactly the uninterrupted run's tail: nothing lost, nothing
    # appended twice, and order ids carried on from the checkpoint rather than restarting.
    assert everything == expected[len(expected) - len(everything):]
    assert final_snapshot == reference.snapshot()
    await redis.aclose()


async def test_cpp_matcher_refuses_a_checkpoint_the_stream_was_trimmed_past(
    settings, clean_redis, monkeypatch
):
    executable = _engine_path()
    if executable is None:
        pytest.skip("build engine/cpp/quant_arena_engine before running C++ worker tests")
    monkeypatch.setenv("QA_CPP_ENGINE_PATH", str(executable))
    redis = Redis.from_url(settings.redis_url, decode_responses=False)

    first = CppMatcher(redis, settings)
    await first.engine.start()
    first.producer.start()
    await _append(redis, settings, _flow(1, 10))
    while await first.step():
        pass
    await first.write_checkpoint()
    await _append(redis, settings, _flow(11, 10))
    while await first.step():
        pass
    await first.stop()

    # A trim that ignores the checkpoint — the old MAXLEN behaviour.
    await redis.xtrim(settings.stream_inbound, maxlen=3, approximate=False)
    second = CppMatcher(redis, settings)
    with pytest.raises(CheckpointRefused):
        await second.recover()
    await second.stop()

    # And with no checkpoint at all, a trimmed stream is refused rather than partially replayed.
    await redis.delete("qa.checkpoint.matcher")
    third = CppMatcher(redis, settings)
    with pytest.raises(CheckpointRefused):
        await third.recover()
    await third.stop()
    await redis.aclose()
