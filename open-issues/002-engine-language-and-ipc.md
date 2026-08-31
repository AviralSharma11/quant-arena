# Open Issue 002 — Engine Language, Process Boundary, and IPC Mechanism

**Status:** closed by user
**CURRENT STATE:** §3–§4 (memory-mapped log) are **SUPERSEDED** — retained as design reasoning
only. Transport = Redis Streams (`XADD` / `XREAD BLOCK`). §5 engine library, reference model,
nanobind adapter, numerics and benchmark split all stand unchanged.
**Opened:** 2026-08-27
**Depends on:** Open Issue 001 (Approach B — single-writer engine over a sequenced log)
**Blocks:** Open Issue 003 (money source of truth), benchmarking plan, testing strategy
**Owner:** _unassigned_

---

## 1. The problem

Two questions that appear separate but are entangled:

1. What language is the matching engine written in?
2. Does it run inside the Python gateway process, or in its own process?

The second question determines whether the boundary is a function call or a transport hop,
which sets both the performance ceiling and the debugging experience. The first constrains
the second: a Python engine has no reason to be a separate process.

### An honest premise

A pure-Python matching engine sustains roughly **20–60k orders/sec**. The agreed
end-to-end target is **5–20k orders/sec**. Python therefore *meets the functional
requirement*; C++ is not required for the system to work.

C++ is adopted for three stated reasons, and the reasons matter because a false performance
justification will not survive scrutiny:

1. It is the artifact most relevant to quantitative and low-latency engineering roles.
2. It removes the engine as a bottleneck, forcing optimisation work onto market-data
   fan-out — a more transferable performance-engineering problem.
3. The team is already competent in it, so it is cheap for this team specifically.

---

## 2. Options considered

### Option A — Pure Python engine, in-process

**Advantages:** ~20 hours to a tested implementation. No build toolchain, no CMake, no
wheels, no compiler in CI. Segfaults are impossible; every failure has a traceback. Meets
the stated throughput target.

**Disadvantages:** roughly 30× less headroom, so the engine remains the bottleneck at the
upper end of the target range and fan-out costs stay masked. Forfeits the strongest
placement-relevant artifact available to this team. The 500k orders/sec goal is unreachable.

### Option B — C++ engine as an in-process Python extension module

**Advantages:** 500k–2M orders/sec on a single core. The boundary is a plain function call:
no serialisation, no transport, no protocol to design or debug. C++ is confined to the one
component with zero I/O — no sockets, no async, no database, no threads — roughly 700–1000
lines of data structures and a match loop. Approximately 40 hours including bindings.

**Disadvantages:** introduces a build toolchain (CMake, compiler in CI, wheel building).
A C++ fault becomes a segfault that kills the Python process with no traceback. Per-call
binding overhead becomes significant when a match takes ~0.5 µs, requiring a batch API.

### Option C — C++ engine as a separate process

**Advantages:** matches how production exchanges are actually deployed. An engine crash
does not take down the web tier. Cleanest possible separation of concerns.

**Disadvantages — as originally costed (~85 h):** requires either a socket protocol
(framing, partial reads, backpressure, reconnection) or a lock-free circular ring buffer
(memory ordering, cache-line padding, slot reclamation). Both are expensive and both
produce failures that are hard to reproduce.

**This estimate was wrong.** It assumed the two expensive IPC mechanisms and overlooked a
third — see §3. Revised incremental cost over Option B: **~15–20 hours.**

### Option D — Naive reference model, differentially tested (an overlay, not an alternative)

A deliberately naive Python implementation — flat list of orders, re-sorted on every
operation, no optimisation whatsoever, roughly 200 lines and ~8 hours — kept permanently.

The common objection is that maintaining two implementations is waste. That objection is
correct when the second implementation is a *second engine*. It does not apply here,
because the model is an **executable specification**: it encodes the matching *rules*, not
the data structures. The C++ engine changes constantly as it is optimised; the model changes
only when the rules change, which after week 1 is almost never.

It does three jobs for the price of one:

1. **Week-1 engine.** Slow (~5k orders/sec) but functionally complete, so the system is
   demoable end-to-end while the C++ engine is still being written.
2. **Permanent test oracle.** Property-based tests generate random order flow and assert
   that both implementations emit identical event streams.
3. **Interface contract between the two developers.** See §6.

---

## 3. The mechanism that changed the decision: memory-mapped append-only logs

