"""Fan-out's state, derived from the outbound stream and checked against its source.

Task 5.2a's own definition of done, since none of Task 5.2's five success criteria can be met
without the WebSocket server that 5.2b adds:

1. A book rebuilt from the stream alone matches **the matcher's own book**, order for order.
2. Replay from `0-0` reproduces identical state.
3. Message shapes match the frozen contract, field by field.
4. Bars close on the right boundaries with correct OHLCV.

The first is the one that earns its place. Fan-out reconstructs from `OrderAccepted`, `Fill` and
`OrderCancelled` because nothing emits `BookChanged`, so the reconstruction is a second opinion
about the same events — and a second opinion is worth exactly as much as its agreement with the
first. `NaiveMatcher` already exposes `resting()` for the same reason.
"""

from __future__ import annotations

import random

import pytest

from config.settings import Settings, Symbol
from contracts.v1.generated.contracts import (
    AccountCreated,
    CancelOrder,
    OrderRejected,
    Side,
    SubmitOrder,
    Tif,
)
from services.fanout.bars import BarBuilder, NS_PER_SECOND
from services.fanout.book import Book
from services.fanout.messages import bar_close, book_l1, book_l2, tape_print, width_label
from services.fanout.state import MarketState
from services.matcher.adapter import NaiveMatcher

BUY, SELL = int(Side.BUY), int(Side.SELL)
SYMBOL = Symbol(symbol_id=1, name="QAA", tick_size_ticks=1, lot_size=1)


def _submit(coid, user, side, price, qty, *, symbol=1, tif=Tif.GTC, ts=0):
    return SubmitOrder.new(
        timestamp_ns=ts, client_order_id=coid, user_id=user, symbol_id=symbol,
        side=int(side), tif=int(tif), price_ticks=price, qty=qty,
    )


def _drive(inbound) -> tuple[NaiveMatcher, MarketState]:
    """Run records through the matcher and fold its output into fan-out, as the stream does."""
    matcher = NaiveMatcher(initial_cash_ticks=1_000_000)
    state = MarketState(bar_widths=(1, 60))
    ordinal = 0
    for record in inbound:
        for out in matcher.apply(record):
            ordinal += 1
            state.apply(out, stream_id=f"1700000000000-{ordinal}")
    return matcher, state


def _books_agree(matcher: NaiveMatcher, state: MarketState) -> None:
    """The matcher reports `(order_id, symbol_id, side, price, qty)`; fan-out holds one book per
    symbol, so its rows are compared symbol by symbol."""
    expected: dict[int, list[tuple[int, int, int, int]]] = {}
    for order_id, symbol_id, side, price, qty in matcher.resting():
        expected.setdefault(symbol_id, []).append((order_id, side, price, qty))

    for symbol_id, rows in expected.items():
        assert state.book(symbol_id).resting() == sorted(rows), symbol_id
    for symbol_id, book in state.books.items():
        if symbol_id not in expected:
            assert book.resting() == [], symbol_id


# --- criterion 1: the derived book agrees with the engine's -----------------------------------


def test_a_resting_order_appears_on_the_book():
    matcher, state = _drive([_submit(1, 10, BUY, 1_000, 5)])
    assert state.book(1).levels(BUY, 10) == [[1_000, 5]]
    _books_agree(matcher, state)


def test_a_full_fill_clears_both_sides():
    """The trap this test exists for: the taker is accepted onto the book *before* it matches,
    so a fill that only decremented the maker would leave every aggressing order resting
    forever and the quoted depth would grow without bound."""
    matcher, state = _drive([
        _submit(1, 10, SELL, 1_000, 5),
        _submit(1, 20, BUY, 1_000, 5),
    ])
    assert state.book(1).levels(SELL, 10) == []
    assert state.book(1).levels(BUY, 10) == []
    assert state.book(1).orders == {}
    _books_agree(matcher, state)


