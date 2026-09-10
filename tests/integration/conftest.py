"""Fixtures for T5 — the thinned integration layer (Task 7.3).

T1 through T4 carry the correctness argument, and they carry it **in process, with no network
and no database**. That is deliberate: a property test that had to start Postgres would run
too slowly to be run at every commit. The cost is that nothing in those layers can see a
wiring fault. A JSON body that deserialises into the wrong field, a session cookie that never
authenticates, a record that is acknowledged but never appended, a matcher that is up but not
consuming, a ledger that stops writing — every one of those passes T1–T4 and breaks the
exchange.

So this layer tests the joints and nothing else:

    httpx -> gateway (auth -> validate -> risk -> reserve) -> Lua claim-and-append -> qa.inbound
          -> matcher -> qa.outbound -> ledger consumer -> Postgres -> gateway GET /portfolio

## Everything here is asynchronous, so nothing here sleeps

`POST /orders` answers **202**, not 200. The acknowledgement means *durably sequenced*, not
*filled* (Open Issue 008 section 9h). `POST /auth/register` appends `CreateAccount` and returns;
the capital grant does not exist until the matcher has forwarded `AccountCreated` and the
ledger has written the row. Every assertion about state is therefore a poll against a deadline.
A fixed `sleep` would be flaky when the stack is loaded and slow when it is not, and it would
report "the value was wrong" when the truth is "the value had not arrived yet".

## `await_state` is the deliverable, not a convenience

Success Criterion 2 is *"a failure points clearly at the layer responsible"*. Four tests cannot
satisfy that on their own — an assertion that times out says only that the number never showed
up. What names the layer is `LayerProbe`, which is read at the deadline and reports which hop
was the last one to make progress. This repository has now recorded four separate
healthy-but-idle faults (fan-out not running, the gateway a build behind, the matcher
dead-but-healthy, the bots' sessions expired), every one of them a process reporting `healthy`
while doing nothing. This layer is where that class of fault is supposed to become obvious.
"""

from __future__ import annotations

import secrets
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
import redis.asyncio as aioredis

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from config.settings import Settings  # noqa: E402
from contracts.v1.generated.contracts import (  # noqa: E402
    AccountCreated,
    OrderAccepted,
    OrderCancelled,
    OrderRejected,
    Side,
    Tif,
    unpack_any,
)
from services.gateway.streams import RECORD_FIELD  # noqa: E402

#: Every module in this package talks to a stack that is actually running. They are excluded
#: from the per-commit gate for the same reason `test_halt.py` is: they need Docker.
pytestmark = [pytest.mark.slow, pytest.mark.anyio]

GATEWAY = "http://localhost:8000"

#: How long a record is given to cross the whole path — gateway, Redis, matcher, Redis, ledger,
#: Postgres. Generous on purpose: a deadline this layer trips on a loaded stack would train the
#: reader to re-run rather than to read the failure, which is the opposite of Criterion 2.
CROSSING_TIMEOUT_S = 20.0
POLL_INTERVAL_S = 0.1


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="session")
def running_stack() -> None:
    """Refuse to run, loudly, unless a whole exchange is actually up.

    `ci.yml` runs `pytest -q -m "not property"`, so `slow` is *not* deselected from the
    per-commit gate — and CI provides only Redis and Postgres as services. There is no gateway,
    no matcher and no ledger there. Without this guard these four tests would fail on every
    push for a reason that has nothing to do with the commit.

    Skipping is the right answer and the message is the important part: it says the criterion
    was **not** verified, so a green run carrying this skip cannot be mistaken for a green run
    that proved something. That is the same contract `test_sizes.py` has for a missing C++20
    compiler and `test_session_expiry.py` has for a missing stack.
    """
    try:
        health = httpx.get(f"{GATEWAY}/health", timeout=2)
    except httpx.TransportError:
        pytest.skip(
            "the compose stack is not running — the critical path was NOT verified. "
            "Start it with: docker compose up -d --build"
        )
    if health.status_code != 200:
        pytest.skip(
            f"GET /health answered {health.status_code} — the critical path was NOT verified"
        )
    if health.json()["halted"]:
        pytest.skip(
            "the exchange is halted, so nothing can be appended — the critical path was "
            "NOT verified"
        )


@pytest.fixture(scope="session")
def settings() -> Settings:
    """The same file the gateway and the matcher hashed at startup.

    Reading the grant and the symbol table from here rather than restating them means a test
    cannot quietly disagree with the exchange about what a symbol is or what an account starts
    with — the disagreement `config_hash` exists to catch.
    """
    return Settings.load()


