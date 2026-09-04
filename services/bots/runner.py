"""Supervises the bots, and owns the seeds.

## Seeding

One master seed in the configuration produces every bot's generator, derived by name:

    Random(f"{seed}:{role}:{symbol_id}:{index}")

Derivation rather than a counter, so that adding a noise trader to one symbol does not shift
every other bot's stream — with a shared counter, changing `count_per_symbol` would silently
change the market maker's price path too, and a "reproducible" run would reproduce nothing you
recognised.

Open Issue 005 sub-decision 5d is worth restating here because it is easy to over-claim:
determinism is a property of **replaying the log**, not of reproducing a live session. These
bots run in real time over HTTP and interleave with each other and with human orders
non-deterministically. What a seed reproduces is each bot's own sequence of decisions; what
reproduces an outcome is replaying the stream those decisions produced.

## Accounts

Every bot is an ordinary account created through `POST /auth/register`. The market makers are
the usernames listed in `bots.designated_market_maker_accounts`, which is the only thing that
grants the negative-inventory exemption — noise traders are retail and may not go short.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import random

from config.settings import Settings
from services.bots.client import BotClient
from services.bots.fairvalue import SyntheticFairValue
from services.bots.market_maker import MarketMaker
from services.bots.noise import NoiseTrader
from services.bots.obligations import ObligationMeter

LOGGER_NAME = "quant_arena.bots"


def derive_rng(seed: int, *parts: object) -> random.Random:
    """A generator for one bot, independent of how many other bots exist."""
    return random.Random(f"{seed}:" + ":".join(str(p) for p in parts))


class NoiseRunner:
    """One noise trader's live loop: wait an exponential interval, then act."""

    def __init__(self, *, client: BotClient, symbol_id: int, trader: NoiseTrader) -> None:
        self.client = client
        self.symbol_id = symbol_id
        self.trader = trader
        self.orders_sent = 0
        self.rejections: dict[str, int] = {}
        self._stop = asyncio.Event()

    async def run(self) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.sleep(self.trader.next_delay_seconds())
                await self.client.refresh()
                intent = self.trader.decide(position=self.client.position(self.symbol_id))
                if intent is None:
                    continue
                outcome = await self.client.submit_market(
                    symbol_id=self.symbol_id, side=intent.side, qty=intent.qty
                )
                if outcome.accepted:
                    self.orders_sent += 1
                else:
                    reason = outcome.reason or "unknown"
                    self.rejections[reason] = self.rejections.get(reason, 0) + 1
                    if outcome.rate_limited:
                        await asyncio.sleep(1.0)
            except asyncio.CancelledError:
                break
            except Exception:  # noqa: BLE001 — one bad arrival must not end the flow
                logging.getLogger(LOGGER_NAME).exception("noise trader failed")

    async def stop(self) -> None:
        self._stop.set()

    def summary(self) -> dict:
        return {
            "event": "noise_trader_summary",
            "username": self.client.username,
            "symbol_id": self.symbol_id,
            "orders_sent": self.orders_sent,
            "position": self.client.position(self.symbol_id),
            "rejections": dict(self.rejections),
        }


class BotRunner:
    """Builds every bot, signs them in, runs them, and reports at the end."""

    def __init__(self, settings: Settings, *, base_url: str, password: str) -> None:
        self.settings = settings
        self.base_url = base_url
        self.password = password
        self.clients: list[BotClient] = []
        self.makers: list[MarketMaker] = []
        self.noise: list[NoiseRunner] = []
        self._tasks: list[asyncio.Task] = []

    def market_maker_name(self, symbol_id: int) -> str | None:
        """The configured market-maker username for a symbol, by convention `dmm_<name>`.

        The convention is only used to *find* a name that is already on the configured list —
        a name not on that list gets no exemption however it is spelled, which is the point of
        the list being explicit rather than a prefix rule.
        """
        symbol = next(
            (s for s in self.settings.symbols if s.symbol_id == symbol_id), None
        )
        if symbol is None:
            return None
        candidate = f"dmm_{symbol.name.lower()}"
        if candidate in self.settings.designated_market_maker_accounts:
            return candidate
        return None

    async def build(self, stack: contextlib.AsyncExitStack) -> None:
        bots = self.settings.bots
        for symbol in self.settings.symbols:
            maker_name = self.market_maker_name(symbol.symbol_id)
            if maker_name is None:
                logging.getLogger(LOGGER_NAME).warning(
                    json.dumps({
                        "event": "no_market_maker",
                        "symbol_id": symbol.symbol_id,
                        "detail": "no configured designated market maker for this symbol",
                    })
                )
            else:
                client = await stack.enter_async_context(
                    BotClient(self.base_url, maker_name, self.password)
                )
                self.clients.append(client)
                self.makers.append(
                    MarketMaker(
                        client=client,
                        symbol_id=symbol.symbol_id,
                        fair_value=SyntheticFairValue(
                            start_ticks=bots.fair_value_start_ticks,
                            volatility_ticks=bots.fair_value_volatility_ticks,
                            rng=derive_rng(bots.seed, "fairvalue", symbol.symbol_id),
                        ),
                        settings=bots,
                        meter=ObligationMeter(
                            symbol_id=symbol.symbol_id, limits=bots.obligations
                        ),
                    )
                )

            for index in range(bots.noise.count_per_symbol):
                client = await stack.enter_async_context(
                    BotClient(
                        self.base_url,
                        f"noise_{symbol.name.lower()}_{index}",
                        self.password,
                    )
                )
                self.clients.append(client)
                self.noise.append(
                    NoiseRunner(
                        client=client,
                        symbol_id=symbol.symbol_id,
                        trader=NoiseTrader(
                            rng=derive_rng(
                                bots.seed, "noise", symbol.symbol_id, index
                            ),
                            settings=bots.noise,
                        ),
                    )
                )

    async def sign_in(self) -> None:
        """Sequential, not concurrent. Registration writes a user row and appends to the
        inbound stream; a burst of them at start-up is the one moment the whole session is
        most likely to hit the rate limiter or a still-halted producer."""
        for client in self.clients:
            await client.sign_in()
            await client.await_funding()
            # Withdraw anything a previous run left resting. See `cancel_all_resting`: without
            # it a restarted market maker's reserved cash only ever grows.
            await client.cancel_all_resting()

    def start(self) -> None:
        # Market makers first, so there is something resting for a noise trader's market order
        # to hit. A market order into an empty book has no reference price and is refused
        # outright (Task 3.1), which would otherwise be every noise order for the first tick.
        for maker in self.makers:
            self._tasks.append(asyncio.create_task(maker.run(), name=f"mm-{maker.symbol_id}"))
        for runner in self.noise:
            self._tasks.append(
                asyncio.create_task(runner.run(), name=f"noise-{runner.client.username}")
            )

    async def stop(self) -> None:
        for maker in self.makers:
            await maker.stop()
        for runner in self.noise:
            await runner.stop()
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self._tasks.clear()

    def summaries(self) -> list[dict]:
        return [maker.summary() for maker in self.makers] + [
            runner.summary() for runner in self.noise
        ]

    def log_summaries(self) -> None:
        logger = logging.getLogger(LOGGER_NAME)
        for summary in self.summaries():
            logger.info(json.dumps(summary, separators=(",", ":")))
