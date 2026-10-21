**Read `STATUS.md` before doing anything else.** It is the current state — what is done, what is
next, what is blocked. This file is only the rules, and it rarely changes.

## What this is

**Quant Arena** — a simulated stock exchange. Phase 1 runs 28 Aug – 15 Oct 2026, ~368 hours,
two developers. The repo was documentation-first: ~2,400 lines of design were settled before any
code was written, so an empty directory does **not** mean an open design question.

The deadline is real and Phase 1 scope is closed. Do not redesign. Build what is planned.

## Who you are working with

The person you are talking to is **Dev B**. Dev A is a different human you cannot see, cannot
ask, and whose work you must not start.

- **Dev A** (134 h): 1.2, 2.3, 2.4, 3.4, 4.1, 4.2, 4.3, 5.3, 6.3, 6.4, 7.4
- **Dev B** (234 h): 1.3, 1.4, 2.1, 2.2, 3.1, 3.2, 3.3, 4.4, 5.1, 5.2, 5.4, 6.1, 6.2, 7.1, 7.2, 7.3
- **Joint**: 1.1 (contracts), 7.5 (definition-of-done walk)
- **Checkpointing (020)** — taken over by Dev B on 2026-09-16, including Dev A's engine and
  matcher changes it requires.

You may read Dev A's output and depend on it. You may not implement it, refactor it, or "just
quickly stub it out" beyond the one sanctioned stub below.

## The blocking picture

The rule the whole schedule rests on: **no task may depend on a task assigned to the other
developer in the same week** (`WEEKLY_PLAN.md` Appendix D.4).

- **Dev B is never blocked on Dev A except at two integration points** — end of week 2 (stub
  engine → naive model over the real stream) and end of week 5 (naive model → C++ engine
  process). Every other Dev B task depends only on other Dev B work.
- **What Dev A is waiting on from Dev B:** 2.1 streams unblocks A's 4.2 · 3.2 idempotency
  unblocks A's 6.4 · 5.2 fan-out unblocks A's 6.3. **Those three must not slip**, and must not be
  reordered for convenience.
- **The one load-bearing stub:** a stub engine Dev B builds in week 1 (part of 1.3), swapped at
  the end of week 2. Keep the call site behind a thin interface.

## Sub-task labels

These appear in `STATUS.md` and are defined only in Appendix D.3:

| Label | Week | What |
|---|---|---|
| 5.4a / 5.4b / 5.4c | 1 / 2 / 4 | Vite+TS+React scaffold / auth screens / WebSocket client, gap detection, rAF loop |
| 5.2a / 5.2b | 4 / 5 | Fan-out begins / completes — conflation, WS server, private stream |
| 6.1a / 6.1b | 5 / 6 | Trading screen begins / completes |

Task numbers never change and are never renumbered. **The chapter a task is documented under in
`WEEKLY_PLAN.md` is not its scheduled week** — 5.x tasks run in weeks 1–5, 6.1 in weeks 5–6.

## Settled decisions — do not relitigate

Most of these are the *unobvious* answer. They were argued through and recorded; a suggestion
that contradicts one is wrong, not the decision.

**Engine.** C++20, single-writer, single-threaded, **zero I/O**, **money-blind** (001, 002).
Price-time priority. Deterministic — no clock reads, no randomness, no unordered iteration. The
naive Python model engine is **permanent**: executable specification and differential test
oracle, not scaffolding (001, 010). **nanobind, not pybind11** (002, 019).

**Ordering and durability.** **Redis Streams** is the log. The memory-mapped log was assessed
and rejected — it is Phase 3 (003, 009 §8). **The Redis stream ID *is* the sequence number** —
never invent a parallel counter (003). The gateway is the single *producer* (007).
**Checkpointing is in Phase 1 (020, reversing 018 §13.1–13.2).** Each replaying process saves
its state and the stream id it reflects; restart = load checkpoint + replay the tail. Streams
trim by `MINID` below the oldest checkpoint, never by `MAXLEN`. A checkpoint older than the
stream's start halts the process — never a partial replay.

**Money.** Risk state lives in gateway **process memory**, not Redis (004); its Redis checkpoint
is a recovery aid, never read on the order path. Order of operations
is **validate → reserve → atomic Lua claim-and-append → release on duplicate** (008).
`client_order_id` is mandatory; two identifiers, client and engine (008). PostgreSQL is a
**derived read model**, rebuildable from the stream, never the source of truth (004).

**Market and delivery.** Ten symbols, fair value from replayed real crypto history at 1 real
second : 1 simulated minute (005, 018 §11.1). Fan-out is a **separate process**, never inside
the gateway; 20 Hz conflation; **complete L2 snapshots, no delta encoding**; trade tape
un-conflated; private data never dropped (006). Frontend: high-frequency data lives in a mutable
buffer **outside React state**, painted by a `requestAnimationFrame` loop; **three screens only**
(014). Backtester is **bar-open fill simulation** — engine-based fills are Phase 2 (011, 018 §3.2).

