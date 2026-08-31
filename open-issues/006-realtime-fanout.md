# Open Issue 006 — Real-Time Market Data Fan-Out

**Status:** CONFIRMED (2026-08-28) — all sub-decisions agreed; not marked LOCKED pending your word
**Opened:** 2026-08-27
**Independent of:** Open Issue 003 (decidable in parallel)
**Serves:** README.md Goal 2 (real-time user experience), Goal 5 (performance)
**Owner:** _unassigned_

---

## 1. The problem

When a trade occurs, every connected client watching that symbol needs to know. The naive
implementation — forward every event to every client — does not survive contact with the
agreed targets.

```
  20,000 order events/sec  ×  500 connected clients  =  10,000,000 messages/sec
```

That is not achievable in any language on one machine, and it is roughly three orders of
magnitude beyond what a Python process will do.

**So the design problem is not "how do we broadcast quickly." It is "what do we decline to
send."** Every technique below is a way of not sending something.

This is also, as predicted during the Open Issue 002 discussion, where the system's real
bottleneck lives. With a C++ engine matching at 500k orders/sec, the engine is idle; fan-out
is what saturates first. That makes this the component where performance work actually pays,
and therefore the substance of the Goal 5 report.

---

## 2. Where the reductions come from, in order of effect

| Technique | Reduction | Result |
|---|---|---|
| **Subscription filtering** — a client receives only symbols it is watching (typically one of twelve) | ~12× | 1,700 events/sec × 500 clients = 830k msg/sec — still fatal |
| **Conflation** — coalesce updates within a window and send only the latest state | **~85×** | 20 msg/sec × 500 clients = **10k msg/sec** — tractable |
| **Serialise once per symbol per tick**, then write identical bytes to every subscriber | ~500× on CPU | 12 symbols × 20 fps = 240 serialisations/sec — trivial |
| Binary encoding instead of JSON | ~5× on bytes and CPU | Worth doing, but last |

The ordering matters and is worth stating explicitly in the benchmark report: **the
algorithmic reductions are worth two to three orders of magnitude; the encoding change is
worth one.** Reaching for a binary protocol before conflation would be optimising the wrong
layer by a factor of a hundred.

Resulting bandwidth: a top-10 book snapshot is roughly 400 bytes as JSON; 10k msg/sec ×
400 B ≈ **4 MB/s**, which is comfortable.

---

## 3. Sub-decision 7a — Transport

| Option | For | Against |
|---|---|---|
| **WebSocket** | Bidirectional, binary-capable, one connection for both market data and order entry | More moving parts than SSE; no built-in reconnect |
| Server-Sent Events | Simpler; plain HTTP; automatic reconnection built in | Unidirectional and text-only, so a binary feed and a WebSocket order path would still be needed |
| Polling | Trivial | Fails the real-time requirement in README.md Goal 2 |

**Proposed: WebSocket.** Open Issue 005 §4 already anticipates a binary order-entry path
over WebSocket for algorithmic participants; using one transport for both avoids maintaining
two. Human order entry may continue to use plain HTTP POST, where the latency difference is
irrelevant.

---

## 4. Sub-decision 7b — Fan-out topology

### Option A — Fan-out inside the gateway process

**For:** simplest; no additional process; no extra hop.

**Against:** the gateway is the sequencer (Open Issue 001 sub-decision 2b). Coupling
order-intake latency to fan-out load means a burst of connecting clients degrades order
acknowledgement p99. A single slow client can stall the event loop. This is precisely the
saturation predicted in the Open Issue 002 discussion.

### Option B — Separate fan-out process(es)

A dedicated market-data process tails the outbound event stream independently and owns the
WebSocket connections.

**For:** the order path is isolated from fan-out load, so the sequencer's p99 stays clean.
**Scales horizontally for free** — additional fan-out processes each tail the same stream at
their own offset, which the append-only log design already supports with no coordination.
This is a direct payoff from Open Issues 001/002/003.

**Against:** an additional process to run and supervise. Clients must be routed to a
process. Private per-user streams need to know which process holds a given user.

### Option C — Redis pub/sub as the fan-out bus

**For:** decoupled; standard; well understood.

**Against:** an extra hop. Redis pub/sub is fire-and-forget with no replay, so a client
reconnecting mid-gap cannot recover from it. If Open Issue 003 selects the mmap log, this
introduces Redis solely for this purpose.

**Proposed: Option B.**

---

## 5. Sub-decision 7c — Two classes of stream, not one

Market data and private user data have opposite requirements and should not share a
mechanism. This distinction is easy to miss and expensive to retrofit.

| | Market data | Private user data |
|---|---|---|
| Content | Book, trades, bars | Order acks, fills, portfolio, balance |
| Audience | Everyone watching a symbol | Exactly one user |
| Volume | High | Very low (a few messages per user per second) |
| May be dropped? | **Yes** — conflation means the next message supersedes it | **No** — a lost fill is a user seeing wrong state |
| Serialisation | Once per symbol, broadcast to all | Once per message |

