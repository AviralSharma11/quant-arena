"""The gateway application.

`create_app()` is a factory on purpose. Success Criterion 2 — the session survives a restart of
the application process — is tested by building one app, logging in, tearing it down, building
a second one against the same Redis, and reusing the cookie. That is only possible if nothing
about a session lives on the app object.
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from config.settings import Settings
from config.settings import settings as default_settings
from config.startup import log_startup
from contracts.v1.generated.contracts import ConfigureReplay
from services import checkpoint
from services.gateway import models  # noqa: F401  — registers tables on SQLModel.metadata
from services.gateway.idempotency import IdempotencyStore
from services.gateway.ratelimit import RateLimiter
from services.gateway.risk import RiskState
from services.gateway.routes_auth import router as auth_router
from services.gateway.routes_orders import router as orders_router
from services.gateway.routes_backtests import router as backtests_router
from services.gateway.routes_symbols import router as symbols_router
from services.gateway.sessions import SessionStore
from services.gateway.streams import (
    UNREACHABLE,
    ExchangeHalted,
    HaltReason,
    HaltState,
    StreamProducer,
    watch_health,
)

logger = logging.getLogger(__name__)

#: This process's checkpoint name, as listed in `[checkpoint].outbound_readers`.
GATEWAY_PROCESS = "gateway"

REPLAY_CONFIGURATION_CLIENT_ORDER_ID = 0


def _replay_configuration(settings: Settings) -> ConfigureReplay:
    digest = bytes.fromhex(settings.config_hash)
    if len(digest) < 16:
        raise ValueError("configuration hash must contain at least 16 bytes")
    return ConfigureReplay.new(
        timestamp_ns=time.time_ns(),
        client_order_id=REPLAY_CONFIGURATION_CLIENT_ORDER_ID,
        real_seconds_per_simulated_minute=settings.replay_real_seconds_per_simulated_minute,
        config_hash_hi=int.from_bytes(digest[:8], "big"),
        config_hash_lo=int.from_bytes(digest[8:16], "big"),
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or default_settings

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        redis = Redis.from_url(settings.redis_url, decode_responses=True)
        # A second client, deliberately NOT decoding responses. Stream entries carry packed
        # fixed-width records; decoding them as text would corrupt the money path silently.
        stream_redis = Redis.from_url(settings.redis_url, decode_responses=False)
        db = create_async_engine(settings.database_url, pool_pre_ping=True)
        async with db.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)

        app.state.settings = settings
        # Task 1.4 criterion 3: the configuration hash appears in every process's startup log.
        app.state.startup_record = log_startup("gateway", settings)
        app.state.redis = redis
        app.state.sessions = SessionStore(redis, settings.session_ttl_seconds)
        app.state.idempotency = IdempotencyStore(redis, settings.idempotency_ttl_seconds)
        # Admission control, ahead of every other check on the order path. Process memory,
        # like RiskState and for the same reasons (Open Issue 004): hot path, single producer.
        app.state.ratelimit = RateLimiter(settings.max_orders_per_second)
        app.state.db_sessionmaker = async_sessionmaker(
            db, class_=AsyncSession, expire_on_commit=False
        )
        async with app.state.db_sessionmaker() as session:
            app.state.risk = await RiskState.from_db(session, settings)
        # Rebuild reservations and balances by replaying the outbound stream, and finish
        # before the first request is served. Task 3.1 Success Criterion 4: restarting the
        # gateway rebuilds reservations identically. Starting the watcher and yielding would
        # leave a window in which the gateway is answering with every commitment forgotten,
        # so an account could spend the same ticks twice in the first moments after a restart.
        #
        # From the gateway's checkpoint when it has one (Open Issue 020): balances and
        # commitments as of a stream id, then only the records after it. Market makers still
        # come from the database above — they are configuration, not replayed state. A stream
        # trimmed past the resume point raises here and the gateway does not start: serving
        # orders against balances rebuilt from a partial history is the failure 020 exists for.
        saved = await checkpoint.resume_positions(
            stream_redis, GATEWAY_PROCESS, [settings.stream_outbound], config_hash=settings.config_hash
        )
        if saved is not None:
            app.state.risk.load_state(saved.state)
            app.state.risk.last_seq = saved.positions[settings.stream_outbound]
        app.state.risk_resumed_from_checkpoint = saved is not None
        app.state.risk_replayed = await app.state.risk.replay_from_stream(
            stream_redis, settings.stream_outbound
        )

        async def save_risk_checkpoint() -> None:
            # `dump_state` is synchronous, so no request can interleave with it; the save
            # afterwards may, and does not need to — the bytes are already one moment.
            state = app.state.risk.dump_state()
            position = app.state.risk.last_seq
            await checkpoint.save(
                stream_redis,
                GATEWAY_PROCESS,
                positions={settings.stream_outbound: position},
                state=state,
                config_hash=settings.config_hash,
            )

        await save_risk_checkpoint()

        app.state.halt = HaltState()
        app.state.streams = StreamProducer(
            stream_redis,
            app.state.halt,
            batch_max=settings.stream_batch_max,
        )
        app.state.streams.start()
        # Stamp the configuration into the same total order as orders. The matcher forwards this
        # record, so recovery can count it as the anchor for the inbound startup record.
        app.state.replay_config_seq = await app.state.streams.append(
            settings.stream_inbound,
            _replay_configuration(settings),
        )
        risk_stop = asyncio.Event()
        app.state.risk_task = asyncio.create_task(
            app.state.risk.watch_stream(
                stream_redis,
                settings.stream_outbound,
                poll_ms=settings.stream_health_poll_ms,
                stop=risk_stop,
            ),
            name="risk-stream-watcher",
        )
        async def checkpoint_forever() -> None:
            while True:
                await asyncio.sleep(settings.checkpoint_interval_ms / 1000)
                try:
                    await save_risk_checkpoint()
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001 — a missed checkpoint pins trimming, no more
                    logger.exception("gateway: risk checkpoint failed; will retry")

        checkpoint_task = asyncio.create_task(checkpoint_forever(), name="risk-checkpoint")
        health_stop = asyncio.Event()
        health_task = asyncio.create_task(
            watch_health(
                stream_redis,
                app.state.halt,
                poll_ms=settings.stream_health_poll_ms,
                stop=health_stop,
            ),
            name="halt-watchdog",
        )
        try:
            yield
        finally:
            checkpoint_task.cancel()
            try:
                await checkpoint_task
            except (asyncio.CancelledError, Exception):
                pass
            risk_stop.set()
            app.state.risk_task.cancel()
            try:
                await app.state.risk_task
            except (asyncio.CancelledError, Exception):
                pass
            health_stop.set()
            health_task.cancel()
            try:
                await health_task
            except (asyncio.CancelledError, Exception):
                pass
            await app.state.streams.stop()
            await stream_redis.aclose()
            await redis.aclose()
            await db.dispose()

    app = FastAPI(title="Quant Arena Gateway", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings

    @app.exception_handler(RequestValidationError)
    async def malformed_request(_, exc: RequestValidationError) -> JSONResponse:
        """FastAPI answers 422 by default; the contract says 400.

        Open Issue 008 section 9h lists 400 for "malformed, or missing client_order_id", and
        Success Criterion 3 of Task 1.3 requires it explicitly.
        """
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": jsonable_encoder(exc.errors())},
        )

    def _halted_response(request, detail: str) -> JSONResponse:
        halt: HaltState = request.app.state.halt
        halt.halt(HaltReason.REDIS_UNREACHABLE, detail)
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"detail": {"status": "halted", "reason": halt.reason}},
        )

    for _unreachable in UNREACHABLE:

        @app.exception_handler(_unreachable)
        async def redis_unreachable(request: Request, exc: Exception) -> JSONResponse:
            """Redis is on the critical path, so its absence must fail loudly everywhere.

            Not only on the order path: `current_user_id` reads the session from Redis and runs
            *before* the order handler, so without this a halted exchange answered `500` on
            `POST /orders` while `/health` correctly reported the halt. Open Issue 003 §8.5
            requires a clear reason, and a 500 is not one.
            """
            return _halted_response(request, str(exc))

    @app.exception_handler(ExchangeHalted)
    async def exchange_halted(request: Request, exc: ExchangeHalted) -> JSONResponse:
        return _halted_response(request, exc.reason)

    @app.get("/health", tags=["ops"])
    async def health(request: Request) -> dict[str, object]:
        """Liveness, plus the halt state.

        Open Issue 003 §8.5 requires that an unreachable Redis be *visible* rather than a
        silent timeout, so the halt is surfaced here and not only in order rejections.
        """
        halt: HaltState = request.app.state.halt
        return {"status": "halted" if halt.halted else "ok", **halt.as_dict()}

    app.include_router(auth_router)
    app.include_router(orders_router)
    app.include_router(symbols_router)
    app.include_router(backtests_router)
    return app


app = create_app()
