"""Repository-wide pytest configuration.

Its one job is the `property` marker. Task 3.3's Boundaries block forbids generated and
property-based tests in the per-commit pipeline — they are slow and variable, and belong in the
nightly workflow.

Applying that by hand would not hold. Hypothesis tests are not in files of their own: `@given`
cases sit beside ordinary unit tests inside `tests/ledger/test_ledger.py` and
`contracts/v1/tests/test_roundtrip.py`. A hand-written label is a thing to remember, and the
next `@given` anyone writes would land in the per-commit suite silently — the pipeline would
still be green, just no longer fast or fixed.

So the marker is applied at collection instead. Hypothesis sets `is_hypothesis_test` on every
function it wraps, so the boundary is enforced by what a test *is*, not by what someone
remembered to type above it.

The hook sees only the collected function, so it misses the case where `@given` wraps an inner
closure rather than the test itself. There is exactly one of those —
`test_roundtrip_is_lossless` in `contracts/v1/tests/` — and it carries an explicit
`@pytest.mark.property` with a comment saying why. If you write another, mark it.

    per-commit:  pytest -m "not property"
    nightly:     pytest -m "property"
"""

from __future__ import annotations

import pytest


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        fn = getattr(item, "function", None)
        if fn is not None and getattr(fn, "is_hypothesis_test", False):
            item.add_marker(pytest.mark.property)
