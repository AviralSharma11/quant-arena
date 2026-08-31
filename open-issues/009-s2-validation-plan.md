# Open Issue 009 — S2 (Redis Streams) Validation Plan

**Status:** CLOSED (2026-08-28) — S2 locked without running the spike; see §11
**Opened:** 2026-08-27
**Blocks:** Open Issue 003 (S2 is provisional until this reports)
**Owner:** _unassigned_

---

## 1. Why this issue exists

Open Issue 003 provisionally selected **S2 — Redis Streams** as the durable ordered record,
in preference to **S1 — a hand-written memory-mapped append-only log**. That selection was
made on reasoning alone, and it is a decision that is expensive to reverse late: it
determines the transport, the durability model, the retention strategy, and whether an
archiver component is needed at all.

This issue exists so the choice can be settled by **measurement rather than argument**. It is
structured as a timeboxed spike with explicit pass/fail criteria and a stated rule for what
would send the decision back to S1.

**Nothing here needs to be decided in advance.** The point is to run the experiments and let
them decide.

---

## 2. The case for S2, restated

Redis Streams is structurally the same design as a hand-written append-only log — a monotonic
append-only sequence with independent per-consumer offsets. It preserves total ordering,
replay from an arbitrary offset, deterministic reconstruction, and independent consumers.

It removes roughly **30–40 hours** of hand-written code from the one part of the system where
a defect silently invents or destroys money, and replaces it with a component that is already
tested and operationally understood.

## 3. The case against S2

| Concern | Detail |
|---|---|
| Latency floor | ~50–100 µs per hop on loopback, against well under 1 µs for `mmap` |
| Durability window | `appendfsync everysec` risks up to 1 s of acknowledged orders; `always` may be too slow |
| Memory, not disk | Streams live in RAM, so replay-from-genesis is no longer free and an archiver becomes necessary |
| Critical-path dependency | If Redis is unreachable, no orders can be accepted |
| Learning value forgone | Hand-writing the log teaches `mmap`, memory ordering, and group commit directly |

That last row is not a technical argument and should not be dressed as one — but it is a
legitimate reason to choose S1, and if it is the deciding factor it should be recorded as
such.

---

## 4. Experiments

Each is small, and each has a criterion decided **before** it is run.

### E1 — Durability versus throughput

Measure sustained `XADD` throughput under three configurations: `appendfsync everysec`;
`appendfsync always` unpipelined; `appendfsync always` with pipelined batches of 10, 50 and
100.

**Pass:** `always` + pipelining sustains ≥ 20k records/sec.
**Consequence if it fails:** either accept a documented 1-second durability window, or S1
becomes materially more attractive, since real durability was the main reason for group
commit in the first place.

*Estimated: 3 hours.*

### E2 — End-to-end latency through the stream

Measure gateway → `XADD` → engine `XREAD` → process → `XADD` → gateway `XREAD`, reporting
p50, p95 and p99.

**Pass:** p99 under 5 ms, comfortably inside the agreed end-to-end target of 50 ms.
**Note:** if p99 lands in the low hundreds of microseconds, the latency objection to S2
effectively disappears and the decision simplifies considerably.

*Estimated: 3 hours.*

### E3 — Batching behaviour under load

Confirm that `XREAD COUNT n BLOCK` genuinely returns batches under sustained load rather than
one record per call, and measure the effective per-record cost at batch sizes of 1, 10, 100
and 1000.

**Pass:** per-record cost falls to roughly 1 µs at a batch size of 100.
**Why it matters:** the entire throughput case for S2 rests on amortising the network hop
across a batch. If batching does not materialise in practice, that case collapses.

*Estimated: 2 hours.*

### E4 — Memory growth and trimming

Run sustained bot flow and measure actual bytes per record and the resulting growth rate.
Validate that `MAXLEN ~` trimming holds memory at the intended ceiling without stalling
writers.

**Pass:** memory stabilises under trimming; the retained window comfortably exceeds the
engine's snapshot interval, so recovery never needs data that has been trimmed away.