**Proposed:** both travel over the same WebSocket connection as distinct message types.
Private messages carry a **per-user sequence number** so a client can detect a gap and
re-synchronise over REST.

---

## 6. Sub-decision 7d — Conflation strategy

**Proposed:** a fixed-rate tick, 10–20 Hz, configurable.

On each tick, per symbol, publish:

| Stream | Content | Notes |
|---|---|---|
| **Book** | Top-N price levels, both sides, as a **complete snapshot** | N = 10 to start |
| **Trades** | Every trade since the last tick, batched | Trades are far rarer than orders; users expect to see every print |
| **Bars** | Completed 1-second or 1-minute candles | Aggregated server-side; also feeds the backtester |

**Conflated top-N snapshots make deltas unnecessary for Phase 1.** Roughly 400 bytes per
message is cheap enough that incremental encoding buys little, and a snapshot is
self-correcting: a client that misses one is fully repaired by the next. Delta encoding
becomes worthwhile only with full depth or much higher update rates, and is recorded as a
Phase 2 optimisation with a before/after measurement.

---

## 7. Sub-decision 7e — Encoding

**Proposed: JSON for Phase 1. Measure. Then add a binary encoding as a documented
optimisation with before/after numbers.**

JSON is debuggable in a browser, needs no client-side decoder, and — per §2 — is worth only
about 5× where conflation is worth 85×. Adopting a binary protocol first would be optimising
the wrong layer, and `README.md` §5 explicitly rejects adding technology ahead of a
demonstrated problem.

The constraint that makes this safe: **design the message schema so a binary encoder can
replace the JSON encoder without changing the schema.** Fixed field order, integer types,
no dynamic keys.

---

## 8. Sub-decision 7f — Slow clients and backpressure

A client on poor network cannot keep up. The three available responses:

| Response | Consequence |
|---|---|
| Buffer without limit | Memory exhaustion; one bad client kills the process |
| Drop messages | Client sees stale state — normally unacceptable |
| Disconnect | Harsh, and unnecessary for most cases |

**Choosing conflation in 7d makes this almost trivial for market data.** Because every book
message is a complete snapshot, **dropping is free** — the next tick fully repairs the
client. The policy becomes: if a client's send buffer is non-empty when the next tick fires,
skip that client for that tick. A slow client simply receives a lower frame rate, and it
cannot affect any other client.

**Private data cannot be dropped.** Policy: a bounded per-user buffer; on overflow,
disconnect the client and let it re-synchronise over REST on reconnect, using the per-user
sequence number from 7c to detect what it missed.

This is worth noting as a design payoff: a decision made for throughput reasons (conflation)
also eliminated an entire class of backpressure bug.

---

## 9. Cost summary

| Item | Hours |
|---|---|
| WebSocket server, connection lifecycle, authentication | 8 |
| Subscription management (per-symbol channels) | 4 |
| Conflation tick loop; serialise-once broadcast | 8 |
| Book / trades / bars stream construction | 6 |
| Private user stream with sequence numbers and REST re-sync | 6 |
| Slow-client policy and buffer accounting | 3 |
| **Total** | **35** |

Matches the 35 hours budgeted for "market data + WebSocket fan-out."

---

## 10. Questions to resolve

1. **Conflation rate.** 10 Hz is smooth enough for a human and halves the load of 20 Hz.
   20 Hz feels more alive. This is a demo-feel judgement; it is configurable either way.
2. **Book depth N.** 10 levels is conventional for a retail view. Deeper is more impressive
   on screen and linearly more expensive.
3. **Separate fan-out process from day one, or start inside the gateway and split when
   measurement demands it?** Splitting later is a contained change and would produce a
   genuine before/after benchmark — but it is also a change made under deadline pressure.
4. **Should the trade tape be conflated too?** Proposed above as un-conflated, on the
   grounds that users expect every print and trades are far rarer than orders. Under heavy
   bot load this assumption should be re-measured.

---

## 11. Decision log

| Date | Status | Note |
|---|---|---|
| 2026-08-27 | OPEN | Sub-decisions 7a–7f proposed. Nothing final. |

---

## 12. Amendment 2026-08-27 — answers recorded

- **7d — conflation rate: 20 Hz.** Chosen for demo feel over the lower load of 10 Hz.
  Remains configurable; revisit if fan-out becomes the measured constraint.
- **7b — separate fan-out process from day one.** The alternative (start inside the gateway,
  split when measurement demands it) was rejected because that split would land under
  deadline pressure. The coupled case can still be *simulated* for the benchmark report by
  running a fan-out worker inside the gateway process, giving the before/after comparison
  without the schedule risk.