def test_a_partial_fill_leaves_the_remainder_resting():
    matcher, state = _drive([
        _submit(1, 10, SELL, 1_000, 10),
        _submit(1, 20, BUY, 1_000, 4),
    ])
    assert state.book(1).levels(SELL, 10) == [[1_000, 6]]
    _books_agree(matcher, state)


def test_a_cancel_removes_the_order():
    inbound = [_submit(1, 10, BUY, 990, 7)]
    matcher = NaiveMatcher(initial_cash_ticks=1_000_000)
    state = MarketState()
    ordinal = 0
    for record in inbound:
        for out in matcher.apply(record):
            ordinal += 1
            state.apply(out, stream_id=f"1700000000000-{ordinal}")
    for out in matcher.apply(CancelOrder.new(
        timestamp_ns=0, client_order_id=99, user_id=10, target_client_order_id=1
    )):
        ordinal += 1
        state.apply(out, stream_id=f"1700000000000-{ordinal}")

    assert state.book(1).levels(BUY, 10) == []
    _books_agree(matcher, state)


def test_an_expired_ioc_remainder_leaves_the_book():
    """An IOC that cannot fully trade on arrival never rests. To the book an order leaving is an
    order leaving; the reason matters to the ledger and the matcher's anchor count, not here."""
    matcher, state = _drive([
        _submit(1, 10, SELL, 1_000, 3),
        _submit(1, 20, BUY, 1_000, 10, tif=Tif.IOC),
    ])
    assert state.book(1).orders == {}
    _books_agree(matcher, state)


def test_levels_aggregate_orders_sharing_a_price():
    _, state = _drive([
        _submit(1, 10, BUY, 1_000, 5),
        _submit(2, 11, BUY, 1_000, 7),
        _submit(3, 12, BUY, 999, 2),
    ])
    assert state.book(1).levels(BUY, 10) == [[1_000, 12], [999, 2]]


def test_bids_descend_and_asks_ascend():
    """Index 0 is always the most aggressive price on that side, so a client reads the top of
    book without knowing which side it asked for."""
    _, state = _drive([
        _submit(1, 10, BUY, 998, 1), _submit(2, 10, BUY, 1_000, 1), _submit(3, 10, BUY, 999, 1),
        _submit(4, 11, SELL, 1_010, 1), _submit(5, 11, SELL, 1_005, 1),
    ])
    assert [lvl[0] for lvl in state.book(1).levels(BUY, 10)] == [1_000, 999, 998]
    assert [lvl[0] for lvl in state.book(1).levels(SELL, 10)] == [1_005, 1_010]


def test_depth_is_capped():
    _, state = _drive([_submit(i, 10, BUY, 1_000 - i, 1) for i in range(1, 26)])
    assert len(state.book(1).levels(BUY, 10)) == 10


def test_books_are_kept_per_symbol():
    """The matcher holds one book per symbol_id because crossing on price alone would trade
    symbol 3 against symbol 7. Fan-out has to agree, or the depth it publishes is a blend."""
    matcher, state = _drive([
        _submit(1, 10, BUY, 1_000, 5, symbol=1),
        _submit(2, 11, SELL, 1_000, 5, symbol=2),
    ])
    assert state.book(1).levels(BUY, 10) == [[1_000, 5]]
    assert state.book(1).levels(SELL, 10) == []
    assert state.book(2).levels(SELL, 10) == [[1_000, 5]]
    _books_agree(matcher, state)


