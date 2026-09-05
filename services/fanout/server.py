"""The fan-out service: `/stream`, and the loops behind it.

Its own process, never inside the gateway. Open Issue 006 §7b is explicit — coupling fan-out to
the sequencer degrades order acknowledgement latency under connection load, and two hundred
WebSocket connections is exactly that load. The gateway stays answerable to HTTP; this tails a
stream, rebuilds books and talks to browsers.

Four things share one event loop here:

| Task | Rate | What it does |
|---|---|---|
| `FanOut.run` | as fast as the stream arrives | outbound records into `MarketState`, and into `PrivateRouter` |
| `Conflator.run` | `market_data.conflation_hz` | encodes once per channel, offers to every subscriber |
| the halt watcher | `streams.health_poll_ms` | reads the key the gateway publishes |
| one writer per connection | whenever it is flushed | awaits the socket, and is skipped while it is still awaiting |

The last row is the important one. Sending is the only thing here that can block on a client,
so it is the only thing that gets its own task — which is what makes "a slow client affects no
other" true by construction rather than by care.

## Authentication

The session cookie, resolved through the gateway's own `SessionStore` against the same Redis
(Open Issue 015: Redis-backed sessions, no JWT). Fan-out therefore needs no auth code of its
own and cannot drift from the gateway's — and because the store is Redis, a session created by
the gateway is visible here with no call between the two processes.

§3 fixes the rest: the private stream is whatever the session owns, never a channel a client
asks for by user id. There is deliberately no code path from a subscription request to a user.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from redis.asyncio import BlockingConnectionPool, Redis

from config.settings import Settings
from config.settings import settings as default_settings
from config.startup import log_startup
from services.fanout import messages
from services.fanout.conflation import Conflator
from services.fanout.halt import HaltView
from services.fanout.private import PrivateRouter
from services.fanout.runner import FanOut
from services.fanout.subscribers import Hub, Subscriber
from services.gateway.sessions import SessionStore

LOGGER_NAME = "quant_arena.fanout"

#: Concurrent Redis connections for session lookups, and what happens past that.
#:
#: Found by `benchmarks/bench_fanout.py`, which is the only thing that could have found it:
#: two hundred browsers reconnecting at once — a fan-out restart, or a laptop waking — is two
#: hundred simultaneous session GETs, and redis-py's default pool *raises* `MaxConnectionsError`
#: when it runs out. Seventy-three of two hundred connections were refused, each of them a
#: browser told to go away because another browser was also connecting.
#:
#: A blocking pool queues instead. That is the right shape for this call: a session lookup is a
#: single GET on the same machine, so waiting a few milliseconds behind other lookups is
#: invisible, while failing is a user staring at a dead feed. The cap stays small on purpose —
#: it is a queue, not a thread pool, and a larger one would only move the wait.
SESSION_POOL_SIZE = 32
SESSION_POOL_TIMEOUT_SECONDS = 10


async def watch_halt(
    redis: Redis, view: HaltView, *, poll_ms: int, stop: asyncio.Event | None = None
) -> None:
    """Poll the key the gateway publishes, at the interval the gateway republishes it.

    Its own loop rather than a line inside the conflation tick: the tick is pure CPU and runs
    twenty times a second, and putting a Redis round trip in it would make the feed's rate
    depend on the store's latency.
    """
    interval = poll_ms / 1000
    while stop is None or not stop.is_set():
        await view.refresh(redis)
        await asyncio.sleep(interval)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or default_settings

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Two clients for the same reason the gateway keeps two: stream entries are packed
        # fixed-width records and decoding them as text would corrupt the money path silently,
        # while sessions and the halt key are text.
        stream_redis = Redis.from_url(settings.redis_url, decode_responses=False)
        redis = Redis(
            connection_pool=BlockingConnectionPool.from_url(
                settings.redis_url,
                decode_responses=True,
                max_connections=SESSION_POOL_SIZE,
                timeout=SESSION_POOL_TIMEOUT_SECONDS,
            )
        )

        app.state.settings = settings
        app.state.startup_record = log_startup("fanout", settings)
        app.state.sessions = SessionStore(redis, settings.session_ttl_seconds)
        app.state.channels = messages.known_channels(
            settings.symbols, settings.bar_bucket_seconds
        )

        hub = Hub()
        private = PrivateRouter(hub, settings)
        halt = HaltView()
        fanout = FanOut(stream_redis, settings)
        conflator = Conflator(hub=hub, state=fanout.state, settings=settings, halt=halt)
        app.state.hub, app.state.private = hub, private
        app.state.halt, app.state.fanout, app.state.conflator = halt, fanout, conflator

        # Rebuild before serving anybody. A client that connected mid-replay would receive a
        # snapshot of a book that was still half-built, and — because a snapshot is complete
        # and self-correcting — would have no way to tell it apart from a real one.
        app.state.replayed = await fanout.recover()
        logging.getLogger(LOGGER_NAME).info(
            '{"event":"fanout_recovered","records":%d,"resuming_at":"%s"}'
            % (app.state.replayed, fanout.state.last_seq)
        )
        # Only now: everything before this point is history (see `private.py`).
        fanout.on_record = lambda record, stream_id: private.route(record, stream_id=stream_id)

        halt_stop = asyncio.Event()
        halt_task = asyncio.create_task(
            watch_halt(
                redis, halt, poll_ms=settings.stream_health_poll_ms, stop=halt_stop
            ),
            name="fanout-halt-watcher",
        )
        fanout.start()
        conflator.start()
        try:
            yield
        finally:
            halt_stop.set()
            halt_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await halt_task
            await conflator.stop()
            await fanout.stop()
            for subscriber in list(hub.subscribers):
                await subscriber.stop()
            await stream_redis.aclose()
            await redis.aclose()

    app = FastAPI(title="Quant Arena Fan-Out", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings

    @app.get("/health")
    async def health() -> dict:
        """Liveness plus the two numbers that say whether the feed is actually working.

        `stream_position` and `ticks` are here rather than in a metrics endpoint because there
        is no Prometheus by decision (Open Issue 012) — the event stream is the trace, and this
        is what a container healthcheck and a human curl can both read.
        """
        state = app.state.fanout.state
        return {
            "status": "ok",
            "stream_position": state.last_seq,
            "records_applied": state.records_applied,
            "ticks": app.state.conflator.ticks,
            "subscribers": len(app.state.hub.subscribers),
            "serialisations": app.state.hub.serialisations,
            "max_tick_seconds": round(app.state.conflator.max_tick_seconds, 6),
            "exchange_halted": app.state.halt.halted,
            "config_hash": settings.config_hash,
        }

    @app.websocket("/stream")
    async def stream(websocket: WebSocket) -> None:
        await websocket.accept()
        session_id = websocket.cookies.get(settings.session_cookie_name, "")
        try:
            user_id = await app.state.sessions.user_id(session_id)
        except Exception:  # noqa: BLE001 — the session store, not the client
            # Distinguished from a missing cookie deliberately. Telling a signed-in user they
            # are unauthenticated would send the frontend to the login screen and destroy a
            # perfectly good session over a transient store failure; `halted` says "the venue
            # has a problem, hold on", which is what is actually true.
            logging.getLogger(LOGGER_NAME).exception("session lookup failed")
            await websocket.send_text(
                app.state.hub.encode(
                    messages.halted("session_store_unreachable", "cannot read the session")
                )
            )
            await websocket.close()
            return
        if user_id is None:
            # Accepted and then closed with a reason, rather than refused at the handshake: a
            # browser given a bare HTTP 403 on a WebSocket upgrade cannot read the body, so the
            # client would show "connection failed" for what is really "please log in".
            await websocket.send_text(
                app.state.hub.encode(
                    messages.error("unauthenticated", "no valid session cookie")
                )
            )
            await websocket.close()
            return

        hub: Hub = app.state.hub
        subscriber = Subscriber(user_id=user_id, send=websocket.send_text)
        subscriber.start()
        hub.add(subscriber)
        try:
            await _serve(app, websocket, subscriber)
        except WebSocketDisconnect:
            pass
        finally:
            hub.remove(subscriber)
            await subscriber.stop()
            with contextlib.suppress(Exception):
                await websocket.close()

    return app


async def _serve(app: FastAPI, websocket: WebSocket, subscriber: Subscriber) -> None:
    """Read subscription requests until the client goes away, or has to be disconnected.

    Every byte this connection sends leaves through `subscriber`, including the direct answers
    to a request. Two coroutines calling `websocket.send_text` on one socket can interleave
    their frames, and one writer per connection is also what the slow-client policy is measured
    against — a reply that bypassed it would be a send this process could block on.

    Market data never leaves from here at all. It goes out on the conflation tick and only
    there, which is what keeps "once per symbol per tick" true: a snapshot sent straight to a
    joining client would be a second encode of the same thing.
    """
    hub: Hub = app.state.hub
    known: set[str] = app.state.channels

    def reply(payload: dict) -> None:
        subscriber.offer_private(hub.encode(payload))
        subscriber.flush()

    while True:
        # Raced against the overflow signal, because a client too slow to drain its private
        # buffer is also a client that is not sending anything — waiting on `receive_text`
        # alone would leave it connected and discarding messages indefinitely.
        receiving = asyncio.ensure_future(websocket.receive_text())
        overflowing = asyncio.ensure_future(subscriber.overflowed.wait())
        done, _ = await asyncio.wait(
            {receiving, overflowing}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in (receiving, overflowing):
            if task not in done:
                task.cancel()

        if overflowing in done:
            receiving.cancel()
            # §3.6: `slow_consumer` precedes a server-initiated close, so a stalled browser tab
            # cannot apply back-pressure to this process. Sent directly — the subscriber's own
            # buffer is the thing that just overflowed.
            with contextlib.suppress(Exception):
                await websocket.send_text(
                    hub.encode(
                        messages.error("slow_consumer", "private buffer overflowed")
                    )
                )
            return

        try:
            raw = receiving.result()
        except (WebSocketDisconnect, RuntimeError, asyncio.CancelledError):
            return

        frame = _parse(raw)
        if frame is None:
            reply(messages.error("unknown_channel", "malformed request"))
            continue

        op, channels = frame
        unknown = channels - known
        if unknown:
            reply(messages.error("unknown_channel", ", ".join(sorted(unknown))))
            channels &= known
        if not channels:
            continue

        if op == "subscribe":
            hub.subscribe(subscriber, channels)
        elif op == "unsubscribe":
            hub.unsubscribe(subscriber, channels)
        else:
            reply(messages.error("unknown_channel", f"unknown op: {op}"))


def _parse(raw: str) -> tuple[str, set[str]] | None:
    """`{"op": ..., "channels": [...]}` or nothing.

    Strict about types on purpose. A client that sends `"channels": "book:QAA:l2"` would
    otherwise subscribe to eleven single-character channels, none of which exist, and get an
    `unknown_channel` naming letters.
    """
    try:
        frame = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(frame, dict):
        return None
    op = frame.get("op")
    channels = frame.get("channels")
    if not isinstance(op, str) or not isinstance(channels, list):
        return None
    if not all(isinstance(c, str) for c in channels):
        return None
    return op, set(channels)
