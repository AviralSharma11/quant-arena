"""Order submission and cancellation.

Open Issue 008 sub-decision 9h: **this API is acknowledgement-shaped, not result-shaped.**
`202` means the order was received, not that it traded. Fills arrive on the private stream,
which exists from week 5. Building the client around that from the start is cheap; retrofitting
it is not.

Deliberately absent this week, and each is a later task rather than an oversight:

- **No idempotency.** A repeated `client_order_id` is not detected yet — Task 3.2.
- **No risk checks or reservations.** Nobody's cash is consulted — Task 3.1.
- **No symbol existence check.** `symbol_id` is range-checked but not looked up; the symbol
  registry arrives with Task 5.1. `RejectReason.UNKNOWN_SYMBOL` already exists for it.
- **No sequence number.** `seq` is the Redis stream id, and there is no stream until Task 2.1.
"""

from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from contracts.v1.generated.contracts import (
    CancelOrder,
    OrderAccepted,
    OrderCancelled,
    Side,
    SubmitOrder,
    Tif,
)
from services.gateway.deps import CurrentUser, Engine

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
    order_id: int
    #: The Redis stream id, once there is a stream to append to (Task 2.1). Null until then,
    #: rather than a locally invented counter — Open Issue 003 forbids a parallel counter.
    seq: str | None
    status: str


@router.post("/orders", status_code=status.HTTP_202_ACCEPTED)
async def submit_order(
    body: SubmitOrderRequest, user_id: CurrentUser, engine: Engine
) -> AcknowledgementResponse:
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
    result = engine.submit(order)

    if isinstance(result, OrderAccepted):
        return AcknowledgementResponse(
            client_order_id=result.client_order_id,
            order_id=result.order_id,
            seq=None,
            status="accepted",
        )

    # Failed validation or a risk check, and never reached the book (Open Issue 008 section 9h).
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"status": "rejected", "reason": int(result.reason)},
    )


@router.delete("/orders/{target_client_order_id}", status_code=status.HTTP_202_ACCEPTED)
async def cancel_order(
    target_client_order_id: int,
    body: CancelOrderRequest,
    user_id: CurrentUser,
    engine: Engine,
) -> AcknowledgementResponse:
    if not 0 <= target_client_order_id <= U64_MAX:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "target_client_order_id out of range")

    cancel = CancelOrder.new(
        timestamp_ns=time.time_ns(),
        client_order_id=body.client_order_id,
        user_id=user_id,
        target_client_order_id=target_client_order_id,
    )
    result = engine.cancel(cancel)

    if isinstance(result, OrderCancelled):
        return AcknowledgementResponse(
            client_order_id=result.client_order_id,
            order_id=result.order_id,
            seq=None,
            status="cancelled",
        )

    # An unknown order is a 409 with a reason, never a 404 — so that a cancel answers the same
    # shape as a submit (contracts/v1/rest_and_ws.md section 2.2).
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"status": "rejected", "reason": int(result.reason)},
    )