def test_the_derived_book_matches_the_engine_across_a_generated_sequence():
    """The strongest form of criterion 1: hundreds of random orders and cancels, then compare.

    Seeded rather than property-based on purpose — this runs in the per-commit suite, and the
    root conftest routes Hypothesis tests to the nightly workflow.
    """
    rng = random.Random(20260904)
    inbound = []
    live: list[int] = []
    for coid in range(1, 400):
        if live and rng.random() < 0.25:
            target = rng.choice(live)
            live.remove(target)
            inbound.append(CancelOrder.new(
                timestamp_ns=coid, client_order_id=10_000 + coid,
                user_id=1, target_client_order_id=target,
            ))
            continue
        side = BUY if rng.random() < 0.5 else SELL
        inbound.append(_submit(
            coid, rng.randint(1, 6), side,
            rng.randint(980, 1_020), rng.randint(1, 20),
            symbol=rng.choice((1, 2)),
            tif=Tif.IOC if rng.random() < 0.1 else Tif.GTC,
            ts=coid,
        ))
        live.append(coid)

    matcher, state = _drive(inbound)
    assert state.tape.total > 0, "the sequence never traded; it proves nothing"
    _books_agree(matcher, state)


# --- criterion 2: replay reproduces the same state --------------------------------------------


def test_replaying_the_same_records_reproduces_identical_state():
    rng = random.Random(7)
    inbound = [
        _submit(i, rng.randint(1, 4), BUY if i % 2 else SELL,
                rng.randint(990, 1_010), rng.randint(1, 9), ts=i * NS_PER_SECOND)
        for i in range(1, 120)
    ]
    _, first = _drive(inbound)
    _, second = _drive(inbound)

    assert first.snapshot() == second.snapshot()
    for symbol_id in first.books:
        assert first.book(symbol_id).resting() == second.book(symbol_id).resting()


def test_records_fanout_has_no_opinion_about_are_ignored():
    """Money records and rejections never reached the book. Ignoring unknown types rather than
    raising also keeps this tolerant of a schema that grows records it does not act on."""
    state = MarketState()
    state.apply(AccountCreated.new(
        timestamp_ns=0, client_order_id=0, user_id=1, initial_cash_ticks=1_000_000))
    state.apply(OrderRejected.new(
        timestamp_ns=0, client_order_id=1, user_id=1, symbol_id=1, reason=6))

    assert state.books == {}
    assert state.tape.total == 0


def test_a_fill_naming_an_order_from_before_the_retention_window_is_survivable():
    """At two million entries and no snapshots, a replay from `0-0` legitimately begins
    mid-history. Raising on an unknown order id would make a trimmed stream unreadable."""
    book = Book(symbol_id=1)
    book.fill(order_id=999, qty=5)
    book.cancel(order_id=999)
    assert book.resting() == []


# --- criterion 4: bars ------------------------------------------------------------------------


def test_a_bar_holds_open_high_low_close_and_volume():
    builder = BarBuilder(symbol_id=1, bucket_seconds=1)
    for price, qty in ((1_000, 2), (1_010, 3), (990, 1), (1_005, 4)):
        builder.add(price_ticks=price, qty=qty, timestamp_ns=500)

    bar = builder.current
    assert (bar.open_ticks, bar.high_ticks, bar.low_ticks, bar.close_ticks) == (
        1_000, 1_010, 990, 1_005
    )
    assert bar.volume == 10


def test_a_bar_closes_when_a_trade_arrives_in_a_later_bucket():
    """Data-driven, not timer-driven: a replay produces identical bars, which a wall clock
    could never guarantee."""
    builder = BarBuilder(symbol_id=1, bucket_seconds=1)
    builder.add(price_ticks=1_000, qty=1, timestamp_ns=0)
    closed = builder.add(price_ticks=1_100, qty=1, timestamp_ns=NS_PER_SECOND)

    assert closed is not None and closed.bar_open_ns == 0 and closed.close_ticks == 1_000
    assert builder.current.bar_open_ns == NS_PER_SECOND


def test_bucket_boundaries_are_exact():
    builder = BarBuilder(symbol_id=1, bucket_seconds=60)
    minute = 60 * NS_PER_SECOND
    assert builder.bucket_start(0) == 0
    assert builder.bucket_start(minute - 1) == 0
    assert builder.bucket_start(minute) == minute


