"""Who is connected, what they asked for, and what happens when they cannot keep up.

Open Issue 006 §1 frames the whole of fan-out as arithmetic rather than engineering:

> 20,000 events/sec x 500 clients is 10 million messages/sec, which is not achievable in any
> language on one machine. So the design problem is not *how to broadcast quickly* — it is
> **what to decline to send.**

This file is where that declining happens, and it declines differently depending on the data,
because the contract requires it to:

| Data | If the client is behind | Why |
|---|---|---|
| market | **skip it for this tick** | the next snapshot is complete, so the client is fully correct one frame later |
| private | **buffer, then disconnect** | a dropped fill is a user seeing wrong state, which no later message repairs |

That is the droppable/non-droppable distinction the whole system is shaped around, arriving at
the last hop. It is also why "complete snapshots, no delta encoding" was settled the way it was:
under delta encoding a skipped frame would corrupt the client's book permanently, and there
would be no cheap way to decline anything.

## Serialisation happens here and nowhere else

`Hub.encode()` is the only place a message becomes a string, and it counts. Task 5.2's fifth
success criterion — "serialisation happens once per symbol per tick, verified by instrumentation"
— is that counter: the tick encodes each channel once and hands the *same string object* to
every subscriber, so `Hub.serialisations` is identical whether one client is attached or two
hundred. A test asserts exactly that.
"""

from __future__ import annotations

import asyncio
import json
from collections import deque
from collections.abc import Awaitable, Callable

#: Private messages held for a client that is not draining them. Private data is never dropped
#: (Open Issue 006), so the alternative to an unbounded queue is disconnection — the client
#: re-synchronises over REST on reconnect, which §3.5 already requires it to do after a gap.
#: A thousand is far more than a reconnect burst and far less than a memory problem.
PRIVATE_BUFFER_MAX = 1_000

SendCallable = Callable[[str], Awaitable[None]]


class Subscriber:
    """One WebSocket connection.

    The socket itself is behind `send`, so every test in this package drives a real subscriber
    with a list-appending coroutine and no network at all.
    """

    def __init__(
        self,
        *,
        user_id: int,
        send: SendCallable,
        private_max: int = PRIVATE_BUFFER_MAX,
    ) -> None:
        self.user_id = user_id
        self.channels: set[str] = set()
        #: Channels subscribed since the last tick. They get the current frame even if the
        #: symbol has not moved — otherwise a client joining a quiet market sees nothing at all
        #: until somebody trades, which looks exactly like a broken feed.
        self.fresh: set[str] = set()

        self._send = send
        self._private_max = private_max
        self._market: list[str] = []
        self._private: deque[str] = deque()
        self._wake = asyncio.Event()
        self._writer: asyncio.Task | None = None

        #: True from the moment a batch is handed over until it has finished being written.
        #: The slow-client test is exactly this flag: "if a client's send buffer is non-empty
        #: when the next tick fires, skip that client for that tick" (Task 5.2).
        self.busy = False
        self.closed = False
        #: Set when the private buffer overflows. An `Event` rather than a flag because the
        #: connection handler is blocked reading from a socket that a stalled client is not
        #: writing to — it has to be woken, not polled.
        self.overflowed = asyncio.Event()

        # Counters. Read by the connection indicator, by the benchmark and by the tests.
        self.sent = 0
        self.skipped = 0
        self.private_sent = 0

    # -- accepting work ----------------------------------------------------------------------

    def offer_market(self, frame: str) -> bool:
        """Queue one market-data frame for the next flush. False means it was skipped."""
        if self.closed:
            return False
        if self.busy:
            self.skipped += 1
            return False
        self._market.append(frame)
        return True

    def offer_private(self, frame: str) -> bool:
        """Queue one private message and hand it over at once.

        **Not held for the tick.** Open Issue 006 exempts private data from conflation, and
        until Task 7.4 measured it that exemption was implemented only as "never *dropped*" —
        the frame still sat in this buffer until the next 20 Hz flush. Measured cost: a
        uniform nought-to-fifty millisecond wait, so a median of twenty-five, on the one path
        the design had decided should not be waiting at all. It was the largest single
        contributor to a trader's acknowledgement latency, and the matching it was waiting on
        takes 166 nanoseconds (`benchmarks/results/7.4-benchmark-report.md`).

        Market data still waits for the tick, which is the entire point of conflation: those
        frames are superseded, so batching them is free. A private frame is never superseded,
        so there is nothing to gain by holding it and twenty-five milliseconds to lose.

        The hand-over sets `busy`, which means a market frame offered while a private write is
        in flight is skipped for that tick. That is the existing, deliberate policy — the next
        snapshot is complete and repairs it one frame later — and it is why the tick could
        afford to skip in the first place.
        """
        if self.closed:
            return False
        if len(self._private) >= self._private_max:
            self.overflowed.set()
            return False
        self._private.append(frame)
        self._hand_over()
        return True

    # -- subscription ------------------------------------------------------------------------

    def subscribe(self, channels: set[str]) -> None:
        new = channels - self.channels
        self.channels |= channels
        self.fresh |= new

    def unsubscribe(self, channels: set[str]) -> None:
        self.channels -= channels
        self.fresh -= channels

    def take_fresh(self) -> set[str]:
        fresh, self.fresh = self.fresh, set()
        return fresh

    # -- writing -----------------------------------------------------------------------------

    def start(self) -> None:
        if self._writer is None:
            self._writer = asyncio.create_task(self._run(), name="subscriber-writer")

    def flush(self) -> None:
        """Hand whatever has accumulated to the writer. Called once per tick.

        Only market frames reach the writer this way now; a private frame handed itself over
        when it was offered. The private drain stays in `_run` regardless, because a frame
        offered while the writer was mid-write is still sitting in the deque when the tick
        arrives, and it must not be left there.
        """
        if self.closed or (not self._market and not self._private):
            return
        self._hand_over()

    def _hand_over(self) -> None:
        """Wake the writer. One writer task per connection, so writes never interleave."""
        self.busy = True
        self._wake.set()

    async def _run(self) -> None:
        while not self.closed:
            await self._wake.wait()
            self._wake.clear()
            # Private first: it is the data that cannot be dropped, so it should not sit
            # behind a book snapshot that will be superseded in fifty milliseconds anyway.
            batch = list(self._private) + self._market
            private_count = len(self._private)
            self._private.clear()
            self._market = []
            try:
                for frame in batch:
                    await self._send(frame)
            except Exception:  # noqa: BLE001 — the socket went away mid-write
                self.closed = True
                self.busy = False
                return
            self.sent += len(batch)
            self.private_sent += private_count
            self.busy = False

    async def stop(self) -> None:
        self.closed = True
        self._wake.set()
        if self._writer is not None:
            self._writer.cancel()
            try:
                await self._writer
            except (asyncio.CancelledError, Exception):
                pass
            self._writer = None