*Estimated: 3 hours.*

### E5 — Consumer group semantics for fan-out

Verify that multiple independent consumers — gateway, fan-out, ledger writer, archiver — each
read at their own offset without interfering, and that a slow consumer does not impede the
others.

**Pass:** four concurrent consumers at independent offsets, with a deliberately slowed
consumer having no measurable effect on the rest.

*Estimated: 2 hours.*

### E6 — Failure and restart behaviour

Kill Redis mid-load and restart it. Confirm that AOF recovery restores the stream, that no
acknowledged order is lost, and that the gateway enters an explicit halt state rather than
failing silently or accepting orders it cannot record.

**Pass:** zero acknowledged-but-lost orders; halt state is entered and cleared correctly.

*Estimated: 3 hours.*

**Total spike: approximately 16 hours**, against the 30–40 hours S2 is expected to save. Even
if the spike concludes against S2, the measurements themselves become benchmark-report
material rather than wasted effort.

---

## 5. Decision rule

Recorded in advance so the outcome is not argued after the fact.

| Outcome | Decision |
|---|---|
| E1–E6 all pass | **Confirm S2.** Proceed with the consequences in Open Issue 003 §8.1 |
| E1 fails, others pass | Confirm S2 with `everysec` and a documented 1-second window, **or** revert to S1 if that window is judged unacceptable for the correctness claim |
| E2 or E3 fails | **Revert to S1.** The throughput and latency case for S2 depended on them |
| E4 or E5 fails | Confirm S2, but redesign retention or fan-out accordingly |
| E6 fails | Investigate before deciding; a durability failure is disqualifying for either option |

---

## 6. Open questions for discussion

1. **Is a spike the right response, or is this over-process for a decision of this size?**
   Sixteen hours is roughly 4% of the Phase 1 budget. The counter-argument is that discovering
   in week 4 that the transport is wrong would cost far more.
2. **Is hand-writing the log a stated learning goal?** If it is, that alone can decide the
   issue and the spike becomes unnecessary. It should be recorded as the actual reason rather
   than justified on performance grounds after the fact.
3. **Is a 1-second durability window acceptable for a play-money exchange?** It is defensible
   if documented. It does slightly weaken the claim of having built a real exchange core, and
   that is a positioning judgement rather than a technical one.
4. **Is Redis acceptable as a permanent operational dependency**, including in the eventual
   deployment?
5. **Should the spike run in week 1 or week 2?** Week 1 settles the architecture before much
   is built on it; week 2 lets the naive Python engine and gateway land first, giving the
   spike a more realistic harness to measure against.

---

## 7. Decision log

| Date | Status | Note |
|---|---|---|
| 2026-08-27 | OPEN | Created after S2 was placed on hold. Six experiments defined with pass/fail criteria and a decision rule agreed in advance. |

---

## 8. Complexity assessment of S1, requested 2026-08-28

Stated preference: Approach B with a custom memory-mapped log (S1), **unless it adds heavy
complexity**, in which case Redis Streams (S2).

The assessment below is deliberately concrete, because "it is complex" is not a decidable
criterion. **No individual piece of S1 is hard.** Each is between twenty and a hundred lines.
The complexity is concentrated in four places, and not where the earlier estimate in Open Issue
002 suggested.

### 8.1 The four real risks

#### R1 — The writer must also be C++ (this was understated earlier)

The publication protocol requires a **release-store** on the write cursor and an
**acquire-load** on the reader side. In C++ that is five lines of `std::atomic`.

**The gateway is Python, and it is the writer.** Python has no release/acquire atomics. Writing
the cursor with `mmap[0:8] = struct.pack(...)` relies on the platform for both atomicity and
ordering:

| Platform | Behaviour |
|---|---|
| x86-64 | Aligned 8-byte stores are atomic, and stores are not reordered with stores. A naive Python write happens to work |
| **ARM (Apple Silicon)** | **Stores may be reordered.** A reader can observe an advanced cursor before the record body it points at |

