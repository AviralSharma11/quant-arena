"""Run the bots as their own process: `python -m services.bots`.

Its own process because Task 4.4's Boundaries require it: bots go through the real API, and
"in-process bots exercise none of the code they would be testing and are worthless as a load
test" (Open Issue 005 sub-decision 5c). This one talks HTTP to the gateway like any browser.

Two infrastructure values, from the environment rather than the hashed configuration file,
because that is where endpoints and secrets live (`config/settings.py`):

- `QA_GATEWAY_URL` — where the exchange is. Defaults to the compose service name.
- `QA_BOT_PASSWORD` — **required, no default.** A password with a default is a password in the
  repository the first time someone forgets to set it.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import sys

from config.settings import settings as default_settings
from config.startup import log_startup
from services.bots.runner import BotRunner, LOGGER_NAME

DEFAULT_GATEWAY_URL = "http://gateway:8000"


def _configure_logging() -> None:
    """One JSON line per event, on stdout. Open Issue 012: structured logs and offline
    analysis — no Prometheus, no Grafana, and the event stream is the trace.

    The handler goes on the **root** logger, not on `quant_arena`. `config.startup.log_startup`
    installs its own handler only when neither its logger nor the root has one, so attaching
    here to `quant_arena` left `quant_arena.startup` with a handler of its own *and* a path up
    to this one — and the startup line, the one line whose whole job is to identify the
    process, was printed twice.
    """
    root = logging.getLogger()
    if not root.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(message)s"))
        root.addHandler(handler)
    root.setLevel(logging.INFO)
    # httpx logs a line per request at INFO, and a market maker at 2 Hz across ten symbols
    # makes several requests a second. The structured records here are the observability
    # (Open Issue 012); a per-request access log buries them and says nothing they do not.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


async def _until_signalled() -> None:
    """Block until the process is asked to stop, by either signal that means it.

    `docker compose stop` and `docker compose down` send **SIGTERM**, and the default handler
    for it ends the process outright — so the `finally` that prints the session summary never
    ran, and stopping the container the ordinary way threw away the very record Success
    Criterion 2 asks for. Only Ctrl-C, which raises `KeyboardInterrupt`, worked.
    """
    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):  # not available on every platform
            loop.add_signal_handler(signum, stopping.set)
    await stopping.wait()


async def main() -> None:
    settings = default_settings
    _configure_logging()
    # Task 1.4 criterion 3: the configuration hash appears in every process's startup log, so
    # a bot session and the exchange it ran against are provably the same configuration.
    log_startup("bots", settings)

    password = os.environ.get("QA_BOT_PASSWORD")
    if not password:
        raise SystemExit(
            "QA_BOT_PASSWORD is required. It is a secret, so it comes from the environment "
            "and never from config/quant_arena.toml, which is version controlled and hashed."
        )

    runner = BotRunner(
        settings,
        base_url=os.environ.get("QA_GATEWAY_URL", DEFAULT_GATEWAY_URL),
        password=password,
    )

    async with contextlib.AsyncExitStack() as stack:
        await runner.build(stack)
        await runner.sign_in()
        runner.start()
        logging.getLogger(LOGGER_NAME).info(
            '{"event":"bots_running","market_makers":%d,"noise_traders":%d}'
            % (len(runner.makers), len(runner.noise))
        )
        try:
            await _until_signalled()
        finally:
            # The session summary is the deliverable of Success Criterion 2 — obligation
            # compliance across a session, with breaches recorded. It has to survive the way
            # the process is actually stopped, which for a container is SIGTERM.
            await runner.stop()
            runner.log_summaries()


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
