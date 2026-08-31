"""The seam between the gateway and whatever is matching orders.

This is the one load-bearing stub in the whole seven weeks (Appendix D.2). Today the
implementation is `services.engine_stub.StubEngine`; at the end of week 2 it becomes the real
stream, and at the end of week 5 the C++ engine process. The gateway must not be able to tell
the difference, which is why this file is this short.

Records in, records out — nothing else crosses.
"""

from __future__ import annotations

from typing import Protocol, Union

from contracts.v1.generated.contracts import (
    CancelOrder,
    OrderAccepted,
    OrderCancelled,
    OrderRejected,
    SubmitOrder,
)

SubmitResult = Union[OrderAccepted, OrderRejected]
CancelResult = Union[OrderCancelled, OrderRejected]


class EnginePort(Protocol):
    def submit(self, order: SubmitOrder) -> SubmitResult: ...

    def cancel(self, cancel: CancelOrder) -> CancelResult: ...
