"""`POST /backtests` and `GET /backtests/{id}` — Task 7.2's server half.

## These endpoints were in the frozen contract already

`contracts/v1/rest_and_ws.md` §2 lists both, and its preamble is emphatic that "an addition
during the build is a scope change to be raised, not absorbed". Nothing is being added here:
the surface was signed off in week 1 and this fills it in. The contract also settles the shape
— **Run** returns an id, **Retrieve** fetches by it — which is why a backtest result has to
survive between two requests instead of being returned inline and forgotten.

## The id is the manifest hash, so running twice is free

Task 7.1 guarantees that two runs of one manifest are byte-identical, under a content hash of
every input that could move a number. So the id a run is stored under *is* that hash. Two
identical requests therefore collapse onto one stored result rather than producing a second
copy under a second id — the endpoint is idempotent, and it is idempotent for a reason that was
already proven rather than by a mechanism added here.

It also means `GET /backtests/{id}` for an expired id is honestly a 404 and not a lie: the
result is recomputable from the manifest, so nothing was lost that cannot be asked for again.

## Results live in Redis, and deliberately not in PostgreSQL

Open Issue 004 makes PostgreSQL a **derived read model, rebuildable from the stream**. A
backtest result is not derived from the stream and could never be rebuilt from it, so a table
of them would quietly break the one property that definition rests on. Redis has the TTL this
wants, and losing a result costs nothing.

## The CPU work goes to a thread; the I/O stays on the loop

A backtest is 30–230 ms of CPU. The gateway is the single *producer* of the inbound stream and
must stay answerable to HTTP (Open Issue 007), so a coroutine doing that work inline would
block the event loop for the whole of it and delay every order acknowledgement in flight.

`asyncio.to_thread` moves exactly the CPU part off the loop while Redis stays awaited on the
one async client the gateway already has. Declaring the whole handler `def` would have worked
for the loop too, but it would have needed a *second*, synchronous Redis client — a second
connection pool to size, to fail, and to explain. The requestable range is capped for the same
reason the work is moved: no single request may queue an unbounded job.
"""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from services.backtest.bars import MINUTES_PER_SIMULATED_DAY, day_range_to_minutes
from services.backtest.report import LIMITATION, tick_size_for
from services.backtest.runner import run_from_dataset
from services.backtest.strategy import SmaCrossover
from services.gateway.deps import Config, CurrentUser, Redis

router = APIRouter(prefix="/backtests", tags=["backtests"])

#: Results are kept for an hour. Long enough that a user can reload the results page or share a
#: link within a session, short enough that a demo instance does not accumulate them forever.
#: Nothing is lost at expiry — a manifest recomputes its own result exactly.
RESULT_TTL_SECONDS = 3600

_KEY_PREFIX = "backtest:"

#: The dataset is seven simulated days (10,080 minutes ÷ 1,440). Kept as a computed bound rather
#: than a literal 7 so a re-fetched dataset of a different length cannot silently leave the form
#: offering days that do not exist.
MAX_DAY = 10_080 // MINUTES_PER_SIMULATED_DAY

#: One built-in strategy — Open Issue 018 §3.2 cut three to one. Served rather than hardcoded in
#: the frontend for the reason the 2026-09-07 decision deleted `STREAM_SYMBOLS`: a list in two
#: places has the same defect one listing later.
STRATEGIES = [
    {
        "id": "sma_crossover",
        "name": "SMA crossover",
        "description": (
            "Long while the fast moving average is above the slow one, flat otherwise. "
            "Windows are fixed at 10 and 30 bars: Task 7.2 forbids a parameter tuning "
            "interface, and a form for hunting window pairs is the definition of one."
        ),
    }
]


class RunRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=32)
    strategy: str = Field(default="sma_crossover")
    bar_minutes: int = Field(default=5, ge=1, le=60)
    # Days, not dates. The pinned dataset carries `minute_index` and no wall-clock time, so a
    # date range would be an invention; a simulated day is 1,440 minutes (Open Issue 005 §5g).
    first_day: int = Field(default=1, ge=1, le=MAX_DAY)
    last_day: int = Field(default=MAX_DAY, ge=1, le=MAX_DAY)


@router.get("/strategies")
async def list_strategies(user_id: CurrentUser) -> dict:
    return {"strategies": STRATEGIES, "max_day": MAX_DAY}


@router.post("", status_code=status.HTTP_201_CREATED)
async def run_backtest(
    body: RunRequest, user_id: CurrentUser, settings: Config, redis: Redis
) -> dict:
    if body.strategy not in {s["id"] for s in STRATEGIES}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"reason": "UNKNOWN_STRATEGY", "strategy": body.strategy},
        )
    if body.last_day < body.first_day:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"reason": "EMPTY_RANGE", "first_day": body.first_day,
                    "last_day": body.last_day},
        )

    first_minute, last_minute = day_range_to_minutes(body.first_day, body.last_day)
    try:
        result = await asyncio.to_thread(
            run_from_dataset,
            strategy=SmaCrossover(),
            symbol=body.symbol,
            bar_minutes=body.bar_minutes,
            first_minute=first_minute,
            last_minute=last_minute,
            settings=settings,
        )
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"reason": "UNKNOWN_SYMBOL", "symbol": body.symbol},
        )
    except ValueError as exc:
        # A range too short to hold two bars: one to decide on and one to fill at. The caller's
        # fault and fixable by widening the range, so 400 rather than 500.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"reason": "RANGE_TOO_SHORT", "message": str(exc)},
        )

    payload = _payload(result, body.symbol)
    await redis.set(
        _KEY_PREFIX + result.manifest.manifest_id,
        json.dumps(payload),
        ex=RESULT_TTL_SECONDS,
    )
    return payload


@router.get("/{backtest_id}")
async def get_backtest(backtest_id: str, user_id: CurrentUser, redis: Redis) -> dict:
    raw = await redis.get(_KEY_PREFIX + backtest_id)
    if raw is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "reason": "NOT_FOUND",
                "message": (
                    "No stored result under that id. Results expire after an hour — re-run the "
                    "same manifest and it will land under the same id, because the id is a hash "
                    "of the inputs."
                ),
            },
        )
    return json.loads(raw)


def _payload(result, symbol: str) -> dict:
    """What the screen renders.

    `limitation` travels **with the result** rather than being a string the frontend keeps. One
    source, so the page and the CLI report cannot drift into describing different fill models —
    which is the failure Success Criterion 2 of this task is written against.
    """
    return {
        "id": result.manifest.manifest_id,
        "manifest": result.manifest.as_dict(),
        "metrics": result.metrics.as_dict(),
        "limitation": LIMITATION,
        "tick_size_ticks": tick_size_for(symbol),
        "trade_count": result.metrics.trade_count,
        "refused_intents": result.refused_intents,
        "unfilled_at_end": result.unfilled_at_end,
        "first_trade_bar_index": result.first_trade_bar_index,
    }
