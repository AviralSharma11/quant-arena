"""The WebSocket server — `/stream`, against a real Redis and a real socket.

Success Criterion 3 lives here: "a client that disconnects and reconnects recovers the full
book from the next snapshot, **with no special handling**". The second half is the interesting
one. It is not a feature to be implemented; it is a property that follows from complete
snapshots, and the way to check it is to look for the absence of machinery — no resume token,
no replay-from-sequence, nothing on the wire but a second `subscribe`.

Starlette's `TestClient.websocket_connect` drives the real ASGI application: the real
authentication path, the real `Hub`, the real conflation tick. No network and no JavaScript test
framework — the decision recorded on 2026-08-31.

Redis is real too, and logical database 15 as everywhere else. Sessions are Redis-backed
(Open Issue 015), so a fake here would prove nothing about the thing that actually matters:
that a session created by the *gateway* is visible to *fan-out*, with no call between them.
"""

from __future__ import annotations

import dataclasses
import json
import secrets
import time

import pytest
import redis as redis_sync
from fastapi.testclient import TestClient

from config.settings import Settings, Symbol
from contracts.v1.generated.contracts import OrderAccepted, Side, Tif
from services.fanout.server import create_app
from services.gateway.streams import HALT_KEY, RECORD_FIELD

QAA = Symbol(symbol_id=1, name="QAA", tick_size_ticks=1, lot_size=1)
QAB = Symbol(symbol_id=2, name="QAB", tick_size_ticks=1, lot_size=1)
USER = 4242


@pytest.fixture
def server_settings(test_settings: Settings) -> Settings:
    return dataclasses.replace(
        test_settings,
        symbols=(QAA, QAB),
        # 100 Hz. The rate is configuration precisely so a test does not have to wait 50 ms per
        # frame; nothing about conflation depends on the number being 20.
        conflation_hz=100,
        stream_outbound="qa.test.outbound",
    )


@pytest.fixture
def store(server_settings: Settings):
    client = redis_sync.Redis.from_url(server_settings.redis_url)
    client.flushdb()
    # The gateway is not running in this test, and an absent halt key reads as
    # `gateway_unreachable` — which is true, and would put a halt frame in front of every
    # assertion below. A healthy state is published so that the tests that are not about halts
    # are not about halts.
    _publish_halt(client, halted=False)
    yield client
    client.flushdb()
    client.close()


def _publish_halt(client, *, halted: bool, reason: str | None = None) -> None:
    client.set(
        HALT_KEY,
        json.dumps({"halted": halted, "reason": reason, "since_ns": None, "detail": reason}),
        px=60_000,
    )


def _sign_in(client) -> str:
    session_id = secrets.token_urlsafe(16)
    client.set(f"session:{session_id}", str(USER), ex=600)
    return session_id


def _append(client, settings: Settings, record) -> None:
    client.xadd(settings.stream_outbound, {RECORD_FIELD: record.pack()})


def _accepted(order_id: int, symbol_id: int, price: int, qty: int = 5):
    return OrderAccepted.new(
        timestamp_ns=time.time_ns(), order_id=order_id, client_order_id=order_id,
        user_id=USER, price_ticks=price, qty=qty, symbol_id=symbol_id,
        side=int(Side.BUY), tif=int(Tif.GTC),
    )


def _read_until(socket, predicate, *, limit: int = 40):
    """Read frames until one satisfies `predicate`. Returns everything read.

    A tick may fire before the record arrives, so the first frame is not necessarily the
    interesting one — but the feed is continuous, so waiting for a *count* of frames would be
    flaky in the other direction.
    """
    seen = []
    for _ in range(limit):
        message = json.loads(socket.receive_text())
        seen.append(message)
        if predicate(message):
            return seen
    raise AssertionError(f"no matching frame in {len(seen)}: {seen[:5]}")


# --- authentication ------------------------------------------------------------------------------


def test_a_connection_without_a_session_is_told_why_and_closed(
    server_settings: Settings, store
):
    """Accepted and then closed with a reason, rather than refused at the handshake. A browser
    given a bare 403 on a WebSocket upgrade cannot read the body, so the client would show
    "connection failed" for what is really "please log in"."""
    with TestClient(create_app(server_settings)) as client:
        with client.websocket_connect("/stream") as socket:
            message = json.loads(socket.receive_text())
    assert message == {
        "ch": "error", "code": "unauthenticated", "detail": "no valid session cookie"
    }


