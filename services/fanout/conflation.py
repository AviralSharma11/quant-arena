"""The 20 Hz tick — the one place market data leaves this process.

Conflation is the whole of Open Issue 006's answer to the arithmetic. 20,000 events/sec x 500
clients is ten million messages a second, which no machine delivers; a fixed 20 Hz tick that
sends *current state* rather than *every change* makes it 20 x 10 symbols x 500, and the
saving is roughly 85x. That is why the design problem was never "broadcast quickly".

## What the tick does, in order

    drain the symbols that changed  ─┐
    encode L1 and L2 once each      ─┼→ hand the SAME string to every subscriber
    drain and encode each print     ─┤
    drain and encode each closed bar─┘
    flush every subscriber

Encoding sits behind `Hub.encode`, which counts. Success Criterion 5 — "serialisation happens
once per symbol per tick, verified by instrumentation" — is that counter, and the test asserts
it is identical with one subscriber and with two hundred.

## Three rates that never have to agree

| Layer | Rate |
|---|---|
| the outbound stream | whatever the matcher produces |
| this tick | `market_data.conflation_hz`, 20 |
| the browser's paint | `requestAnimationFrame`, up to 60 |

Each is decoupled from the next by something that overwrites rather than queues — the book here,
`MarketBuffer` there. A queue anywhere in that chain would turn a slow consumer into unbounded
memory, which is precisely what the slow-client policy in `subscribers.py` exists to prevent.

## What is not conflated

The tape and closed bars are **drained**, not sampled: every print accumulated since the last
tick is sent, because a dropped trade is a wrong tape rather than a stale one (§3.3). Batching
them onto the same tick is not conflation — nothing is superseded, it is only carried together.

A slow client can still miss prints, through the skip in `subscribers.py`. That is the
contract's own answer: §3.5 says a gap on `tape:*` leaves a hole that is not backfilled, because
the tape is a display and the archive is the record of truth.
"""

from __future__ import annotations

import asyncio
import logging
import time

from config.settings import Settings, Symbol
from services.fanout import messages
from services.fanout.halt import HaltView
from services.fanout.state import MarketState
from services.fanout.subscribers import Hub

LOGGER_NAME = "quant_arena.fanout"


