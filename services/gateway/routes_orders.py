"""Order submission and cancellation.

The gateway is the single **producer** (Open Issue 007): it `XADD`s validated requests to the
inbound stream. Ordering is not computed here — it emerges from there being exactly one writer.

Open Issue 008 §9h: **this API is acknowledgement-shaped, not result-shaped.** `202` means the
request was durably recorded, not that it traded or that the order existed. `order_id` is
therefore null — the engine assigns it, and it arrives on the private stream. `seq` is the
Redis stream ID, which *is* the sequence number (Open Issue 003); it is never a counter kept
alongside.

Validation is now complete on this path: idempotency (3.2), risk and reservations (3.1), the
market-order band (3.1), and — from week 4 — the symbol lookup, against the registry
`GET /symbols` serves. The symbol table itself is still provisional; Task 5.1 replaces it with
ten symbols drawn from replayed crypto history, and this code does not change when it does.

The inbound stream is consumed by `services/matcher`, which wraps Dev A's naive model — the
end-of-week-2 integration point (Appendix D.2). The stub engine is gone.
"""

from __future__ import annotations

import time
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field, model_validator

from config.settings import Settings
from contracts.v1.generated.contracts import CancelOrder, Side, SubmitOrder, Tif
from services.gateway.deps import (
    Config,
    CurrentUser,
    DbSession,
    Idempotency,
    RateLimit,
    Streams,
)
from services.gateway.ratelimit import RateLimiter
from services.gateway.streams import ExchangeHalted

router = APIRouter(tags=["orders"])

# Wire limits, straight from the frozen contract's integer types.
U64_MAX = 2**64 - 1
I64_MAX = 2**63 - 1
I16_MAX = 2**15 - 1


class SubmitOrderRequest(BaseModel):
    #: Mandatory (Open Issue 008 sub-decision 9g). Absent or out of range is a 400 before any
    #: other processing, which is what makes the field a usable idempotency key in week 3.
    client_order_id: int = Field(ge=0, le=U64_MAX)
    symbol_id: int = Field(ge=0, le=I16_MAX)
    side: Side
    tif: Tif
    #: A limit price of zero or less is not an order. RejectReason.INVALID_PRICE exists for it.
    #: Omitted for a market order, where the gateway derives the banded limit instead.
    price_ticks: int | None = Field(default=None, gt=0, le=I64_MAX)
    qty: int = Field(gt=0, le=I64_MAX)
    #: "limit" (the default) or "market". A market order never reaches the engine as such —
    #: the contract has no market order type, and the engine is deliberately simple. The
    #: gateway converts it to a marketable limit order with a price band (Task 3.1).
    order_type: Literal["limit", "market"] = "limit"

    @model_validator(mode="after")
    def _price_matches_order_type(self) -> "SubmitOrderRequest":
        if self.order_type == "limit" and self.price_ticks is None:
            raise ValueError("price_ticks is required for a limit order")
        if self.order_type == "market" and self.price_ticks is not None:
            raise ValueError(
                "price_ticks must be omitted for a market order — the band sets it"
            )
        return self


class CancelOrderRequest(BaseModel):
    client_order_id: int = Field(ge=0, le=U64_MAX)


class AcknowledgementResponse(BaseModel):
    client_order_id: int
    #: Engine-assigned, so the gateway does not know it at append time. Null here; it arrives
    #: on the private stream. Open Issue 008 §9h: this API is acknowledgement-shaped.
    order_id: int | None
    #: The Redis stream id — which *is* the sequence number (Open Issue 003). Never a counter
    #: the gateway keeps alongside it.
    seq: str
    status: str


class OpenOrderItem(BaseModel):
    order_id: int
    client_order_id: int
    user_id: int
    symbol_id: int
    side: int
    price_ticks: int
    qty: int
    remaining_qty: int
    tif: int
    created_at_ns: int


class PositionItem(BaseModel):
    symbol_id: int
    qty: int


