# Open Issue 007 — Service Boundaries and Deployment Shape

**Status:** RESOLVED (2026-08-28) — framing plus 8a, 8d, 8e confirmed; 8b deployment deferred
**CURRENT STATE:** One project · separate processes · one coherent event flow. No co-location
constraint (S2 locked in OI 003). Sections 12–14 record the reversal history; §16 is current.
**Opened:** 2026-08-27
**Depends on:** Open Issue 003 (co-location constraint)
**Consolidates:** the process topology implied by Open Issues 001, 002, 005, 006
**Owner:** _unassigned_

---

## 1. The problem

Four separate decisions have each introduced a process, without anyone deciding the overall
topology:

- Open Issue 001/002 — the matching engine runs as its own process.
- Open Issue 001 — the gateway is the sequencer and owns risk state.
- Open Issue 005 — bots run as separate processes over the real API.
- Open Issue 006 — fan-out runs as a separate process from day one.

This issue consolidates that into one picture, checks it is coherent, and decides how it is
deployed. It also resolves an apparent contradiction in `README.md`.

---

## 2. Resolving the contradiction in README.md

`README.md` §1 lists "large-scale microservices" as **not required**, while Problem 6 asks
that analytics, logging, and notifications not slow the trading path. Taken naively these
pull in opposite directions, and the risk is that someone later "resolves" it by splitting
the system into a dozen services.

The resolution is that **this architecture is not microservices**, and the distinction is
worth stating precisely because it is what makes the design defensible rather than
resume-driven:

| | Microservices | This system |
|---|---|---|
| Split along | Business domains (users, orders, payments) | **Execution characteristics** |
| Deployed | Independently, on separate schedules | **One unit, one version, together** |
| Communicate via | Network RPC / HTTP between services | An ordered event stream on one machine |
| Owned by | Separate teams | Two developers, one repository |
| Data | A database per service | One event stream, one derived read model |

The processes here exist because they have genuinely incompatible execution requirements: a
single-writer engine that must never block, an event loop that must sequence with low
jitter, and I/O-bound socket fan-out that scales with connection count. Those cannot share a
process without one degrading another — which is the same reason `README.md` Problem 6
gives.

**Proposed framing for the README: a modular monolith with process separation by execution
requirement — a pipeline architecture, not a service mesh.**

---

## 3. Sub-decision 8a — Process inventory

```
                     ┌──────────────────────────────────────────┐
   browsers ─────────►  GATEWAY  (Python, single event loop)    │
   (HTTP + WS)       │  • auth, validation, idempotency          │
                     │  • risk checks + reservations (OI 004)    │
                     │  • assigns sequence numbers (OI 001, 2b)  │
                     └───────────────┬──────────────────────────┘
                                     │ append
                     ┌───────────────▼──────────────────────────┐
                     │        INBOUND ORDERED STREAM             │  ← technology
                     └───────────────┬──────────────────────────┘     per OI 003
                                     │ tail
                     ┌───────────────▼──────────────────────────┐
                     │  ENGINE  (C++, single writer)             │
                     │  • order book per symbol, matching only   │
                     │  • money-blind (OI 004)                   │
                     └───────────────┬──────────────────────────┘
                                     │ append
                     ┌───────────────▼──────────────────────────┐
                     │       OUTBOUND EVENT STREAM               │
                     └──┬────────────┬────────────┬─────────────┘
                        │ tail       │ tail       │ tail
              ┌─────────▼──┐  ┌──────▼──────┐  ┌──▼──────────────┐
              │  GATEWAY   │  │  FAN-OUT    │  │  LEDGER WRITER  │
              │ (releases  │  │ (×N, OI 006)│  │ → read model DB │
              │ reserv'ns) │  │ 20 Hz, L1/L2│  │                 │
              └────────────┘  └──────┬──────┘  └─────────────────┘
                                     │ WebSocket
   bots (separate procs) ────────────┴──────────────► browsers
   over the real API (OI 005)
```

| Process | Language | Count | Why separate |
|---|---|---|---|
| Gateway | Python | 1 | Must be the single sequencer; low-jitter event loop |
| Engine | C++ | 1 | Single writer; must never block on I/O |
| Fan-out | Python | N (1 in Phase 1) | I/O-bound; scales with connections; must not affect order latency |
| Ledger writer | Python | 1 | Batched database writes; explicitly off the hot path |
| Bots | Python | 1–2 | Real API clients; also the load generator |
| Read model DB | — | 1 | Derived state (OI 004 sub-decision 4a) |
| Redis | — | 0 or 1 | Only if Open Issue 003 selects it |

