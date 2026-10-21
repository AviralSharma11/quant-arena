"""Open Issue 020: fan-out market state survives a checkpoint."""

from __future__ import annotations

from services.fanout.state import MarketState


def test_checkpointed_state_continues_exactly_as_an_uninterrupted_one():
    """Open Issue 020: books and open bars survive dump/load at every cut."""
    from contracts.v1.generated.contracts import Fill, OrderAccepted, OrderCancelled, Side, Tif

    records = []
    for i in range(1, 40):
        side = int(Side.BUY) if i % 2 else int(Side.SELL)
        records.append(OrderAccepted.new(
            timestamp_ns=i * 400_000_000, order_id=i, client_order_id=i, user_id=i % 3,
            symbol_id=1 + i % 2, side=side, price_ticks=100 + i % 5, qty=5, tif=int(Tif.GTC),
        ))
        if i > 2 and i % 3 == 0:
            records.append(Fill.new(
                timestamp_ns=i * 400_000_000, maker_order_id=i - 1, taker_order_id=i,
                maker_user_id=0, taker_user_id=1, price_ticks=100 + i % 5, qty=2,
                symbol_id=1 + (i - 1) % 2, aggressor_side=side,
            ))
        if i % 7 == 0:
            records.append(OrderCancelled.new(
                timestamp_ns=i * 400_000_000, order_id=i - 3, client_order_id=i - 3, user_id=0,
                symbol_id=1 + (i - 3) % 2, remaining_qty=1, reason=1,
            ))

    def run(items, state=None):
        state = state or MarketState(bar_widths=(1, 60))
        for n, record in enumerate(items):
            state.apply(record, stream_id=f"{n + 1}-0")
        return state

    whole = run(records)
    for cut in range(0, len(records) + 1, 5):
        restored = MarketState(bar_widths=(1, 60))
        restored.load_state(run(records[:cut]).dump_state())
        for n, record in enumerate(records[cut:], start=cut):
            restored.apply(record, stream_id=f"{n + 1}-0")
        assert restored.dump_state() == whole.dump_state()