class PortfolioResponse(BaseModel):
    user_id: int
    cash_ticks: int
    positions: list[PositionItem]


def _enforce_rate_limit(limiter: RateLimiter, user_id: int) -> None:
    """Admission control, ahead of every other check — including the idempotency claim.

    Order matters. A request rejected *after* claiming its key would leave that
    `client_order_id` permanently answered "rejected: RATE_LIMITED", and the client's correct
    retry of the very same intent would keep getting that stored answer back for the whole TTL.
    A 429 means "not processed, ask again", so nothing about it may be recorded against the key.
    """
    if limiter.allow(user_id):
        return
    raise HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail={"status": "rate_limited", "reason": "MAX_ORDERS_PER_SECOND"},
        headers={"Retry-After": str(max(1, round(limiter.retry_after_seconds(user_id))))},
    )


def _abandon(risk, user_id: int, client_order_id: int) -> None:
    """Undo a reservation for an order that was never appended.

    Two things had to be undone and only one was: the committed resource, and the pending
    entry itself. Leaving the entry behind meant a later `OrderAccepted` — for a *different*
    order that happened to reuse the id after the idempotency TTL — could adopt a reservation
    belonging to an order that never existed.

    It also poked `reserved` directly, which is right for a buy and silently wrong for a sell.
    Going through `release` means the resource is chosen by the reservation, not by the caller.
    """
    reservation = risk.pending_by_client.pop((user_id, client_order_id), None)
    if reservation is not None:
        risk.release(reservation, reservation.qty)