---

## 4. Sub-decision 8b — Deployment unit

**A constraint from Open Issue 003 must be resolved first.** If the ordered stream is a
memory-mapped file (S1), the gateway, engine, fan-out, and ledger writer **must share a
machine**, because `mmap` shared memory does not cross a network. If it is Redis Streams
(S2), that constraint disappears.

**This is a deployment consequence of Open Issue 003 and should be weighed when deciding it.**
For Phase 1 targets it changes nothing — everything fits comfortably on one machine either
way — but it determines whether horizontal scaling is even possible later without replacing
the transport.

| Option | For | Against |
|---|---|---|
| **Single VM + `docker-compose`** | ~$10–20/month; satisfies co-location; trivial to reason about; one `up` command locally and in production | Single point of failure; vertical scaling only |
| Managed container platform (Fly.io, Railway, Render, ECS) | Less operations work; TLS and deploys handled | Co-locating processes with shared memory ranges from awkward to impossible; costlier |
| Kubernetes | Scales; conventional | Explicitly excluded by `README.md` §1; weeks of work for two developers; solves no problem this system has |

**Proposed: a single VM running `docker-compose`, with 2–4 vCPU.**

Note the interaction with Open Issue 002 §7: busy-polling the stream consumes a full core.
On a 2-vCPU instance that is half the machine. **Adaptive backoff — spin, then sleep, then
block — is mandatory in the deployed configuration**, not optional.

---

## 5. Sub-decision 8c — Supervision and startup ordering

Processes have a genuine startup dependency: the streams must exist before the engine tails
them, and the engine should be consuming before the gateway accepts orders.

**Proposed:** `docker-compose` with health checks and `depends_on`, plus a restart policy on
each service. Each consumer already recovers by replaying from its checkpointed offset
(Open Issues 002 §4, 004 sub-decision 4b), so a restart is an ordinary event rather than an
incident. No additional supervision layer is needed.

**This is worth an explicit test:** kill the engine mid-session and confirm the system
recovers with no lost or duplicated fills. That single demonstration covers `README.md`
Problem 9 and a large part of the Reliability section of the Definition of Success — and it
is a far better artifact than a paragraph claiming recovery works.

---

## 6. Sub-decision 8d — Configuration

Several decisions have introduced parameters that must be identical across processes and
must be recorded for reproducibility: symbol list and tick sizes, the replay clock ratio
(Open Issue 005 §10.5), bot seeds, quoting obligations, conflation rate, book depth, and
group-commit batch size.

**Proposed:** a single version-controlled configuration file, loaded by every process, with
its **content hash stamped into the event stream at startup**. That makes any recorded
session self-describing: a replay can verify it is running under the configuration that
produced it, and a benchmark result carries the parameters that produced it. Roughly two
hours, and it removes an entire category of "why does this not reproduce" investigation.

---

## 7. Sub-decision 8e — What is claimed versus what is built

Worth recording explicitly so the benchmark report does not overstate.

| | Built in Phase 1 | Claimed as the scaling path |
|---|---|---|
| Fan-out | 1 process | N processes, each tailing at its own offset — no coordination needed |
| Engine | 1 thread, all symbols | Shard by symbol across threads or processes |
| Gateway | 1 process, is the sequencer | Multiple gateways behind a dedicated sequencer (OI 001, 2b) |
| Deployment | 1 VM | Requires replacing the transport if the stream is `mmap` (see 8b) |

The honest framing: **the design admits these paths; Phase 1 does not walk them, because
measurement does not yet justify it.** That is a stronger position than having built
unnecessary scaling, and it matches `README.md` §5.

---

## 8. Cost summary

| Item | Hours |
|---|---|
| Dockerfiles for each process | 5 |
| `docker-compose` with health checks and restart policies | 4 |
| Deployment to a VM; TLS; domain | 5 |
| CI: build, test, image publish | 4 |
| Configuration loader with hash stamping | 2 |
| **Total** | **20** |

Matches the 20 hours budgeted. **Per the Phase 1 build rule, this happens in week 1**, while
the system is trivially small — not in week 7. Deployment problems found in week 1 cost
hours; found in week 7 they cost the deadline.