# --- layer probing ------------------------------------------------------------------------


@dataclass(frozen=True)
class StackPosition:
    """The last id on each stream at one instant.

    The **last id**, deliberately, and never the length. `streams.maxlen` is 2,000,000 with
    `MAXLEN ~` trimming, so on a stack that has been up for a day `XLEN` has plateaued: it
    hovers around the cap while records pour through, and two readings taken a minute apart can
    be equal, or lower, while the exchange is perfectly healthy. A probe built on length would
    call that a dead matcher. Stream ids are monotonic and never reused (Open Issue 003), so
    comparing them says what length cannot: whether anything new arrived.
    """

    inbound_id: str
    outbound_id: str


class LayerProbe:
    """Reads the stream positions either side of the matcher.

    The point is not monitoring. It is that when a poll gives up, the reader is told which hop
    stopped making progress, so the next thing they do is look at the right process.
    """

    def __init__(self, redis: aioredis.Redis, settings: Settings) -> None:
        self._redis = redis
        self._settings = settings

    async def position(self) -> StackPosition:
        return StackPosition(
            inbound_id=await self._last_id(self._settings.stream_inbound),
            outbound_id=await self._last_id(self._settings.stream_outbound),
        )

    async def _last_id(self, stream: str) -> str:
        entries = await self._redis.xrevrange(stream, count=1)
        if not entries:
            return ""
        entry_id = entries[0][0]
        return entry_id.decode() if isinstance(entry_id, bytes) else str(entry_id)

    async def answered(self, since_id: str, matches) -> bool:
        """Has the matcher emitted a record satisfying `matches` since `since_id`?

        This is the question "did the outbound stream move?" cannot answer, and the difference
        is not academic — it produced a wrong diagnosis on the first live run of this suite.
        The market makers trade continuously, so on a live stack the outbound stream is
        *always* advancing. A probe that reads that as "the matcher is fine" will blame the
        ledger for a matcher that has simply not reached our record yet, which is precisely
        what happened: a matcher restarted mid-run replayed 1,630,776 records in 110 seconds,
        answered nothing during it, and then emitted every queued answer in a burst.

        So the question asked here is about *our* record, not about traffic. Every inbound
        record produces exactly one anchor (`services/matcher/runner.py`), so the anchor's
        presence is the matcher's receipt for the thing we are waiting on.
        """
        entries = await self._redis.xrange(
            self._settings.stream_outbound, min=since_id or "-", max="+"
        )
        decoded = 0
        for _entry_id, fields in entries:
            try:
                record = unpack_any(fields[RECORD_FIELD])
            except ValueError:
                # `unpack_any` raises ValueError for a record type or a schema_version this
                # build does not know. Such a record is certainly not our anchor, so skipping
                # it is right — but the catch is narrow on purpose. A broad `except Exception`
                # here would swallow the decoding mistakes that make this probe silently
                # answer "no anchor" for every record, which is exactly the failure that had
                # it blaming the matcher for the ledger's outage.
                continue
            decoded += 1
            if matches(record):
                return True
        # Records went past and none of them decoded. That is a fault in this probe, not in the
        # exchange, and saying so is the difference between a wrong diagnosis and no diagnosis.
        assert not entries or decoded, (
            f"the probe scanned {len(entries)} outbound records since {since_id} and decoded "
            f"none of them. This is a defect in the test harness — do not read the layer blame "
            f"below as evidence about the exchange."
        )
        return False

    async def contains(self, stream: str, seq: str) -> bool:
        """Is exactly this record on this stream?

        `seq` is the Redis stream id the gateway returned, and the stream id *is* the sequence
        number (Open Issue 003) — there is no parallel counter to consult. So an acknowledgement
        can be checked against durability directly, which is the one assertion that separates
        "the gateway accepted it" from "the exchange has it".
        """
        return bool(await self._redis.xrange(stream, min=seq, max=seq, count=1))

    async def diagnose(
        self, before: StackPosition, seq: str | None, anchor=None
    ) -> str:
        """Name the layer that owes the caller an explanation.

        Read at the deadline, never before. The checks run in path order, so the first thing
        that failed to happen is the first thing reported.
        """
        now = await self.position()
        lines = [
            f"inbound  last id {before.inbound_id} -> {now.inbound_id}",
            f"outbound last id {before.outbound_id} -> {now.outbound_id}",
        ]
        if seq is not None and not await self.contains(self._settings.stream_inbound, seq):
            lines.append(
                f"BLAME: the gateway acknowledged seq {seq} but it is not on "
                f"{self._settings.stream_inbound}. The producer or the claim-and-append Lua "
                f"script is the layer at fault, not the matcher."
            )
            return "\n".join(lines)
        if anchor is not None and not await self.answered(before.outbound_id, anchor):
            lines.append(
                "BLAME: the record reached the inbound stream and THE MATCHER HAS NOT ANSWERED "
                "IT — no anchor for it on the outbound stream. Note that the matcher may still "
                "report (healthy), and that the outbound stream may well be advancing: the "
                "market makers trade continuously, so traffic proves nothing about our record. "
                "Two causes, and the log tells them apart:\n"
                "  (a) it is REPLAYING. A restarted matcher rebuilds by full replay of the "
                "retained stream, because Phase 1 has no snapshots and no checkpointing by "
                "decision (Open Issue 018 section 13.1). At the 2,000,000-record cap that is "
                "around 90 seconds during which it consumes nothing and answers nothing. Look "
                "for a `replayed N inbound records in Ts` line; if the last startup has no "
                "such line yet, it is still going and this failure is impatience, not a fault.\n"
                "  (b) it is DEAD. HANDOFF.md section 3a: CppMatcher.run() has no exception "
                "handling, so a Redis restart kills it permanently and silently.\n"
                "Check `docker compose logs matcher`."
            )
            return "\n".join(lines)
        lines.append(
            "BLAME: the matcher answered on the outbound stream but the read model never "
            "caught up. THE LEDGER CONSUMER is the layer at fault — GET /portfolio and "
            "GET /orders/open both read Postgres, which the ledger writes. Check "
            "`docker compose logs ledger`."
        )
        return "\n".join(lines)