def test_a_quiet_bucket_produces_no_empty_bar():
    """A bar with no trade in it is not published. There was no candle, so there is nothing to
    draw — and inventing a flat one would put a body on a period that never traded."""
    builder = BarBuilder(symbol_id=1, bucket_seconds=1)
    builder.add(price_ticks=1_000, qty=1, timestamp_ns=0)
    builder.add(price_ticks=1_100, qty=1, timestamp_ns=10 * NS_PER_SECOND)

    assert [b.bar_open_ns for b in builder.closed] == [0]


def test_flush_closes_the_open_bar_at_the_end_of_a_replay():
    builder = BarBuilder(symbol_id=1, bucket_seconds=1)
    builder.add(price_ticks=1_000, qty=3, timestamp_ns=0)
    assert builder.flush().volume == 3
    assert builder.flush() is None


def test_every_configured_width_gets_its_own_bar():
    state = MarketState(bar_widths=(1, 60))
    for i in range(5):
        state.apply(_fill(price=1_000 + i, qty=1, ts=i * NS_PER_SECOND, ordinal=i))

    widths = state.bar_set(1).builders
    assert sorted(widths) == [1, 60]
    assert len(widths[1].closed) == 4, "one-second bars close every second"
    assert len(widths[60].closed) == 0, "the minute has not elapsed"


def _fill(*, price: int, qty: int, ts: int, ordinal: int):
    from contracts.v1.generated.contracts import Fill
    return Fill.new(
        timestamp_ns=ts, maker_order_id=1, taker_order_id=2, maker_user_id=1,
        taker_user_id=2, price_ticks=price, qty=qty, symbol_id=1, aggressor_side=BUY,
    )


def test_a_bar_width_below_one_second_is_refused():
    with pytest.raises(ValueError, match="at least one second"):
        BarBuilder(symbol_id=1, bucket_seconds=0)


# --- the tape ---------------------------------------------------------------------------------


def test_every_trade_reaches_the_tape():
    """Never conflated. A dropped print is a wrong tape, not a stale one — it leaves a hole in
    history that nothing backfills."""
    _, state = _drive([
        _submit(1, 10, SELL, 1_000, 10),
        _submit(2, 20, BUY, 1_000, 3),
        _submit(3, 21, BUY, 1_000, 4),
    ])
    prints = state.tape.pending(1)
    assert [t.qty for t in prints] == [3, 4]
    assert all(t.aggressor_side == BUY for t in prints)


def test_draining_the_tape_empties_it():
    _, state = _drive([_submit(1, 10, SELL, 1_000, 5), _submit(2, 20, BUY, 1_000, 5)])
    assert len(state.tape.drain(1)) == 1
    assert state.tape.drain(1) == []


# --- criterion 3: the message shapes are the frozen contract's --------------------------------


def test_the_l2_snapshot_matches_the_contract():
    _, state = _drive([
        _submit(1, 10, BUY, 1_000, 3), _submit(2, 10, BUY, 999, 11),
        _submit(3, 11, SELL, 1_002, 5),
    ])
    msg = book_l2(SYMBOL, state.book(1), depth=10, seq="1693526400000-4", ts_ns=17)

    assert list(msg) == ["ch", "seq", "ts_ns", "bids", "asks"], "fixed field order"
    assert msg["ch"] == "book:QAA:l2"
    assert msg["bids"] == [[1_000, 3], [999, 11]]
    assert msg["asks"] == [[1_002, 5]]


def test_l1_carries_the_same_shape_as_l2():
    """One layout for both tiers, so a client renders either from the same code and the binary
    encoder in Phase 2 has one layout to learn rather than two."""
    _, state = _drive([_submit(1, 10, BUY, 1_000, 3), _submit(2, 11, SELL, 1_002, 5)])
    l1 = book_l1(SYMBOL, state.book(1), seq="1-0", ts_ns=17)

    assert list(l1) == ["ch", "seq", "ts_ns", "bids", "asks"]
    assert l1["ch"] == "book:QAA:l1"
    assert l1["bids"] == [[1_000, 3]] and l1["asks"] == [[1_002, 5]]