---

## 9. Questions to resolve

1. **Does the co-location constraint change the Open Issue 003 decision?** The mmap log
   permanently ties these processes to one machine. Redis Streams does not. Neither matters
   at Phase 1 scale, but one forecloses a future option.
2. **Single VM, or a managed platform?** A VM is cheaper and satisfies co-location; a managed
   platform is less operational work but fits this topology poorly.
3. **Is "modular monolith with process separation by execution requirement" the framing to
   use in the README?** It is accurate and it pre-empts the "why not microservices" question.
4. **Should the engine-kill recovery test be part of CI**, or a manual demonstration? In CI
   it is stronger evidence but needs a harness; manual is cheaper but easier to let rot.

---

## 10. Decision log

| Date | Status | Note |
|---|---|---|
| 2026-08-27 | OPEN | Topology consolidated from OI 001/002/005/006; sub-decisions 8a–8e proposed. Nothing final. |

---

## 11. Appendix — the two deployment questions in plain language

### 11.1 What "co-location" means, and why Open Issue 003 decides it

**The memory-mapped file (S1).** Two programs open the same file and the operating system
gives both of them a pointer into the *same physical block of RAM*. When the gateway writes
a number, the engine can already see it — nothing was sent anywhere, because there is only
one copy of the data.

The analogy: two people writing in **one notebook lying on one desk**. Instant, but both
people must be in that room. There is no version of this that works if one of them is in
another building.

**Redis Streams (S2).** A separate program (Redis) holds the notebook. Both the gateway and
the engine talk to it over a network connection.

The analogy: the notebook is in **another office, and every read or write is a phone call**.
Slower per operation — tens of microseconds instead of well under one — but the callers can
be anywhere.

| | mmap file (S1) | Redis Streams (S2) |
|---|---|---|
| Speed per operation | Well under 1 µs | ~50–100 µs on loopback |
| Durability window | ~200 µs (group commit) | Up to 1 s by default; closer with pipelining plus `appendfsync always` |
| Machines | **One, permanently** | Any number |
| Code written by the team | ~30–40 h, in the riskiest part of the system | Almost none |
| Extra service to operate | None | Redis |
| Scaling later | Requires replacing the transport | Already supported |

**Does it matter at Phase 1 scale?** No. The agreed targets — 8–12 symbols, 200–1000 clients,
5–20k orders/sec — fit on one machine either way, with room to spare.

**What it actually costs.** It is a one-way door for horizontal scaling. With S1, the answer
to "how would you scale beyond one machine?" is *"replace the transport layer, which is
isolated behind an interface."* That is a perfectly good answer — the engine only knows
"records in, records out" — but it is weaker than *"add another process; it already works."*

### 11.2 Single VM versus managed platform, in plain language

**Single VM.** Rent one computer (a DigitalOcean droplet, an EC2 instance, a Hetzner server),
install Docker, and run everything on it. The team is responsible for the operating system,
TLS certificates, the firewall, restarts, and backups.

**Managed platform.** Push the code to Fly.io, Railway, Render, or similar. They run the
containers and handle TLS, restarts, and deployments.

| | Single VM | Managed platform |
|---|---|---|
| Cost | ~$10–20/month | ~$20–50/month at this size |
| Setup effort | Higher: OS, Docker, TLS, firewall | Lower: connect the repository and push |
| Ongoing effort | Team handles updates and restarts | Mostly handled |
| Co-location of processes | **Guaranteed** — it is one machine | Ranges from awkward to impossible; containers are assumed independent |
| Control | Full — can pin CPUs, tune the kernel, run `perf` | Limited; hard to benchmark meaningfully |
| Skills demonstrated | Linux, Docker, deployment, operations | Platform configuration |

**Two considerations specific to this project.** If Open Issue 003 selects the mmap log, a
managed platform is close to unworkable, because these platforms are built on the assumption
that containers are independent and relocatable. And Goal 5 requires reproducible
benchmarking, which needs predictable, uncontended hardware — noisy shared infrastructure
produces numbers that cannot be defended.

---

## 12. Appendix — on adopting microservices deliberately

The question was raised that if microservices are genuinely necessary, complexity should not
be the reason to avoid them. That is the right instinct, so the argument here is not about
difficulty.

