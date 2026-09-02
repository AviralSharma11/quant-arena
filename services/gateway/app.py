"""The gateway application.

`create_app()` is a factory on purpose. Success Criterion 2 — the session survives a restart of
the application process — is tested by building one app, logging in, tearing it down, building
a second one against the same Redis, and reusing the cookie. That is only possible if nothing
about a session lives on the app object.
"""

from __future__ import annotations

import asyncio
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
from services.gateway import models  # noqa: F401  — registers tables on SQLModel.metadata
from services.gateway.idempotency import IdempotencyStore
from services.gateway.risk import RiskState
from services.gateway.routes_auth import router as auth_router
from services.gateway.routes_orders import router as orders_router
from services.gateway.sessions import SessionStore
from services.gateway.streams import (
    UNREACHABLE,
    ExchangeHalted,
    HaltReason,
    HaltState,
    StreamProducer,
    watch_health,
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
        app.state.db_sessionmaker = async_sessionmaker(
            db, class_=AsyncSession, expire_on_commit=False
        )
        async with app.state.db_sessionmaker() as session:
            app.state.risk = await RiskState.from_db(session)

        app.state.halt = HaltState()
        app.state.streams = StreamProducer(
            stream_redis,
            app.state.halt,
            maxlen=settings.stream_maxlen,
            batch_max=settings.stream_batch_max,
        )
        app.state.streams.start()
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
    return app


app = create_app()