- **7g — the trade tape is not conflated.** Every print is delivered. Re-measure under heavy
  bot load; trades are far rarer than orders, so this is expected to hold.

### 7h — Book depth: match industry practice by tiering, not by picking a number

There is no single industry standard depth, because real exchanges do not publish one feed.
They publish **tiers**:

| Tier | Content | Real-world examples |
|---|---|---|
| **L1** | Best bid and offer, plus last trade | Universal; the majority of consumers need only this |
| **L2** | Aggregated depth, N price levels per side | NSE market depth publishes **5**; US Level 2 conventionally **10**; Binance depth streams offer **5 / 10 / 20** |
| **L3** | Full order-by-order book | Nasdaq TotalView and equivalents; rarely needed by retail |

**Proposed:** publish **L1 and L2 with N = 10**, and treat tiering itself as the standard
being matched.

Three reasons this is better than choosing a single depth:

1. It mirrors how real market data is actually distributed, which is the more defensible
   answer when asked why.
2. **It reduces load.** Most clients — chart views, portfolio pages, watchlists, and the
   majority of bots — need only L1, which is a fraction of the bytes of L2. Only the client
   with an order book panel open subscribes to L2.
3. N = 10 sits between the NSE convention of 5 and Binance's 20, and matches the US Level 2
   convention, so it is defensible against any of the three reference points.

L3 is explicitly out of scope for Phase 1 and is a natural Phase 2 addition once delta
encoding exists, since full-depth feeds are impractical as repeated snapshots.

## 13. Decision log

| Date | Status | Note |
|---|---|---|
| 2026-08-27 | OPEN | Sub-decisions 7a–7f proposed |
| 2026-08-27 | AMENDED | 20 Hz conflation; separate fan-out process from day one; trade tape un-conflated; 7h tiered L1/L2-10 feeds added. Still not final. |

### 7h amendment — N is a configuration value, not a design choice

N = 5 is **not** easier to build than N = 10. Both are the same operation — take the first N
aggregated price levels from each side of the book. There is no structural difference and no
extra code for the larger value.

The only real difference is bandwidth: N = 5 is roughly half the bytes of N = 10.

**Decided approach:** make N a configuration value with a **default of 10**, so it can be
lowered to 5 during load testing if L2 bandwidth turns out to matter. Cost of making it
configurable: zero, since it must be read from the shared configuration file (Open Issue 007
sub-decision 8d) in any case.

---

## 14. Confirmed 2026-08-28 — all sub-decisions agreed

Every sub-decision in this issue is confirmed as proposed. Nothing required reconciliation.

| Decision | Confirmed | Where |
|---|---|---|
| Transport | WebSocket | 7a |
| Fan-out location | Separate fan-out process, from day one | 7b |
| Market data | Subscription filtering + 20 Hz conflation | 7d |
| Book updates | Complete snapshots, not deltas | 7d |
| Trade tape | Every trade delivered, un-conflated | 7g |
| Private data | Never dropped; per-user sequence numbers | 7c |
| Encoding | JSON first; binary only if measurement proves it necessary | 7e |
| Depth | L1 + L2, configurable N, default 10 | 7h |

**Cost: 35 hours**, matching budget.

### 14.1 The four consequences that must hold elsewhere

Recorded here because each is enforced in a different component, and each is cheap now and
expensive later:

1. **Delta encoding is not built in Phase 1.** Complete snapshots make it unnecessary and make
   dropped frames self-correcting. Phase 2, with a before/after measurement.
2. **Backpressure policy follows from snapshots:** if a client's send buffer is non-empty when
   the next tick fires, skip that client for that tick. A slow client receives a lower frame
   rate and cannot affect any other client. Private data is instead buffered with a bound, then
   the client is disconnected and re-synchronises over REST.
3. **The message schema must be binary-ready** even while JSON is emitted — fixed field order,
   integer types, no dynamic keys (Open Issue 016 sub-decision 16e). Otherwise "binary later"
   becomes a rewrite rather than an encoder swap.
4. **The 20 Hz conflation window is the largest single contributor to end-to-end latency** —
   up to 50 ms against roughly 1 µs for matching (Open Issue 012 §3). It was chosen for demo
   feel, which is legitimate, but the benchmark report must state it rather than let the
   figure stand unexplained.

## 15. Decision log

| Date | Status | Note |
|---|---|---|
| 2026-08-27 | OPEN | Sub-decisions 7a–7f proposed |
| 2026-08-27 | AMENDED | 20 Hz; separate fan-out process; tape un-conflated; tiered L1/L2-10 feeds |
| 2026-08-28 | **CONFIRMED** | All eight sub-decisions agreed as proposed. Consequences for other issues recorded in §14.1. Not marked LOCKED pending explicit confirmation. |
