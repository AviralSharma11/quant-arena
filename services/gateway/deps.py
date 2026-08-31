"""Request-scoped dependencies.

Everything is read off `request.app.state`, which is populated in the lifespan. That keeps the
app a factory — `create_app()` can be called twice to get two genuinely independent gateways,
which is exactly what the session-survives-restart test needs.
"""

from __future__ import annotations

from typing import Annotated, AsyncIterator

from fastapi import Depends, HTTPException, Request, status
from sqlmodel.ext.asyncio.session import AsyncSession

from config.settings import Settings
from services.gateway.engine_port import EnginePort
from services.gateway.idempotency import IdempotencyStore
from services.gateway.sessions import SessionStore
from services.gateway.streams import HaltState, StreamProducer


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_sessions(request: Request) -> SessionStore:
    return request.app.state.sessions


def get_idempotency(request: Request) -> IdempotencyStore:
    return request.app.state.idempotency


def get_engine(request: Request) -> EnginePort:
    return request.app.state.engine


def get_streams(request: Request) -> StreamProducer:
    return request.app.state.streams


def get_halt(request: Request) -> HaltState:
    return request.app.state.halt


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.db_sessionmaker() as session:
        yield session


async def current_user_id(
    request: Request,
    sessions: Annotated[SessionStore, Depends(get_sessions)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> int:
    session_id = request.cookies.get(settings.session_cookie_name, "")
    user_id = await sessions.user_id(session_id)
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="not authenticated"
        )
    return user_id


DbSession = Annotated[AsyncSession, Depends(get_db)]
Sessions = Annotated[SessionStore, Depends(get_sessions)]
Idempotency = Annotated[IdempotencyStore, Depends(get_idempotency)]
Config = Annotated[Settings, Depends(get_settings)]
Engine = Annotated[EnginePort, Depends(get_engine)]
CurrentUser = Annotated[int, Depends(current_user_id)]
Streams = Annotated[StreamProducer, Depends(get_streams)]
Halt = Annotated[HaltState, Depends(get_halt)]
