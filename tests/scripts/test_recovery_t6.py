from __future__ import annotations

import pytest
from PIL import Image

from contracts.v1.generated.contracts import Fill, OrderAccepted, Side, Tif
from scripts.recovery_t6 import (
    validate_cash_conservation,
    validate_order_fills,
    write_recovery_gif,
)


def accepted(*, order_id: int, client_order_id: int, user_id: int, side: Side):
    return OrderAccepted.new(
        timestamp_ns=order_id,
        order_id=order_id,
        client_order_id=client_order_id,
        user_id=user_id,
        price_ticks=10_000,
        qty=1,
        symbol_id=1,
        side=int(side),
        tif=int(Tif.GTC),
    )


def fill(*, maker_order_id: int, taker_order_id: int):
    return Fill.new(
        timestamp_ns=taker_order_id,
        maker_order_id=maker_order_id,
        taker_order_id=taker_order_id,
        maker_user_id=10,
        taker_user_id=20,
        price_ticks=10_000,
        qty=1,
        symbol_id=1,
        aggressor_side=int(Side.BUY),
    )


def test_recovery_assertions_match_every_buyer_order_to_one_fill():
    records = [
        accepted(order_id=1, client_order_id=100, user_id=10, side=Side.SELL),
        accepted(order_id=2, client_order_id=201, user_id=20, side=Side.BUY),
        accepted(order_id=3, client_order_id=202, user_id=20, side=Side.BUY),
        fill(maker_order_id=1, taker_order_id=2),
        fill(maker_order_id=1, taker_order_id=3),
    ]

    _, fills, maker_order_id = validate_order_fills(
        records,
        maker_client_order_id=100,
        buyer_client_order_ids=[201, 202],
        expected_qty=1,
    )

    assert maker_order_id == 1
    assert len(fills) == 2


def test_recovery_assertions_reject_a_lost_fill():
    records = [
        accepted(order_id=1, client_order_id=100, user_id=10, side=Side.SELL),
        accepted(order_id=2, client_order_id=201, user_id=20, side=Side.BUY),
        accepted(order_id=3, client_order_id=202, user_id=20, side=Side.BUY),
        fill(maker_order_id=1, taker_order_id=2),
    ]

    with pytest.raises(AssertionError, match="fill count"):
        validate_order_fills(
            records,
            maker_client_order_id=100,
            buyer_client_order_ids=[201, 202],
            expected_qty=1,
        )


def test_recovery_assertions_reject_a_duplicate_fill():
    records = [
        accepted(order_id=1, client_order_id=100, user_id=10, side=Side.SELL),
        accepted(order_id=2, client_order_id=201, user_id=20, side=Side.BUY),
        accepted(order_id=3, client_order_id=202, user_id=20, side=Side.BUY),
        fill(maker_order_id=1, taker_order_id=2),
        fill(maker_order_id=1, taker_order_id=2),
    ]

    with pytest.raises(AssertionError, match="duplicate fill"):
        validate_order_fills(
            records,
            maker_client_order_id=100,
            buyer_client_order_ids=[201, 202],
            expected_qty=1,
        )


def test_recovery_assertions_reject_broken_cash_conservation():
    with pytest.raises(AssertionError, match="cash plus fees"):
        validate_cash_conservation(999, 0, expected=1_000)


def test_recovery_gif_is_animated_and_uses_valid_lzw_codes(tmp_path):
    path = tmp_path / "recovery.gif"
    write_recovery_gif(path, orders=100, replayed=104, recovery_seconds=1.25)
    image = path.read_bytes()

    assert image[:6] == b"GIF89a"
    assert image[-1:] == b";"
    with Image.open(path) as animation:
        assert animation.n_frames == 3
        for index in range(animation.n_frames):
            animation.seek(index)
            animation.load()
