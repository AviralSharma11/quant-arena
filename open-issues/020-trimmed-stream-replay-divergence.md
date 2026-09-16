# Open Issue 020 — A restart after the stream trims silently forks the engine from its consumers

**Status:** RESOLVED (2026-09-16) — checkpointing adopted in Phase 1, by Dev B's decision
**Opened:** 2026-09-16
**Sources:** Open Issue 003 (Redis Streams is the log), 018 §13.1 and §13.2 (no snapshots; the
retained window must exceed any session), `config/quant_arena.toml` `[streams] maxlen`
**Owner:** Dev B (engine and matcher taken over from Dev A, 2026-09-16)

---

## 1. What happened

On 2026-09-16 the live market stopped trading QAA, and the trading screen showed a **crossed
book**: best bid 83,033.63 above best ask 76,883.02. The cause took about an hour to find, and it
is not a bug in any one service. It is a consequence of two Phase 1 decisions combined, which
were each fine on their own.

1. **The whole stack restarted** (~10:38 local). Redis came up while still loading its dataset, and
   every consumer rebuilt itself by full replay, as §13.1 prescribes.
2. **Both streams had been trimmed.** `XADD ... MAXLEN ~ 2000000` is applied to `qa.inbound` and
   `qa.outbound` alike. With the bots running, two million entries is roughly half a day. At the
   restart, `qa.inbound` began at 15 Sep 15:21 and `qa.outbound` at 15 Sep 19:27, with different
   retention windows because the two streams grow at different rates.
3. **The matcher replayed a truncated input history** (1,645,244 inbound records) and so built a
   **different engine**. The clearest evidence is that order ids went backwards: new orders took
   ids near 1.27 M, while orders from before the restart held ids near 2.55 M.
4. **Orders resting before the restart vanished from the engine with no `OrderCancelled`.** The
   engine never knew them, so it had nothing to cancel. The fan-out, ledger and gateway rebuild
   from `qa.outbound`, where those orders' `OrderAccepted` records were still retained, so all
   three kept them.

The concrete example is market-maker order `2551091`: bid 100 QAA @ 8,303,363 ticks, accepted
12:59, never filled or cancelled in `qa.outbound`, absent from the engine. A user who sold into it
rested instead of trading. A marketable buy against the real ask filled at once, which confirmed
the engine itself was matching correctly.

## 2. Why it matters

The damage is silent, it outlives the restart, and it reaches every consumer:

| Consumer | Effect |
|---|---|
| Fan-out | Phantom levels in L2, including crossed books. Users price orders against quotes that do not exist |
| Gateway risk (process memory, Open Issue 004) | Cash and inventory stay **reserved** against phantom orders, and are never released. An account can be refused an order it can afford |
| Ledger / PostgreSQL | Phantom rows in `open_orders`. Because ids restart, a new engine order can **reuse the id** of a phantom, and a fill is applied to the wrong order. This is the likely source of a negative cash balance observed on one account, but that link has not been proven |
| Bots | Quote against a book that is not the engine's; QAA saw **0 fills a minute** while other symbols saw 13–52 |

Nothing logs it. Each process believes its replay succeeded, and by its own inputs it did.

The only recovery available today is `docker compose down -v`, which destroys every account.
It was used on 2026-09-16.

## 3. The decision this collides with

§13.1 accepted "every consumer rebuilds by replaying the retained stream from its start", and
§13.2 made it conditional: **the retained window must comfortably exceed any session the system
is expected to survive.** In practice the window is about half a day and the market runs for
days, so that condition does not hold. §13.1 also assumes a replay reproduces the state that was
lost. That is true only when the replay starts at genesis, or from a snapshot, and Phase 1 has no
snapshots.

This issue does not reopen §13.1. Snapshots remain Phase 2. The question is what Phase 1 does so
that a trimmed stream can never silently produce a divergent engine.

## 4. Options

### A. Fail loudly when a replay cannot be faithful, and size retention to the session *(recommended)*

- **Guard.** On startup the matcher checks that `qa.inbound` still begins at genesis. The simplest
  test is that its first entry is the first record ever written; a small marker record at genesis,
  or a Redis key naming the stream's first id, would make the check exact. If the stream has
  been trimmed, the matcher **refuses to start and halts the exchange** with a named reason,
  instead of rebuilding a different engine.
- **Retention.** `qa.inbound` stops being trimmed by `MAXLEN` for the length of a session — no
  trim, or a trim sized from measured bot throughput × the longest session to be supported, with
  headroom. `qa.outbound` may keep its current trim, since consumers already tolerate a
  mid-history start (`services/fanout/book.py`, `Book.fill`), *provided* the engine it describes
  is the one that replayed from genesis.
- **Cost:** a few hours. Redis memory rises; that is measurable, and is the honest price of
  "no snapshots".
- **Keeps every settled decision.** It makes §13.2's precondition enforced instead of assumed.

### B. The engine publishes what it forgot

On startup the matcher emits `OrderCancelled` for every order a consumer could believe is resting
but the engine does not hold. The difficulty is that the engine cannot know which orders those are
without reading `qa.outbound`, which makes the engine depend on its own output, and it is still
built from the wrong history. The phantoms go away but the divergence stays: the rebuilt engine's
positions, ids and book are not the ones users traded against. **Not recommended.**

### C. Bring checkpointing forward from Phase 2

This fixes the problem properly, but it is exactly the ~12 hours and the snapshot-consistency risk
that §13.1 removed, and Phase 1 scope is closed. **Not recommended for Phase 1.**

## 5. Related, found the same day

- **The ledger could not keep up with a large resting book** (fixed on
  `fix/ledger-flush-and-chart-order`, `63e8a4b`): each batch rewrote every open-order row, and the
  read model froze four hours behind. Separate cause, same symptom family ("the screen does not
  match the market").
- **The benchmark's probe orders** (~228,000 one-tick orders, Task 7.4) inflate every consumer's
  replay and memory. They were cleared by the wipe. A future benchmark run should cancel its own
  probes.
- **The bots container sits behind a Compose profile**, so `docker compose down -v` does not stop
  it. It kept running against a wiped gateway with deleted accounts and needed a separate
  `docker compose --profile bots up -d --force-recreate bots`, with `QA_BOT_PASSWORD` in the
  environment.

## 6. Decision

**C, with A's guard — decided by Dev B, 2026-09-16.** Checkpointing moves from Phase 2 into
Phase 1, reversing 018 §13.1–13.2:

- Every replaying process (engine and matcher, gateway risk, ledger, fan-out, archiver) saves its
  state and the stream id it reflects; a restart loads the checkpoint and replays only the tail.
- Streams trim by `MINID` below the oldest checkpoint among their readers, never by `MAXLEN`.
  A reader with no checkpoint blocks trimming.
- A checkpoint older than the stream's first entry, or no checkpoint on a stream that no longer
  starts at genesis, **halts** the process — never a partial replay.
- The gateway's risk checkpoint is a recovery aid only; risk state still lives in process memory
  and nothing on the order path reads Redis for it (004).
- Dev B takes over the engine and matcher changes this needs from Dev A.
