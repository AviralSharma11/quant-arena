"""The browser wire, built to the frozen contract.

`contracts/v1/rest_and_ws.md` §3.2–3.3 fixes these shapes, and Open Issue 006 §7e fixes the
encoding: **JSON for Phase 1, measured, then a binary encoder as a documented optimisation with
before/after numbers.** Conflation is worth about 85×; encoding about 5×. Adopting a binary
protocol first would be optimising the wrong layer.

What makes that safe is a constraint on these builders rather than a promise about later:

> Design the message schema so a binary encoder can replace the JSON encoder **without changing
> the schema**. Fixed field order, integer types, no dynamic keys.

So every function here returns a dict with the same keys in the same order every time — no field
that appears only when it has a value, no map keyed by something that varies. A `None` is
emitted as `null` rather than omitted, because a field that sometimes vanishes has no fixed
offset and is exactly what a binary encoder cannot follow.

Money stays in integer ticks even here. The browser divides by the symbol's `tick_size_ticks`
for display and never sends a divided value back; a float that reaches the gateway is a bug, not
a rounding concern (§1).

## `seq` on every message

§3.5 states the rule plainly — every message carries `seq`, and a client tracks the last one per
channel to detect a gap. The bar example in §3.3 happens to show no `seq`; read as an abbreviated
example rather than a contradiction, since a channel without one is the single channel a client
could not gap-check. Worth confirming with Dev A before 5.2b codes against it — see this
package's README.
"""

from __future__ import annotations

from config.settings import Symbol
from contracts.v1.generated.contracts import Side
from services.fanout.bars import Bar, Trade
from services.fanout.book import Book

BUY, SELL = int(Side.BUY), int(Side.SELL)

#: Seconds to the suffix the contract uses in a channel name. Bar widths are configuration, so
#: an unmapped width falls back to `<n>s` rather than failing — a new width should appear on the
#: wire under an obvious name, not stop the process.
_WIDTH_SUFFIX = {1: "1s", 60: "1m", 300: "5m", 3600: "1h"}


def width_label(bucket_seconds: int) -> str:
    return _WIDTH_SUFFIX.get(bucket_seconds, f"{bucket_seconds}s")


def book_l2(symbol: Symbol, book: Book, *, depth: int, seq: str, ts_ns: int) -> dict:
    """A **complete** top-`depth` snapshot of both sides.

    Not a delta. Complete snapshots are what make 20 Hz conflation safe to do at all: a client
    that misses a message is fully correct again on the next one, so dropping a frame for a slow
    consumer is free rather than corrupting. Delta encoding is Phase 2, with a before/after
    measurement attached (Open Issue 006 §14.1).
    """
    return {
        "ch": f"book:{symbol.name}:l2",
        "seq": seq,
        "ts_ns": ts_ns,
        "bids": book.levels(BUY, depth),
        "asks": book.levels(SELL, depth),
    }


def book_l1(symbol: Symbol, book: Book, *, seq: str, ts_ns: int) -> dict:
    """Best bid and offer only — the same shape as L2, one level deep.

    Tiering is deliberate (Open Issue 006 §7h): most clients want L1, which is a fraction of the
    bytes, and only a client with the depth panel open subscribes to L2. Keeping one shape for
    both means a client can render either from the same code, and the binary encoder has one
    layout to learn instead of two.
    """
    bid, ask = book.best(BUY), book.best(SELL)
    return {
        "ch": f"book:{symbol.name}:l1",
        "seq": seq,
        "ts_ns": ts_ns,
        "bids": [bid] if bid else [],
        "asks": [ask] if ask else [],
    }


def tape_print(symbol: Symbol, trade: Trade) -> dict:
    """One trade. Never conflated — a dropped print is a wrong tape, not a stale one."""
    return {
        "ch": f"tape:{symbol.name}",
        "seq": trade.seq,
        "ts_ns": trade.timestamp_ns,
        "price_ticks": trade.price_ticks,
        "qty": trade.qty,
        "aggressor_side": trade.aggressor_side,
    }


def bar_close(symbol: Symbol, bar: Bar, *, seq: str) -> dict:
    """A completed candle, published on close."""
    return {
        "ch": f"bars:{symbol.name}:{width_label(bar.bucket_seconds)}",
        "seq": seq,
        "open_ticks": bar.open_ticks,
        "high_ticks": bar.high_ticks,
        "low_ticks": bar.low_ticks,
        "close_ticks": bar.close_ticks,
        "volume": bar.volume,
        "bar_open_ns": bar.bar_open_ns,
    }


def error(code: str, detail: str) -> dict:
    """§3.6. `slow_consumer` precedes a server-initiated close, so a stalled browser tab
    cannot apply back-pressure to the fan-out process."""
    return {"ch": "error", "code": code, "detail": detail}