`mmap` maps a file's pages directly into a process's address space. Two processes mapping
the same file with `MAP_SHARED` are backed by the *same physical pages* in the OS page
cache, at different virtual addresses. A write by one is immediately visible to the other —
no syscall, no copy. Because the mapping is backed by a real file, the kernel also persists
it to disk.

That dual nature — shared memory that is simultaneously a durable file — is the key.

### Layout

```
 offset 0
 ┌────────────────┬──────────┬──────────┬──────────┬─────────┬─────
 │     HEADER     │ record 1 │ record 2 │ record 3 │ record 4│ ...
 │ magic          │ len      │ len      │ len      │ len     │
 │ version        │ seq=1    │ seq=2    │ seq=3    │ seq=4   │
 │ write_cursor ──┼──────────┴──────────┴──────────┴─────────┴──┐
 └────────────────┴──────────────────────────────────────────┬──┘
                                                             ▼
                                            everything before the cursor
                                            is complete and valid
```

Records are fixed-layout plain data: sequence number, timestamp, symbol id, side, price in
ticks, quantity, order id. All fixed-width integers. No strings, no pointers, no allocation.

`write_cursor` is the only coordination primitive in the design.

### Publication protocol

**Writer:** copy the record body at `write_cursor`, then **release-store** the advanced
cursor.
**Reader:** **acquire-load** the cursor, then read every record between its own offset and
the cursor.

A single acquire/release pair guarantees that a reader observing the new cursor also
observes the record body written before it. That is the entire concurrency protocol.

### Why this beats a ring buffer here

| Problem | Ring buffer | Append-only log |
|---|---|---|
| Wraparound | Modular arithmetic throughout | Does not exist |
| Slot reuse / reclamation | Must prove reader is finished | Does not exist; bytes are never reused |
| Writer overtaking reader | Silent corruption | Cannot happen |
| Backpressure | Subtle and easy to get wrong | Bounded by disk, not RAM |
| Multiple independent readers | Slowest reader blocks all | Free — each keeps its own offset |

The last row matters: the outbound event log has four consumers (gateway, ledger,
market-data publisher, and later the history/analytics writer). Each simply remembers its
own offset and reads at its own pace. No coordination between them at all.

---

## 4. What this collapses into one component

The system needs, independently of IPC:

- a durable sequenced input log (Open Issue 001, sub-decisions 2b and 2c),
- a durable output event stream consumed by ledger, market data, and backtester,
- a replay-based recovery path (README.md, Problem 9).

If the IPC channel *is* those logs, all three become one mechanism.

```
  Gateway ──append──► [ inbound log: mmap, sequenced, durable ] ──tail──► Engine
                                                                            │
  Gateway ◄──tail─── [ outbound event log: mmap, durable ] ◄──append────────┘
     │                          │
     │                          ├──tail──► Ledger / portfolio
     │                          ├──tail──► Market data → WebSocket fan-out
     └─► clients                └──tail──► (Phase 2) analytics, backtest history
```

**Durability (group commit).** The gateway appends for ~200 µs or N records, calls `msync`
once over that range, *then* publishes the advanced write cursor, *then* acknowledges the
client. One flush amortised across a batch. Anything the engine can see has already reached
disk, because the cursor is published only after the flush.

**Recovery.** The engine periodically checkpoints "processed through offset X" alongside an
order-book snapshot. After a crash it loads the snapshot, sets its offset to X, and reads
forward. **This is the same code path as normal operation** — the main loop is always "read
records from my offset forward and apply them." Recovery is not a special mode, so there is
no separate recovery logic that can drift out of sync with live logic.

---

## 5. Decision

**Option C, built on memory-mapped append-only logs, with Option D as an overlay.**

| Element | Decision |
|---|---|
| Engine core | C++ **static library**, zero I/O, ~700–1000 lines |
| Live transport | Separate engine process; mmap append-only inbound and outbound logs |
| Test/backtest transport | **nanobind** adapter over the same library, in-process |
| Reference model | Naive Python model: week-1 engine, permanent oracle, interface contract |
| Durability | Group commit — batch, `msync`, publish cursor, acknowledge |
| Recovery | Snapshot + replay forward; identical to the normal read loop |
| Numerics | int64 price ticks, int64 quantities, uint64 order ids, int64 nanosecond timestamps injected by the gateway. No floating point below the presentation layer. |
| Benchmarks | Two separately-labelled numbers: native engine throughput, and end-to-end system throughput |