**The development machines are macOS on Apple Silicon; the deployment target is Linux x86.**
That is precisely the configuration in which this defect does not reproduce where it is being
debugged, and appears only in production — or on one developer's machine and not the other's.

**Mitigation:** write the journal writer in C++ as well, exposed to the gateway through the
existing nanobind module. Roughly 100 lines and about 8 hours, and it makes S1's correctness
depend on C++ in **both** processes rather than one.

This was not accounted for in the earlier 30–40 hour estimate.

#### R2 — Platform divergence in durability

`msync(MS_SYNC)` does not carry the same guarantee on macOS as on Linux; durable writes on
macOS generally require `F_FULLFSYNC`. Durability therefore behaves differently on the
development machines than in production — the second dev/prod divergence in a mechanism whose
entire purpose is durability.

#### R3 — Segment rotation boundaries

The fiddliest part. Rolling to a new file requires handling: a reader reaching the end of a
segment before the next exists; partial records at a segment tail; a writer creating segment
N+1 while a reader is still finishing N; and retention deleting a segment a slow consumer has
not yet read.

Roughly 10 hours, and the bugs are boundary conditions that appear under load rather than in
unit tests.

#### R4 — The failure mode is close to untestable

A memory-ordering defect is rare, non-deterministic, platform-dependent, and **corrupts money
silently**. ThreadSanitizer helps within a process; it does not cover two processes sharing a
mapping. There is no reliable way to write a test that fails when the ordering is wrong.

This is the decisive concern. Every other risk in this project is caught by the testing strategy
in Open Issue 010. **This one is not.**

### 8.2 What is genuinely easier in S1 than assumed

Recorded for fairness:

- **Torn writes on process crash are impossible by construction.** A record half-written when
  the process dies was never published, because the cursor was not advanced. Readers cannot see
  it. Machine-level power loss still requires a per-record checksum, which is trivial.
- **Pre-allocated sparse segments avoid remapping entirely.** Growing an mmap is awkward;
  allocating 1 GB sparse files sidesteps it.
- **Multiple independent consumers are genuinely free**, exactly as claimed.

### 8.3 Revised cost comparison

| | S1 | S2 |
|---|---|---|
| Core log implementation | 35 | — |
| C++ journal writer for correct ordering (R1) | 8 | — |
| Segment rotation (R3) | 10 | — |
| Per-record checksums | 2 | — |
| Redis integration | — | 10 |
| Archiver | 8 | 8 |
| Halt-state handling | 4 | 4 |
| **Total** | **67** | **22** |
| Validation spike (this issue) | 16 | 16 |

The earlier figure of 30–40 hours for S1 omitted R1 and understated R3. **The realistic
difference is roughly 45 hours, not 30**, and those hours fall on Dev A, who is already carrying
the engine, the frontend, the testing programme and the backtester.

### 8.4 Assessment against the stated criterion

**S1 crosses the "heavy complexity" line**, on the strength of R1 and R4 together: it forces C++
into both processes, it introduces a defect class that platform divergence hides from
development, and that defect class is the one thing the testing strategy cannot catch.

By the criterion as stated, the answer is **S2**.

### 8.5 The recommended shape — S1 as a Phase 2 optimisation

This does not mean abandoning the memory-mapped log. It means changing when it is built.

**Ship on S2. Then, in Phase 2, replace it with a memory-mapped log and publish the
comparison.**

| | Building S1 first | Building S1 second |
|---|---|---|
| Risk to the 15 October deadline | High — 45 extra hours on the most loaded developer | None |
| Correctness risk during Phase 1 | The untestable defect class is live in the money path | None |
| What the artifact says | "We built a memory-mapped log" | **"We replaced Redis Streams with a memory-mapped log; here is the measured latency difference"** |

The second is a **better** artifact, not a consolation. A migration with before-and-after
numbers demonstrates measurement, justification and execution — precisely what `README.md`
Goal 5 asks for — whereas building it first demonstrates only that it was built.

