# Fan-out — market data derived from the outbound stream

Task 5.2a. This half builds the **state and the message shapes**; Task 5.2b adds the WebSocket
server, subscription filtering, the 20 Hz conflation tick, the private per-user stream and the
slow-client policy.

Open Issue 006 §1 frames the whole task as arithmetic rather than engineering:

> 20,000 events/sec × 500 clients is 10 million messages/sec, which is not achievable in any
> language on one machine. So the design problem is not *how to broadcast quickly* — it is
> **what to decline to send.**

It is also where the system's real bottleneck lives: with a C++ engine matching at 500k
orders/sec the engine is idle and fan-out saturates first.

---

## What "done" means for 5.2a

None of Task 5.2's five success criteria can be met by this half — every one of them needs the
WebSocket server, so all five land in 5.2b. Recorded here so that 5.2a is not marked complete on
the strength of nothing. What this half is judged on instead:

1. A book rebuilt from the outbound stream alone matches the matcher's own book, order for order.
2. Killing the process and replaying from `0-0` reproduces identical book, tape and bar state.
3. Every message shape matches `contracts/v1/rest_and_ws.md` §3.2–3.3, asserted field by field.
4. Bars close on the right boundaries and their OHLCV is arithmetically correct.

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

## One question for Dev A, before 5.2b

`rest_and_ws.md` §3.5 states the rule that **every** message carries `seq`, and the client tracks
the last one per channel to detect a gap. But the bar example in §3.3 shows no `seq` and no
`ts_ns`.

Read as an abbreviated example rather than a contradiction, `seq` is included here — a channel
that omitted it would be the one channel a client could not gap-check. It is worth confirming
rather than assuming, since the contract is frozen and this is a shape both sides will code
against.

---

## No compose service until 5.2b

A container that maintains order books and serves nothing to anybody is a container that does
nothing observable. The process and its entry point exist (`python -m services.fanout`) and can be
run by hand against the live stack, which is how this half is verified; the compose service
arrives with the port it needs to expose.
