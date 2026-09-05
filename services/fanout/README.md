# Fan-out — market data derived from the outbound stream

Task 5.2, both halves. **5.2a** built the state and the message shapes; **5.2b** added the
WebSocket server, subscription filtering, the 20 Hz conflation tick, the private per-user stream,
the slow-client policy and the halt relay.

Open Issue 006 §1 frames the whole task as arithmetic rather than engineering:

> 20,000 events/sec × 500 clients is 10 million messages/sec, which is not achievable in any
> language on one machine. So the design problem is not *how to broadcast quickly* — it is
> **what to decline to send.**

It is also where the system's real bottleneck lives: with a C++ engine matching at 500k
orders/sec the engine is idle and fan-out saturates first.

---

## The files

| File | What it owns |
|---|---|
| `book.py` `bars.py` `state.py` | pure derivation from the stream — no clock, no socket, no Redis |
| `messages.py` | every wire shape, and the channel registry |
| `runner.py` | the stream tail, and recovery by full replay from `0-0` |
| `subscribers.py` | who is connected, and what is declined to whom |
| `conflation.py` | the 20 Hz tick — the one place market data leaves this process |
| `private.py` | the per-user stream and its dense sequence |
| `halt.py` | reading the halt state the gateway publishes |
| `server.py` | `/stream`, `/health`, and the loops |

## Where each success criterion is proved

| # | Criterion | Evidence |
|---|---|---|
| 1 | 200+ clients, no measurable ack degradation | `benchmarks/results/5.2-fanout-conflation.md` — median 3.50 → 3.65 ms |
| 2 | a slowed client gets fewer frames and affects no other | `tests/fanout/test_conflation.py` |
| 3 | reconnect recovers the full book from the next snapshot | `tests/fanout/test_stream_server.py` |
| 4 | a private-stream gap is detectable from its sequence | `tests/fanout/test_private.py`, through the shipped TypeScript tracker |
| 5 | serialisation once per symbol per tick | `Hub.serialisations` — 400 encodes at 1, 50 and 200 clients |

5.2a could meet none of these on its own, so it was judged on its own four instead: the derived
book matching the matcher's order for order, replay reproducing identical state, message shapes
matching §3.2–3.3 field by field, and bars closing on the right boundaries. All still hold.

---

## Four decisions, and what would make each worth revisiting

### 1. The book is derived, not read from `BookChanged`

`schema.toml` designed `BookChanged` for exactly this — one aggregated price level per record,
with fan-out reassembling the book. **Nothing emits it.** Outside the generated contracts and two
schema tests, the record type appears nowhere.

So the book is rebuilt from `OrderAccepted` + `Fill` + `OrderCancelled`, which between them carry
everything needed. The alternative was to emit `BookChanged` from `services/matcher/adapter.py`,
and it was rejected for one reason: the C++ engine would have to emit it too at the week-5
integration point, and that is Dev A's work — creating exactly the same-week cross-developer
dependency Appendix D.4 forbids.

The derived approach has a second benefit worth keeping even if `BookChanged` appears later: it
is **engine-agnostic**, so the week-5 swap from the naive model to the C++ engine does not touch
fan-out at all.

> **Revisit when:** the C++ engine emits `BookChanged` natively and the extra records are cheaper
> than the reconstruction — measure both before switching, and note that switching re-couples
> fan-out to the engine's choice of records.

### 2. A focused `Book`, not a shared resting-order projection

`services/gateway/risk.py`, `services/ledger/ledger.py` and `services/matcher/adapter.py` each
already rebuild resting orders from the same three records. This is the fourth.

They need genuinely different shapes — cash reservations per user, open orders per user, engine
book per symbol, aggregate depth per price level — so a shared abstraction serving all four would
be worse than four small correct ones. Duplication was chosen with open eyes.

> **Revisit when:** a fifth consumer needs it, or when two of the four disagree about the same
> stream. That second one is the real risk: the maker-price defect in `adapter.py` showed how
> subtle a divergence can be, and four copies of event-handling logic is how they drift.

### 3. Price levels are aggregated on read

