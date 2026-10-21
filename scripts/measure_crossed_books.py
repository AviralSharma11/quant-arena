"""BUG-004: how often does a published L2 snapshot show a crossed book?

Subscribes to every symbol's `book:*:l2` channel on the running stack for a fixed window and
counts snapshots whose best bid is at or above the best ask. A real cross in the engine is
impossible — price-time matching removes it in the same input — so any crossed snapshot is the
feed showing a moment between an order's acceptance and the fills that took it off the book.

It also reports the longest run of consecutive crossed snapshots per symbol: a one-tick cross
that the next snapshot repairs is BUG-004's signature, while a cross that persists means the
books themselves have diverged (the restart fork Open Issue 020 fixed).

    docker compose --profile bots up -d
    python scripts/measure_crossed_books.py --seconds 60
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import httpx  # noqa: E402
import websockets  # noqa: E402

from config.settings import Settings  # noqa: E402

GATEWAY = os.environ.get("QA_GATEWAY_URL", "http://localhost:8000")
FANOUT_WS = os.environ.get("QA_FANOUT_WS", "ws://localhost:8001/stream")


def sign_in() -> str:
    """A throwaway account, so the watcher has a session cookie and nothing else."""
    credentials = {"username": f"xwatch_{uuid.uuid4().hex[:10]}", "password": uuid.uuid4().hex}
    with httpx.Client(base_url=GATEWAY, timeout=10) as client:
        client.post("/auth/register", json=credentials).raise_for_status()
        client.post("/auth/login", json=credentials).raise_for_status()
        return "; ".join(f"{k}={v}" for k, v in client.cookies.items())


async def measure(seconds: float) -> dict:
    channels = [f"book:{symbol.name}:l2" for symbol in Settings.load().symbols]
    snapshots: dict[str, int] = {ch: 0 for ch in channels}
    crossed: dict[str, int] = {ch: 0 for ch in channels}
    run: dict[str, int] = {ch: 0 for ch in channels}
    longest: dict[str, int] = {ch: 0 for ch in channels}

    async with websockets.connect(
        FANOUT_WS, additional_headers={"Cookie": sign_in()}, open_timeout=30
    ) as socket:
        await socket.send(json.dumps({"op": "subscribe", "channels": channels}))
        deadline = time.monotonic() + seconds
        while (remaining := deadline - time.monotonic()) > 0:
            try:
                frame = json.loads(await asyncio.wait_for(socket.recv(), timeout=remaining))
            except asyncio.TimeoutError:
                break
            channel = frame.get("ch")
            if channel not in snapshots:
                continue
            snapshots[channel] += 1
            bids, asks = frame.get("bids") or [], frame.get("asks") or []
            if bids and asks and bids[0][0] >= asks[0][0]:
                crossed[channel] += 1
                run[channel] += 1
                longest[channel] = max(longest[channel], run[channel])
            else:
                run[channel] = 0

    return {
        "seconds": seconds,
        "snapshots": sum(snapshots.values()),
        "crossed": sum(crossed.values()),
        "longest_crossed_run": max(longest.values()),
        "per_symbol": {
            ch.split(":")[1]: {"snapshots": snapshots[ch], "crossed": crossed[ch],
                               "longest_crossed_run": longest[ch]}
            for ch in channels
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seconds", type=float, default=60.0)
    args = parser.parse_args()
    result = asyncio.run(measure(args.seconds))
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
