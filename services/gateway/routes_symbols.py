"""`GET /symbols` — static per-deployment configuration.

`contracts/v1/rest_and_ws.md` section 2.3: this is the **only** place symbol names and tick
sizes are defined, and therefore the only thing that maps a `symbol_id` on the wire to
something a human reads.

The enum tables are served here for the same reason, and it is the more interesting half.
`schema.toml` owns `Side`, `Tif`, `CancelReason` and `RejectReason`; every one of them crosses
the browser wire as a bare integer. Without this endpoint a client has to hard-code `1` for
`BUY`, and the schema's ownership of that number becomes a comment rather than a fact — the
frontend would keep working after a schema change and be quietly wrong.

So the tables are **derived from the generated enums**, never typed out here. A hand-written
copy is a second definition, which is the thing the whole contracts pipeline exists to avoid.

Unauthenticated on purpose: it is static configuration, identical for every caller, and the
login screen needs it before a session exists.

Scope note — `CancelReason` is served although the contract's example names only `side`, `tif`
and `reject_reason`. That example is abbreviated (its `reject_reason` lists one of thirteen
values), and `CancelReason` reaches the browser on every private-stream `OrderCancelled`, so
omitting it would leave exactly the hard-coded integer this endpoint exists to prevent.
"""

from __future__ import annotations

from enum import IntEnum

from fastapi import APIRouter
from pydantic import BaseModel

from contracts.v1.generated.contracts import (
    SCHEMA_VERSION,
    CancelReason,
    RejectReason,
    Side,
    Tif,
)
from services.gateway.deps import Config

router = APIRouter(tags=["symbols"])

#: The enums a browser client has to resolve, keyed by the name used on the wire. Derived from
#: the generated module, so adding a value to `schema.toml` publishes it here automatically.
_WIRE_ENUMS: dict[str, type[IntEnum]] = {
    "side": Side,
    "tif": Tif,
    "cancel_reason": CancelReason,
    "reject_reason": RejectReason,
}


def enum_tables() -> dict[str, dict[str, int]]:
    return {
        name: {member.name: int(member) for member in enum}
        for name, enum in _WIRE_ENUMS.items()
    }


class SymbolItem(BaseModel):
    symbol_id: int
    name: str
    #: The divisor the presentation layer applies for display. Prices always travel as integer
    #: ticks; a divided value coming back is a bug, not a rounding concern (Open Issue 016).
    tick_size_ticks: int
    lot_size: int


class SymbolsResponse(BaseModel):
    symbols: list[SymbolItem]
    enums: dict[str, dict[str, int]]
    schema_version: int


@router.get("/symbols")
async def get_symbols(settings: Config) -> SymbolsResponse:
    return SymbolsResponse(
        symbols=[
            SymbolItem(
                symbol_id=symbol.symbol_id,
                name=symbol.name,
                tick_size_ticks=symbol.tick_size_ticks,
                lot_size=symbol.lot_size,
            )
            for symbol in settings.symbols
        ],
        enums=enum_tables(),
        schema_version=SCHEMA_VERSION,
    )
