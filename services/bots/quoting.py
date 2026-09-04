"""The market maker's decision, as a pure function.

Everything in this file is arithmetic on integers with no clock, no network and no randomness.
That is what makes Success Criterion 4 — "seeded runs reproduce identical bot behaviour" —
testable at all: the loop that carries these quotes to the exchange runs in real time and
interleaves with other participants non-deterministically, exactly as Open Issue 005
sub-decision 5d says it will. The decision is reproducible; the session is not, and pretending
otherwise would produce a flaky test wearing the costume of a strong one.

## Inventory skew, which is the whole idea

    mid = fair_value - skew(inventory)
    bid = mid - half_spread
    ask = mid + half_spread

The skew is measured in **basis points of fair value per one full `quote_size` of inventory**,
not in ticks per unit. Ticks per unit was the obvious first choice and it is wrong twice over:
it means something different on a 100-tick symbol and a 100,000-tick one, and it does not
scale with the size being quoted, so changing `quote_size` silently changes how violently the
maker reacts to a single fill. The unit here is dimensionless, so one full fill always moves
the mid by the same *proportion* whatever the symbol or the size.

With `fair_value = 1000`, `half_spread_bps = 50`, `skew_bps = 25`, `quote_size = 100`:

| inventory | mid | bid | ask | effect |
|---|---|---|---|---|
| 0 | 1000 | 995 | 1005 | balanced |
| +100 (one fill long) | 997 | 992 | **1002** | the ask falls toward fair value, so selling is likelier |
| -100 (one fill short) | 1003 | **998** | 1008 | the bid rises, so buying is likelier |

One fill moves the mid by half a spread — enough to bias the next trade, not enough to jump
the market. A skew equal to `half_spread_bps` is the natural starting point for that reason.

Open Issue 005 sub-decision 5b calls this "the detail that matters most", and the reason is
Success Criterion 3: a market maker that quotes symmetrically regardless of position
accumulates unbounded one-sided inventory and eventually cannot quote at all. Skewing against
inventory is what real market makers do, and it is what keeps the simulation stable over hours
rather than minutes.
"""

from __future__ import annotations

from dataclasses import dataclass

from contracts.v1.generated.contracts import Side

#: Basis points per unit. 10,000 bps = 100%.
BPS = 10_000


@dataclass(frozen=True)
class Quote:
    """One side of a two-sided market."""

    side: int
    price_ticks: int
    qty: int


@dataclass(frozen=True)
class TwoSidedQuote:
    bid: Quote
    ask: Quote

    @property
    def spread_ticks(self) -> int:
        return self.ask.price_ticks - self.bid.price_ticks

    @property
    def mid_ticks(self) -> int:
        return (self.ask.price_ticks + self.bid.price_ticks) // 2


def desired_quote(
    *,
    fair_value_ticks: int,
    inventory: int,
    half_spread_bps: int,
    skew_bps: int,
    size: int,
) -> TwoSidedQuote:
    """The two-sided market this maker wants to show, given what it is holding.

    Returns quotes, not orders: whether these need sending at all is the requote policy's
    question, and whether they are affordable is the gateway's.
    """
    # Half the spread, in ticks, off fair value — so it scales with price instead of being a
    # tick count that means something different on a 100-tick symbol and a 100,000-tick one.
    half_spread = max(1, (fair_value_ticks * half_spread_bps) // BPS)

    # Magnitude then sign, rather than one signed division: Python floors toward negative
    # infinity, so a signed `//` would skew a short position one tick further than the
    # equivalent long one and quietly bias the maker in one direction.
    magnitude = (fair_value_ticks * skew_bps * abs(inventory)) // (max(1, size) * BPS)
    skew = -magnitude if inventory < 0 else magnitude
    mid = fair_value_ticks - skew

    # Both sides floored at one tick. Enough inventory skew will otherwise push a quote through
    # zero, and the gateway would answer INVALID_PRICE — a correct rejection, but a confusing
    # way to discover that the skew coefficient is too large for the price level.
    bid_price = max(1, mid - half_spread)
    ask_price = max(bid_price + 1, mid + half_spread)

    return TwoSidedQuote(
        bid=Quote(side=int(Side.BUY), price_ticks=bid_price, qty=size),
        ask=Quote(side=int(Side.SELL), price_ticks=ask_price, qty=size),
    )


def needs_requote(resting: Quote | None, desired: Quote) -> bool:
    """Whether a resting quote should be pulled and replaced.

    Requoting on every tick regardless would burn the per-user rate limit built in week 4 and
    fill the durable stream with churn that carries no information — and the stream is retained
    and replayed by every consumer, so noise there is not free.
    """
    if resting is None:
        return True
    return (
        resting.price_ticks != desired.price_ticks or resting.qty != desired.qty
    )
