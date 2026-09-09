"""The run manifest — what makes a result reproducible rather than merely repeatable.

Open Issue 011 sub-decision 11f: every run records strategy and parameters, dataset and range,
configuration content hash, engine version and seed, so that "run twice, assert byte-identical"
is a fixed, fast test in the per-commit suite (Task 7.1's last bullet).

## `engine_version` is deliberately absent, and that is a correction

11f was written when sub-decision 11c stood — fill simulation reconstructing a book from an
archived L2 snapshot and matching through the real engine. Open Issue 018 §3.2 **reversed 11c**:
Phase 1 fills at the next bar's open, and engine-based fills move to Phase 2. So no engine takes
part in a Phase 1 backtest, and a manifest field naming one would assert that a result depended
on something it never touched — the exact class of claim a manifest exists to prevent.

What determined the numbers is recorded instead: `fill_model`, and the `schema_version` of the
contracts the fee code is written against. When Phase 2 restores engine-based fills, an
`engine_version` field arrives with it and means something.

## What the hash is for

`manifest_id` is a content hash of every field. Two runs share an id only if every input that
could move a number was identical, so the reproducibility test compares results *under* an id
rather than trusting that two invocations were configured the same way.

`first_minute` and `last_minute` are the range in the dataset's own coordinate. The dataset
carries no wall-clock time at all, so a date range would be an invention; `minute_index` is the
only time there is, and the screen expresses a selection of it in simulated days.

`config_hash` is the exchange's, from `config/quant_arena.toml`. It is recorded for provenance,
and it is honest about a gap: **the maker and taker fee rates are not in that file**. They are
module constants in `services/ledger/ledger.py`, so `config_hash` does not describe them. The
manifest therefore records the rates themselves, so a result stays interpretable even if the
constants change.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field

from contracts.v1.generated.contracts import SCHEMA_VERSION
from services.ledger.ledger import MAKER_FEE_BPS, TAKER_FEE_BPS

#: The only fill model Phase 1 has. Named in the manifest and printed in the report, because
#: Success Criterion 4 says the limitation must be visible rather than buried.
FILL_MODEL = "next_bar_open"


@dataclass(frozen=True)
class Manifest:
    strategy: str
    parameters: dict[str, int]
    symbol: str
    dataset: str
    dataset_sha256: str
    bar_minutes: int
    first_minute: int
    last_minute: int
    first_bar_index: int
    last_bar_index: int
    initial_cash_ticks: int
    config_hash: str
    schema_version: int = SCHEMA_VERSION
    fill_model: str = FILL_MODEL
    maker_fee_bps: int = MAKER_FEE_BPS
    taker_fee_bps: int = TAKER_FEE_BPS
    #: Recorded because 11f asks for it, and because Phase 2's strategies may be stochastic.
    #: Nothing in Phase 1 reads it: SMA crossover over a pinned dataset has no random input,
    #: which is *why* two runs agree. A seed that changed nothing but was omitted would leave
    #: the first stochastic strategy silently unreproducible.
    seed: int = 0

    def as_dict(self) -> dict:
        return asdict(self)

    def canonical_json(self) -> str:
        """Sorted keys and fixed separators, so the hash depends on values and not on layout."""
        return json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"))

    @property
    def manifest_id(self) -> str:
        return hashlib.sha256(self.canonical_json().encode()).hexdigest()[:16]