**Auth and ops.** Redis-backed **session cookies**, `httpOnly` / `Secure` / `SameSite`. **No
JWT.** Argon2id (015). **No Kafka, no Kubernetes, no Protobuf, no OpenTelemetry, no
Prometheus/Grafana** — the event stream is the trace, and observability is structured logs plus
offline plots (012, 019).

**Stack — closed list.** FastAPI + uvicorn · SQLModel + PostgreSQL · Redis · C++20 + CMake +
Catch2 · pytest + Hypothesis · TypeScript + Vite + React + react-router-dom · TradingView
Lightweight Charts · Parquet (pyarrow) · Docker Compose · GitHub Actions. **A dependency
outside this list needs my approval before you add it.**

To change any of the above, say so explicitly and amend both the open issue and this file.
Never drift.

## Not in Phase 1

**Phase 2** — user-submitted strategy code, sandboxed execution, competitions, margin and short
selling, momentum/value bots, delta-encoded feeds, binary wire protocol, L3 feed, multiple
gateways, analytics as a separate service, engine-based backtest
fills, email verification.
**Phase 3** — memory-mapped log replacing Redis Streams, engine sharding, market-impact
modelling, multi-region.
**Never** — real money, real exchange integration.

"While we're here" is how a 368-hour plan becomes a 500-hour one. If something on this list looks
easy, it is still not now.

## Where things are

| File | What is in it | When to open it |
|---|---|---|
| `STATUS.md` | Current state, task board, what is next | Every session, first |
| `WEEKLY_PLAN.md` | 29 tasks in full; Appendix D is the developer split | For the task you are about to do |
| `ARCHITECTURE.md` | How the system works, plain language, diagrams | When you need the shape of something |
| `open-issues/001`–`019` | Why each decision was made, options weighed | When tempted to change a settled decision |
| `README.md` | Original goals and definition of success | Rarely |

**Precedence:** `open-issues/` > `WEEKLY_PLAN.md` > `ARCHITECTURE.md` > `README.md`. Within the
plan, **Appendix D outranks a task's own Dependencies line.**

**Never read `WEEKLY_PLAN.md` whole — it is 1,700+ lines.** Read one task:

```bash
grep -n '^### Task' WEEKLY_PLAN.md      # find the line range
sed -n 'START,ENDp' WEEKLY_PLAN.md      # read exactly that task
```

Always read a task's **Boundaries / Constraints** and **Success Criteria** before writing code.
The Boundaries block is where the scope trap is.

## Working agreements

**At session start:** read `STATUS.md` · run `git log --oneline -5` and compare against the
`HEAD` in its stamp — if HEAD has moved past it, say so before doing anything else · take the
`NOW` item and read that task's Boundaries and Success Criteria · if `NOW` is a Dev A task, stop
and ask.

- A task is done when **its own Success Criteria pass**, not when the code looks finished.
- Do not build past the current task. Later tasks add risk checks, idempotency, streams — leave
  the seams, do not fill them early.
- Small commits. A task's completion should be nameable by a commit.
- Say "I don't know" rather than invent a field name, a path, or Dev A's progress.

## Updating STATUS.md — the protocol

`STATUS.md` is the one file here you may not edit on your own initiative.

1. **Never write to `STATUS.md` without showing me the change first.** This holds even when I say
   "update the status" — that is a request for the proposal, not permission to write.
2. **Propose at the end of the session.** Propose early if context is running low or you are
   about to be compacted. A session that ends without a proposal loses everything it learned.
3. **The proposal is a diff, not a rewrite.** Fenced block, unified-diff style, one hunk per
   section touched. Do not reprint unchanged parts.
4. **Cover these, in this order:** the `Last updated` stamp — today's date and
   `git rev-parse --short HEAD` · every task whose status changed · the `NOW` block rewritten to
   the next single action · blockers added, and blockers removed because they cleared · any
   decision made this session, one line · anything you found in the file that was wrong.
5. **Name your evidence for every status change.** "1.4 → done" arrives with the criterion that
   passed, the test, or the commit. If you cannot name it, the status is `wip`, not `done`.
6. **Wait for my explicit confirmation.** I will reply "apply", or with edits. Silence, a new
   question, or a change of subject is not confirmation.
7. **Apply exactly what I confirmed.** If I edited the diff, apply my version. Do not re-derive
   it and do not tidy other sections while you are in the file.
8. **Never edit `STATUS.md` to make it agree with something you said.** If the file and the
   repository disagree, **the repository is right** — say so out loud and put the correction in
   the proposal.
9. **Keep it bounded.** 250 lines. When a week closes, collapse it to one line in Archive and
   delete the rest — git history is the real record. Resolved blockers are deleted, not annotated.
10. **This file follows the same rule.** `CLAUDE.md` changes are proposed and confirmed
    separately. A settled decision can only change here — never by a line in `STATUS.md`.

## Maintenance

This file is capped at **180 lines**. It holds rules and settled facts only — never task detail,
never current state, never "we are currently doing X". If you want to add something and the file
would exceed the cap, something else has to go, and you must say what.
