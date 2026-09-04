"""Quoting obligations, measured — which is what makes the bot a *designated* market maker.

Open Issue 005 sub-decision 5h: a designated market maker is defined by its obligations, not
merely by its privileges. The inventory exemption built in week 4 is granted **in exchange for**
these, so leaving them unmeasured would keep the privilege and quietly drop the price of it.

Three obligations, from the configuration:

| Obligation | Meaning |
|---|---|
| `max_spread_bps` | the quoted spread, against the quoted mid |
| `min_quote_size` | resting size on each side, separately |
| `min_two_sided_uptime` | the fraction of samples with both sides live |

Sampled once per quote tick. Every breach is an **assertable event** rather than a subjective
judgement, which is the third reason section 5h gives for building this: it hands the
inventory-skew logic a defined failure condition to test against.

Output is structured log lines and an end-of-session summary — Open Issue 012 settled
observability as structured logs plus offline analysis, with no Prometheus and no Grafana. The
same figures become market-quality metrics for the Task 7.4 benchmark report, where spread,
depth and uptime under load say considerably more than throughput alone.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from config.settings import ObligationLimits
from services.bots.quoting import BPS, TwoSidedQuote

LOGGER_NAME = "quant_arena.bots.obligations"

#: Breach kinds. Named constants because a benchmark run greps for them.
SPREAD_TOO_WIDE = "spread_too_wide"
SIZE_TOO_SMALL = "size_too_small"
NOT_TWO_SIDED = "not_two_sided"


@dataclass(frozen=True)
class Breach:
    kind: str
    symbol_id: int
    detail: dict


@dataclass
class ObligationMeter:
    """Accumulates compliance for one market maker on one symbol."""

    symbol_id: int
    limits: ObligationLimits
    samples: int = 0
    two_sided_samples: int = 0
    breaches: list[Breach] = field(default_factory=list)
    #: Counted separately from `breaches`, which keeps every event for the session summary.
    #: A long outage would otherwise be a very large list and a very small number.
    breach_counts: dict[str, int] = field(default_factory=dict)

    def observe(self, quote: TwoSidedQuote | None) -> list[Breach]:
        """Record one sample. `None` means the maker had no two-sided market up.

        Returns the breaches this sample produced, so the caller can log them as they happen
        rather than only at the end.
        """
        self.samples += 1
        found: list[Breach] = []

        if quote is None:
            found.append(
                Breach(NOT_TWO_SIDED, self.symbol_id, {"reason": "no two-sided quote"})
            )
        else:
            self.two_sided_samples += 1
            mid = quote.mid_ticks
            # Guarded: a mid of zero cannot happen while both sides are floored at one tick,
            # but dividing by it would turn a quoting bug into a crash in the meter.
            if mid > 0:
                spread_bps = (quote.spread_ticks * BPS) // mid
                if spread_bps > self.limits.max_spread_bps:
                    found.append(Breach(SPREAD_TOO_WIDE, self.symbol_id, {
                        "spread_bps": spread_bps,
                        "limit_bps": self.limits.max_spread_bps,
                    }))
            for side_name, side in (("bid", quote.bid), ("ask", quote.ask)):
                if side.qty < self.limits.min_quote_size:
                    found.append(Breach(SIZE_TOO_SMALL, self.symbol_id, {
                        "side": side_name,
                        "qty": side.qty,
                        "minimum": self.limits.min_quote_size,
                    }))

        for breach in found:
            self.breaches.append(breach)
            self.breach_counts[breach.kind] = self.breach_counts.get(breach.kind, 0) + 1
            _log(breach)
        return found

    @property
    def two_sided_uptime(self) -> float:
        """The fraction of samples with a live two-sided market. 1.0 before any sample.

        An unsampled session is vacuously compliant rather than a zero-uptime failure — the
        alternative reports a breach for a market maker that has not been asked to quote yet.
        """
        if self.samples == 0:
            return 1.0
        return self.two_sided_samples / self.samples

    @property
    def meets_uptime(self) -> bool:
        return self.two_sided_uptime >= self.limits.min_two_sided_uptime

    def summary(self) -> dict:
        """The end-of-session record. Task 4.4's second success criterion reads this."""
        return {
            "symbol_id": self.symbol_id,
            "samples": self.samples,
            "two_sided_uptime": round(self.two_sided_uptime, 4),
            "min_two_sided_uptime": self.limits.min_two_sided_uptime,
            "meets_uptime": self.meets_uptime,
            "breaches": dict(self.breach_counts),
            "total_breaches": len(self.breaches),
        }


def _log(breach: Breach) -> None:
    logging.getLogger(LOGGER_NAME).warning(
        json.dumps(
            {
                "event": "obligation_breach",
                "kind": breach.kind,
                "symbol_id": breach.symbol_id,
                **breach.detail,
            },
            separators=(",", ":"),
        )
    )
