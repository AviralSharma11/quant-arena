"""The controlled strategy interface — plain data in, plain data out.

Open Issue 011 sub-decision 11e is unusually specific about this, and the reason is Phase 2:
user-submitted strategies run in a sandbox, and a sandbox can only pass **serialisable** things
across its boundary. Every convenience taken here — handing the strategy a live portfolio
object, a dataset handle, an engine reference — is a convenience Phase 2 would have to undo,
turning serialisation work into a redesign. So the contract is deliberately poorer than Python
allows:

| | |
|---|---|
| **In** | The current `Bar`, and a `PortfolioView` — a frozen snapshot of the strategy's own cash and position |
| **Out** | Zero or more `OrderIntent` records |
| **Never** | A handle to the engine, the adapter, the dataset, the runner, or any future bar |

## Lookahead is prevented by the shape, not by a rule

`on_bar` receives **one** bar. Not a series, not an index into one, not a callback that could
fetch another. There is no expression a strategy can write that reaches bar N+1, because bar
N+1 is not reachable from anything it was handed. That is Task 7.1's third success criterion —
"provably cannot see future bars, **enforced by the interface rather than convention**" — and
it is why the criterion is met by the type signature rather than by a test.

A strategy that wants history keeps its own. `SmaCrossover` does exactly that, accumulating
closes it has already been shown. That is legitimate: it is remembering the past, which is what
every real strategy does, rather than reading the future.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Protocol

from contracts.v1.generated.contracts import Side

from services.backtest.bars import Bar


@dataclass(frozen=True)
class PortfolioView:
    """What the strategy is allowed to know about itself. Frozen, and a copy.

    Frozen so a strategy cannot mutate the runner's accounting by writing to what it was
    handed — under Phase 2 that object will have crossed a sandbox boundary and be a copy
    whether or not anyone remembered to make one, so it behaves that way here too.
    """

    cash_ticks: int
    position: int
    bar_index: int


@dataclass(frozen=True)
class OrderIntent:
    """A decision, not an order. The runner decides what it fills at, and whether it can.

    Deliberately not a `SubmitOrder` contract record: those carry a `client_order_id`, a `tif`
    and a price, all of which are answers to questions a backtest does not ask. A market
    intent at the next bar's open is the whole vocabulary Phase 1 needs (Open Issue 018 §3.2).
    """

    side: int
    qty: int


class Strategy(Protocol):
    """Everything a strategy must provide, and everything it may."""

    name: str

    def parameters(self) -> dict[str, int]:
        """Recorded verbatim in the run manifest, so a run can be repeated exactly."""
        ...

    def on_bar(self, bar: Bar, portfolio: PortfolioView) -> list[OrderIntent]:
        ...


class SmaCrossover:
    """The one built-in strategy. Open Issue 018 §3.2 cut three to one, deliberately.

    Long when the fast mean is above the slow mean, flat otherwise.

    **It states a target and closes the gap to it**, rather than remembering whether it thinks
    it is long. That difference is not cosmetic, and it fixes two faults:

    - A strategy that tracked its own flag never traded on the *first* full window, because
      there was no previous state to have crossed from — so a run beginning in an uptrend sat
      out the entire first trend and entered only on the second crossing.
    - Worse, the flag could disagree with reality. A buy refused for want of cash (Phase 1 has
      no margin) left the flag saying "long" against a position of zero, and the strategy never
      tried again for the rest of the run. Reading `portfolio.position` means a refusal
      self-corrects on the next bar.

    Trading only on a crossing still falls out, and now it falls out for a reason: an intent is
    emitted only when the target differs from what is actually held. A strategy that re-sent its
    intent every bar would pay the taker fee repeatedly to hold a position it already had, which
    measures the fee schedule rather than the signal.

    Both means are computed over closes the strategy has already been shown. It holds them
    itself precisely because the interface will not hand it a series.
    """

    def __init__(self, *, fast: int = 10, slow: int = 30, qty: int = 1) -> None:
        if fast < 1 or slow < 1:
            raise ValueError("both windows must be at least one bar")
        if fast >= slow:
            raise ValueError(
                f"the fast window must be shorter than the slow one, got {fast} >= {slow}"
            )
        if qty < 1:
            raise ValueError("qty must be at least one unit")
        self.name = "sma_crossover"
        self.fast = fast
        self.slow = slow
        self.qty = qty
        self._closes: deque[int] = deque(maxlen=slow)

    def parameters(self) -> dict[str, int]:
        return {"fast": self.fast, "slow": self.slow, "qty": self.qty}

    def on_bar(self, bar: Bar, portfolio: PortfolioView) -> list[OrderIntent]:
        self._closes.append(bar.close_ticks)
        # Until the slow window is full there is no slow mean, and a mean over a short window
        # is a different statistic wearing the same name. Warming up in silence is not
        # inactivity — it is refusing to trade on a number that does not exist yet.
        if len(self._closes) < self.slow:
            return []

        closes = list(self._closes)
        fast_mean = sum(closes[-self.fast :]) / self.fast
        slow_mean = sum(closes) / self.slow

        # The position the signal asks for, not the position it believes it has.
        target = self.qty if fast_mean > slow_mean else 0
        gap = target - portfolio.position
        if gap == 0:
            return []
        if gap > 0:
            return [OrderIntent(side=int(Side.BUY), qty=gap)]
        # Never more than is held. Phase 1 has no short selling — that is Phase 2 — so an
        # oversized sell would be an intent the runner must refuse, which is noise in the trade
        # count rather than a decision.
        return [OrderIntent(side=int(Side.SELL), qty=min(-gap, portfolio.position))]
