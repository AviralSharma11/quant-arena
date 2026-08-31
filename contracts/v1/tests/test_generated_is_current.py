"""Success Criterion 1 — one definition file generates both outputs, and the committed outputs
are what it currently generates.

Without this the generator is decorative: someone edits contracts.hpp by hand to fix a build,
the two languages drift, and the silent-misread failure mode is back.
"""

import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from _schema import CONTRACTS_DIR, GENERATED_DIR, SCHEMA_PATH

GENERATOR = CONTRACTS_DIR / "generate.py"
EXPECTED_OUTPUTS = ("contracts.hpp", "contracts.py", "size_check.cpp", "__init__.py")


@pytest.fixture(scope="module")
def freshly_generated() -> Path:
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "generated"
        result = subprocess.run(
            [sys.executable, str(GENERATOR), "--out", str(out)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr
        yield out


def test_one_definition_file_produces_both_language_outputs(freshly_generated):
    produced = {p.name for p in freshly_generated.iterdir()}
    assert {"contracts.hpp", "contracts.py"} <= produced


@pytest.mark.parametrize("name", EXPECTED_OUTPUTS)
def test_committed_output_matches_a_fresh_run(freshly_generated, name):
    committed = (GENERATED_DIR / name).read_text()
    fresh = (freshly_generated / name).read_text()
    assert committed == fresh, (
        f"contracts/v1/generated/{name} is stale or was hand-edited. "
        "Run: python contracts/v1/generate.py"
    )


def test_generation_is_deterministic(freshly_generated):
    """No timestamps, no dict ordering luck — two runs must be byte-identical, or the drift
    check above would be noise."""
    with tempfile.TemporaryDirectory() as tmp:
        second = Path(tmp) / "generated"
        subprocess.run(
            [sys.executable, str(GENERATOR), "--out", str(second)], check=True,
            capture_output=True,
        )
        for name in EXPECTED_OUTPUTS:
            assert (freshly_generated / name).read_text() == (second / name).read_text()


def test_check_mode_reports_the_committed_tree_as_current():
    result = subprocess.run(
        [sys.executable, str(GENERATOR), "--check"], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr + result.stdout


def test_generated_files_record_the_schema_hash():
    """So a reviewer can tell at a glance which definition a generated file came from."""
    import hashlib

    sha = hashlib.sha256(SCHEMA_PATH.read_bytes()).hexdigest()
    for name in ("contracts.hpp", "contracts.py", "size_check.cpp"):
        assert sha in (GENERATED_DIR / name).read_text(), f"{name} lacks the schema hash"


def test_generated_files_are_marked_do_not_edit():
    for name in ("contracts.hpp", "contracts.py", "size_check.cpp"):
        assert "DO NOT EDIT" in (GENERATED_DIR / name).read_text()[:400]