def test_a_session_created_outside_this_process_is_accepted(
    server_settings: Settings, store
):
    """The whole point of Redis-backed sessions (Open Issue 015 §15a): the gateway created this
    one and fan-out honours it, with no call between the two processes and no shared memory."""
    session_id = _sign_in(store)
    with TestClient(create_app(server_settings)) as client:
        client.cookies.set(server_settings.session_cookie_name, session_id)
        with client.websocket_connect("/stream") as socket:
            socket.send_text(json.dumps({"op": "subscribe", "channels": ["book:QAA:l2"]}))
            _append(store, server_settings, _accepted(1, 1, 100))
            frames = _read_until(socket, lambda m: m.get("ch") == "book:QAA:l2")
    assert frames[-1]["bids"] == [[100, 5]]


# --- subscription filtering -----------------------------------------------------------------------


def test_a_client_receives_only_the_channels_it_subscribed_to(
    server_settings: Settings, store
):
    session_id = _sign_in(store)
    with TestClient(create_app(server_settings)) as client:
        client.cookies.set(server_settings.session_cookie_name, session_id)
        with client.websocket_connect("/stream") as socket:
            socket.send_text(json.dumps({"op": "subscribe", "channels": ["book:QAA:l2"]}))
            _append(store, server_settings, _accepted(1, 2, 200))
            _append(store, server_settings, _accepted(2, 1, 100))
            frames = _read_until(socket, lambda m: m.get("ch") == "book:QAA:l2")

    assert not any(f.get("ch", "").startswith("book:QAB") for f in frames)


def test_an_unknown_channel_is_named_rather_than_silently_ignored(
    server_settings: Settings, store
):
    """§3.6's `unknown_channel`. A typo that silently subscribed to nothing would look exactly
    like a symbol that is not trading, which is the worst possible failure to debug."""
    session_id = _sign_in(store)
    with TestClient(create_app(server_settings)) as client:
        client.cookies.set(server_settings.session_cookie_name, session_id)
        with client.websocket_connect("/stream") as socket:
            socket.send_text(
                json.dumps({"op": "subscribe", "channels": ["book:NOPE:l2", "book:QAA:l2"]})
            )
            frames = _read_until(socket, lambda m: m.get("code") == "unknown_channel")

    assert frames[-1]["detail"] == "book:NOPE:l2"


def test_the_private_channel_cannot_be_subscribed_to_by_name(
    server_settings: Settings, store
):
    """§3: the private stream is whatever the session owns, never a channel a client asks for.
    There is deliberately no path from a subscription request to a user id, so asking for
    `private` — or for somebody else's — is an unknown channel."""
    session_id = _sign_in(store)
    with TestClient(create_app(server_settings)) as client:
        client.cookies.set(server_settings.session_cookie_name, session_id)
        with client.websocket_connect("/stream") as socket:
            socket.send_text(json.dumps({"op": "subscribe", "channels": ["private"]}))
            frames = _read_until(socket, lambda m: m.get("code") == "unknown_channel")
    assert frames[-1]["detail"] == "private"


def test_unsubscribing_stops_the_channel(server_settings: Settings, store):
    session_id = _sign_in(store)
    with TestClient(create_app(server_settings)) as client:
        client.cookies.set(server_settings.session_cookie_name, session_id)
        with client.websocket_connect("/stream") as socket:
            socket.send_text(json.dumps({"op": "subscribe", "channels": ["book:QAA:l2"]}))
            _append(store, server_settings, _accepted(1, 1, 100))
            _read_until(socket, lambda m: m.get("ch") == "book:QAA:l2")

            socket.send_text(json.dumps({"op": "unsubscribe", "channels": ["book:QAA:l2"]}))
            socket.send_text(json.dumps({"op": "subscribe", "channels": ["tape:QAA"]}))
            _append(store, server_settings, _accepted(2, 1, 101))
            socket.send_text(json.dumps({"op": "subscribe", "channels": ["book:NOPE:l2"]}))
            # The error is the marker: everything the server owed before it has been sent, so
            # if a book frame were still coming it would have arrived first.
            frames = _read_until(socket, lambda m: m.get("code") == "unknown_channel")

    assert not any(f.get("ch") == "book:QAA:l2" for f in frames)


# --- Success Criterion 3 ---------------------------------------------------------------------------


