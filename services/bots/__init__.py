"""Simulated participants — a designated market maker and noise traders.

Open Issue 005 §1 is the reason this is not a demo prop: **the simulated participants are the
data-generating process for the entire quantitative half of the project.** Every price, trade
and candle the backtester consumes in week 7 comes from here, and they double as the load
generator for the Goal 5 benchmarks and the data source for the archiver.

The split throughout is between **pure decisions** and **live loops**. `quoting`, `noise` and
`fairvalue` hold arithmetic with no clock, no socket and no randomness beyond an injected
generator; `market_maker`, `runner` and `client` hold the parts that talk to a real gateway
over HTTP. That is what makes "seeded runs reproduce identical bot behaviour" a testable claim
rather than a flaky one.
"""

from services.bots.client import BotClient
from services.bots.fairvalue import SyntheticFairValue
from services.bots.market_maker import MarketMaker
from services.bots.noise import NoiseTrader
from services.bots.obligations import ObligationMeter
from services.bots.quoting import desired_quote, needs_requote
from services.bots.runner import BotRunner

__all__ = [
    "BotClient",
    "BotRunner",
    "MarketMaker",
    "NoiseTrader",
    "ObligationMeter",
    "SyntheticFairValue",
    "desired_quote",
    "needs_requote",
]