def test_an_empty_side_is_an_empty_list_not_a_zero_quote():
    """A side offering nothing and a side offering nothing *at a price of zero* are different
    facts, and rendering the second is showing a market nobody is making."""
    _, state = _drive([_submit(1, 10, BUY, 1_000, 3)])
    assert book_l1(SYMBOL, state.book(1), seq="1-0", ts_ns=0)["asks"] == []


def test_every_message_carries_a_sequence_number():
    """§3.5: the client tracks the last `seq` per channel to detect a gap. A channel without
    one is the single channel it could not gap-check."""
    _, state = _drive([_submit(1, 10, SELL, 1_000, 5), _submit(2, 20, BUY, 1_000, 5)])
    trade = state.tape.pending(1)[0]
    builder = state.bar_set(1).builders[1]
    builder.flush()

    for msg in (
        book_l2(SYMBOL, state.book(1), depth=10, seq="9-0", ts_ns=0),
        book_l1(SYMBOL, state.book(1), seq="9-0", ts_ns=0),
        tape_print(SYMBOL, trade),
        bar_close(SYMBOL, builder.closed[0], seq="9-0"),
    ):
        assert msg["seq"], msg["ch"]


def test_the_tape_message_matches_the_contract():
    _, state = _drive([_submit(1, 10, SELL, 1_000, 5), _submit(2, 20, BUY, 1_000, 2)])
    msg = tape_print(SYMBOL, state.tape.pending(1)[0])

    assert list(msg) == ["ch", "seq", "ts_ns", "price_ticks", "qty", "aggressor_side"]
    assert msg["ch"] == "tape:QAA"
    assert msg["price_ticks"] == 1_000 and msg["qty"] == 2


def test_the_bar_message_matches_the_contract():
    builder = BarBuilder(symbol_id=1, bucket_seconds=60)
    builder.add(price_ticks=1_000, qty=4, timestamp_ns=0)
    bar = builder.flush()
    msg = bar_close(SYMBOL, bar, seq="9-0")

    assert list(msg) == [
        "ch", "seq", "open_ticks", "high_ticks", "low_ticks", "close_ticks",
        "volume", "bar_open_ns",
    ]
    assert msg["ch"] == "bars:QAA:1m"


@pytest.mark.parametrize(("seconds", "label"), [(1, "1s"), (60, "1m"), (3_600, "1h"), (7, "7s")])
def test_bar_channel_suffixes(seconds: int, label: str):
    assert width_label(seconds) == label


def test_no_message_carries_a_float():
    """Money and quantities stay integer ticks even in JSON. The frontend divides by the
    symbol's tick size for display and never sends a divided value back (§1)."""
    _, state = _drive([_submit(1, 10, SELL, 1_000, 5), _submit(2, 20, BUY, 1_000, 5)])
    builder = state.bar_set(1).builders[1]
    builder.flush()

    messages = [
        book_l2(SYMBOL, state.book(1), depth=10, seq="9-0", ts_ns=0),
        tape_print(SYMBOL, state.tape.pending(1)[0]),
        bar_close(SYMBOL, builder.closed[0], seq="9-0"),
    ]

    def numbers(value):
        if isinstance(value, dict):
            for v in value.values():
                yield from numbers(v)
        elif isinstance(value, list):
            for v in value:
                yield from numbers(v)
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            yield value

    for msg in messages:
        assert all(isinstance(n, int) for n in numbers(msg)), msg


def test_the_shipped_configuration_supplies_the_bar_widths(settings: Settings):
    assert settings.bar_bucket_seconds == (1, 60)
    assert settings.book_depth == 10
    assert settings.conflation_hz == 20