def test_a_reconnect_recovers_the_whole_book_from_the_next_snapshot(
    server_settings: Settings, store
):
    """The criterion. Disconnect, miss updates, reconnect, and the first frame is complete.

    The orders arriving while the client is away are the point: under delta encoding it would
    have to be told what it missed, and here it simply is not — the next snapshot is the whole
    book, so being away costs nothing to repair.
    """
    session_id = _sign_in(store)
    app = create_app(server_settings)
    with TestClient(app) as client:
        client.cookies.set(server_settings.session_cookie_name, session_id)

        with client.websocket_connect("/stream") as socket:
            socket.send_text(json.dumps({"op": "subscribe", "channels": ["book:QAA:l2"]}))
            _append(store, server_settings, _accepted(1, 1, 100))
            _read_until(socket, lambda m: m.get("ch") == "book:QAA:l2")

        # Away. Two more orders join the book with nobody watching.
        _append(store, server_settings, _accepted(2, 1, 99))
        _append(store, server_settings, _accepted(3, 1, 98))

        with client.websocket_connect("/stream") as socket:
            socket.send_text(json.dumps({"op": "subscribe", "channels": ["book:QAA:l2"]}))
            frames = _read_until(socket, lambda m: m.get("ch") == "book:QAA:l2")

    assert frames[-1]["bids"] == [[100, 5], [99, 5], [98, 5]]


def test_nothing_on_the_wire_resumes_from_a_position(server_settings: Settings, store):
    """The "with no special handling" half, asserted as an absence.

    A recovery mechanism is a thing that can be wrong, and this one has none: the client's
    reconnect is a plain `subscribe` with no cursor in it, and `StreamClient` clears its
    sequence tracker on open rather than comparing across the gap.
    """
    session_id = _sign_in(store)
    with TestClient(create_app(server_settings)) as client:
        client.cookies.set(server_settings.session_cookie_name, session_id)
        with client.websocket_connect("/stream") as socket:
            socket.send_text(json.dumps({"op": "subscribe", "channels": ["book:QAA:l2"]}))
            _append(store, server_settings, _accepted(1, 1, 100))
            frames = _read_until(socket, lambda m: m.get("ch") == "book:QAA:l2")

    book = frames[-1]
    assert set(book) == {"ch", "seq", "ts_ns", "bids", "asks"}, "no resume field"


# --- the halt relay ----------------------------------------------------------------------------------


def test_the_halt_the_gateway_publishes_reaches_the_browser(
    server_settings: Settings, store
):
    """§3.6's `halted`, which nothing could send before this task.

    The signal is *published* by the gateway rather than inferred here. Fan-out pinging Redis
    would be a different claim — "fan-out can reach Redis" is not "the gateway can durably
    record orders", and a store that is readable but not writable separates the two in the
    direction that matters.
    """
    session_id = _sign_in(store)
    with TestClient(create_app(server_settings)) as client:
        client.cookies.set(server_settings.session_cookie_name, session_id)
        with client.websocket_connect("/stream") as socket:
            socket.send_text(json.dumps({"op": "subscribe", "channels": ["book:QAA:l2"]}))
            _publish_halt(store, halted=True, reason="redis_unreachable")
            frames = _read_until(socket, lambda m: m.get("code") == "halted", limit=200)

            _publish_halt(store, halted=False)
            resumed = _read_until(socket, lambda m: m.get("code") == "resumed", limit=200)

    assert frames[-1]["detail"] == "redis_unreachable"
    assert resumed[-1]["ch"] == "error"


def test_a_halt_is_sent_once_per_transition_and_not_once_per_tick(
    server_settings: Settings, store
):
    """Twenty identical `halted` frames a second would be an outage of its own. The state does
    not change between ticks, so neither does the wire."""
    session_id = _sign_in(store)
    with TestClient(create_app(server_settings)) as client:
        client.cookies.set(server_settings.session_cookie_name, session_id)
        with client.websocket_connect("/stream") as socket:
            socket.send_text(json.dumps({"op": "subscribe", "channels": ["book:QAA:l2"]}))
            _publish_halt(store, halted=True, reason="redis_unreachable")
            _read_until(socket, lambda m: m.get("code") == "halted", limit=200)
            # Give several ticks and several halt polls a chance to repeat it.
            _append(store, server_settings, _accepted(1, 1, 100))
            frames = _read_until(socket, lambda m: m.get("ch") == "book:QAA:l2", limit=200)

    assert [f for f in frames if f.get("code") == "halted"] == []


# --- health -------------------------------------------------------------------------------------


def test_health_reports_the_feed_moving_not_merely_the_process_being_up(
    server_settings: Settings, store
):
    """A container healthcheck that only proved the process was alive would go green over a
    fan-out that had stopped reading the stream — which is exactly the failure that took a
    week to notice in `RiskState.watch_stream`."""
    with TestClient(create_app(server_settings)) as client:
        _append(store, server_settings, _accepted(1, 1, 100))
        for _ in range(50):
            body = client.get("/health").json()
            if body["records_applied"] and body["ticks"]:
                break
            time.sleep(0.02)

    assert body["status"] == "ok"
    assert body["records_applied"] >= 1
    assert body["ticks"] >= 1
    assert body["config_hash"] == server_settings.config_hash
