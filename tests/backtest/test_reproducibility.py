"""Success Criterion 2: two runs with the same manifest produce byte-identical results.

Task 7.1's last bullet puts this in the **per-commit** suite, and Task 3.3's Boundaries are
what make that safe to promise: it is fixed, not generated, so it belongs in the fast gate
rather than in the nightly workflow. It runs against the real pinned dataset because that is
what a reader will run, and it still finishes in well under a second.

Byte-identical, not "equal enough". The comparison is on the serialised JSON, so a difference
in key order or in a float's last place fails here rather than being discovered later by
someone trying to reproduce a published number.
"""

from __future__ import annotations

from services.backtest.report import to_json
from services.backtest.runner import run_from_dataset
from services.backtest.strategy import SmaCrossover


def a_run(**overrides):
    params = {"fast": 10, "slow": 30, "qty": 1}
    params.update({k: v for k, v in overrides.items() if k in params})
    return run_from_dataset(
        strategy=SmaCrossover(**params),
        symbol=overrides.get("symbol", "QAA"),
        bar_minutes=overrides.get("bar_minutes", 5),
    )


def test_two_runs_of_one_manifest_are_byte_identical():
    first, second = a_run(), a_run()
    assert first.manifest.manifest_id == second.manifest.manifest_id
    assert to_json(first) == to_json(second)


def test_the_manifest_id_changes_when_an_input_that_moves_a_number_changes():
    """Otherwise the identity is decoration: two runs could share an id and disagree, which is
    worse than having no id at all."""
    baseline = a_run().manifest.manifest_id
    assert a_run(fast=5).manifest.manifest_id != baseline
    assert a_run(slow=40).manifest.manifest_id != baseline
    assert a_run(qty=2).manifest.manifest_id != baseline
    assert a_run(bar_minutes=10).manifest.manifest_id != baseline
    assert a_run(symbol="QAB").manifest.manifest_id != baseline


def test_a_different_bar_width_actually_produces_different_numbers():
    """Guards the guard. If widths silently collapsed to one series the ids above would still
    differ — they hash the input — while the results were identical and the test vacuous."""
    assert to_json(a_run(bar_minutes=5)) != to_json(a_run(bar_minutes=10))


def test_the_manifest_records_the_fee_rates_because_config_hash_does_not():
    """MAKER_FEE_BPS and TAKER_FEE_BPS are module constants in `services/ledger/ledger.py`,
    not entries in `config/quant_arena.toml`, so `config_hash` does not describe them. A result
    would otherwise stop being interpretable the day someone edited a constant."""
    manifest = a_run().manifest
    assert manifest.maker_fee_bps == 2
    assert manifest.taker_fee_bps == 10
    assert "maker_fee_bps" in manifest.canonical_json()


def test_the_manifest_names_the_fill_model_and_not_an_engine_version():
    """Open Issue 011 §11f asks for an engine version, but Open Issue 018 §3.2 reversed 11c:
    no engine takes part in a Phase 1 backtest. A field naming one would assert that a result
    depended on something it never touched."""
    manifest = a_run().manifest
    assert manifest.fill_model == "next_bar_open"
    assert "engine_version" not in manifest.as_dict()
    assert manifest.schema_version == 2
