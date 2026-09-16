"""`trace <client_order_id>` — reconstruct one order's path, with per-hop timings.

Task 7.4 asks for distributed tracing, and `README.md` Goal 6 lists it outright. The
conventional answer is OpenTelemetry with a collector and a backend, at roughly fifteen hours
plus permanent operational weight. Open Issue 012 §5 rejects that for one reason:

> **the event stream already is the trace.**

Every request carries a `client_order_id`, every record gets a Redis stream id which *is* its
sequence number (Open Issue 003), and every downstream record references one or both. So an
order's path is a **query**, not a second system that has to be kept alive, sampled, and trusted.
It is also strictly better than sampled spans here, because it traces the actual ordered record
that the exchange acted on rather than a probabilistic shadow of it.

## How an order is found

Three lookups, cheapest first, and the tool says which one answered:

1. **The idempotency key** (`qa.idempotent:<user>:<client_order_id>`, one-hour TTL). The gateway
   already records the outcome there for duplicate replay, and it holds `seq` — the inbound
   stream id. For an order from the last hour that is the whole search: one `GET`, then a
   one-record `XRANGE`. It also answers a question the stream cannot: an order **rejected by the
   gateway never reaches the stream at all** (risk, an unknown symbol, a bad price), so "absent
   from the log" and "refused before the log" look identical without it.
2. **A bounded backwards scan** of the inbound stream, for an order older than the TTL or when
   no user was given. `client_order_id` is unique *per user*, not globally (Open Issue 008), so
   without `--user` this can legitimately find several different orders. It collects every match
   in the scanned window and refuses to guess between them.
3. **Forward from the inbound record** through the outbound stream, for what the engine did:
   the acknowledgement, and then any `Fill` naming the `order_id` the acknowledgement assigned.

## What the timings mean, and what they cannot mean

Three different clocks, and the tool is explicit about which it is using:

- `timestamp_ns` — the gateway's own nanosecond stamp, written into the record. Note that every
  consumer *replays* this value identically; it is a property of the record, not a per-hop clock,
  and it is the only nanosecond-resolution number here.
- The **inbound stream id** — Redis's millisecond clock when the append landed.
- The **outbound stream id** — the same clock when the engine's answer landed.

So the hops between stream ids are **millisecond-granular**. That is coarse next to a 166 ns
match or a 100 µs `XADD`, and it is the reason this tool is not the instrument for the latency
budget: `benchmarks/bench_e2e.py` measures those from the client side at nanosecond resolution.
`trace` answers a different question — *what happened to this order, in what order, and roughly
when* — and a hop printed as `1 ms` may be anything from 1 µs to 2 ms. It says so rather than
implying a precision it does not have.

    python scripts/trace.py 7314159 --user 42
    python scripts/trace.py 7314159 --json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from redis.asyncio import Redis

from config.settings import settings as default_settings
from contracts.v1.generated.contracts import (
    Fill,
    OrderAccepted,
    OrderCancelled,
    OrderRejected,
    unpack_any,
)
from services.gateway.idempotency import IDEMPOTENCY_PREFIX
from services.gateway.streams import RECORD_FIELD

NS_PER_MS = 1_000_000

#: Records that name a `client_order_id` and therefore identify an order directly.
#: A `Fill` deliberately does not — it names order ids only — which is why fills are found by
#: joining through the `order_id` the acknowledgement assigned.
CLIENT_KEYED = (OrderAccepted, OrderRejected, OrderCancelled)


def stream_id_ms(stream_id: str) -> int:
    return int(str(stream_id).split("-", 1)[0])


def clock(ns: int) -> str:
    if not ns:
        return "—"
    return datetime.fromtimestamp(ns / 1e9, tz=timezone.utc).strftime("%H:%M:%S.%f")


@dataclass
class Hop:
    name: str
    at_ns: int
    detail: str = ""
    #: True when this hop's time came from a millisecond stream id rather than a nanosecond
    #: stamp. Printed, because a reader is owed the resolution of every number.
    coarse: bool = False
    stream_id: str = ""


@dataclass
class Trace:
    client_order_id: int
    user_id: int | None = None
    found_by: str = ""
    inbound_id: str = ""
    order_id: int | None = None
    gateway_outcome: dict = field(default_factory=dict)
    hops: list[Hop] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    ambiguous: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "client_order_id": self.client_order_id,
            "user_id": self.user_id,
            "found_by": self.found_by,
            "inbound_stream_id": self.inbound_id,
            "order_id": self.order_id,
            "gateway_outcome": self.gateway_outcome or None,
            "hops": [
                {
                    "hop": h.name,
                    "at_unix_ns": h.at_ns,
                    "stream_id": h.stream_id or None,
                    "detail": h.detail,
                    "resolution": "millisecond" if h.coarse else "nanosecond",
                }
                for h in self.hops
            ],
            "notes": self.notes,
            "ambiguous_matches": self.ambiguous or None,
        }


async def read_idempotency(
    redis: Redis, user_id: int, client_order_id: int
) -> dict | None:
    raw = await redis.get(f"{IDEMPOTENCY_PREFIX}{user_id}:{client_order_id}")
    if raw is None:
        return None
    text = raw.decode() if isinstance(raw, bytes) else raw
    if text == "in_progress":
        return {"status": "in_progress"}
    try:
        return json.loads(text)
    except ValueError:
        return {"status": "unparsable", "raw": text}


async def inbound_at(redis: Redis, stream: str, stream_id: str):
    entries = await redis.xrange(stream, min=stream_id, max=stream_id, count=1)
    if not entries:
        return None
    return unpack_any(entries[0][1][RECORD_FIELD])


async def scan_back_for(
    redis: Redis,
    stream: str,
    client_order_id: int,
    user_id: int | None,
    budget: int,
    chunk: int = 5000,
) -> tuple[list[tuple[str, object]], int]:
    """Walk the inbound stream backwards looking for this `client_order_id`.

    Bounded on purpose. The stream holds millions of records and a full scan for a missing id
    would look like a hang; a tool that reports "not found in the last N" is more honest than
    one that searches forever and no more wrong.
    """
    found: list[tuple[str, object]] = []
    scanned = 0
    cursor = "+"
    while scanned < budget:
        entries = await redis.xrevrange(stream, max=cursor, min="-", count=chunk)
        if not entries:
            break
        for raw_id, fields in entries:
            scanned += 1
            sid = raw_id.decode() if isinstance(raw_id, bytes) else raw_id
            try:
                record = unpack_any(fields[RECORD_FIELD])
            except ValueError:
                continue
            if getattr(record, "client_order_id", None) != client_order_id:
                continue
            if user_id is not None and getattr(record, "user_id", None) != user_id:
                continue
            found.append((sid, record))
        last = entries[-1][0]
        last_id = last.decode() if isinstance(last, bytes) else last
        ms, ordinal = last_id.split("-")
        if ordinal == "0":
            cursor = f"{int(ms) - 1}"
        else:
            cursor = f"{ms}-{int(ordinal) - 1}"
        if int(ms) <= 0:
            break
    return found, scanned


async def scan_forward(
    redis: Redis,
    stream: str,
    start_id: str,
    client_order_id: int,
    user_id: int | None,
    budget: int,
    chunk: int = 5000,
) -> tuple[list[tuple[str, object]], int | None, int]:
    """Collect this order's outbound records, and the fills that name its `order_id`.

    Two passes would mean two scans, so it does one: acknowledgements are matched on
    `client_order_id`, and once an `OrderAccepted` has handed over an `order_id` every later
    `Fill` naming it is collected too.
    """
    hits: list[tuple[str, object]] = []
    order_id: int | None = None
    scanned = 0
    cursor = start_id
    while scanned < budget:
        entries = await redis.xrange(stream, min=f"({cursor}", max="+", count=chunk)
        if not entries:
            break
        for raw_id, fields in entries:
            scanned += 1
            sid = raw_id.decode() if isinstance(raw_id, bytes) else raw_id
            cursor = sid
            try:
                record = unpack_any(fields[RECORD_FIELD])
            except ValueError:
                continue
            if isinstance(record, CLIENT_KEYED):
                if record.client_order_id != client_order_id:
                    continue
                if user_id is not None and record.user_id != user_id:
                    continue
                hits.append((sid, record))
                if isinstance(record, OrderAccepted):
                    order_id = record.order_id
            elif isinstance(record, Fill) and order_id is not None:
                if order_id in (record.maker_order_id, record.taker_order_id):
                    hits.append((sid, record))
    return hits, order_id, scanned


def describe(record) -> str:
    name = type(record).__name__
    if isinstance(record, OrderAccepted):
        return (
            f"{name} order_id={record.order_id} "
            f"{record.qty} @ {record.price_ticks} side={int(record.side)}"
        )
    if isinstance(record, OrderRejected):
        return f"{name} reason={int(record.reason)}"
    if isinstance(record, OrderCancelled):
        return f"{name} order_id={record.order_id} remaining={record.remaining_qty}"
    if isinstance(record, Fill):
        return (
            f"{name} {record.qty} @ {record.price_ticks} "
            f"maker={record.maker_order_id} taker={record.taker_order_id}"
        )
    return name


async def build_trace(
    redis: Redis,
    settings,
    client_order_id: int,
    user_id: int | None,
    back_budget: int,
    forward_budget: int,
) -> Trace:
    trace = Trace(client_order_id=client_order_id, user_id=user_id)

    inbound_id = ""
    inbound_record = None

    # 1. The gateway's own answer, which is the only place a pre-stream rejection exists.
    if user_id is not None:
        outcome = await read_idempotency(redis, user_id, client_order_id)
        if outcome:
            trace.gateway_outcome = outcome
            if outcome.get("status") == "rejected":
                trace.found_by = "idempotency key"
                trace.notes.append(
                    f"Rejected by the gateway with {outcome.get('reason')!r}. A gateway "
                    "rejection never reaches the stream — there is no path to trace, which is "
                    "the point: it was refused before it could become durable."
                )
                return trace
            if outcome.get("status") == "in_progress":
                trace.notes.append(
                    "The idempotency key is still 'in_progress' — the submission was claimed "
                    "and has not yet been recorded as accepted or rejected."
                )
            if outcome.get("seq"):
                inbound_id = outcome["seq"]
                trace.found_by = "idempotency key"

    # 2. Fall back to walking the log backwards.
    if not inbound_id:
        matches, scanned = await scan_back_for(
            redis, settings.stream_inbound, client_order_id, user_id, back_budget
        )
        trace.notes.append(f"Scanned back {scanned:,} inbound records.")
        if not matches:
            trace.notes.append(
                "Not found. It may be older than the scanned window (raise --scan-back), or "
                "it may never have reached the stream at all — pass --user so the gateway's "
                "idempotency key can be read, which is where a pre-stream rejection lives."
            )
            return trace
        if len(matches) > 1:
            users = {getattr(r, "user_id", None) for _sid, r in matches}
            if len(users) > 1:
                trace.ambiguous = [
                    {
                        "stream_id": sid,
                        "user_id": getattr(r, "user_id", None),
                        "record": type(r).__name__,
                    }
                    for sid, r in matches
                ]
                trace.notes.append(
                    "A client_order_id is unique per user, not globally (Open Issue 008), and "
                    f"this one belongs to {len(users)} different users. Re-run with --user to "
                    "say which order you mean; guessing between them would be a lie."
                )
                return trace
        inbound_id, inbound_record = matches[-1]
        trace.found_by = "inbound scan"

    if inbound_record is None:
        inbound_record = await inbound_at(redis, settings.stream_inbound, inbound_id)
    if inbound_record is None:
        trace.notes.append(
            f"The gateway recorded inbound id {inbound_id}, but no record is there. The "
            "stream is capped by MAXLEN, so this order has most likely aged out of it."
        )
        trace.inbound_id = inbound_id
        return trace

    trace.inbound_id = inbound_id
    if trace.user_id is None:
        trace.user_id = getattr(inbound_record, "user_id", None)

    # The hops. The gateway's own stamp is the only nanosecond figure.
    trace.hops.append(
        Hop(
            "gateway accepted",
            getattr(inbound_record, "timestamp_ns", 0),
            detail=f"{type(inbound_record).__name__} from user {trace.user_id}",
        )
    )
    trace.hops.append(
        Hop(
            "inbound append",
            stream_id_ms(inbound_id) * NS_PER_MS,
            detail="durable — this id IS the sequence number",
            coarse=True,
            stream_id=inbound_id,
        )
    )

    # 3. What the engine did with it.
    hits, order_id, scanned = await scan_forward(
        redis,
        settings.stream_outbound,
        inbound_id,
        client_order_id,
        trace.user_id,
        forward_budget,
    )
    trace.order_id = order_id
    trace.notes.append(f"Scanned forward {scanned:,} outbound records.")
    for sid, record in hits:
        trace.hops.append(
            Hop(
                "engine output" if not isinstance(record, Fill) else "fill",
                stream_id_ms(sid) * NS_PER_MS,
                detail=describe(record),
                coarse=True,
                stream_id=sid,
            )
        )
    if not hits:
        trace.notes.append(
            "No outbound record found for it within the forward window. If the order was "
            "appended very recently the engine may not have answered yet; otherwise raise "
            "--scan-forward."
        )
    return trace


def render(trace: Trace) -> str:
    lines: list[str] = []
    who = f"user {trace.user_id}" if trace.user_id is not None else "user unknown"
    lines.append(f"\nclient_order_id {trace.client_order_id} · {who}")
    if trace.found_by:
        lines.append(f"found by: {trace.found_by}")
    if trace.order_id:
        lines.append(f"engine order_id: {trace.order_id}")
    if trace.gateway_outcome:
        lines.append(f"gateway outcome: {json.dumps(trace.gateway_outcome)}")

    if trace.ambiguous:
        lines.append("\nambiguous — this client_order_id belongs to more than one user:")
        for match in trace.ambiguous:
            lines.append(
                f"  {match['stream_id']:>22}  user {match['user_id']}  {match['record']}"
            )

    if trace.hops:
        lines.append("")
        lines.append(
            f"  {'hop':<16} {'stream id':>22} {'at (UTC)':>16} {'+hop':>10}  detail"
        )
        lines.append("  " + "-" * 104)
        previous = 0
        previous_coarse = False
        for hop in trace.hops:
            if previous and hop.at_ns:
                delta_ms = (hop.at_ns - previous) / NS_PER_MS
                # A stream id names the millisecond an append landed in, truncated to that
                # millisecond's start. So a hop measured from the gateway's nanosecond stamp
                # to a stream id reads up to 1 ms low, and can come out slightly negative —
                # which is truncation, not an append that happened before the order existed.
                # The tilde marks every delta that has a truncated end, rather than quietly
                # clamping a number the reader would then have no way to question.
                approx = hop.coarse or previous_coarse
                delta = f"{'~' if approx else ''}{delta_ms:+.3f} ms"
            else:
                delta = "—"
            lines.append(
                f"  {hop.name:<16} {hop.stream_id:>22} {clock(hop.at_ns):>16} "
                f"{delta:>10}  {hop.detail}"
            )
            if hop.at_ns:
                previous = hop.at_ns
                previous_coarse = hop.coarse
        first = next((h.at_ns for h in trace.hops if h.at_ns), 0)
        last = max((h.at_ns for h in trace.hops if h.at_ns), default=0)
        if first and last > first:
            lines.append(
                f"  {'total':<16} {'':>22} {'':>16} ~{(last - first) / NS_PER_MS:>6.3f} ms"
            )
        lines.append(
            "\n  ~ marks a hop whose end is a Redis stream id. An id names the millisecond the"
            " append landed\n  in, truncated to its start, so such a hop reads up to 1 ms low and"
            " may even print negative.\n  That is truncation, not time travel. For the latency"
            " budget at nanosecond resolution, from\n  the client's side, see"
            " benchmarks/bench_e2e.py."
        )

    for note in trace.notes:
        lines.append(f"\nnote: {note}")
    return "\n".join(lines) + "\n"


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="Reconstruct one order's path through the exchange, with per-hop timings."
    )
    parser.add_argument("client_order_id", type=int)
    parser.add_argument(
        "--user",
        type=int,
        default=None,
        help="user id — a client_order_id is unique per user, not globally",
    )
    parser.add_argument("--scan-back", type=int, default=200_000)
    parser.add_argument("--scan-forward", type=int, default=200_000)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    settings = default_settings
    redis = Redis.from_url(settings.redis_url, decode_responses=False)
    try:
        trace = await build_trace(
            redis,
            settings,
            args.client_order_id,
            args.user,
            args.scan_back,
            args.scan_forward,
        )
    finally:
        await redis.aclose()

    if args.json:
        print(json.dumps(trace.as_dict(), indent=2))
    else:
        print(render(trace), end="")
    return 0 if trace.hops or trace.gateway_outcome else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
