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

#: Simulated minutes to the suffix the contract uses in a channel name.
#:
#: **A bar channel is named in SIMULATED time, never in real seconds.** Buckets are cut on
#: `Fill.timestamp_ns`, which is the gateway's real wall clock, and the replay clock maps one
#: real second to one simulated minute (Open Issue 005 §5g, `[replay]` in the configuration). So
#: the one-second bucket *is* the market's one-minute candle and goes out as `1m`, and the
#: sixty-second bucket is one simulated hour and goes out as `1h`.
#:
#: This is what `bars:*:1m` means on the browser wire, and it is the question the configuration
#: parked for Task 5.1. Labelling by literal seconds instead put the chart on one candle per
#: *real* minute — sixty simulated minutes of price action compressed into a single candle,
#: which is precisely the failure the decision was written to prevent.
#:
#: An unmapped width falls back to `<n>m` rather than failing: a new width should appear on the
#: wire under an obvious name, not stop the process.
_SIMULATED_MINUTES_SUFFIX = {1: "1m", 5: "5m", 15: "15m", 60: "1h", 240: "4h", 1_440: "1d"}

#: The configured ratio, as a default so that every caller without a `Settings` in hand still
#: agrees with the ones that have one. It is the value in `[replay]` and has never been anything
#: else; a deployment that changed it must pass it, and the two call sites that matter do.
DEFAULT_REAL_SECONDS_PER_SIMULATED_MINUTE = 1


def width_label(
    bucket_seconds: int, *, real_seconds_per_simulated_minute: int = DEFAULT_REAL_SECONDS_PER_SIMULATED_MINUTE
) -> str:
    """The channel suffix for a bucket width, in simulated time.

    `bucket_seconds` is real seconds of stream time — that is what `BarBuilder` cuts on — and
    the label is what the market experiences. At the configured 1:1 ratio a 1-second bucket is
    one simulated minute (`1m`) and a 60-second bucket is one simulated hour (`1h`).
    """
    ratio = max(1, real_seconds_per_simulated_minute)
    minutes = bucket_seconds // ratio
    if minutes < 1:
        # Finer than one simulated minute. Named in real seconds because there is no smaller
        # simulated unit to name it in, and a `0m` channel would collide for every such width.
        return f"{bucket_seconds}s"
    return _SIMULATED_MINUTES_SUFFIX.get(minutes, f"{minutes}m")


def known_channels(
    symbols,
    bar_widths,
    real_seconds_per_simulated_minute: int = DEFAULT_REAL_SECONDS_PER_SIMULATED_MINUTE,
) -> set[str]:
    """Every channel name a client may subscribe to.

    Built from the configuration rather than pattern-matched, so `unknown_channel` (§3.6) is
    answered by comparing against a set instead of by parsing — which means a typo in a symbol
    name is refused rather than silently subscribing a client to a feed that never sends.

    `private` is not in here. §3 is explicit that the private stream is whatever the session
    owns and never a channel the client asks for by name.
    """
    channels: set[str] = set()
    for symbol in symbols:
        channels.add(f"book:{symbol.name}:l1")
        channels.add(f"book:{symbol.name}:l2")
        channels.add(f"tape:{symbol.name}")
        for width in bar_widths:
            channels.add(
                f"bars:{symbol.name}:"
                f"{width_label(width, real_seconds_per_simulated_minute=real_seconds_per_simulated_minute)}"
            )
    return channels


def halted(reason: str | None, detail: str | None) -> dict:
    """§3.6's `halted`. The exchange cannot durably record orders; the socket is fine.

    Distinct from every other error code here in that it says nothing about this connection —
    which is why `web/src/stream/client.ts` renders it as a connection *state* rather than
    logging it and carrying on showing a confident green indicator over an exchange that is
    refusing orders.
    """
    return {"ch": "error", "code": "halted", "detail": detail or reason or "exchange halted"}


def resumed() -> dict:
    """The halt lifting.

    **A deviation from the frozen contract, and it needs Dev A's sign-off.** §3.6 enumerates
    four codes — `unauthenticated`, `unknown_channel`, `slow_consumer`, `halted` — all of them
    failures, and provides no way at all to say that a halt has ended.

    Something has to. A halt clears on its own within one watchdog interval (Task 2.1 Success
    Criterion 5), so an indicator that could only be reset by reconnecting would show HALTED
    over a working exchange for as long as the tab stayed open. The two alternatives are worse:
    inferring resumption from the arrival of market data is simply wrong, because market data
    keeps flowing throughout a halt — fan-out is still reading a stream the matcher is still
    draining, and it is only the *appending* of new orders that stopped — and inferring it from
    the *absence* of repeated `halted` frames makes the indicator a timeout.

    Additive, so it is the smallest possible change: a client that does not know this code
    ignores an error it cannot classify, which is what §3.6's shape already asks of it.
    """
    return {"ch": "error", "code": "resumed", "detail": "exchange accepting orders"}


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


def bar_close(
    symbol: Symbol,
    bar: Bar,
    *,
    seq: str,
    real_seconds_per_simulated_minute: int = DEFAULT_REAL_SECONDS_PER_SIMULATED_MINUTE,
) -> dict:
    """A completed candle, published on close.

    The ratio is threaded in rather than read from a module-level constant so that this and the
    channel the conflation tick broadcasts on are computed the same way from the same value —
    two independent labellings of one bar is exactly how a message ends up on a channel whose
    name does not match its own `ch` field.
    """
    return {
        "ch": (
            f"bars:{symbol.name}:"
            f"{width_label(bar.bucket_seconds, real_seconds_per_simulated_minute=real_seconds_per_simulated_minute)}"
        ),
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
