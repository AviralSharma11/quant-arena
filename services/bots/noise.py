"""Noise traders — Poisson arrivals, and the constraint that shapes them.

Their job is order flow: without someone crossing the spread the market maker's quotes rest
untouched, nothing trades, no candle has a body, and the archive records a book that never
moved. Open Issue 005 sub-decision 5b costs them at four hours and calls them mandatory
alongside the market maker.

## Why Poisson

Arrivals are drawn from an exponential distribution, which is what makes the *counts* Poisson.
A fixed interval would produce a metronome, and a metronome is the one arrival pattern that
never queues — so the load generator built on these bots in week 6 would systematically
understate latency by never producing the bursts that cause it.

## Why a noise trader can only sell what it holds

A noise trader is an **ordinary retail account**. Open Issue 004 section 5, invariant 2 is
`position >= 0` for every retail account, and only designated market makers are exempt
(Open Issue 005 section 10.6). So the side is chosen from what the trader can actually do:
it may always buy, and may sell only up to its position.

That is not a limitation worked around — it produces the property that makes the whole
arrangement coherent. Units are never created, so the market maker's short inventory is the
exact mirror of the noise traders' long inventory, and **quantity conservation holds by
construction** with the designated market maker absorbing the short side. Which is what a
designated market maker is for.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from config.settings import NoiseSettings
from contracts.v1.generated.contracts import Side


@dataclass(frozen=True)
class Intent:
    """What a noise trader wants to do next. A market order — see `decide`."""

    side: int
    qty: int


class NoiseTrader:
    """One trader's decision process. Pure apart from its injected generator.

    The generator is injected rather than created here so that a seeded run reproduces an
    identical sequence of decisions (Open Issue 005 sub-decision 5d).
    """

    def __init__(self, *, rng: random.Random, settings: NoiseSettings) -> None:
        if settings.min_qty < 1 or settings.max_qty < settings.min_qty:
            raise ValueError("noise quantities must satisfy 1 <= min_qty <= max_qty")
        self._rng = rng
        self._settings = settings

    def next_delay_seconds(self) -> float:
        """Time until this trader's next order. Exponential, so counts are Poisson."""
        return self._rng.expovariate(self._settings.arrivals_per_second)

    def decide(self, *, position: int) -> Intent | None:
        """The next order, or None when there is nothing this trader is allowed to do.

        Orders are **market orders**: the gateway turns one into a marketable limit at the
        band and forces it to IOC (Task 3.1), so it either prints against the resting book or
        expires. That is the point — a limit order that rests adds depth but no trade, and
        Success Criterion 1 is that trades print continuously.
        """
        qty = self._rng.randint(self._settings.min_qty, self._settings.max_qty)
        sell_is_possible = position > 0

        # The coin is flipped whether or not selling is possible, so that a trader's random
        # sequence does not depend on its own position. Two traders with the same seed then
        # make the same draws, which is what keeps a seeded run reproducible.
        wants_to_buy = self._rng.random() < 0.5

        if wants_to_buy or not sell_is_possible:
            return Intent(side=int(Side.BUY), qty=qty)

        # Never more than it holds: a retail account may not go short, and an order the
        # gateway would refuse for INSUFFICIENT_POSITION is flow that never reaches the book.
        return Intent(side=int(Side.SELL), qty=min(qty, position))
