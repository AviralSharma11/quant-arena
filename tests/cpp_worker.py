from __future__ import annotations

import os
import shutil
import struct
import subprocess
import tempfile
from pathlib import Path
from typing import Iterable

import pytest

from contracts.v1.generated.contracts import unpack_any

ROOT = Path(__file__).resolve().parents[1]
ENGINE_SOURCES = (
    ROOT / "engine" / "cpp" / "order_book.cpp",
    ROOT / "engine" / "cpp" / "stream_engine.cpp",
)
FRAME_HEADER = struct.Struct("<I")


def _configured_worker() -> Path | None:
    configured = os.environ.get("QA_CPP_ENGINE_PATH")
    if not configured:
        return None
    path = Path(configured)
    return path if path.is_file() else None


def _compiler() -> str | None:
    configured = os.environ.get("QA_CPP_COMPILER")
    if configured:
        return configured
    for name in ("g++", "clang++", "cl", "c++"):
        path = shutil.which(name)
        if path:
            return path
    return None


def _compile_command(compiler: str, output: Path) -> list[str] | str:
    if Path(compiler).stem.lower() != "cl":
        return [
            compiler,
            "-std=c++20",
            "-O2",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-I",
            str(ROOT),
            "-o",
            str(output),
            *(str(source) for source in ENGINE_SOURCES),
        ]

    compiler_path = Path(compiler).resolve()
    visual_studio_root = compiler_path.parents[7]
    vcvars = visual_studio_root / "VC" / "Auxiliary" / "Build" / "vcvars64.bat"
    sources = " ".join(f'"{source}"' for source in ENGINE_SOURCES)
    command = (
        f'call "{vcvars}" >nul && cl /nologo /std:c++20 /EHsc /O2 /W4 /WX '
        f'/I"{ROOT}" '
        f'/Fe:"{output}" {sources}'
    )
    return command


@pytest.fixture(scope="session")
def cpp_worker() -> Path:
    configured = _configured_worker()
    if configured is not None:
        return configured

    compiler = _compiler()
    if compiler is None:
        pytest.skip(
            "No C++20 compiler or QA_CPP_ENGINE_PATH was provided; "
            "Task 4.1 C++ comparisons were not run."
        )

    with tempfile.TemporaryDirectory() as directory:
        output = Path(directory) / ("quant_arena_engine.exe" if os.name == "nt" else "quant_arena_engine")
        compile_command = _compile_command(compiler, output)
        result = subprocess.run(
            compile_command,
            cwd=directory,
            shell=isinstance(compile_command, str),
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            pytest.skip(
                "The available C++ compiler cannot build the C++20 stream worker: "
                f"{result.stderr.strip()}"
            )
        yield output


def encode_frames(records: Iterable) -> bytes:
    frames = []
    for record in records:
        payload = record.pack()
        frames.append(FRAME_HEADER.pack(len(payload)))
        frames.append(payload)
    return b"".join(frames)


def run_cpp_worker(worker: Path, records: Iterable, *, initial_cash_ticks: int = 1_000_000):
    result = subprocess.run(
        [str(worker), "--initial-cash-ticks", str(initial_cash_ticks)],
        input=encode_frames(records),
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")

    offset = 0
    outputs = []
    stdout = result.stdout
    for _ in records:
        while True:
            if offset + FRAME_HEADER.size > len(stdout):
                raise AssertionError("C++ worker ended before the response delimiter")
            length = FRAME_HEADER.unpack_from(stdout, offset)[0]
            offset += FRAME_HEADER.size
            if length == 0:
                break
            end = offset + length
            if end > len(stdout):
                raise AssertionError("C++ worker emitted a truncated response frame")
            outputs.append(unpack_any(stdout[offset:end]))
            offset = end
    assert offset == len(stdout), "C++ worker emitted bytes after the final response"
    return outputs, result.stdout
