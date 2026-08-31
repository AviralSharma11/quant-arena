# Open Issue 017 — Sandboxed Strategy Execution (Goal 4)

**Status:** OPEN — Phase 2 design; the Phase 1 constraint in §6 is the actionable part
**Opened:** 2026-08-27
**Serves:** README.md Goal 4 (safe strategy execution environment)
**Owner:** _unassigned_

---

## 1. Why discuss a Phase 2 feature now

Sandboxing is explicitly excluded from Phase 1 (Open Issue 013 §3). It is discussed here for
one reason:

> **A small number of Phase 1 decisions determine whether Phase 2's sandbox is straightforward
> or requires a rewrite.**

The actionable output of this issue is §6 — a constraint on the Phase 1 strategy interface that
costs nothing now and saves a great deal later. The rest is Phase 2 design, recorded so that
the reasoning exists when the work begins.

`README.md` states the success condition precisely: *a badly written or malicious strategy
should not crash the main platform or affect other users.*

---

## 2. The threat, stated concretely

User-submitted code is hostile by assumption, not by suspicion. A strategy may:

| Threat | Example |
|---|---|
| Consume unbounded CPU | `while True: pass` |
| Consume unbounded memory | Allocate until the host swaps |
| Read data belonging to others | Open the archive, read the database, read other users' positions |
| Reach the network | Exfiltrate data; attack third parties from your host |
| Escape into the platform | Import the engine module and call it directly |
| Never terminate | Block a worker indefinitely |

The fifth is the one specific to this architecture, and it is why §6 matters: the backtester
runs the real matching engine in-process through the nanobind adapter (Open Issue 011
sub-decision 11c). **Any user code running in that process can reach the engine.**

---

## 3. Approaches

### A — Restricted Python in-process

Strip builtins, use `RestrictedPython`, deny dangerous imports.

**Rejected.** Python's introspection makes this unreliable in principle, not merely in
practice — object graph traversal from any surviving reference reliably reaches back to
builtins. It is a known-broken pattern, and shipping it would be worse than shipping nothing,
because it looks like protection.

### B — Separate process with operating-system limits

Run each strategy in its own process: `RLIMIT_CPU` and `RLIMIT_AS`, a wall-clock kill, a
seccomp-bpf syscall filter, an empty network namespace, a read-only filesystem, dropped
privileges, and an import allowlist.

**For:** genuine kernel-enforced isolation. Linux-native, no additional runtime. Fast startup —
milliseconds. Roughly 40 hours.

**Against:** seccomp policies are fiddly to get right, and a policy that is too permissive
provides less protection than it appears to.

### C — Container per run

Docker, or gVisor for a stronger boundary.

**For:** stronger isolation than B, particularly with gVisor's user-space kernel. Operationally
familiar.

**Against:** 100–500 ms startup per run, which matters when a backtest invokes the strategy
across many bars. Heavier to operate.

### D — WebAssembly

Compile or interpret the strategy inside a WASM runtime.

**For:** isolation by construction — no syscalls exist to filter. Deterministic, which suits
reproducible backtests well.

**Against:** Python-in-WASM (Pyodide) is slow and the toolchain is awkward. Choosing a
WASM-native language changes what users are asked to write.

### E — A restricted domain-specific language

Do not execute user Python at all. Provide a small rule or expression language, interpreted by
the platform.

