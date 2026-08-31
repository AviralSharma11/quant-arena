"""The gateway application.

`create_app()` is a factory on purpose. Success Criterion 2 — the session survives a restart of
the application process — is tested by building one app, logging in, tearing it down, building
a second one against the same Redis, and reusing the cookie. That is only possible if nothing
about a session lives on the app object.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from config.settings import Settings
from config.settings import settings as default_settings
from services.engine_stub import StubEngine
from services.gateway import models  # noqa: F401  — registers tables on SQLModel.metadata
from services.gateway.engine_port import EnginePort
from services.gateway.routes_auth import router as auth_router
from services.gateway.routes_orders import router as orders_router
from services.gateway.sessions import SessionStore


def create_app(
    settings: Settings | None = None, engine: EnginePort | None = None
) -> FastAPI:
    settings = settings or default_settings

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        redis = Redis.from_url(settings.redis_url, decode_responses=True)
        db = create_async_engine(settings.database_url, pool_pre_ping=True)
        async with db.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)

        app.state.settings = settings
        app.state.redis = redis
        app.state.sessions = SessionStore(redis, settings.session_ttl_seconds)
        app.state.db_sessionmaker = async_sessionmaker(
            db, class_=AsyncSession, expire_on_commit=False
        )
        # The one load-bearing stub (Appendix D.2). Swapped at the end of week 2.
        app.state.engine = engine or StubEngine()
        try:
            yield
        finally:
            await redis.aclose()
            await db.dispose()

    app = FastAPI(title="Quant Arena Gateway", version="0.1.0", lifespan=lifespan)

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

    @app.get("/health", tags=["ops"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(auth_router)
    app.include_router(orders_router)
    return app


app = create_app()
