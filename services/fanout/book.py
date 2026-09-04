"""The order book, rebuilt from the outbound stream.

`schema.toml` designed `BookChanged` for this — one aggregated price level per record, with
fan-out reassembling the book from them. Nothing emits it. So the book is derived instead from
the three records the matcher does emit, which between them carry everything needed:

| Record | Effect on the book |
|---|---|
| `OrderAccepted` | an order rests at `(symbol, side, price)` with `qty` |
| `Fill` | **both** sides lose `qty` |
| `OrderCancelled` | the order leaves, whatever it had left |

Both sides of a fill matter, and it is the easiest thing here to get wrong. The taker is
accepted onto the book *before* it matches — `services/matcher/adapter.py` emits `OrderAccepted`
first precisely so that no consumer applies a fill to an order it has never heard of — so a fill
reduces the resting maker and the just-arrived taker alike. Decrementing only the maker leaves
every aggressing order sitting on the book forever, and the quoted depth grows without bound.

See this package's README for why the book is derived rather than read from `BookChanged`, and
for what would make that worth revisiting.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from contracts.v1.generated.contracts import Side

BUY, SELL = int(Side.BUY), int(Side.SELL)


@dataclass
class RestingOrder:
    """One live order, as much of it as the book needs to know."""

    order_id: int
    side: int
    price_ticks: int
    remaining_qty: int


@dataclass
class Book:
    """One symbol's resting orders.

    Price levels are **aggregated on read** rather than maintained alongside. A maintained
    price map is faster and is a second structure that can fall out of step with the first;
    summing on read cannot drift by construction. At ten symbols and twenty ticks a second that
    trade is not close to mattering — see the README for when it would be.
    """

    symbol_id: int
    orders: dict[int, RestingOrder] = field(default_factory=dict)

    # -- the three events ----------------------------------------------------------------------

    def accept(self, *, order_id: int, side: int, price_ticks: int, qty: int) -> None:
        self.orders[order_id] = RestingOrder(
            order_id=order_id, side=side, price_ticks=price_ticks, remaining_qty=qty
        )

    def fill(self, order_id: int, qty: int) -> None:
        """Reduce one side of a trade. Silent on an unknown id, on purpose.

        A fill can name an order accepted before the retained stream begins — the stream is
        trimmed at two million entries and there are no snapshots (Open Issue 018 §13.1), so a
        replay from `0-0` legitimately starts mid-history. Raising here would make a trimmed
        stream unreadable; ignoring it means the book is missing an order it could never have
        known about, which is the honest state.
        """
        order = self.orders.get(order_id)
        if order is None:
            return
        order.remaining_qty -= qty
        if order.remaining_qty <= 0:
            del self.orders[order_id]

    def cancel(self, order_id: int) -> None:
        self.orders.pop(order_id, None)

    # -- reads ---------------------------------------------------------------------------------

    def levels(self, side: int, depth: int) -> list[list[int]]:
        """Top `depth` price levels for one side, as `[price_ticks, qty]` pairs.

        Bids descend and asks ascend, so index 0 is always the most aggressive price on that
        side and a client can read the top of book without knowing which side it asked for.
        """
        totals: dict[int, int] = {}
        for order in self.orders.values():
            if order.side == side and order.remaining_qty > 0:
                totals[order.price_ticks] = (
                    totals.get(order.price_ticks, 0) + order.remaining_qty
                )
        ordered = sorted(totals.items(), reverse=(side == BUY))
        return [[price, qty] for price, qty in ordered[:depth]]

    def best(self, side: int) -> list[int] | None:
        """The top level on one side, or None when that side is empty.

        None rather than a zero pair: an empty side and a side offering nothing at a price of
        zero are different facts, and a client that renders the second as a real quote is
        showing a market nobody is making.
        """
        top = self.levels(side, 1)
        return top[0] if top else None

    @property
    def is_two_sided(self) -> bool:
        return bool(self.orders) and self.best(BUY) is not None and self.best(SELL) is not None

    def resting(self) -> list[tuple[int, int, int, int]]:
        """Every live order as `(order_id, side, price_ticks, remaining_qty)`, sorted.

        Sorted by `order_id`, so two books built from the same stream compare equal — which is
        how the replay criterion is checked without inventing a snapshot format. The matcher
        exposes the same shape for the same reason.
        """
        return sorted(
            (o.order_id, o.side, o.price_ticks, o.remaining_qty)
            for o in self.orders.values()
        )
