"""The fair-value process the market maker quotes around.

**This is the synthetic fallback, not the real thing.** Task 5.1 replaces the driver with
replayed crypto history at 1 real second : 1 simulated minute, which brings volatility
clustering, regime changes and genuine trends for free — statistical structure a model would
approximate worse and take longer to write (Open Issue 005 section 10).

The fallback is not scaffolding either. Open Issue 005 section 10.2 keeps it explicitly "for
tests and for offline development, where a deterministic price path with no data dependency is
more convenient", and Task 5.1's fourth success criterion is that it still produces a
deterministic path with no data file present. So 5.1 adds the crypto loader *beside* this
behind the same `next_ticks()` call, rather than replacing it.

Integer ticks throughout. A float fair value would be the one place a float could leak into a
price the gateway then receives, and Open Issue 016 is clear that a float below the
presentation layer is a bug rather than a rounding concern.
"""

from __future__ import annotations

import random
from typing import Protocol


class FairValue(Protocol):
    """What the market maker needs from a price source, and nothing more.

    Deliberately this small: it is the seam Task 5.1 substitutes at. A market maker written
    against a richer interface would have to change when the real data arrives.
    """

    def next_ticks(self) -> int: ...


class SyntheticFairValue:
    """A seeded random walk in integer ticks.

    Gaussian steps rounded to whole ticks. It has no mean reversion and no volatility
    clustering, and that is honest rather than a shortcoming — modelling those was assessed
    (Open Issue 005 sub-decision 5a, option 3) and dropped once real data was adopted, because
    replayed history contains them already. What this generator is for is a price that moves
    reproducibly, so a bot session can be replayed exactly.
    """

    def __init__(
        self, *, start_ticks: int, volatility_ticks: int, rng: random.Random
    ) -> None:
        if start_ticks <= 0:
            raise ValueError("fair value starts above zero — a price of zero is not a price")
        self._value = start_ticks
        self._volatility = volatility_ticks
        self._rng = rng

    @property
    def value_ticks(self) -> int:
        """The current value, without advancing. For assertions and for logging."""
        return self._value

    def next_ticks(self) -> int:
        step = round(self._rng.gauss(0.0, self._volatility))
        # Floored at one tick. A random walk is unbounded below and would otherwise eventually
        # cross zero, at which point every quote derived from it is nonsense and the gateway
        # rejects it as INVALID_PRICE — a slow, confusing failure instead of a bounded one.
        self._value = max(1, self._value + step)
        return self._value