It also becomes considerably safer to build in Phase 2: by then the differential and property
test suites exist, the invariants are established, and there is a working S2 implementation to
compare against record for record.

### 8.6 The one thing that would change this answer

Open Issue 009 §6.2 asked whether hand-writing the log is a **stated learning goal**, and it was
not answered.

If understanding `mmap`, memory ordering and group commit is something the team wants to have
done — rather than something adopted for performance — that is a legitimate and sufficient
reason to choose S1, and it overrides the assessment above. It should then be recorded as the
actual reason, because the performance argument does not support it: matching is roughly 0.002%
of end-to-end latency (Open Issue 012 §3), so the transport is not what makes this system fast.

## 9. Decision log

| Date | Status | Note |
|---|---|---|
| 2026-08-27 | OPEN | Six experiments defined with pass/fail criteria and a decision rule |
| 2026-08-28 | ASSESSED | S1 preference recorded, conditional on complexity. Assessment: S1 crosses the stated line (R1, R4); revised gap ~45 h, not ~30. Recommended shape is S2 now, S1 as a Phase 2 migration with published before/after numbers. Awaiting confirmation; the learning-goal question in §8.6 would override. |

---

## 10. S1 selected — mitigation plan, 2026-08-28

S1 was selected after the §8 assessment recommended against it. That assessment is retained; it
is not withdrawn. What follows is the plan to make S1 as safe as it can be, since the risks in
§8.1 are now risks the project owns rather than risks it avoided.

The validation spike in §4 is **cancelled** — its experiments measured Redis, which is no longer
the transport. Sixteen hours return to the schedule.

### 10.1 R1 is solvable, and the mitigation is structural

The R1 hazard was never ARM as such. It was **Python writing the cursor without release
semantics**. Correct `std::atomic` release/acquire is architecturally portable and is exactly
as correct on ARM as on x86.

**Mitigation: the Python gateway never touches the mapping.**

The journal writer is C++, exposed through the existing nanobind module, with a single entry
point of the shape `journal.append(records) -> seq`. The Python side holds no pointer, no
`mmap` object, and no cursor. Making the unsafe path *unavailable* rather than merely
discouraged is what closes this risk — a comment saying "do not write the cursor from Python"
would not.

Cost: approximately 8 hours, already included in the 67-hour figure.

**With this in place, R1 is closed** rather than mitigated. It was a consequence of the Python
writer, and the Python writer is gone.

### 10.2 R2 — develop against Linux

`msync` semantics differ on macOS. **Mitigation:** run the full stack in Linux containers on the
development machines, so the durability code path never executes natively on macOS. The
architecture remains ARM under Apple Silicon, which §10.1 has already made safe.

Native macOS execution should be treated as unsupported rather than as a second target to keep
working.

### 10.3 R3 — a concrete segment-rotation design

Specified now rather than discovered under load:

| Element | Rule |
|---|---|
| Segment size | Fixed, pre-allocated as a sparse file (~1 GB). Never grown, so no remapping |
| End of segment | A **sentinel record** is written as the final record of every segment |
| Reader behaviour | On reading the sentinel, close the segment and open the next by name, spinning until it exists |
| Partial tail | Impossible — a record is published only after its body is written and the cursor advanced |
| Retention | A segment is deleted only when **every** registered consumer's checkpoint is past its end |

The last rule is the one most easily forgotten and the one whose absence loses data silently.

### 10.4 R4 — largely testable after all

R4 was called "close to untestable" because ThreadSanitizer does not cover two processes sharing
a mapping. **There is a way around that, and it materially changes the risk.**

The publication protocol — release-store the cursor, acquire-load the cursor, read the body — is
**identical whether the writer and reader are two processes or two threads.** So:

> Build a single-process test harness that runs the same writer and reader code as two threads
> over the same mapping, and run it under ThreadSanitizer.

TSan works fully in that configuration and detects exactly the class of ordering defect that R4
describes. The deployed system then runs the same protocol across processes.