@pytest.fixture
async def probe(running_stack, settings: Settings):
    redis = aioredis.from_url(settings.redis_url)
    try:
        yield LayerProbe(redis, settings)
    finally:
        await redis.aclose()


async def await_state(
    probe: LayerProbe,
    read,
    want,
    *,
    what: str,
    seq: str | None = None,
    anchor=None,
    since: StackPosition | None = None,
):
    """Poll `read` until `want` accepts its result, then return it.

    On timeout the message carries what was being waited for **and** which layer stopped, which
    together are Success Criterion 2. `read` is an async callable returning the observed value;
    `want` is a predicate on it; `anchor` is a predicate on a decoded outbound record that
    identifies the matcher's receipt for the specific thing being waited on. Without `anchor`
    the diagnosis cannot separate a matcher that is behind from a ledger that is stuck, so
    every caller that has one passes it.

    `since` must be a position captured **before the request was sent**, and callers that pass
    an `anchor` must pass it. The matcher answers in single-digit milliseconds on a warm stack,
    so a scan origin sampled here — after the request — routinely starts *after* the anchor was
    already written. The anchor is then not found, and the diagnosis blames the matcher for
    work it had already done. That is not a hypothetical: it is what this function did on its
    first run with the ledger deliberately stopped, naming the wrong layer with total
    confidence, which is the one thing Success Criterion 2 forbids.
    """
    before = since if since is not None else await probe.position()
    deadline = time.monotonic() + CROSSING_TIMEOUT_S
    observed = None
    while time.monotonic() < deadline:
        observed = await read()
        if want(observed):
            return observed
        await _sleep(POLL_INTERVAL_S)
    blame = await probe.diagnose(before, seq, anchor)
    raise AssertionError(
        f"timed out after {CROSSING_TIMEOUT_S:.0f}s waiting for {what}.\n"
        f"last observed: {observed!r}\n{blame}"
    )


async def _sleep(seconds: float) -> None:
    import anyio

    await anyio.sleep(seconds)


# --- anchors ---------------------------------------------------------------------------------
#
# One anchor per inbound record, and these are the three shapes this layer waits on. The set is
# `runner.py`'s, not a new one: `is_anchor` is the definition, and a predicate here that drifted
# from it would make a diagnosis confidently wrong.


def account_anchor(user_id: int):
    """The matcher's receipt for a `CreateAccount` — the forwarded, now-sequenced record."""
    return lambda record: (
        isinstance(record, AccountCreated) and record.user_id == user_id
    )