@router.post("/orders", status_code=status.HTTP_202_ACCEPTED)
async def submit_order(
    body: SubmitOrderRequest,
    request: Request,
    user_id: CurrentUser,
    streams: Streams,
    settings: Config,
    idempotency: Idempotency,
    limiter: RateLimit,
) -> AcknowledgementResponse:
    # Step 0: admission control. Bots are subject to this exactly as a browser is —
    # Task 4.4 forbids special-casing them anywhere in the gateway.
    _enforce_rate_limit(limiter, user_id)

    # Step 1: claim the idempotency key
    outcome = await idempotency.claim(user_id, body.client_order_id)

    # If this is a duplicate (not in_progress), return the stored outcome
    if outcome.status == "accepted":
        # Retry after acceptance: return the same order_id and seq
        return AcknowledgementResponse(
            client_order_id=body.client_order_id,
            order_id=outcome.order_id,
            seq=outcome.seq,
            status="accepted",
        )
    elif outcome.status == "rejected":
        # Retry after rejection: return the same rejection
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"status": "rejected", "reason": outcome.reason},
        )
    # Otherwise the status is in_progress.
    # A retry that arrived while the original is still in flight. It must not submit
    # alongside it: a duplicate here is a real trade against a real counterparty whose
    # position also moved, and it cannot be undone without unwinding someone else's fill
    # (Open Issue 008). The client is told to ask again, not answered with a second order.
    if not outcome.claimed:
        return AcknowledgementResponse(
            client_order_id=body.client_order_id,
            order_id=None,
            seq="",
            status="in_progress",
        )

    risk = request.app.state.risk

    # Step 2: the symbol must exist. `symbol_id` was only ever range-checked against the
    # contract's i16 until now; the registry that makes a lookup possible arrived with
    # `GET /symbols` in week 4. The check lives here and not in the matcher because the matcher
    # is money-blind and registry-free by design (`services/matcher/adapter.py`), and because a
    # rejection recorded against the idempotency key is one the client can retry into.
    if body.symbol_id not in {symbol.symbol_id for symbol in settings.symbols}:
        await idempotency.record_rejected(
            user_id, body.client_order_id, "UNKNOWN_SYMBOL"
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"status": "rejected", "reason": "UNKNOWN_SYMBOL"},
        )

    # Step 3: a market order becomes a marketable limit order, banded off the opposing side.
    price_ticks = body.price_ticks
    tif = int(body.tif)
    if body.order_type == "market":
        price_ticks = risk.banded_market_price(
            symbol_id=body.symbol_id,
            side=int(body.side),
            band_bps=settings.market_order_band_bps,
        )
        if price_ticks is None:
            # Nothing resting on the other side. There is no reference price, so there is no
            # band, so there is no safe price to send — Success Criterion 6 is precisely that
            # a market order into a thin book cannot execute outside its band.
            await idempotency.record_rejected(
                user_id, body.client_order_id, "INVALID_PRICE"
            )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"status": "rejected", "reason": "INVALID_PRICE"},
            )
        # A market order that rested would not be a market order. Anything it cannot trade
        # against the book on arrival is cancelled rather than left sitting at the band price.
        tif = int(Tif.IOC)

    # Step 4: validate the resource this side actually consumes — cash for a buy, inventory
    # for a sell. `RiskState` owns the rule so that the check and the accounting that
    # implements it cannot drift apart.
    reason = risk.reject_reason_for(
        user_id=user_id,
        symbol_id=body.symbol_id,
        side=int(body.side),
        price_ticks=price_ticks,
        qty=body.qty,
    )
    if reason is not None:
        await idempotency.record_rejected(user_id, body.client_order_id, reason.name)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"status": "rejected", "reason": reason.name},
        )

    # Step 5: reserve the cash, at the price that will actually be sent
    order = SubmitOrder.new(
        timestamp_ns=time.time_ns(),  # gateway-assigned; the engine never reads a clock
        client_order_id=body.client_order_id,
        user_id=user_id,
        symbol_id=body.symbol_id,
        side=int(body.side),
        tif=tif,
        price_ticks=price_ticks,
        qty=body.qty,
    )
    risk.reserve(
        user_id=user_id,
        client_order_id=body.client_order_id,
        symbol_id=body.symbol_id,
        side=int(body.side),
        price_ticks=price_ticks,
        qty=body.qty,
    )

    # Step 6: append to stream (claim and append are atomic via the idempotency key)
    try:
        stream_id = await streams.append(settings.stream_inbound, order)
    except ExchangeHalted as halted:
        # The exchange cannot durably record this. Fail loudly rather than time out silently,
        # or worse, accept an order that was never written (Open Issue 003 §8.5).
        _abandon(risk, user_id, body.client_order_id)
        await idempotency.record_rejected(
            user_id, body.client_order_id, "EXCHANGE_HALTED"
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"status": "halted", "reason": halted.reason},
        ) from halted
    except Exception:
        _abandon(risk, user_id, body.client_order_id)
        await idempotency.record_rejected(
            user_id, body.client_order_id, "INTERNAL_ERROR"
        )
        raise

    # Step 7: record the acceptance
    # Note: order_id is None because the engine assigns it; we store None (not 0)
    await idempotency.record_accepted(
        user_id, body.client_order_id, None, stream_id
    )

    return AcknowledgementResponse(
        client_order_id=order.client_order_id,
        order_id=None,
        seq=stream_id,
        status="accepted",
    )


# --- Re-synchronisation endpoints (Open Issue 014 §14e) --------------------------------------
#
# IMPORTANT: GET /orders/open MUST be registered BEFORE DELETE /orders/{target_client_order_id}.
# FastAPI route matching would otherwise treat "open" as the target_client_order_id path parameter
# of the delete route, rejecting GET /orders/open with a 405 Method Not Allowed error.


@router.get("/orders/open")
async def get_open_orders(user_id: CurrentUser, db: DbSession) -> list[OpenOrderItem]:
    """Retrieve open resting orders for the current user for stream gap re-synchronisation."""
    from services.gateway.models import OpenOrder
    from sqlmodel import select

    result = await db.exec(
        select(OpenOrder).where(OpenOrder.user_id == user_id).order_by(OpenOrder.order_id)
    )
    return [
        OpenOrderItem(
            order_id=o.order_id,
            client_order_id=o.client_order_id,
            user_id=o.user_id,
            symbol_id=o.symbol_id,
            side=o.side,
            price_ticks=o.price_ticks,
            qty=o.qty,
            remaining_qty=o.remaining_qty,
            tif=o.tif,
            created_at_ns=o.created_at_ns,
        )
        for o in result.all()
    ]