`Book.levels()` walks the resting orders and sums by price each time it is asked, rather than
maintaining a price→quantity map alongside. A maintained map is faster and is a second structure
that can fall out of step with the first; aggregating on read cannot drift by construction.

At Phase 1 scale this is not close to mattering: ten symbols, a few hundred resting orders, ten
levels per side, twenty times a second.

> **Revisit when:** 4.3 or 7.4 measures it as a real cost — this is the file to look at first when
> fan-out is the bottleneck, since it is the one place that is knowingly O(resting orders) per
> tick.

### 4. Bars bucket on stream time, with the width in configuration

Bars use `Fill.timestamp_ns` — the timestamp the gateway stamped, which every consumer replays
identically. The width comes from `market_data.bar_bucket_seconds`.

> **Revisit at Task 5.1**, which is when this stops being academic. The replay clock maps one real
> second to one simulated minute (Open Issue 005 §5g), so "a one-minute bar" becomes ambiguous —
> one real minute, or one simulated minute? The backtester in 7.1 consumes whichever this means,
> which is why Open Issue 005 §10.5 says the ratio must be stamped into the stream. The width is
> configuration precisely so 5.1 can answer this by changing a line rather than the aggregation.

---

### 5. `seq` on `private` is a dense per-user counter

§3.4's example shows `"seq": "1693526400000-9"`, a Redis stream id. §3.5 requires the client to
detect a gap from `seq`, and Open Issue 006 §7c says private messages carry a per-user sequence
number precisely so that a gap *is* detectable.

Both cannot be literal. Stream ids count every record on the stream, most of which concern other
users, so they are dense for nobody — under the §3.4 reading no private gap is detectable and
Success Criterion 4 cannot be met by any implementation. So `private.py` stamps a dense integer,
one per message delivered to that user, which is what `web/src/stream/gaps.ts` already assumed.

> **Revisit when:** Dev A answers. The reading is isolated in `PrivateRouter.next_seq` on this
> side and in `privateSequence()` on the client, so it is one function each.

---

## Two open items with Dev A

Both are contract questions rather than design choices, and both are answerable in one message.

1. **Does a `bars:*` message carry `seq`?** §3.5 says every message does; the §3.3 example omits
   it. Read here as an abbreviated example, since a channel without `seq` would be the one
   channel a client could not gap-check.
2. **`resumed` is a fifth error code, and it is a deviation from the frozen contract.** §3.6
   enumerates `unauthenticated`, `unknown_channel`, `slow_consumer` and `halted` — all failures,
   with no way to say a halt has *ended*. Something has to: a halt clears on its own within one
   watchdog interval (Task 2.1 Success Criterion 5), so an indicator that could only be reset by
   reconnecting would show HALTED over a working exchange for as long as the tab stayed open.
   Inferring resumption from market data is simply wrong — market data keeps flowing throughout a
   halt, because it is only the *appending* of new orders that stopped. The addition is the
   smallest available: a client that does not know the code ignores an error it cannot classify.

## The halt relay

`contracts/v1/rest_and_ws.md` §3.6 gives the browser a `halted` frame and `web/src/stream/
client.ts` has rendered it since 5.4c, but until this task **nothing could send one**. The halt
flag is a plain object in gateway process memory (Open Issue 004 keeps risk state out of Redis),
and fan-out is a different process.

So the gateway publishes it — `services/gateway/streams.py` `HALT_KEY`, written by the watchdog
that was already polling — and `halt.py` reads it. Fan-out pinging Redis itself would have been
cheaper and would have been a different claim: "fan-out can reach Redis" is not "the gateway can
durably record orders", and the two come apart in the direction that matters when a store is
readable but not writable.

An absent key is itself a halt. With nothing publishing, no order can be recorded — and it
clears on its own within one poll of the gateway returning.

## Running it

    docker compose up -d                    # fanout is a service from 5.2b, port 8001
    curl localhost:8001/health              # stream position, ticks, subscribers, serialisations

    QA_REDIS_URL=redis://localhost:6379/0 python -m services.fanout    # or by hand

The Vite dev proxy sends `/stream` **straight to port 8001**, not through the gateway. Proxying
two hundred WebSocket connections through the gateway would recreate exactly the connection load
Open Issue 006 §7b exists to keep off the order path.