class Hub:
    """Every connection, indexed the two ways the hot loops read it.

    By channel for the conflation tick, by user for the private stream. Neither ever walks the
    full set: at two hundred clients and ten symbols, scanning everybody per channel per tick
    would be forty thousand set membership tests a second to answer a question a dict already
    knows.
    """

    def __init__(self) -> None:
        self.subscribers: set[Subscriber] = set()
        self._by_channel: dict[str, set[Subscriber]] = {}
        self._by_user: dict[int, set[Subscriber]] = {}
        #: The instrumentation for Success Criterion 5. Incremented only in `encode`.
        self.serialisations = 0

    # -- the counted chokepoint ----------------------------------------------------------------

    def encode(self, payload: dict) -> str:
        """The one place a message becomes a string.

        `separators` without spaces because this runs twenty times a second per symbol per
        channel and the whitespace is a measurable fraction of a book snapshot.
        """
        self.serialisations += 1
        return json.dumps(payload, separators=(",", ":"))

    # -- membership ----------------------------------------------------------------------------

    def add(self, subscriber: Subscriber) -> None:
        self.subscribers.add(subscriber)
        self._by_user.setdefault(subscriber.user_id, set()).add(subscriber)

    def remove(self, subscriber: Subscriber) -> None:
        self.subscribers.discard(subscriber)
        for channel in subscriber.channels:
            holders = self._by_channel.get(channel)
            if holders is not None:
                holders.discard(subscriber)
                if not holders:
                    del self._by_channel[channel]
        holders = self._by_user.get(subscriber.user_id)
        if holders is not None:
            holders.discard(subscriber)
            if not holders:
                del self._by_user[subscriber.user_id]

    def subscribe(self, subscriber: Subscriber, channels: set[str]) -> None:
        subscriber.subscribe(channels)
        for channel in channels:
            self._by_channel.setdefault(channel, set()).add(subscriber)

    def unsubscribe(self, subscriber: Subscriber, channels: set[str]) -> None:
        subscriber.unsubscribe(channels)
        for channel in channels:
            holders = self._by_channel.get(channel)
            if holders is not None:
                holders.discard(subscriber)
                if not holders:
                    del self._by_channel[channel]

    # -- delivery ------------------------------------------------------------------------------

    def listeners(self, channel: str) -> set[Subscriber]:
        return self._by_channel.get(channel, set())

    def broadcast(self, channel: str, frame: str) -> int:
        """Offer one already-encoded frame to every subscriber of `channel`."""
        return sum(1 for s in self.listeners(channel) if s.offer_market(frame))

    def broadcast_all(self, frame: str) -> int:
        """Every connection, regardless of subscription — the halt and error frames."""
        return sum(1 for s in self.subscribers if s.offer_market(frame))

    def to_user(self, user_id: int, frame: str) -> int:
        return sum(1 for s in self._by_user.get(user_id, ()) if s.offer_private(frame))

    def has_user(self, user_id: int) -> bool:
        return bool(self._by_user.get(user_id))

    def flush(self) -> None:
        for subscriber in self.subscribers:
            subscriber.flush()
