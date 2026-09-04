"""The designated market maker's live loop.

All decisions come from `services.bots.quoting`, which is pure; this file is the part with a
clock, a socket and a retry policy. Keeping the split sharp is what makes Success Criterion 4
provable — the arithmetic is reproducible from a seed, and this loop is honestly not.

One maker per symbol, holding one bid and one ask. On each tick it advances fair value, reads
its own inventory, computes the market it wants to show, replaces whichever side moved, and
records compliance.
"""

from __future__ import annotations

import asyncio
import json
import logging

from config.settings import BotSettings
from contracts.v1.generated.contracts import Side
from services.bots.client import BotClient
from services.bots.fairvalue import FairValue
from services.bots.obligations import ObligationMeter
from services.bots.quoting import Quote, TwoSidedQuote, desired_quote, needs_requote

LOGGER_NAME = "quant_arena.bots.market_maker"


class MarketMaker:
    """Quotes two-sided around fair value, skewed by inventory."""

    def __init__(
        self,
        *,
        client: BotClient,
        symbol_id: int,
        fair_value: FairValue,
        settings: BotSettings,
        meter: ObligationMeter,
    ) -> None:
        self.client = client
        self.symbol_id = symbol_id
        self.fair_value = fair_value
        self.settings = settings
        self.meter = meter
        #: The quote believed to be resting, per side, with the `client_order_id` that placed
        #: it — cancelling needs the *target's* id, not the cancel's own.
        self._resting: dict[int, tuple[Quote, int]] = {}
        self.quotes_placed = 0
        self.quotes_cancelled = 0
        self.rejections: dict[str, int] = {}
        self._stop = asyncio.Event()

    @property
    def inventory(self) -> int:
        return self.client.position(self.symbol_id)

    def current_two_sided(self) -> TwoSidedQuote | None:
        """What is actually resting, or None if either side is missing."""
        bid = self._resting.get(int(Side.BUY))
        ask = self._resting.get(int(Side.SELL))
        if bid is None or ask is None:
            return None
        return TwoSidedQuote(bid=bid[0], ask=ask[0])

    async def tick(self) -> None:
        """One quote cycle."""
        fair = self.fair_value.next_ticks()
        await self.client.refresh()

        wanted = desired_quote(
            fair_value_ticks=fair,
            inventory=self.inventory,
            half_spread_bps=self.settings.half_spread_bps,
            skew_bps=self.settings.inventory_skew_bps,
            size=self.settings.quote_size,
        )

        for quote in (wanted.bid, wanted.ask):
            resting = self._resting.get(quote.side)
            if not needs_requote(resting[0] if resting else None, quote):
                continue
            if resting is not None:
                await self._pull(quote.side, resting[1])
            await self._place(quote)

        # Sampled after the replacement, so the measurement is of the market the maker is
        # actually showing rather than the one it intended to show.
        self.meter.observe(self.current_two_sided())

    async def _pull(self, side: int, target_client_order_id: int) -> None:
        outcome = await self.client.cancel(target_client_order_id=target_client_order_id)
        # The resting entry is dropped either way. A cancel that was refused means the order
        # is already gone — filled, or expired — and keeping it would make the maker believe
        # it has a market up that it does not, which is an uptime lie in its own favour.
        self._resting.pop(side, None)
        if outcome.accepted:
            self.quotes_cancelled += 1

    #: How many times to re-send a quote the exchange could not durably record. Small: the
    #: obligation is measured per tick, so a quote that takes longer than a tick to place has
    #: already cost the uptime it was protecting.
    HALT_RETRIES = 3
    HALT_BACKOFF_SECONDS = 0.2

    async def _place(self, quote: Quote) -> None:
        """Send one side, retrying a halt with the **same** `client_order_id`.

        A 503 means the exchange could not durably record the order — most often the first
        write after Redis returns, where a pooled connection died with the server and the
        producer refuses rather than risk duplicating a part-written pipeline (Open Issue 003
        section 8.5 puts the retry in the client for exactly this reason).

        Abandoning the quote instead was measurably wrong: a Redis restart cost this maker four
        placements and dropped its two-sided uptime to 0.913, breaching a 0.95 obligation it
        was otherwise meeting perfectly — a market maker going dark for a blip the protocol
        already knows how to survive.

        The key is allocated **once** and reused on every attempt. That is what makes the retry
        safe: if an earlier attempt did reach the stream, the gateway answers from the
        idempotency store with the original outcome instead of placing a second order.
        """
        client_order_id = self.client.take_client_order_id()
        for attempt in range(self.HALT_RETRIES):
            outcome = await self.client.submit_limit(
                symbol_id=self.symbol_id,
                side=quote.side,
                price_ticks=quote.price_ticks,
                qty=quote.qty,
                client_order_id=client_order_id,
            )
            if outcome.accepted:
                self._resting[quote.side] = (quote, client_order_id)
                self.quotes_placed += 1
                return
            if outcome.halted and attempt < self.HALT_RETRIES - 1:
                await asyncio.sleep(self.HALT_BACKOFF_SECONDS)
                continue
            break

        # Refused for good. The side stays empty, which the meter will see as a two-sided
        # breach on this sample — correct, and the honest reading of the obligation.
        reason = outcome.reason or ("rate_limited" if outcome.rate_limited else "unknown")
        self.rejections[reason] = self.rejections.get(reason, 0) + 1
        if outcome.rate_limited:
            # Not a rejection: nothing was recorded against the key and the order can be sent
            # again. Give the bucket a moment rather than hammering it.
            await asyncio.sleep(1.0)

    async def run(self) -> None:
        interval = 1.0 / max(1, self.settings.quote_hz)
        while not self._stop.is_set():
            try:
                await self.tick()
            except asyncio.CancelledError:
                break
            except Exception:  # noqa: BLE001 — one bad tick must not end the market
                logging.getLogger(LOGGER_NAME).exception("market maker tick failed")
            await asyncio.sleep(interval)

    async def stop(self) -> None:
        self._stop.set()

    def summary(self) -> dict:
        return {
            "event": "market_maker_summary",
            "symbol_id": self.symbol_id,
            "username": self.client.username,
            "inventory": self.inventory,
            "cash_ticks": self.client.cash_ticks,
            "quotes_placed": self.quotes_placed,
            "quotes_cancelled": self.quotes_cancelled,
            "rejections": dict(self.rejections),
            **self.meter.summary(),
        }

    def log_summary(self) -> None:
        logging.getLogger(LOGGER_NAME).info(
            json.dumps(self.summary(), separators=(",", ":"))
        )
