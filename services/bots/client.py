"""A bot's connection to the exchange — the real one, over HTTP.

Task 4.4's Boundaries are explicit: bots go through the real API, not in process, because
"in-process bots exercise none of the code they would be testing and are worthless as a load
test" (Open Issue 005 sub-decision 5c). So a bot registers, logs in, holds a session cookie and
submits orders exactly as a browser does — through validation, risk checks, the idempotency
claim and the rate limiter. Nothing in the gateway knows it is talking to a bot.

## `client_order_id` is a per-bot counter that survives a restart

Open Issue 008: the identifier is client-assigned and must exist *before* the request is sent,
because the failure it protects against is the response being lost. A bot increments it once
per **decision** and holds it fixed across every retry of that decision — which is the whole
protocol, and the reason a retry cannot become a second order.

**It starts from a millisecond timestamp, not from one.** A counter restarting at one is the
same counter the previous run used, and the gateway remembers outcomes for
`idempotency.ttl_seconds` — an hour. So a restarted bot re-sent keys the store had already
answered, and got the stored `202 accepted` back, complete with the original `seq`. Every order
was acknowledged, no order was appended, and the market was dead while every participant
reported success. `restart: unless-stopped` on the compose service makes that a crash away.

Milliseconds since the epoch is about 1.8e12, so a `uint64` holds roughly 5 million years of
them, and any restart begins above every id the previous run could have reached.

Zero is reserved: `POST /auth/register` uses it for the account's own `CreateAccount`.

## Feedback is REST polling, until Task 5.2b

There is no private stream yet, so a bot learns its own fills by reading `GET /portfolio` and
`GET /orders/open`. The frozen contract says those exist "solely for re-synchronisation after a
detected sequence gap" and that **the UI** must not poll them (Open Issue 014 sub-decision
14e) — a constraint aimed at the browser, which will have the stream. A bot in week 4 has
nothing else. Both reads sit behind `refresh()`, so 5.2b replaces the poller by changing one
method rather than every caller.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

import httpx

from contracts.v1.generated.contracts import Side, Tif

def new_client_order_id_base() -> int:
    """Where a fresh bot process starts counting. See the module docstring.

    Milliseconds rather than nanoseconds: still monotonic across restarts, still far above any
    previous run, and short enough to read in a log line.
    """
    return time.time_ns() // 1_000_000


@dataclass
class OrderOutcome:
    """What the gateway said. An acknowledgement, never a result (Open Issue 008 section 9h)."""

    accepted: bool
    client_order_id: int
    seq: str | None = None
    reason: str | None = None
    rate_limited: bool = False
    halted: bool = False


@dataclass
class BotClient:
    """One authenticated participant."""

    base_url: str
    username: str
    password: str
    _client: httpx.AsyncClient | None = field(default=None, repr=False)
    #: Never 1. A restart inside the idempotency TTL would otherwise replay the previous
    #: run's keys and be answered from the store without placing anything.
    _next_client_order_id: int = field(default_factory=new_client_order_id_base)
    user_id: int | None = None
    cash_ticks: int = 0
    positions: dict[int, int] = field(default_factory=dict)

    async def __aenter__(self) -> "BotClient":
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=10.0)
        return self

    async def __aexit__(self, *_exc) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def http(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("BotClient must be used as an async context manager")
        return self._client

    def take_client_order_id(self) -> int:
        """One per decision. Held fixed across retries of that decision, never reused."""
        value = self._next_client_order_id
        self._next_client_order_id += 1
        return value

    # -- lifecycle ---------------------------------------------------------------------------

    async def sign_in(self, *, attempts: int = 30, delay: float = 0.5) -> None:
        """Register if new, then log in — retrying only what is worth retrying.

        Registration can legitimately fail on a cold stack: the grant is appended to the
        inbound stream, so a gateway whose producer is still halted answers 503. Retrying that
        is what a real client does, and it is why the retry lives here rather than in the
        gateway (Open Issue 003 section 8.5 — fail loudly, let the caller decide).

        **A wrong password is not transient**, and this is the distinction that matters. A bot
        whose account already exists under different credentials gets 409 then 401 forever;
        retrying that thirty times turns a one-line diagnosis into a five-minute timeout with
        a message that names the wrong problem. It is exactly the case a stale account from an
        earlier run produces, so it is the failure most likely to be met.
        """
        credentials = {"username": self.username, "password": self.password}
        for attempt in range(attempts):
            registered = await self.http.post("/auth/register", json=credentials)
            if registered.status_code in (201, 409):
                login = await self.http.post("/auth/login", json=credentials)
                if login.status_code == 200:
                    self.user_id = login.json()["user_id"]
                    return
                if login.status_code == 401:
                    raise RuntimeError(
                        f"{self.username} exists with different credentials — the account is "
                        f"left over from an earlier run under another QA_BOT_PASSWORD. "
                        f"Reset the stack (docker compose down -v) or reuse that password."
                    )
            if attempt < attempts - 1:
                await asyncio.sleep(delay)
        raise RuntimeError(
            f"{self.username} could not sign in after {attempts} attempts "
            f"(last register: {registered.status_code})"
        )

    async def await_funding(self, *, attempts: int = 60, delay: float = 0.5) -> int:
        """Block until the cash grant has been projected into the read model.

        The grant is an event now, not a row written at registration, so an account is briefly
        real but broke. A bot that started quoting before this returned would have its first
        orders rejected for INSUFFICIENT_CASH and count them as breaches of its own
        obligations.
        """
        for _ in range(attempts):
            await self.refresh()
            if self.cash_ticks > 0:
                return self.cash_ticks
            await asyncio.sleep(delay)
        raise RuntimeError(f"{self.username} was never funded")

    # -- reads --------------------------------------------------------------------------------

    async def refresh(self) -> None:
        """Re-read cash and positions. Replaced by the private stream at Task 5.2b."""
        response = await self.http.get("/portfolio")
        if response.status_code != 200:
            return
        body = response.json()
        self.cash_ticks = body["cash_ticks"]
        self.positions = {p["symbol_id"]: p["qty"] for p in body["positions"]}

    def position(self, symbol_id: int) -> int:
        return self.positions.get(symbol_id, 0)

    async def open_orders(self) -> list[dict]:
        response = await self.http.get("/orders/open")
        return response.json() if response.status_code == 200 else []

    async def cancel_all_resting(self) -> int:
        """Pull every order this account still has on the book. Returns how many were cancelled.

        Called once at start-up, and it is not tidiness — it is what keeps a restarted bot able
        to trade at all. A new process builds a fresh view of what it has resting, so without
        this it never cancels what the previous one left, and every orphaned buy keeps holding
        `limit x qty` of cash reserved. At a 100-lot quote against a 1,000,000 grant that is ten
        restarts before the account cannot bid: the maker goes quietly one-sided, and the
        rejection reason it reports is `INSUFFICIENT_CASH` on an account whose settled balance
        has been *rising* the whole time.

        `GET /orders/open` is exactly the re-synchronisation endpoint the frozen contract
        provides for rebuilding a view after a gap (Open Issue 014 sub-decision 14e), and a
        process restart is the largest gap there is.

        Stale quotes are worth cancelling on their own account too: they were priced off a fair
        value from a previous session, and resting on them is offering a market nobody computed.
        """
        cancelled = 0
        for order in await self.open_orders():
            outcome = await self.cancel(
                target_client_order_id=order["client_order_id"]
            )
            cancelled += outcome.accepted
        return cancelled

    # -- writes -------------------------------------------------------------------------------

    async def submit_limit(
        self,
        *,
        symbol_id: int,
        side: int,
        price_ticks: int,
        qty: int,
        client_order_id: int | None = None,
    ) -> OrderOutcome:
        """Place a limit order. Pass `client_order_id` to **retry an existing decision**.

        This is the whole point of the identifier (Open Issue 008): a caller that resends with
        the same key cannot create a second order, whatever happened to the first response. A
        retry that allocated a fresh key would be a new order wearing a retry's clothes.
        """
        return await self._submit({
            "client_order_id": (
                self.take_client_order_id()
                if client_order_id is None
                else client_order_id
            ),
            "symbol_id": symbol_id,
            "side": int(side),
            "tif": int(Tif.GTC),
            "price_ticks": price_ticks,
            "qty": qty,
        })

    async def submit_market(self, *, symbol_id: int, side: int, qty: int) -> OrderOutcome:
        """A market order — banded and forced to IOC by the gateway (Task 3.1).

        It carries no price: the gateway derives the band off the opposing side, and refuses
        the order outright when there is nothing resting there, which is exactly the thin-book
        protection the band exists for.
        """
        return await self._submit({
            "client_order_id": self.take_client_order_id(),
            "symbol_id": symbol_id,
            "side": int(side),
            "tif": int(Tif.IOC),
            "qty": qty,
            "order_type": "market",
        })

    async def cancel(self, *, target_client_order_id: int) -> OrderOutcome:
        """Cancel by the *target's* client order id, with a fresh key for the cancel itself.

        Two identifiers, and the cancel is a request in its own right (Open Issue 008): a
        client can cancel an order whose acknowledgement it never received, which is precisely
        the case a market maker hits when it requotes through a lost response.
        """
        client_order_id = self.take_client_order_id()
        response = await self.http.request(
            "DELETE",
            f"/orders/{target_client_order_id}",
            json={"client_order_id": client_order_id},
        )
        return self._outcome(response, client_order_id)

    async def _submit(self, body: dict) -> OrderOutcome:
        response = await self.http.post("/orders", json=body)
        return self._outcome(response, body["client_order_id"])

    @staticmethod
    def _outcome(response: httpx.Response, client_order_id: int) -> OrderOutcome:
        if response.status_code in (200, 202):
            return OrderOutcome(
                accepted=True,
                client_order_id=client_order_id,
                seq=response.json().get("seq"),
            )
        try:
            detail = response.json().get("detail", {})
            reason = detail.get("reason") if isinstance(detail, dict) else str(detail)
        except ValueError:
            reason = response.text
        return OrderOutcome(
            accepted=False,
            client_order_id=client_order_id,
            reason=reason,
            # Rate limiting is not a rejection. It means "not processed, ask again", and
            # nothing was recorded against the key — so a bot backs off rather than treating
            # the order as refused.
            rate_limited=response.status_code == 429,
            halted=response.status_code == 503,
        )
