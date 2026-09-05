"""The private stream — the records a user is party to, with a gap-detectable sequence.

`contracts/v1/rest_and_ws.md` §3.4: the private channel mirrors the outbound records this
session's user is party to — `OrderAccepted`, `OrderRejected`, `Fill`, `OrderCancelled`,
`AccountCreated`, `CashCredited` — as JSON with `schema.toml`'s own field names plus a `type`.

Two things here are decisions rather than transcription.

## `seq` is a dense per-user counter

§3.4's example shows `"seq": "1693526400000-9"`, which is a Redis stream id. §3.5 requires the
client to detect a gap from `seq`, and Open Issue 006 §7c says private messages carry a
**per-user sequence number** precisely so that a gap is detectable.

Those cannot both be literal. Stream ids are not dense for anyone — the id counts every record
on the stream, most of which concern other users — so under the §3.4 reading no private gap is
detectable and Task 5.2's fourth success criterion cannot be met by any implementation.

So `seq` here is a dense integer, one per message delivered to that user. It is what
`web/src/stream/gaps.ts` already assumes, and that file isolates the assumption in
`privateSequence()` so a single function changes if Dev A reads §3.4 differently. The question
still goes to Dev A; it no longer blocks.

The counter is per user and not per connection: two tabs belonging to one account see the same
numbers, and each sees a dense run for as long as it is connected. A client that joins mid-run
starts at whatever the counter has reached, which is why `StreamClient` resets its tracker on
every open rather than comparing across a reconnect.

## `role` is derived here, not sent by the engine

§3.4 again: `role` is "maker" or "taker", worked out from `aggressor_side` and which side this
user was on, so the browser does not have to reason about which fee it paid. The engine is
money-blind (Open Issue 001) and could not tell it anyway.

## Nothing is emitted during recovery

A restarted fan-out replays the retained stream from `0-0`. Those records are history — the
orders they describe were acknowledged long ago — so the private router is attached *after*
recovery finishes. Re-emitting them would deliver a user thousands of stale fills, each with a
fresh sequence number, which is worse than the gap it was trying to avoid.
"""

from __future__ import annotations

from config.settings import Settings
from contracts.v1.generated.contracts import (
    AccountCreated,
    CashCredited,
    Fill,
    OrderAccepted,
    OrderCancelled,
    OrderRejected,
    Side,
)
from services.fanout.subscribers import Hub

BUY, SELL = int(Side.BUY), int(Side.SELL)


class PrivateRouter:
    """Turns one outbound record into the private messages it owes, and delivers them."""

    def __init__(self, hub: Hub, settings: Settings) -> None:
        self.hub = hub
        self._symbol_names = {s.symbol_id: s.name for s in settings.symbols}
        self._seq: dict[int, int] = {}
        self.delivered = 0

    def symbol_name(self, symbol_id: int) -> str | None:
        """`None` for a symbol not in the configuration.

        Not an error: the retained stream outlives a configuration change, so a replay can
        legitimately meet a symbol that has since been delisted. `null` on the wire is the
        honest answer, and the field keeps its fixed position for the binary encoder.
        """
        return self._symbol_names.get(symbol_id)

    def next_seq(self, user_id: int) -> int:
        nxt = self._seq.get(user_id, 0) + 1
        self._seq[user_id] = nxt
        return nxt

    # -- the entry point -------------------------------------------------------------------------

    def route(self, record, *, stream_id: str) -> int:
        """Deliver this record to whichever connected users it concerns. Returns how many."""
        delivered = 0
        for user_id, body in self.messages_for(record):
            # Cheapest possible test first: most records on a busy stream belong to bots and
            # to accounts with no browser attached, and building the message for nobody is
            # the one cost this loop can avoid entirely.
            if not self.hub.has_user(user_id):
                continue
            frame = self.hub.encode(
                {
                    "ch": "private",
                    "type": type(record).__name__,
                    "seq": str(self.next_seq(user_id)),
                    "ts_ns": record.timestamp_ns,
                    **body,
                }
            )
            delivered += self.hub.to_user(user_id, frame)
        self.delivered += delivered
        return delivered

    def messages_for(self, record) -> list[tuple[int, dict]]:
        """`(user_id, fields)` for every user this record concerns.

        A `Fill` yields two — one per side — and they are genuinely different messages: the
        prices match but `role` does not, and each carries its own sequence number.
        """
        if isinstance(record, OrderAccepted):
            return [(
                record.user_id,
                {
                    "order_id": record.order_id,
                    "client_order_id": record.client_order_id,
                    "symbol": self.symbol_name(record.symbol_id),
                    "price_ticks": record.price_ticks,
                    "qty": record.qty,
                    "side": int(record.side),
                    "tif": int(record.tif),
                },
            )]

        if isinstance(record, OrderRejected):
            return [(
                record.user_id,
                {
                    # No order_id: the order never reached the book and none was assigned.
                    # Sent as null rather than omitted — a field that sometimes vanishes has
                    # no fixed offset, which is exactly what a binary encoder cannot follow.
                    "order_id": None,
                    "client_order_id": record.client_order_id,
                    "symbol": self.symbol_name(record.symbol_id),
                    "reason": int(record.reason),
                },
            )]

        if isinstance(record, Fill):
            aggressor = int(record.aggressor_side)
            common = {
                "symbol": self.symbol_name(record.symbol_id),
                "price_ticks": record.price_ticks,
                "qty": record.qty,
                "aggressor_side": aggressor,
            }
            return [
                (
                    record.maker_user_id,
                    {
                        "order_id": record.maker_order_id,
                        "client_order_id": None,
                        **common,
                        "role": "maker",
                    },
                ),
                (
                    record.taker_user_id,
                    {
                        "order_id": record.taker_order_id,
                        "client_order_id": None,
                        **common,
                        "role": "taker",
                    },
                ),
            ]

        if isinstance(record, OrderCancelled):
            return [(
                record.user_id,
                {
                    "order_id": record.order_id,
                    "client_order_id": record.client_order_id,
                    "symbol": self.symbol_name(record.symbol_id),
                    "remaining_qty": record.remaining_qty,
                    "reason": int(record.reason),
                },
            )]

        if isinstance(record, AccountCreated):
            return [(
                record.user_id,
                {"initial_cash_ticks": record.initial_cash_ticks},
            )]

        if isinstance(record, CashCredited):
            return [(record.user_id, {"amount_ticks": record.amount_ticks})]

        # `BookChanged` and anything the schema grows later. Market data, or not addressed to
        # a user; ignoring it keeps this tolerant of a stream that evolves.
        return []