**For:** safe by construction — there is no escape because there is no host language.
Cheapest of all the options. Covers most of what users actually want (*"buy when the 5-period
average crosses above the 30-period average"*).

**Against:** limits expressiveness, and demonstrates no sandboxing capability — which is the
stated point of Goal 4.

### Proposed Phase 2 shape

**A layered answer, in this order:**

1. **E first** — a restricted DSL. Cheap, safe, and it serves the majority of users.
2. **B second** — sandboxed Python for power users, which is what actually satisfies Goal 4.

Layering is not a compromise. It mirrors how real platforms handle this, and it means the
expensive, risky component serves only the users who need it, rather than sitting on every
request path.

---

## 4. Resource limits

| Limit | Starting value | Enforced by |
|---|---|---|
| CPU time per run | 10 s | `RLIMIT_CPU` |
| Memory | 256 MB | `RLIMIT_AS` |
| Wall clock | 30 s | Parent process kill |
| Output size | 1 MB | Parent reads a bounded pipe |
| Network | None | Empty network namespace |
| Filesystem | Read-only, empty | Mount namespace |
| Imports | Allowlist — `math`, `statistics`, `numpy` | Import hook |

---

## 5. What the strategy may see

Only what it is given: the current bar, its own portfolio, and its own parameters. No access to
other users, the live order book, the archive, the database, or the network.

This is enforced structurally rather than by policy — the sandboxed process is handed a
serialised slice of data and returns a serialised list of orders. **It has no handle to
anything else, so there is nothing to restrict.**

---

## 6. The Phase 1 constraint — the actionable part of this issue

Open Issue 011 sub-decision 11d already specifies a strategy interface that receives the
current bar and portfolio and returns zero or more orders, with bars fed one at a time to
prevent lookahead structurally.

**That interface, designed for a completely different reason, is already the correct shape for
a sandbox.** Data in, data out; no I/O in the contract; no reference to anything the strategy
should not reach. Moving it across a process boundary in Phase 2 is serialisation work, not
redesign.

**The constraint to hold in Phase 1, at zero cost:**

| Rule | Why |
|---|---|
| The strategy receives **plain data**, never live objects | A portfolio object could be mutated; an engine handle is a direct escape |
| The strategy returns **plain data** — a list of order records | Reuses the Open Issue 016 schema; already serialisable |
| The strategy performs **no I/O** in its contract | Nothing to strip out later |
| The strategy holds **no reference to the engine, the adapter, or the dataset** | This is the escape route in §2 |

Violating any of these in Phase 1 — for instance by passing the backtester's engine handle into
a built-in strategy for convenience — would turn a serialisation task into a redesign. The
rules cost nothing to follow now.

---

## 7. Cost

**Phase 1: zero.** The constraint in §6 is a discipline, not an implementation.

**Phase 2:** approximately 15 hours for the DSL (E) and approximately 40 for sandboxed Python
(B), plus the strategy submission interface, storage, and versioning.

---

## 8. Questions to resolve (Phase 2, recorded now)

1. **Is the layered DSL-then-Python approach right**, or should Phase 2 go straight to
   sandboxed Python since that is what Goal 4 actually asks for?
2. **B or C** — operating-system limits, or containers per run? B is faster and lighter; C is a
   stronger boundary at a real startup cost.
3. **Is the §6 constraint accepted for Phase 1?** It is the only part of this issue that
   affects work starting now.

---

## 9. Decision log

| Date | Status | Note |
|---|---|---|
| 2026-08-27 | OPEN | Phase 2 approaches surveyed; restricted-in-process Python rejected; §6 recorded as the Phase 1 constraint. Nothing final. |

---

## 10. Amendment 2026-08-27 — answers recorded

- **§8.1 — the DSL is the first layer.** Read as the layered approach rather than DSL-only,
  because §8.2 was also answered — see §10.1.
- **§8.2 — operating-system limits (Option B)**, not containers per run, for the Python tier.
- **§8.3 — the four Phase 1 rules in §6 are accepted.**

### 10.1 Reading of the answer

"DSL" was selected over "straight to sandboxed Python", and Option B was selected for isolation
mechanism. Since the isolation mechanism only matters if untrusted Python is eventually run,
the two answers together are read as confirming the **layered plan**: a restricted DSL first,
and sandboxed Python on operating-system limits second.

If the intention was DSL-only, with sandboxed Python dropped entirely, that changes §10.2 below
and should be corrected.

### 10.2 What the DSL alone does and does not satisfy

Worth separating, so the claim made later is accurate.

`README.md` Goal 4's success condition is: *a badly written or malicious strategy should not
crash the main platform or affect other users.*

**A restricted DSL satisfies that condition completely, by construction.** No untrusted code
executes at all, so there is nothing to escape from. This is not a partial answer to Goal 4 —
it is a total one, and it is the answer a security-minded engineer would give first.

**What it does not do is demonstrate the capability.** "We avoided running untrusted code" and
"we safely ran untrusted code" are different engineering claims, and only the second exercises
process isolation, resource limits, and syscall filtering.

Both are legitimate. The layered plan gets both, in the order that puts the safe answer first.

### 10.3 A property of the DSL worth designing for deliberately

If the DSL is **expression-based with no loops and no recursion**, then every program in it is
guaranteed to terminate in bounded time. That is not a minor convenience:

| Concern | With a loop-free DSL |
|---|---|
| Infinite loops | **Impossible by construction** |
| CPU time limits | Unnecessary for this tier |
| Wall-clock kill | Unnecessary for this tier |
| Memory exhaustion | Bounded by expression depth |

The entire resource-limit apparatus in §4 becomes unnecessary for the DSL tier and is needed
only for the Python tier. **Proposed:** design the DSL loop-free from the start. Rules of the
form *"buy when the 5-period average crosses above the 30-period average"* need no loops —
iteration lives in the indicator functions the platform provides, not in user-written code.

Accepting a loop in the DSL later would forfeit this property entirely, so it is worth fixing
now as a design constraint rather than discovering the cost later.

### 10.4 DSL design questions, deferred to Phase 2

Recorded so they are not rediscovered: expression syntax and parser choice; which indicators
are exposed as built-ins; how parameters are declared and validated; how a DSL strategy is
stored and versioned; whether DSL strategies and Python strategies share the same submission
and results interface.

## 11. Decision log

| Date | Status | Note |
|---|---|---|
| 2026-08-27 | OPEN | Phase 2 approaches surveyed; restricted in-process Python rejected; §6 recorded as the Phase 1 constraint |
| 2026-08-27 | AMENDED | Layered plan confirmed — DSL first, sandboxed Python on OS limits second; four Phase 1 rules accepted; loop-free DSL adopted as a design constraint (§10.3). Still not final. |