**What microservices provide:** independent deployment, independent scaling, team autonomy,
and fault isolation.

- *Independent deployment* — with one repository, two developers, and one deadline, there is
  nothing to deploy independently.
- *Independent scaling* — already available. Fan-out scales by adding processes that tail the
  same stream (Open Issue 006 sub-decision 7b).
- *Team autonomy* — with two developers the seam already exists at the engine interface
  (Open Issue 002 §6).
- *Fault isolation* — **already achieved.** These are separate operating-system processes. The
  engine crashing does not take down the gateway.

**What splitting further would add is network boundaries and separate databases, and that is
the part that would cause harm here.** This system's correctness rests on a single total
order of events (Open Issue 001). Splitting order flow across services with their own
databases reintroduces exactly the consistency problems that Open Issues 001 and 004 exist to
solve — two writers, two orderings, and no way to prove cash conservation. It would make the
system measurably worse at the thing it is being built to demonstrate.

**Where a genuine microservice boundary does exist:** the Phase 2 analytics, reporting, and
backtesting side. It is read-only, consumes the event stream, has entirely different scaling
characteristics, and can legitimately own a separate database. That is a real service
boundary and the honest place to demonstrate one — not the trading path.

---

## 13. Amendment 2026-08-27 — co-location constraint lifted

Open Issue 003 selected Redis Streams, so the constraint described in sub-decision 8b and
appendix 11.1 — that gateway, engine, fan-out and ledger writer must share one machine — **no
longer applies.** Managed container platforms become viable, and horizontal scaling no longer
requires replacing the transport.

Two points survive the change:

1. **Benchmark reproducibility still argues for predictable hardware.** Goal 5 requires
   defensible numbers, and shared or noisy infrastructure undermines them regardless of which
   transport is used.
2. **Redis joins the process inventory** (sub-decision 8a) as a required service rather than
   a conditional one, and it is on the critical path — see Open Issue 003 §8.5 for the
   required halt-state behaviour.

The archiver process from Open Issue 003 §8.4 also joins the inventory.

**Deployment itself is deferred**, by decision, to the later part of Phase 1. This issue's
sub-decision 8b is therefore left open rather than decided, and the build-rule guidance to
deploy in week 1 stands as a recommendation the team has chosen to weigh later.

Sub-decision 8c's recovery test is **confirmed**: build it as a scripted local test (~5 h,
one command — start the stack, drive load, kill the engine, assert no lost or duplicated
fills and cash conservation), add it to CI later if week 7 allows, and record a GIF of it for
the README.

> **Conditional 2026-08-27:** the amendment above follows from the S2 (Redis Streams)
> selection in Open Issue 003, which has since been **placed on hold** pending the validation
> plan in Open Issue 009. Treat this amendment as provisional. If S2 is not confirmed, the
> superseded material above it becomes current again.

---

## 14. Amendment 2026-08-28 — co-location constraint reinstated

Open Issue 003 selected S1, so the amendment in §13 is **withdrawn** and the constraint in
sub-decision 8b and appendix 11.1 **applies again**: gateway, engine, fan-out and ledger writer
must share one machine, because `mmap` shared memory does not cross a network.

Consequences for the deployment decision, which remains deferred to late Phase 1:

- **A single VM is now strongly indicated.** Managed container platforms assume containers are
  independent and relocatable, which is incompatible with a shared mapping.
- **Horizontal scaling beyond one machine would require replacing the transport.** Recorded as
  a known limitation rather than a defect; the engine is unaffected, since it only knows
  "records in, records out."
- **Redis remains in the process inventory** for sessions, and currently for idempotency
  deduplication — but it is **no longer on the critical order path** (Open Issue 003 §11.2).
- The archiver remains in the inventory, with its purpose changed from rescuing RAM-bounded
  events to building queryable derivatives.

---

## 15. Amendment 2026-08-28 — co-location lifted, final

Open Issue 003 §12 locked **S2**. Section 14 is **withdrawn**; the co-location constraint does
**not** apply.

**Final position on deployment**, which remains deferred to late Phase 1:

- Processes need not share a machine. Both a single VM and a managed container platform are
  viable.
- Horizontal scaling does not require replacing the transport — additional fan-out processes
  tail the same stream at their own offsets.
- **Redis is a required service and is on the critical order path.** The halt state in Open
  Issue 003 §8.5 is mandatory, not optional.
