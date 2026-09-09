"""`python -m services.backtest` — the entry point a reader actually runs."""

from __future__ import annotations

import json

from services.backtest.__main__ import main
from services.backtest.report import LIMITATION


def test_the_text_report_runs_and_carries_the_limitation(capsys):
    assert main(["--symbol", "QAA", "--bar-minutes", "5"]) == 0
    out = capsys.readouterr().out
    assert LIMITATION in out
    assert "EXCESS RETURN" in out


def test_the_json_report_is_parseable_and_carries_the_manifest(capsys):
    assert main(["--symbol", "QAA", "--bar-minutes", "5", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["manifest"]["fill_model"] == "next_bar_open"
    assert payload["metrics"]["buy_and_hold_return_pct"] is not None
    assert payload["fill_model_limitation"] == LIMITATION


def test_an_unlisted_symbol_exits_nonzero_with_a_message_not_a_traceback(capsys):
    assert main(["--symbol", "NOPE"]) == 2
    assert "backtest:" in capsys.readouterr().err


def test_a_bad_window_pair_is_refused_before_anything_is_loaded(capsys):
    assert main(["--fast", "50", "--slow", "10"]) == 2
    assert "shorter" in capsys.readouterr().err
