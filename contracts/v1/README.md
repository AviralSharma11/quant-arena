# `contracts/v1` — the wire contract

Everything in Quant Arena is written against this directory. It is the contract between every
component *and* the contract between the two developers (Open Issue 002 §6).

**Status: FROZEN 2026-08-31 — agreed by Dev A and Dev B.** Task 1.1 is joint and both
developers have signed off. Everything from week 1 onward is written against this directory.

Changing a record from here is a schema change, not an edit. It needs both developers, a bump of
`schema_version`, and — because Phase 1 has no snapshots (Open Issue 018 §13.1) — truncating the
retained stream and rebuilding. Raise it rather than absorbing it.

## Layout

| Path | What |
|---|---|
| `schema.toml` | **The single definition.** Conventions, enums, records |
| `generate.py` | Reads `schema.toml`, writes everything in `generated/` |
| `generated/contracts.hpp` | C++20 packed structs, enums, `static_assert`s |
| `generated/contracts.py` | Python `NamedTuple`s, format strings, `pack`/`unpack` |
| `generated/size_check.cpp` | Prints real `sizeof`/`offsetof`, used by the size-parity test |
| `rest_and_ws.md` | REST paths, status codes, WebSocket message shapes |
| `tests/` | The five Success Criteria, each as a test that runs |

## Regenerating

```bash
python contracts/v1/generate.py          # write generated/
python contracts/v1/generate.py --check  # exit 1 if generated/ is stale (use in CI)
```

**Never hand-edit anything in `generated/`.** That is the exact failure the generator exists to
prevent: these records are replayed, so a layout mismatch between the C++ and Python sides does
not raise an error — it *misreads fields, in the money path*. `tests/test_generated_is_current.py`
fails if a generated file differs from a fresh run.

## Using it

```python
from contracts.v1.generated.contracts import SubmitOrder, Side, Tif, with_seq, unpack_any

order = SubmitOrder.new(
    timestamp_ns=time.time_ns(),   # gateway-assigned; the engine never reads a clock
    client_order_id=1042, user_id=7, symbol_id=1,
    price_ticks=6_412_500, qty=3, side=Side.BUY, tif=Tif.GTC,
)
payload = order.pack()                       # 64 bytes
stream_id = redis.xadd("orders", {"r": payload})
order = with_seq(order, stream_id)           # seq is assigned here, not authored
```

`contracts.py` imports nothing but the standard library, deliberately — every service depends on
it, so it must not drag a dependency graph behind it.

```cpp
#include "contracts.hpp"
using namespace quant_arena::contracts::v1;
static_assert(sizeof(SubmitOrder) == 64);    // already asserted in the header
```

## Two things that are easy to get wrong

**`seq` is derived, never authored.** Open Issue 003 fixes that the Redis stream id *is* the
sequence number and forbids a parallel counter — but a producer cannot know its own id before
`XADD` returns. So `seq_ms`/`seq_ord` are written as `SEQ_UNASSIGNED` and filled in by the
consumer via `with_seq()`. Every replay re-derives the same value.

**No snapshots exist in Phase 1** (Open Issue 018 §13.1). Open Issue 016 §2 justified schema
compatibility as "one snapshot interval"; with snapshots removed, every consumer replays the
retained stream from its start, so consumers accept the **current `schema_version` only** and a
breaking change during development means truncating the stream and rebuilding.

## Running the tests

```bash
python -m pytest contracts/v1/tests -q
```

`tests/test_sizes.py` compiles `size_check.cpp` with a real C++20 compiler and compares its
`sizeof` and `offsetof` output against the Python module. If no compiler is present it **skips
loudly** — a green run with that skip has not verified Success Criterion 3.
