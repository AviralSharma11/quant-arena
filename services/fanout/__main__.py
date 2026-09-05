"""Run fan-out as its own process: `python -m services.fanout`.

Never inside the gateway. Open Issue 006 §7b is explicit: coupling fan-out to the sequencer
degrades order-acknowledgement latency under connection load, and two hundred WebSocket
connections is precisely that load. The gateway must stay answerable to HTTP while this tails a
stream, rebuilds books and talks to browsers.

    QA_REDIS_URL=redis://localhost:6379/0 python -m services.fanout

Everything — the stream tail, the 20 Hz tick, the halt watcher, the WebSocket server — runs in
one event loop inside `services/fanout/server.py`; this file is uvicorn and a port.
"""

from __future__ import annotations

import logging
import os
import sys

import uvicorn

from services.fanout.server import create_app

#: Its own port, not the gateway's. The Vite dev proxy and the compose service both point here.
DEFAULT_PORT = 8001


def _configure_logging() -> None:
    root = logging.getLogger()
    if not root.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(message)s"))
        root.addHandler(handler)
    root.setLevel(logging.INFO)


def main() -> None:
    _configure_logging()
    uvicorn.run(
        create_app(),
        host=os.environ.get("QA_FANOUT_HOST", "0.0.0.0"),
        port=int(os.environ.get("QA_FANOUT_PORT", DEFAULT_PORT)),
        # uvicorn's own access log would print a line per WebSocket upgrade and nothing
        # useful afterwards. The structured startup line and /health are the observability
        # story here (Open Issue 012).
        access_log=False,
        log_config=None,
    )


if __name__ == "__main__":
    main()