@router.get("/portfolio")
async def get_portfolio(user_id: CurrentUser, db: DbSession) -> PortfolioResponse:
    """Retrieve settled cash and positions for the current user for stream gap re-sync."""
    from services.gateway.models import Account, Position
    from sqlmodel import select

    acc_result = await db.exec(select(Account).where(Account.user_id == user_id))
    acc = acc_result.first()
    cash = acc.cash_ticks if acc is not None else 0

    pos_result = await db.exec(select(Position).where(Position.user_id == user_id))
    positions = [
        PositionItem(symbol_id=p.symbol_id, qty=p.qty)
        for p in pos_result.all()
        if p.qty != 0
    ]
    return PortfolioResponse(user_id=user_id, cash_ticks=cash, positions=positions)


@router.delete("/orders/{target_client_order_id}", status_code=status.HTTP_202_ACCEPTED)
async def cancel_order(
    target_client_order_id: int,
    body: CancelOrderRequest,
    request: Request,
    user_id: CurrentUser,
    streams: Streams,
    settings: Config,
    idempotency: Idempotency,
    limiter: RateLimit,
) -> AcknowledgementResponse:
    _enforce_rate_limit(limiter, user_id)

    if not 0 <= target_client_order_id <= U64_MAX:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "target_client_order_id out of range")

    # Step 1: claim the idempotency key for the cancel
    outcome = await idempotency.claim(user_id, body.client_order_id)

    # If this is a duplicate cancel (not in_progress), return the stored outcome
    if outcome.status == "accepted":
        # Retry after acceptance: return the same seq
        return AcknowledgementResponse(
            client_order_id=body.client_order_id,
            order_id=None,
            seq=outcome.seq,
            status="accepted",
        )
    elif outcome.status == "rejected":
        # Retry after rejection: return the same rejection
        # (Note: cancels are no-ops so rejections are rare, but handle for uniformity)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"status": "rejected", "reason": outcome.reason},
        )
    # Otherwise the status is in_progress.
    # A retry that arrived while the original is still in flight. It must not submit
    # alongside it: a duplicate here is a real trade against a real counterparty whose
    # position also moved, and it cannot be undone without unwinding someone else's fill
    # (Open Issue 008). The client is told to ask again, not answered with a second order.
    if not outcome.claimed:
        return AcknowledgementResponse(
            client_order_id=body.client_order_id,
            order_id=None,
            seq="",
            status="in_progress",
        )

    # Step 2: append the cancel to the stream
    cancel = CancelOrder.new(
        timestamp_ns=time.time_ns(),
        client_order_id=body.client_order_id,
        user_id=user_id,
        target_client_order_id=target_client_order_id,
    )
    try:
        stream_id = await streams.append(settings.stream_inbound, cancel)
    except ExchangeHalted as halted:
        # Record the failure
        await idempotency.record_rejected(
            user_id, body.client_order_id, "EXCHANGE_HALTED"
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"status": "halted", "reason": halted.reason},
        ) from halted
    except Exception:
        await idempotency.record_rejected(
            user_id, body.client_order_id, "INTERNAL_ERROR"
        )
        raise

    # Step 3: record the acceptance
    await idempotency.record_accepted(
        user_id, body.client_order_id, None, stream_id
    )

    # Accepted, not cancelled. Whether the order existed is the engine's answer, and it arrives
    # on the private stream — the gateway no longer knows and must not pretend to.
    return AcknowledgementResponse(
        client_order_id=cancel.client_order_id,
        order_id=None,
        seq=stream_id,
        status="accepted",
    )