class Conflator:
    """One tick's worth of work, and the loop that runs it."""

    def __init__(
        self,
        *,
        hub: Hub,
        state: MarketState,
        settings: Settings,
        halt: HaltView | None = None,
        clock=time.monotonic,
    ) -> None:
        self.hub = hub
        self.state = state
        self.settings = settings
        self.halt = halt
        self.clock = clock
        self.interval = 1.0 / max(1, settings.conflation_hz)
        self.depth = settings.book_depth
        self._symbols: dict[int, Symbol] = {s.symbol_id: s for s in settings.symbols}
        #: One real second to one simulated minute (Open Issue 005 §5g). Bar channels are named
        #: in simulated time, so this is what turns a bucket width into a channel suffix.
        self._replay_ratio = settings.replay_real_seconds_per_simulated_minute
        self._halt_transitions = 0

        self.ticks = 0
        self.frames_offered = 0
        #: Longest single tick, in seconds. The one number that says whether fan-out is keeping
        #: up: a tick that takes longer than `interval` means the feed is late for everybody.
        self.max_tick_seconds = 0.0
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    # -- one tick ------------------------------------------------------------------------------

    def tick(self, *, ts_ns: int | None = None) -> int:
        """Encode and offer everything owed since the last tick. Returns frames offered."""
        started = self.clock()
        ts_ns = time.time_ns() if ts_ns is None else ts_ns
        offered = 0

        offered += self._halt_frame()
        offered += self._books(ts_ns)
        offered += self._tape()
        offered += self._bars()

        # Every subscriber's `fresh` set is cleared whether or not it got a frame: a channel
        # for a symbol that has never traded has nothing to send, and leaving it fresh would
        # re-check it on every tick for the life of the connection.
        for subscriber in self.hub.subscribers:
            subscriber.take_fresh()

        self.hub.flush()
        self.ticks += 1
        self.frames_offered += offered
        self.max_tick_seconds = max(self.max_tick_seconds, self.clock() - started)
        return offered

    def _halt_frame(self) -> int:
        """One frame per transition, not one per tick.

        Twenty identical `halted` frames a second would be an outage of its own, and the state
        does not change between them.
        """
        if self.halt is None or self.halt.transitions == self._halt_transitions:
            return 0
        self._halt_transitions = self.halt.transitions
        payload = (
            messages.halted(self.halt.reason, self.halt.detail)
            if self.halt.halted
            else messages.resumed()
        )
        return self.hub.broadcast_all(self.hub.encode(payload))

    def _books(self, ts_ns: int) -> int:
        changed = self.state.drain_dirty()
        offered = 0
        for symbol_id, symbol in self._symbols.items():
            book = self.state.books.get(symbol_id)
            if book is None:
                continue
            moved = symbol_id in changed
            for suffix, build in (
                ("l1", lambda: messages.book_l1(
                    symbol, book, seq=self.state.last_seq, ts_ns=ts_ns
                )),
                ("l2", lambda: messages.book_l2(
                    symbol, book, depth=self.depth, seq=self.state.last_seq, ts_ns=ts_ns
                )),
            ):
                channel = f"book:{symbol.name}:{suffix}"
                listeners = self.hub.listeners(channel)
                if not listeners:
                    continue
                if moved:
                    # Encoded once, handed to everybody. This is the criterion.
                    offered += self.hub.broadcast(channel, self.hub.encode(build()))
                    continue
                # The symbol has not moved, so nobody needs a repeat — except a client that
                # has only just subscribed, which would otherwise stare at an empty panel
                # until somebody traded. Still exactly one encode.
                joining = [s for s in listeners if channel in s.fresh]
                if joining:
                    frame = self.hub.encode(build())
                    offered += sum(1 for s in joining if s.offer_market(frame))
        return offered

    def _tape(self) -> int:
        offered = 0
        for symbol_id, symbol in self._symbols.items():
            # Drained whether or not anyone is listening: an unsubscribed symbol must not
            # accumulate prints for the life of the process.
            trades = self.state.tape.drain(symbol_id)
            channel = f"tape:{symbol.name}"
            if not trades or not self.hub.listeners(channel):
                continue
            for trade in trades:
                offered += self.hub.broadcast(
                    channel, self.hub.encode(messages.tape_print(symbol, trade))
                )
        return offered

    def _bars(self) -> int:
        offered = 0
        for bar in self.state.drain_closed_bars():
            symbol = self._symbols.get(bar.symbol_id)
            if symbol is None:
                continue
            # One label, used for both the channel and the message's own `ch`. Bar channels are
            # named in *simulated* time — at the configured ratio the one-second bucket is the
            # market's one-minute candle — so the ratio travels with the width. See
            # `messages.width_label`.
            channel = (
                f"bars:{symbol.name}:"
                f"{messages.width_label(bar.bucket_seconds, real_seconds_per_simulated_minute=self._replay_ratio)}"
            )
            if not self.hub.listeners(channel):
                continue
            offered += self.hub.broadcast(
                channel,
                self.hub.encode(
                    messages.bar_close(
                        symbol,
                        bar,
                        seq=self.state.last_seq,
                        real_seconds_per_simulated_minute=self._replay_ratio,
                    )
                ),
            )
        return offered

    # -- the loop ------------------------------------------------------------------------------

    async def run(self) -> None:
        """Tick on a fixed period, measured from the deadline rather than from the finish.

        `sleep(interval)` after the work drifts: a tick that takes 5 ms makes the period 55 ms,
        and at 20 Hz that is a feed running visibly slow. Sleeping until the next absolute
        deadline keeps the rate honest, and a tick that overran simply fires the next one
        immediately rather than trying to catch up by sending twice.
        """
        deadline = self.clock()
        while not self._stop.is_set():
            try:
                self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — one bad frame must not end the feed
                logging.getLogger(LOGGER_NAME).exception("conflation tick failed")
            deadline += self.interval
            delay = deadline - self.clock()
            if delay < 0:
                deadline = self.clock()
                delay = 0
            await asyncio.sleep(delay)

    def start(self) -> None:
        if self._task is None:
            self._stop.clear()
            self._task = asyncio.create_task(self.run(), name="fanout-conflation")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
