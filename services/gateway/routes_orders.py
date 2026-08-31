"""Order submission and cancellation.

The gateway is the single **producer** (Open Issue 007): it `XADD`s validated requests to the
inbound stream. Ordering is not computed here — it emerges from there being exactly one writer.

Open Issue 008 §9h: **this API is acknowledgement-shaped, not result-shaped.** `202` means the
request was durably recorded, not that it traded or that the order existed. `order_id` is
therefore null — the engine assigns it, and it arrives on the private stream. `seq` is the
Redis stream ID, which *is* the sequence number (Open Issue 003); it is never a counter kept
alongside.

Deliberately absent, each a later task rather than an oversight:

- **No idempotency.** A repeated `client_order_id` is not detected yet — Task 3.2.
- **No risk checks or reservations.** Nobody's cash is consulted — Task 3.1.
- **No symbol existence check.** `symbol_id` is range-checked but not looked up; the symbol
  registry arrives with Task 5.1. `RejectReason.UNKNOWN_SYMBOL` already exists for it.
- **Nothing consumes the inbound stream yet.** Dev A's naive model connects at the
  end-of-week-2 integration point (Appendix D.2).
"""

from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from config.settings import Settings
from contracts.v1.generated.contracts import CancelOrder, Side, SubmitOrder, Tif
from services.gateway.deps import Config, CurrentUser, DbSession, Idempotency, Streams
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
    price_ticks: int = Field(gt=0, le=I64_MAX)
    qty: int = Field(gt=0, le=I64_MAX)


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


@router.post("/orders", status_code=status.HTTP_202_ACCEPTED)
async def submit_order(
    body: SubmitOrderRequest,
    request: Request,
    user_id: CurrentUser,
    streams: Streams,
    settings: Config,
    idempotency: Idempotency,
) -> AcknowledgementResponse:
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
    # else: status == "in_progress" - this is either first submission or currently in flight

    # Step 2: validate available cash
    risk = request.app.state.risk
    order_cost = body.price_ticks * body.qty
    if risk.available_cash(user_id) < order_cost:
        # Reject and record the rejection
        await idempotency.record_rejected(
            user_id, body.client_order_id, "INSUFFICIENT_CASH"
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"status": "rejected", "reason": "INSUFFICIENT_CASH"},
        )

    # Step 3: reserve the cash
    order = SubmitOrder.new(
        timestamp_ns=time.time_ns(),  # gateway-assigned; the engine never reads a clock
        client_order_id=body.client_order_id,
        user_id=user_id,
        symbol_id=body.symbol_id,
        side=int(body.side),
        tif=int(body.tif),
        price_ticks=body.price_ticks,
        qty=body.qty,
    )
    risk.reserve(
        user_id=user_id,
        client_order_id=body.client_order_id,
        symbol_id=body.symbol_id,
        side=int(body.side),
        price_ticks=body.price_ticks,
        qty=body.qty,
    )

    # Step 4: append to stream (claim and append are atomic via the idempotency key)
    try:
        stream_id = await streams.append(settings.stream_inbound, order)
    except ExchangeHalted as halted:
        # The exchange cannot durably record this. Fail loudly rather than time out silently,
        # or worse, accept an order that was never written (Open Issue 003 §8.5).
        risk.reserved[user_id] = max(0, risk.reserved.get(user_id, 0) - order_cost)
        await idempotency.record_rejected(
            user_id, body.client_order_id, "EXCHANGE_HALTED"
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"status": "halted", "reason": halted.reason},
        ) from halted
    except Exception:
        risk.reserved[user_id] = max(0, risk.reserved.get(user_id, 0) - order_cost)
        await idempotency.record_rejected(
            user_id, body.client_order_id, "INTERNAL_ERROR"
        )
        raise

    # Step 5: record the acceptance
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
    user_id: CurrentUser,
    streams: Streams,
    settings: Config,
) -> AcknowledgementResponse:
    if not 0 <= target_client_order_id <= U64_MAX:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "target_client_order_id out of range")

    cancel = CancelOrder.new(
        timestamp_ns=time.time_ns(),
        client_order_id=body.client_order_id,
        user_id=user_id,
        target_client_order_id=target_client_order_id,
    )
    try:
        stream_id = await streams.append(settings.stream_inbound, cancel)
    except ExchangeHalted as halted:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"status": "halted", "reason": halted.reason},
        ) from halted

    # Accepted, not cancelled. Whether the order existed is the engine's answer, and it arrives
    # on the private stream — the gateway no longer knows and must not pretend to.
    return AcknowledgementResponse(
        client_order_id=cancel.client_order_id,
        order_id=None,
        seq=stream_id,
        status="accepted",
    )

