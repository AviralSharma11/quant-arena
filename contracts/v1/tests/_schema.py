"""Shared loader — the tests read schema.toml directly so they check the *definition*,
not the generator's opinion of it."""

import sys
import tomllib
from pathlib import Path

CONTRACTS_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = CONTRACTS_DIR.parents[1]
SCHEMA_PATH = CONTRACTS_DIR / "schema.toml"
GENERATED_DIR = CONTRACTS_DIR / "generated"

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def load_schema() -> dict:
    with SCHEMA_PATH.open("rb") as fh:
        return tomllib.load(fh)
