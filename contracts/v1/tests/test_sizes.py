"""Success Criterion 3 — C++ `sizeof` equals Python `struct.calcsize`, for every record.

The criterion is *proven*, not asserted: this compiles the generated header with a real C++20
compiler, runs it, and compares the numbers it prints. Field offsets are compared too, so the
layouts are shown to be identical rather than merely the same total length.
"""

import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from _schema import GENERATED_DIR
from contracts.v1.generated import contracts

COMPILERS = ("c++", "clang++", "g++")


def find_compiler() -> str | None:
    for name in COMPILERS:
        path = shutil.which(name)
        if path:
            return path
    return None


@pytest.fixture(scope="module")
def cxx_layout():
    compiler = find_compiler()
    if compiler is None:
        pytest.skip(
            "NO C++20 COMPILER FOUND — Success Criterion 3 was NOT verified on this machine. "
            f"Looked for: {', '.join(COMPILERS)}. The static_asserts in contracts.hpp still "
            "guard the engine build, but this run proves nothing about size parity."
        )
    with tempfile.TemporaryDirectory() as tmp:
        binary = Path(tmp) / "size_check"
        compile_result = subprocess.run(
            [compiler, "-std=c++20", "-Wall", "-Wextra", "-Werror",
             "-o", str(binary), str(GENERATED_DIR / "size_check.cpp")],
            capture_output=True, text=True,
        )
        assert compile_result.returncode == 0, (
            "contracts.hpp failed to compile — a static_assert on size or offset probably "
            f"fired:\n{compile_result.stderr}"
        )
        run_result = subprocess.run([str(binary)], capture_output=True, text=True, check=True)

    sizes: dict[str, int] = {}
    offsets: dict[str, dict[str, int]] = {}
    for line in run_result.stdout.splitlines():
        parts = line.split()
        if parts[0] == "size":
            sizes[parts[1]] = int(parts[2])
        else:
            offsets.setdefault(parts[1], {})[parts[2]] = int(parts[3])
    return sizes, offsets


@pytest.mark.parametrize("cls", contracts.ALL_RECORDS, ids=lambda c: c.__name__)
def test_cpp_sizeof_equals_python_calcsize(cxx_layout, cls):
    sizes, _ = cxx_layout
    assert cls.__name__ in sizes, f"{cls.__name__} is missing from size_check.cpp"
    assert sizes[cls.__name__] == cls.SIZE, (
        f"C++ sizeof({cls.__name__}) == {sizes[cls.__name__]} but Python SIZE == {cls.SIZE}"
    )


@pytest.mark.parametrize("cls", contracts.ALL_RECORDS, ids=lambda c: c.__name__)
def test_cpp_field_offsets_match_python(cxx_layout, cls):
    _, offsets = cxx_layout
    assert offsets[cls.__name__] == cls.OFFSETS, (
        f"{cls.__name__} field layout differs between C++ and Python"
    )


def test_every_record_is_covered_by_the_cpp_probe(cxx_layout):
    sizes, _ = cxx_layout
    assert set(sizes) == {cls.__name__ for cls in contracts.ALL_RECORDS}