This does not prove the cross-process case, because TSan cannot observe it. But **the logic
being verified is the same logic**, and the overwhelming majority of ordering defects are
protocol errors rather than deployment-topology errors.

Supplemented by:

- A **soak test**: writer and reader hammering the log for hours, with the reader verifying a
  per-record checksum and unbroken sequence continuity. It cannot prove correctness, but it
  reliably catches gross errors.
- **Per-record CRC32**, which turns a torn or misordered read into a detected error rather than
  a silent one.

**Revised assessment of R4: it moves from "untestable" to "testable in-process, verified by
checksum in production."** That is a meaningful improvement over the §8.4 position, and it is
the single most valuable thing to build early — before the C++ engine, since the journal is what
the engine reads.

### 10.5 Recommended build order

Ahead of everything else in weeks 1–2, since correctness here underpins all of it:

1. Journal record format and the codegen from Open Issue 016
2. C++ journal writer and reader, with correct atomics
3. **The single-process TSan harness from §10.4**
4. Segment rotation per §10.3, with the retention rule
5. Group commit and `msync`, benchmarked for the batch-size trade-off
6. Only then, the C++ matching engine

### 10.6 What is retained from the S2 analysis

The archiver, the halt state, and the retention discipline are all still required — see Open
Issue 003 §11.2 for how their purpose changes under S1.

## 11. Decision log

| Date | Status | Note |
|---|---|---|
| 2026-08-27 | OPEN | Six experiments defined with pass/fail criteria |
| 2026-08-28 | ASSESSED | S1 assessed as crossing the stated complexity line; S2 recommended |
| 2026-08-28 | **CLOSED** | S1 selected notwithstanding. Spike cancelled (−16 h). Mitigation plan in §10; R1 closed structurally, R4 downgraded from untestable to testable in-process. |

---

## 11. Closed 2026-08-28 — S2 locked without the spike

Open Issue 003 §12 locked **S2**. The spike in §4 existed to decide between S1 and S2; that
decision has been made directly, so the spike is **not run**. Section 10 (the S1 mitigation
plan) is **withdrawn** and retained only as history.

### 11.1 What survives as ordinary configuration work

Four of the six experiments measured things that must be known regardless of whether they were
ever decision criteria. They are folded into the Redis integration line rather than run as a
separate spike:

| From | Now | Hours |
|---|---|---|
| E1 — durability versus throughput | Choose and record the `appendfsync` setting: `always` with pipelining if it sustains the target, otherwise `everysec` with the window documented | 2 |
| E3 — batching behaviour | Confirm `XREAD COUNT n BLOCK` returns real batches under load, and set the batch size | 1 |
| E4 — memory growth and trimming | Measure bytes per record; set `MAXLEN` so the retained window exceeds the snapshot interval | 1 |
| E6 — failure and restart | Verify AOF recovery and the halt state — already required by the reliability checklist in OI 013 §6 | 1 |

**Total: 5 hours**, against 16 for the spike as originally scoped.

E2 (end-to-end latency) and E5 (consumer-group semantics) are dropped as separate exercises;
both are covered by the benchmark work in Open Issue 012 and by the fan-out implementation
itself.

### 11.2 Why the S1 analysis is kept

Sections 8 and 10 are retained because they are the substance of the answer to "why not a
memory-mapped log?" — a question this design invites, and one worth being able to answer with
a costed comparison and a named risk rather than a preference. The migration remains available
as a Phase 2 project with published before/after numbers (§8.5).

## 12. Decision log

| Date | Status | Note |
|---|---|---|
| 2026-08-27 | OPEN | Six experiments defined with pass/fail criteria |
| 2026-08-28 | ASSESSED | S1 assessed as crossing the stated complexity line |
| 2026-08-28 | S1 selected, mitigation plan written | §10 |
| 2026-08-28 | **CLOSED** | S2 locked directly. Spike not run; §11.1 folds 5 hours of configuration work into Redis integration. |
