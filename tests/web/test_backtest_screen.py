"""Task 7.2 — the backtest screen.

Same approach as 6.1's tests, for the same reason: no JavaScript test framework, by the decision
of 2026-08-31. The source is read with comments stripped, and the built bundle is checked, so
the assertions are about what the code does rather than about what it explains.

**What these reach and what they do not.** 7.2 has two criteria. The second — "the limitation
text is visible on the page, not hidden in documentation" — is mechanism and is proven here: the
text comes from the API response and is rendered above the metrics table. The first — "a user
runs a backtest from the interface and sees results" — is the server round trip, and it is
covered end to end in `tests/gateway/test_backtests.py`; that a human can click the button is
the same browser-verification deviation 6.1 carries.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
WEB = REPO_ROOT / "web"
SCREEN = WEB / "src" / "screens" / "Backtest.tsx"


def code_of(path: Path) -> str:
    """The file with its comments stripped. These assertions are about behaviour, and the
    module explains in prose why it does *not* draw charts — which trips a naive substring
    check on the very comment recording the rule."""
    out, in_block = [], False
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if in_block:
            if "*/" in stripped:
                in_block = False
            continue
        if stripped.startswith("/*"):
            in_block = "*/" not in stripped
            continue
        if stripped.startswith("//") or stripped.startswith("*"):
            continue
        out.append(line)
    return "\n".join(out)


@pytest.fixture(scope="module")
def code() -> str:
    return code_of(SCREEN)


def test_the_limitation_is_rendered_from_the_response_not_from_a_local_copy(code: str):
    """Criterion 2. One source, so the page and the CLI report cannot drift into describing
    different fill models."""
    assert "result.limitation" in code
    assert "no partial fills" not in code, (
        "the limitation text is duplicated in the frontend — it must come from the API"
    )


def test_the_limitation_is_placed_above_the_metrics_table(code: str):
    """A number a reader has already believed cannot be un-believed by a paragraph further
    down the page."""
    assert code.index("result.limitation") < code.index("<table>")


def test_the_screen_draws_no_charts(code: str):
    """7.2's Boundaries: a metrics table only. `lightweight-charts` is 6.1's, and importing it
    here is how "while we're here" becomes an equity curve."""
    assert "lightweight-charts" not in code
    assert "createChart" not in code


def test_there_is_no_strategy_editor_and_no_window_tuning(code: str):
    """The Boundaries forbid a parameter tuning interface, and a form for hunting fast/slow
    window pairs is the definition of one."""
    for forbidden in ('name="fast"', 'name="slow"', "setFast", "setSlow"):
        assert forbidden not in code, forbidden


def test_the_range_is_in_simulated_days_and_not_dates(code: str):
    """The pinned dataset carries `minute_index` and no wall-clock time at all, so a date
    picker would be showing an invented fact."""
    assert "From day" in code and "To day" in code
    assert 'type="date"' not in code


def test_prices_are_formatted_through_the_symbols_tick_size(code: str):
    """The 2026-09-07 decision: `formatTicks` throws without a tick size rather than rendering
    raw ticks beside a correct price."""
    assert "formatTicks" in code
    assert "symbolForResult" in code


def test_the_strategy_list_is_fetched_rather_than_hardcoded(code: str):
    assert '"/backtests/strategies"' in code
    assert "SMA crossover" not in code, "the strategy name is the server's to supply"


def test_the_symbol_default_comes_from_the_symbol_table(code: str):
    """`GET /symbols` is the only source of names and scales (§2.3), and the 2026-09-07
    decision deleted a hard-coded list for having the same defect one listing later."""
    assert '"QAA"' not in code
    assert "symbols[0].name" in code


def test_the_screen_is_wired_into_the_router_and_the_placeholder_is_gone():
    app = code_of(WEB / "src" / "App.tsx")
    assert "screens/Backtest" in (WEB / "src" / "App.tsx").read_text()
    assert 'route.id === "backtest"' in app


def test_the_route_table_no_longer_promises_charts():
    """`routes.ts` advertised "Equity curve, drawdown, metrics" — which 7.2's Boundaries
    forbid. A summary that describes a screen nobody built is a promise the demo breaks."""
    routes = (WEB / "src" / "routes.ts").read_text()
    assert "Equity curve" not in routes
    assert "buy-and-hold" in routes


def test_the_app_still_builds():
    if shutil.which("npm") is None:
        pytest.skip("npm is not installed — the backtest screen was NOT built")
    result = subprocess.run(
        ["npm", "run", "build"], cwd=WEB, capture_output=True, text=True, timeout=300
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (WEB / "dist" / "index.html").exists()
