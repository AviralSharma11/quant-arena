from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Deque, Dict, Iterable, Optional


class Side(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass
class Order:
    """A single order in the simulated exchange book."""

    order_id: int
    user_id: int
    symbol: str
    side: Side
    price: int
    quantity: int
    created_at: int
    remaining_quantity: int = field(init=False)

    def __post_init__(self) -> None:
        if self.price <= 0:
            raise ValueError("price must be positive")
        if self.quantity <= 0:
            raise ValueError("quantity must be positive")
        self.remaining_quantity = self.quantity

    @property
    def is_open(self) -> bool:
        return self.remaining_quantity > 0

    def reduce(self, qty: int) -> int:
        if qty <= 0:
            raise ValueError("qty must be positive")
        if qty > self.remaining_quantity:
            raise ValueError("cannot reduce more than the remaining quantity")
        self.remaining_quantity -= qty
        return self.remaining_quantity


@dataclass
class Fill:
    """A trade record produced when one order crosses another."""

    buy_order_id: int
    sell_order_id: int
    symbol: str
    price: int
    quantity: int

    def __post_init__(self) -> None:
        if self.quantity <= 0:
            raise ValueError("quantity must be positive")


@dataclass
class PriceLevel:
    """All orders resting at the same price."""

    price: int
    orders: Deque[Order] = field(default_factory=deque)

    def __post_init__(self) -> None:
        if self.price <= 0:
            raise ValueError("price must be positive")

    def append(self, order: Order) -> None:
        self.orders.append(order)

    def remove(self, order: Order) -> None:
        try:
            self.orders.remove(order)
        except ValueError as exc:  # pragma: no cover - defensive guard
            raise ValueError(f"order {order.order_id} is not in price level {self.price}") from exc

    def popleft(self) -> Order:
        return self.orders.popleft()

    @property
    def first(self) -> Optional[Order]:
        return self.orders[0] if self.orders else None

    @property
    def empty(self) -> bool:
        return not self.orders


@dataclass
class OrderBook:
    """Typed order book shell for the naive reference model."""

    bids: Dict[int, PriceLevel] = field(default_factory=dict)
    asks: Dict[int, PriceLevel] = field(default_factory=dict)
    orders_by_id: Dict[int, Order] = field(default_factory=dict)
    _arrival_sequence: Dict[int, int] = field(default_factory=dict, init=False, repr=False)
    _next_arrival_sequence: int = field(default=0, init=False, repr=False)

    def side_map(self, side: Side) -> Dict[int, PriceLevel]:
        return self.bids if side == Side.BUY else self.asks

    def add_order(self, order: Order, *, match_immediately: bool = False) -> list[Fill]:
        if order.order_id in self.orders_by_id:
            raise ValueError(f"order {order.order_id} already exists in the book")
        self.orders_by_id[order.order_id] = order
        self._arrival_sequence[order.order_id] = self._next_arrival_sequence
        self._next_arrival_sequence += 1
        target = self.side_map(order.side)
        if order.price not in target:
            target[order.price] = PriceLevel(order.price)
        target[order.price].append(order)
        return self.match() if match_immediately else []

    def match_order(self, order: Order) -> list[Fill]:
        return self.add_order(order, match_immediately=True)

    def remove_order(self, order_id: int) -> Optional[Order]:
        order = self.orders_by_id.pop(order_id, None)
        if order is None:
            return None
        self._arrival_sequence.pop(order_id, None)
        target = self.side_map(order.side)
        level = target.get(order.price)
        if level is None:
            return order
        level.remove(order)
        if level.empty:
            del target[order.price]
        return order

    def match(self) -> list[Fill]:
        fills: list[Fill] = []

        while True:
            bid_price = self.best_bid
            ask_price = self.best_ask
            if bid_price is None or ask_price is None:
                return fills
            if bid_price < ask_price:
                return fills

            bid_order = self.best_bid_order
            ask_order = self.best_ask_order
            if bid_order is None or ask_order is None:
                return fills

            trade_qty = min(bid_order.remaining_quantity, ask_order.remaining_quantity)
            bid_arrival = self._arrival_sequence[bid_order.order_id]
            ask_arrival = self._arrival_sequence[ask_order.order_id]
            maker_order = bid_order if bid_arrival < ask_arrival else ask_order
            trade_price = maker_order.price
            fills.append(
                Fill(
                    buy_order_id=bid_order.order_id,
                    sell_order_id=ask_order.order_id,
                    symbol=ask_order.symbol,
                    price=trade_price,
                    quantity=trade_qty,
                )
            )

            bid_order.reduce(trade_qty)
            ask_order.reduce(trade_qty)

            if bid_order.remaining_quantity == 0:
                self.remove_order(bid_order.order_id)
            if ask_order.remaining_quantity == 0:
                self.remove_order(ask_order.order_id)

    def level(self, side: Side, price: int) -> Optional[PriceLevel]:
        return self.side_map(side).get(price)

    @property
    def best_bid(self) -> Optional[int]:
        return max(self.bids, default=None)

    @property
    def best_ask(self) -> Optional[int]:
        return min(self.asks, default=None)

    @property
    def best_bid_order(self) -> Optional[Order]:
        bid_price = self.best_bid
        if bid_price is None:
            return None
        return self.bids[bid_price].first

    @property
    def best_ask_order(self) -> Optional[Order]:
        ask_price = self.best_ask
        if ask_price is None:
            return None
        return self.asks[ask_price].first

    @property
    def has_orders(self) -> bool:
        return bool(self.orders_by_id)

    def iter_orders(self, side: Side) -> Iterable[Order]:
        for level in self.side_map(side).values():
            for order in level.orders:
                yield order