def order_anchor(client_order_id: int):
    """The receipt for a `SubmitOrder`: accepted or rejected, either way answered."""
    return lambda record: (
        isinstance(record, (OrderAccepted, OrderRejected))
        and record.client_order_id == client_order_id
    )


def cancel_anchor(client_order_id: int):
    """The receipt for a `CancelOrder`.

    `OrderCancelled` also retires an IOC remainder, which is *not* an anchor — the two are told
    apart by `reason`, which is why `CancelReason` earned its second value. Matching on the
    cancel's own `client_order_id` is enough here: an IOC remainder is retired under the
    order's id, never under the cancel's.
    """
    return lambda record: (
        isinstance(record, (OrderCancelled, OrderRejected))
        and record.client_order_id == client_order_id
    )


# --- accounts -------------------------------------------------------------------------------


@dataclass
class Account:
    """A registered, signed-in user and the client that carries its session."""

    client: httpx.AsyncClient
    user_id: int
    username: str

    #: Client order ids must be unique per user (Open Issue 008). A counter beats a random
    #: number here: the duplicate test needs to *choose* to reuse one, and a collision it did
    #: not intend would look exactly like the property under test passing.
    _next_coid: int = 1

    def coid(self) -> int:
        value = self._next_coid
        self._next_coid += 1
        return value


@pytest.fixture
async def account(probe: LayerProbe, settings: Settings):
    """A brand-new funded account, and the signed-in client to reach it with.

    Fresh per test and randomly named. The bot suite's accounts are shared and accumulate
    inventory and resting orders, which is why five of its tests fail on a used stack; nothing
    here inherits state from a previous run, so a failure is about this run.

    Waiting for the grant before yielding is not setup convenience — it proves the first half
    of the critical path (register -> CreateAccount -> matcher -> AccountCreated -> ledger ->
    Postgres) before any test does anything else. If the stack is broken, it is broken here,
    with the layer named, rather than three assertions later.
    """
    username = f"t53_{secrets.token_hex(6)}"
    password = secrets.token_urlsafe(16)
    async with httpx.AsyncClient(base_url=GATEWAY, timeout=10.0) as client:
        # Sampled before the request, so the anchor cannot already be behind the scan origin.
        since = await probe.position()
        registered = await client.post(
            "/auth/register", json={"username": username, "password": password}
        )
        assert registered.status_code == 201, (
            f"registration failed at the gateway: {registered.status_code} {registered.text}"
        )
        user_id = registered.json()["user_id"]

        # Registration does not sign you in. The cookie comes from /auth/login and the client
        # holds it for every later call — Redis-backed session cookies, no JWT (OI 015).
        signed_in = await client.post(
            "/auth/login", json={"username": username, "password": password}
        )
        assert signed_in.status_code == 200, (
            f"login failed at the gateway: {signed_in.status_code} {signed_in.text}"
        )
        assert settings.session_cookie_name in client.cookies, (
            "login returned 200 without setting the session cookie — the gateway's auth layer"
        )

        async def cash() -> int:
            response = await client.get("/portfolio")
            assert response.status_code == 200, f"GET /portfolio: {response.text}"
            return response.json()["cash_ticks"]

        await await_state(
            probe,
            cash,
            lambda observed: observed == settings.initial_cash_ticks,
            what=(
                f"the {settings.initial_cash_ticks} tick grant for {username} to reach "
                f"GET /portfolio"
            ),
            seq=registered.json()["seq"],
            anchor=account_anchor(user_id),
            since=since,
        )
        yield Account(client=client, user_id=user_id, username=username)


# --- order helpers --------------------------------------------------------------------------


def limit_order(
    *,
    client_order_id: int,
    symbol_id: int,
    side: Side,
    price_ticks: int,
    qty: int,
    tif: Tif = Tif.GTC,
) -> dict:
    return {
        "client_order_id": client_order_id,
        "symbol_id": symbol_id,
        "side": int(side),
        "tif": int(tif),
        "price_ticks": price_ticks,
        "qty": qty,
        "order_type": "limit",
    }


def market_order(*, client_order_id: int, symbol_id: int, side: Side, qty: int) -> dict:
    """A market order carries no price — the gateway derives the banded limit (Task 3.1)."""
    return {
        "client_order_id": client_order_id,
        "symbol_id": symbol_id,
        "side": int(side),
        "tif": int(Tif.IOC),
        "qty": qty,
        "order_type": "market",
    }
