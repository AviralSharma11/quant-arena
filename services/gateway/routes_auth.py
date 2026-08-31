"""Register, login, logout.

Redis-backed session cookies, Argon2id passwords, no JWT (Open Issue 015). No email
verification — Phase 2.
"""

from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlmodel import select

from services.gateway.deps import Config, CurrentUser, DbSession, Sessions
from services.gateway.models import Account, User
from services.gateway.security import hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=8, max_length=256)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class RegisterResponse(BaseModel):
    user_id: int
    username: str
    cash_ticks: int


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(
    body: RegisterRequest, db: DbSession, settings: Config
) -> RegisterResponse:
    existing = await db.exec(select(User).where(User.username == body.username))
    if existing.first() is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "username already taken")

    now_ns = time.time_ns()
    user = User(
        username=body.username,
        password_hash=hash_password(body.password),
        created_at_ns=now_ns,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    # The virtual capital grant. Amount comes from config, not from a literal here.
    account = Account(
        user_id=user.id, cash_ticks=settings.initial_cash_ticks, created_at_ns=now_ns
    )
    db.add(account)
    await db.commit()

    return RegisterResponse(
        user_id=user.id, username=user.username, cash_ticks=account.cash_ticks
    )


@router.post("/login")
async def login(
    body: LoginRequest,
    response: Response,
    db: DbSession,
    sessions: Sessions,
    settings: Config,
) -> dict[str, int | str]:
    result = await db.exec(select(User).where(User.username == body.username))
    user = result.first()

    # One response for "no such user" and "wrong password" alike: a different answer for each
    # turns the login endpoint into a way to enumerate usernames.
    if user is None or not verify_password(user.password_hash, body.password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid credentials")

    session_id = await sessions.create(user.id)
    response.set_cookie(
        key=settings.session_cookie_name,
        value=session_id,
        max_age=settings.session_ttl_seconds,
        httponly=True,                              # JavaScript cannot read it
        secure=settings.session_cookie_secure,      # HTTPS only
        samesite=settings.session_cookie_samesite,  # other sites cannot ride on it
        path="/",
    )
    return {"user_id": user.id, "username": user.username}


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    response: Response,
    user_id: CurrentUser,
    sessions: Sessions,
    settings: Config,
) -> None:
    """Destroy the session in Redis, then clear the cookie.

    Order matters: deleting the Redis key is what actually revokes access. Clearing the cookie
    only tidies the browser, and a cookie the client kept a copy of must stop working anyway.
    """
    session_id = request.cookies.get(settings.session_cookie_name, "")
    await sessions.destroy(session_id)
    response.delete_cookie(settings.session_cookie_name, path="/")