- The archiver is required, and its purpose is rescuing events from a RAM-resident stream
  before trimming — not merely building derivatives.
- Benchmark reproducibility (Goal 5) still argues for predictable, uncontended hardware
  regardless of platform choice.

---

## 16. Framing confirmed 2026-08-28

The architecture is stated as:

> **ONE PROJECT + SEPARATE PROCESSES + ONE COHERENT EVENT FLOW**

This replaces the wordier "modular monolith with process separation by execution requirement"
from §2 and answers §9 question 3. It is better than the original phrasing and should be the
formulation used in the README.

### 16.1 What each line means, and the misreading it pre-empts

| Line | Concretely | Pre-empts |
|---|---|---|
| **ONE PROJECT** | One repository, one version, one deployment unit, one configuration file, two developers | *"Is this microservices?"* — no. Nothing deploys independently, and nothing is meant to |
| **SEPARATE PROCESSES** | Split by **execution requirement**, not by business domain: a single-writer engine that must never block, a low-jitter event loop, I/O-bound socket fan-out | *"Is this a monolith that cannot isolate failure?"* — no. These are separate OS processes; the engine crashing does not take down the gateway |
| **ONE COHERENT EVENT FLOW** | Every component derives its state from one ordered stream. No component owns a private source of truth | *"How do you keep them consistent?"* — there is nothing to reconcile. One ordering, and everything else is derived |

Each line answers a question the design invites, which is what makes it a useful framing rather
than a slogan.

### 16.2 It is a guardrail, not only a description

The third line is falsifiable, and that is its value. **If any component ever needs to write to
a second source of truth, the framing has been violated** and the change should be challenged
rather than accommodated.

Concretely, this is what forbids: a service holding its own private database of orders; the
gateway writing balances directly to Postgres instead of deriving them from the stream; a
second stream requiring a deterministic merge rule (rejected in Open Issue 004 sub-decision 4c);
and any future service that reads from the stream but writes authoritative state elsewhere.

The Phase 2 analytics service (§12) is the one legitimate exception and remains legitimate
precisely because it is **read-only** with respect to the event flow.

### 16.3 What remains open in this issue

| # | Status |
|---|---|
| **8a** — process inventory | Proposed; unchanged since the S2 lock except that Redis is a required service and the archiver has joined |
| **8b** — deployment unit | **Deferred by decision** to late Phase 1. No co-location constraint, so both a single VM and a managed platform are viable |
| **8d** — configuration | Proposed: one version-controlled file loaded by every process, with its **content hash stamped into the event stream at startup**, so every recorded session and benchmark result is self-describing (~2 h) |
| **8e** — claimed versus built | Proposed: record the scaling paths the design admits without walking them, so the benchmark report does not overstate |

Already settled: §9 question 1 (co-location — no constraint under S2), question 3 (framing,
above), and question 4 (recovery test as a scripted local test, CI later if week 7 allows,
with a GIF for the README).

## 17. Decision log

| Date | Status | Note |
|---|---|---|
| 2026-08-27 | OPEN | Topology consolidated from OI 001/002/005/006; 8a–8e proposed |
| 2026-08-27 | AMENDED | Co-location lifted (S2), then reinstated (S1), then lifted again (S2 locked) — §13, §14, §15 |
| 2026-08-28 | **FRAMING CONFIRMED** | One project + separate processes + one coherent event flow. Recorded as a guardrail in §16.2. 8a, 8d, 8e remain open; 8b deferred. |

---

## Amendment 2026-08-28 — sub-decisions resolved (Open Issue 018 §13)

- **8a confirmed** as recorded, with Redis a required service and the archiver in the inventory.
- **8d confirmed.** One version-controlled configuration file loaded by every process, with its
  content hash stamped into the event stream at startup (~2 h).
- **8e confirmed.** The scaling paths the design admits are documented without being walked, so
  the benchmark report does not overstate.
- **8b remains deferred** to late Phase 1 by prior decision.

**New operating rule from Open Issue 018 §13.2:** benchmarks run on a **scratch deployment**,
separate from the demo instance. A load test at 20k orders/sec consumes the retained stream
window in roughly 100 seconds, which would leave the demo instance unable to rebuild state by
replay. Isolating benchmark runs also keeps the measurements uncontaminated, which the
reproducibility requirement in Goal 5 wants anyway.
