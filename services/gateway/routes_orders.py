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

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from config.settings import Settings
from contracts.v1.generated.contracts import CancelOrder, Side, SubmitOrder, Tif
from services.gateway.deps import Config, CurrentUser, Streams
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


@router.post("/orders", status_code=status.HTTP_202_ACCEPTED)
async def submit_order(
    body: SubmitOrderRequest, user_id: CurrentUser, streams: Streams, settings: Config
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
    try:
        stream_id = await streams.append(settings.stream_inbound, order)
    except ExchangeHalted as halted:
        # The exchange cannot durably record this. Fail loudly rather than time out silently,
        # or worse, accept an order that was never written (Open Issue 003 §8.5).
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"status": "halted", "reason": halted.reason},
        ) from halted

    return AcknowledgementResponse(
        client_order_id=order.client_order_id,
        order_id=None,
        seq=stream_id,
        status="accepted",
    )


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
