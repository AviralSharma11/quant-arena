"""Register, login, logout.

Redis-backed session cookies, Argon2id passwords, no JWT (Open Issue 015). No email
verification — Phase 2.

## Registration writes to two places, and only one of them is the stream

Open Issue 004 makes the event stream authoritative for **money** and PostgreSQL a derived read
model. It says nothing about credentials, and the distinction is deliberate here:

| | Where it is written | Why |
|---|---|---|
| `users` — username, Argon2id hash | **PostgreSQL, directly** | Not money, and a password hash in a replayable retained log is a hazard with no upside. There is no `User` record in `schema.toml` and there must not be |
| the cash grant | **`CreateAccount` on the inbound stream** | It is money. The gateway writing an `accounts` row directly made PostgreSQL the source of truth for a balance, which is exactly what Open Issue 004 forbids |

Until week 4 the grant was written straight into `accounts`, which passed every test while
leaving `LedgerConsumer` unable to run: the ledger rebuilds `accounts` from the stream, and
replaying a stream that never carried the grant would have reset every balance to zero. The
consequence was that `/portfolio` never moved after a fill.

**Registration is therefore acknowledgement-shaped, exactly like `POST /orders`.** The response
carries no `cash_ticks`, because at the moment it is written the gateway genuinely does not
know one: the grant amount is applied by the matcher forwarding `CreateAccount` as
`AccountCreated`, and reaches `accounts` when the ledger projects it. A number in the response
would be the gateway asserting a balance it has not observed.
"""

from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlmodel import select

from contracts.v1.generated.contracts import CreateAccount
from services.gateway.deps import Config, CurrentUser, DbSession, Sessions, Streams
from services.gateway.models import User
from services.gateway.security import hash_password, verify_password
from services.gateway.streams import ExchangeHalted

router = APIRouter(prefix="/auth", tags=["auth"])

#: `CreateAccount` carries a `client_order_id` because every inbound record does — it is the
#: idempotency key. A user is created exactly once and this is their first request, so zero is
#: the natural key rather than a counter the gateway would have to keep. It cannot collide:
#: `user_id` is freshly allocated by PostgreSQL and the pair (user, key) is what identifies a
#: request (Open Issue 008).
ACCOUNT_CREATION_CLIENT_ORDER_ID = 0


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=8, max_length=256)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class RegisterResponse(BaseModel):
    user_id: int
    username: str
    #: The Redis stream id of the `CreateAccount` record — which *is* its sequence number
    #: (Open Issue 003). A client that wants to know when the grant has landed watches for this
    #: position on the private stream; it does not poll `/portfolio`.
    seq: str


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(
    body: RegisterRequest,
    request: Request,
    db: DbSession,
    settings: Config,
    streams: Streams,
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

    # A designated market maker is permitted to hold negative inventory; a retail account is
    # not (Open Issue 005 section 10.6, and Task 4.4's Boundaries). The privilege is granted by
    # `bots.designated_market_maker_accounts` in the shared configuration and nowhere else —
    # notably not by anything in this request, so it cannot be claimed by registering under a
    # chosen name. `RiskState.from_db` resolves the same list on a restart.
    if body.username in settings.designated_market_maker_accounts:
        request.app.state.risk.market_makers.add(user.id)

    # The virtual capital grant, as an event rather than a row. The amount is deliberately
    # absent from the record: `CreateAccount` has no cash field, because the engine is
    # money-blind (Open Issue 001). The matcher reads `initial_cash_ticks` from the shared
    # configuration when it forwards `AccountCreated` — the same file, hashed into both
    # processes' startup lines, so a gateway and a matcher that disagreed would announce it.
    create = CreateAccount.new(
        timestamp_ns=now_ns,
        client_order_id=ACCOUNT_CREATION_CLIENT_ORDER_ID,
        user_id=user.id,
    )
    try:
        seq = await streams.append(settings.stream_inbound, create)
    except ExchangeHalted as halted:
        # The user row is already committed, and a user who can log in but has no account is
        # worse than a failed registration — every order they place would be rejected for
        # insufficient cash with nothing to explain why. So the row is withdrawn and the
        # caller is told to try again, which is a state they can act on.
        await db.delete(user)
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"status": "halted", "reason": halted.reason},
        ) from halted

    return RegisterResponse(user_id=user.id, username=user.username, seq=seq)


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
