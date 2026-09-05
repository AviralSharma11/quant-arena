"""Task 5.2 Success Criterion 1 — two hundred concurrent clients, measured.

> Two hundred or more concurrent clients receive updates without gateway acknowledgement
> latency degrading measurably.

The criterion is about *the gateway*, not about fan-out, and that is the whole point of Open
Issue 006 §7b: fan-out is a separate process precisely so that connection load cannot reach the
order path. This script is the check on that claim. It samples order acknowledgement latency
with nobody connected, opens two hundred WebSockets to fan-out, and samples again.

The second measurement is fan-out's own: `serialisations` divided by `ticks` from `/health`,
which is Success Criterion 5 stated as a ratio. It should not move when the client count does.

    docker compose up -d                      # gateway, matcher, ledger, fanout
    QA_BENCH_PASSWORD=... python benchmarks/bench_fanout.py

Nothing here is a unit test. The suite proves the mechanisms in isolation; this is the number
that says the mechanism survives contact with two hundred sockets, and it goes in
`benchmarks/results/5.2-fanout-conflation.md` alongside the machine it was measured on.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import httpx
import websockets

from config.settings import settings as default_settings

GATEWAY = os.environ.get("QA_GATEWAY_URL", "http://localhost:8000")
FANOUT_WS = os.environ.get("QA_FANOUT_WS", "ws://localhost:8001/stream")
FANOUT_HTTP = os.environ.get("QA_FANOUT_URL", "http://localhost:8001")
PASSWORD = os.environ.get("QA_BENCH_PASSWORD", "benchmark-password-1")


async def sign_in(client: httpx.AsyncClient, username: str) -> None:
    """Register if needed, then always log in. Idempotent across runs.

    The login is unconditional on purpose. `POST /auth/register` returns the new account and
    does *not* set a session cookie — a registration that silently signed you in would make the
    two endpoints mean different things on the same cookie jar. Skipping it here cost an
    afternoon: every churn order came back 401, the book never moved, and the benchmark
    reported a perfectly healthy feed sending almost nothing.
    """
    response = await client.post(
        "/auth/register", json={"username": username, "password": PASSWORD}
    )
    if response.status_code not in (200, 201, 409):
        response.raise_for_status()
    response = await client.post(
        "/auth/login", json={"username": username, "password": PASSWORD}
    )
    response.raise_for_status()


async def sample_ack_latency(client: httpx.AsyncClient, *, count: int, symbol_id: int) -> list[float]:
    """Milliseconds from request to acknowledgement, one order at a time.

    Sequential on purpose. Concurrency here would measure the group-commit batching that Task
    2.1 already measured; what this needs is the latency of a *single* acknowledgement, which
    is what a trader experiences and what connection load would degrade.
    """
    samples: list[float] = []
    base = time.time_ns() // 1_000_000
    for index in range(count):
        payload = {
            "client_order_id": base + index,
            "symbol_id": symbol_id,
            "side": 1,
            "tif": 1,
            "price_ticks": 1,
            "qty": 1,
        }
        started = time.perf_counter()
        response = await client.post("/orders", json=payload)
        samples.append((time.perf_counter() - started) * 1000)
        if response.status_code >= 500:
            raise RuntimeError(f"gateway returned {response.status_code}: {response.text}")
    return samples


class Client:
    """One browser. Counts frames and nothing else."""

    def __init__(self, cookie: str, channels: list[str]) -> None:
        self.cookie = cookie
        self.channels = channels
        self.frames = 0
        self._socket = None

    async def open(self) -> None:
        self._socket = await websockets.connect(
            FANOUT_WS, additional_headers={"Cookie": self.cookie}, open_timeout=20
        )
        await self._socket.send(json.dumps({"op": "subscribe", "channels": self.channels}))

    async def drain(self, seconds: float) -> None:
        deadline = time.monotonic() + seconds
        try:
            while time.monotonic() < deadline:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                await asyncio.wait_for(self._socket.recv(), timeout=remaining)
                self.frames += 1
        except (asyncio.TimeoutError, Exception):
            pass

    async def close(self) -> None:
        if self._socket is not None:
            await self._socket.close()


async def churn(client: httpx.AsyncClient, symbols, *, stop: asyncio.Event) -> int:
    """Keep the book moving for the duration of the run.

    Without it the only thing changing is the latency probe's own orders, and a feed of a
    motionless book would flatter every number here: conflation skips a symbol that has not
    moved, so an idle market makes both the encode count and the frame count meaningless.

    Resting buys, low and cheap, alternating across symbols. Not a market shape at all — a
    market shape is `services/bots`, and this only has to make the book *move* so that the tick
    has something to encode. Deliberately far below the resting asks so nothing crosses and
    nothing is rejected: a rejected order changes no book, and a run where the churn account
    quietly ran out of cash would report a healthy feed sending almost nothing.
    """
    placed = 0
    base = time.time_ns() // 1_000_000
    while not stop.is_set():
        symbol = symbols[placed % len(symbols)]
        response = await client.post(
            "/orders",
            json={
                "client_order_id": base + 1_000_000 + placed,
                "symbol_id": symbol.symbol_id,
                "side": 1,
                "tif": 1,
                "price_ticks": 5 + (placed % 20),
                "qty": 1,
            },
        )
        if response.status_code >= 400 and response.status_code != 409:
            # 409 is a risk rejection — expected once the account is fully committed, and the
            # reason the churn uses a fresh account. Anything else means this loop is not doing
            # what the report will claim it did.
            raise RuntimeError(f"churn order {placed}: {response.status_code} {response.text}")
        placed += 1
        await asyncio.sleep(0.005)
    return placed


def summarise(name: str, samples: list[float]) -> dict:
    ordered = sorted(samples)
    return {
        "case": name,
        "n": len(ordered),
        "median_ms": round(statistics.median(ordered), 3),
        "p95_ms": round(ordered[int(len(ordered) * 0.95) - 1], 3),
        "max_ms": round(ordered[-1], 3),
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--clients", type=int, default=200)
    parser.add_argument("--orders", type=int, default=200)
    parser.add_argument("--seconds", type=float, default=10.0)
    args = parser.parse_args()

    settings = default_settings
    symbols = [s.name for s in settings.symbols]
    symbol_id = settings.symbols[0].symbol_id
    channels = [f"book:{name}:l2" for name in symbols] + [f"tape:{name}" for name in symbols]

    async with (
        httpx.AsyncClient(base_url=GATEWAY, timeout=30) as trader,
        httpx.AsyncClient(base_url=GATEWAY, timeout=30) as noisy,
        httpx.AsyncClient(base_url=GATEWAY, timeout=30) as watcher,
        httpx.AsyncClient(base_url=FANOUT_HTTP, timeout=10) as probe,
    ):
        await sign_in(trader, "bench_trader")
        # A fresh account per run. The churn leaves its orders resting, and a buy reserves cash
        # at its limit price for as long as it rests (Task 3.1), so a reused account runs out
        # after a few thousand orders and every later order is rejected — which changes no book
        # and would quietly report a healthy feed sending almost nothing.
        await sign_in(noisy, f"bench_churn_{int(time.time())}")
        await sign_in(watcher, "bench_watcher")
        cookie = "; ".join(f"{k}={v}" for k, v in watcher.cookies.items())

        # The churn runs across BOTH samples. Comparing a quiet exchange with a busy one would
        # measure the churn, not the clients; the only difference the comparison may contain is
        # how many browsers are attached.
        stop = asyncio.Event()
        churning = asyncio.create_task(churn(noisy, settings.symbols, stop=stop))
        await asyncio.sleep(1.0)

        print("warming up...")
        await sample_ack_latency(trader, count=20, symbol_id=symbol_id)

        print("sampling with no clients connected...")
        quiet = await sample_ack_latency(trader, count=args.orders, symbol_id=symbol_id)

        before = (await probe.get("/health")).json()

        print(f"opening {args.clients} websockets...")
        clients = [Client(cookie, channels) for _ in range(args.clients)]
        await asyncio.gather(*(c.open() for c in clients))
        drains = [asyncio.create_task(c.drain(args.seconds)) for c in clients]
        await asyncio.sleep(1.0)

        print("sampling under load...")
        loaded = await sample_ack_latency(trader, count=args.orders, symbol_id=symbol_id)

        await asyncio.gather(*drains)
        after = (await probe.get("/health")).json()
        await asyncio.gather(*(c.close() for c in clients))
        stop.set()
        placed = await churning

    ticks = after["ticks"] - before["ticks"]
    encodes = after["serialisations"] - before["serialisations"]
    received = [c.frames for c in clients]

    report = {
        "clients": args.clients,
        "churn_orders": placed,
        "quiet": summarise("no clients", quiet),
        "loaded": summarise(f"{args.clients} clients", loaded),
        "ticks": ticks,
        "serialisations": encodes,
        "encodes_per_tick": round(encodes / ticks, 3) if ticks else None,
        "frames_min": min(received),
        "frames_median": statistics.median(received),
        "frames_max": max(received),
        "max_tick_seconds": after["max_tick_seconds"],
    }
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
