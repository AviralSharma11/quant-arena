"""Fan-out — market data derived from the outbound stream.

Task 5.2a builds the state and the message shapes; Task 5.2b adds the WebSocket server, the
20 Hz conflation tick, the private per-user stream and the slow-client policy. See README.md in
this package for the four decisions taken here and what would make each worth revisiting.

The split is the same one the matcher and the ledger use: `book`, `bars`, `state` and `messages`
are pure — no clock, no socket, no store — and `runner` owns every piece of I/O.
"""

from services.fanout.bars import Bar, BarBuilder, BarSet, Tape, Trade
from services.fanout.book import Book, RestingOrder
from services.fanout.runner import FanOut
from services.fanout.state import MarketState

__all__ = [
    "Bar", "BarBuilder", "BarSet", "Book", "FanOut", "MarketState",
    "RestingOrder", "Tape", "Trade",
]