### Why two adapters over one library

```
        ┌─────────────────────────────┐
        │  C++ matching engine        │   pure logic, no I/O
        │  (static library)           │
        └─────────────────────────────┘
              ▲                    ▲
   ┌──────────┴────────┐   ┌───────┴────────────┐
   │ standalone binary │   │ nanobind module    │
   │ tails mmap log    │   │ direct calls       │
   │ → LIVE TRADING    │   │ → TESTS, BACKTESTS │
   └───────────────────┘   └────────────────────┘
```

The second adapter costs roughly six hours and is correct design rather than a compromise.
The backtester driving the *actual* matching engine in-process means backtest fills obey
exactly the same rules as live trading — a claim most simulated backtesters cannot make.
It is also why nanobind was chosen over pybind11: the backtester calls the engine millions
of times in a tight loop, and pybind11's ~1 µs call overhead would dominate a 0.5 µs match.

---

## 6. Consequence: the developer split

The engine interface, defined in week 1 by the naive Python model, is the contract between
the two developers.

- **Dev A — engine:** C++ library, both adapters, mmap log, replay, engine benchmarks.
- **Dev B — platform:** gateway, auth, ledger, bots, market data, fan-out, frontend, backtester.

Dev B codes against the model and is never blocked waiting for C++. Dev A optimises freely
so long as the differential tests pass. Neither has to explain the other's code.

---

## 7. Known costs and risks — accepted, not dismissed

1. **Unbounded file growth.** At 20k orders/sec × 64 bytes ≈ 1.3 MB/s ≈ 110 GB/day.
   Requires **segment rotation** (roll at ~1 GB) plus a retention policy. ~8 hours, and the
   fiddly part is a reader crossing a segment boundary.
2. **Polling costs CPU.** Busy-spinning gives sub-microsecond latency but consumes a full
   core — half of a 2-vCPU cloud instance. Use adaptive backoff: spin, then `nanosleep`,
   then block. Idle latency rises to ~10 µs, which is irrelevant at the target and provides
   a useful benchmark axis for Goal 5.
3. **Both processes must share a machine.** `mmap` shared memory does not cross a network.
   Acceptable for Phase 1. If the engine must ever move to a separate host, the transport
   layer is rewritten — but the engine itself is unaffected, since it only knows "records
   in, records out."
4. **`msync` semantics differ across platforms.** Develop and benchmark on Linux; treat
   macOS as a convenience target only.
5. **Two processes means no single stack trace.** Accepted deliberately; the team reports
   high confidence with gdb, sanitizers, and core dumps.

### On "why not Kafka?"

Kafka's core data structure is a segmented append-only log with per-consumer offsets —
conceptually the same design. What Kafka adds is a network hop, a broker process, and
substantial operational overhead, all of which solve problems this system does not have:
there is a single producer, on a single machine. The defensible position is that the log
structure was adopted and the distribution machinery was not, with a latency measurement to
support it. Revisit only when producers become multiple or cross-host.

---

## 8. Decision log

| Date | Status | Note |
|---|---|---|
| 2026-08-27 | PROPOSED | Option B (in-process nanobind) + D, with Option C deferred to Phase 2 |
| 2026-08-27 | REVISED | Option C re-costed from ~85 h to ~15–20 h after identifying mmap append-only logs as the transport; original estimate had assumed sockets or a circular ring buffer |
| 2026-08-27 | DECIDED | Option C on mmap logs, plus a nanobind adapter for tests and backtesting, plus the naive Python model as executable specification |

---

## 9. Downgraded to PROVISIONAL 2026-08-27

The engine-language portion of this issue (C++ static library, naive Python reference model,
nanobind adapter, integer-only numerics, two separate benchmark numbers) is unaffected by
the alternatives now under evaluation and remains the working proposal.

The **transport and durability portion** — memory-mapped append-only logs — is conditional
on Open Issue 003, which evaluates Redis Streams and a relational database as alternatives
to a hand-written mmap log. If Open Issue 003 selects a different durable record, §3, §4
and the transport row of §5 are superseded; the engine library itself is unaffected, since
it only knows "records in, records out."

---

## 10. Superseded 2026-08-27 — transport replaced by Redis Streams

Open Issue 003 selected **S2 (Redis Streams)** as the durable ordered record. Consequently:

