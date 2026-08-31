"""The startup stamp, shared by every process.

Task 1.4's third success criterion is that the configuration hash appears in the startup log of
**every** process. One helper, called by each, is how that stays true as the engine, fan-out,
bots and archiver arrive — rather than four processes each remembering to log it their own way.

A single JSON line, because Open Issue 012 settled observability as structured logs plus
offline analysis: no Prometheus, no Grafana. The event stream is the trace.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import sys

from config.settings import Settings

LOGGER_NAME = "quant_arena.startup"

#: The key downstream tooling greps for. Changing it breaks the benchmark manifests in 7.4.
CONFIG_HASH_KEY = "config_hash"


def startup_record(process: str, settings: Settings) -> dict[str, object]:
    from contracts.v1.generated.contracts import SCHEMA_VERSION

    return {
        "event": "startup",
        "process": process,
        CONFIG_HASH_KEY: settings.config_hash,
        "config_path": settings.config_path,
        "schema_version": SCHEMA_VERSION,
        "python": platform.python_version(),
        "pid": os.getpid(),
    }


def log_startup(process: str, settings: Settings) -> dict[str, object]:
    """Emit the stamp and return it, so a caller (or a test) can assert on it."""
    record = startup_record(process, settings)
    logger = logging.getLogger(LOGGER_NAME)
    if not logger.handlers and not logging.getLogger().handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    logger.info(json.dumps(record, separators=(",", ":")))
    return record