- **§3 and §4 (memory-mapped append-only logs) are superseded.** Retain them as recorded
  design reasoning — the analysis of why an append-only log with per-consumer offsets is the
  right *structure* still holds, and Redis Streams implements exactly that structure.
- **The transport row of §5 is replaced:** the standalone engine adapter reads with
  `XREAD BLOCK` and writes with `XADD` through hiredis or redis-plus-plus.
- **§7 risks 1, 2, 3 and 4 no longer apply** — segment rotation, busy-poll CPU cost,
  the same-machine constraint, and platform `msync` differences all belong to Redis now.
  Risk 5 (two processes, no single stack trace) still applies.

**Everything else in this issue stands unchanged:** the C++ static library with no I/O, the
naive Python reference model as executable specification and week-1 engine, the nanobind
adapter for tests and backtesting, integer-only numerics, two separately-labelled benchmark
numbers, and the developer split in §6.

Worth recording: a complete substitution of the transport layer left the engine library
untouched, because the library only ever knew "records in, records out." That is evidence
the boundary in §5 was drawn in the right place.

> **Conditional 2026-08-27:** the amendment above follows from the S2 (Redis Streams)
> selection in Open Issue 003, which has since been **placed on hold** pending the validation
> plan in Open Issue 009. Treat this amendment as provisional. If S2 is not confirmed, the
> superseded material above it becomes current again.

---

## 11. Reinstated 2026-08-28

Open Issue 003 selected **S1** — a custom memory-mapped append-only log. The supersession
recorded in §10 is therefore **withdrawn**.

**§3 and §4 are current again**: the memory-mapped log layout, the release/acquire publication
protocol, group commit, and recovery by snapshot plus replay forward. The transport row of §5
reverts to the standalone engine tailing the mapped log rather than reading a Redis stream.

**§7 risks 1–4 apply again** — segment rotation, polling CPU cost, the same-machine constraint,
and platform `msync` differences — with concrete mitigations now specified in Open Issue 009
§10.

**One amendment to §5.** The journal *writer* is also C++, exposed to the Python gateway through
the nanobind module (Open Issue 009 §10.1). The Python side never touches the mapping. This is
an addition to the original design, which had assumed the gateway would write to the mapping
directly.

---

## 12. Superseded again 2026-08-28 — Redis Streams is final

Open Issue 003 §12 **locked S2**. The reinstatement in §11 is **withdrawn**.

**Current transport: Redis Streams.** §3 and §4 (memory-mapped log layout, release/acquire
publication protocol, hand-written group commit) are superseded and retained as design
reasoning only. §7 risks 1–4 — segment rotation, polling CPU cost, the same-machine
constraint, and platform `msync` differences — do not apply; they belong to Redis. Risk 5 (two
processes, no single stack trace) still applies.

**The §11 amendment requiring a C++ journal writer is also withdrawn.** With Redis as the
transport, the Python gateway writes via a normal client call and there is no mapping, no
cursor, and no memory-ordering hazard.

**Unchanged, and unchanged throughout all three reversals:** the C++ static library with no
I/O; the naive Python reference model as executable specification, week-1 engine and
differential-test oracle; the nanobind adapter for tests and backtesting; integer-only
numerics; two separately-labelled benchmark numbers; and the developer split in §6.

That the transport was substituted three times without the engine library changing once is the
strongest available evidence that the boundary in §5 was drawn in the right place. **This is
worth stating in the README** — it is a concrete demonstration of why the interface was defined
before either implementation, rather than an abstract claim about modularity.

---

## Amendment 2026-08-28 — no snapshots in Phase 1 (Open Issue 018 §13.1)

Section 4 describes recovery as "load the snapshot, set the offset, read forward." **Phase 1 has
no snapshots.** The engine rebuilds its order book by replaying the retained stream from its
start, which at a `MAXLEN` of ~2 M entries takes on the order of ten seconds.

The property that made snapshotting attractive is unaffected and is in fact strengthened:
recovery remains **the same code path as normal operation** — the main loop is still "read
records from my offset forward and apply them", now always starting from the beginning of the
retained window. There is one less mechanism and one less consistency question, namely whether a
snapshot matches the offset recorded beside it.

Checkpointing returns in Phase 2 as an optimisation, with a measured before/after on recovery
time — a metric `README.md` Goal 5 already names.
