#!/usr/bin/env python3
"""Generate the C++ header and the Python module from contracts/v1/schema.toml.

One definition, two languages. These records are replayed, so a layout mismatch between the
C++ and Python sides does not raise an error — it misreads fields, in the money path. Deriving
both sides from one file removes that failure class (Open Issue 016 sub-decision 16b).

    python contracts/v1/generate.py            # write into generated/
    python contracts/v1/generate.py --out DIR  # write elsewhere (used by the drift test)
    python contracts/v1/generate.py --check    # fail if generated/ is stale
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCHEMA_PATH = HERE / "schema.toml"
DEFAULT_OUT = HERE / "generated"

BANNER = """\
{c} GENERATED FILE — DO NOT EDIT.
{c}
{c} Source:     contracts/v1/schema.toml
{c} Source sha: {sha}
{c} Regenerate: python contracts/v1/generate.py
{c}
{c} Hand-editing this file reintroduces exactly the C++/Python drift the generator exists to
{c} prevent. contracts/v1/tests/test_generated_is_current.py fails if you do.
"""


# ---------------------------------------------------------------------------------------------
# Load and validate
# ---------------------------------------------------------------------------------------------


class SchemaError(Exception):
    pass


def load(path: Path) -> dict:
    with path.open("rb") as fh:
        schema = tomllib.load(fh)
    validate(schema)
    return schema


def validate(schema: dict) -> None:
    types = schema["types"]
    enums = {e["name"]: e for e in schema["enums"]}

    if "RecordType" in enums:
        raise SchemaError("RecordType is synthesised from [[records]]; do not declare it by hand")

    for enum in schema["enums"]:
        if enum["underlying"] not in types:
            raise SchemaError(f"enum {enum['name']}: unknown underlying type {enum['underlying']}")
        seen: dict[int, str] = {}
        for value in enum["values"]:
            if value["value"] in seen:
                raise SchemaError(
                    f"enum {enum['name']}: value {value['value']} used by both "
                    f"{seen[value['value']]} and {value['name']}"
                )
            seen[value["value"]] = value["name"]

    seen_types: dict[int, str] = {}
    seen_names: set[str] = set()
    header_names = {f["name"] for f in schema["header"]["fields"]}

    if "schema_version" not in header_names:
        raise SchemaError("the header must carry schema_version (Success Criterion 4)")

    for record in schema["records"]:
        name = record["name"]
        if name in seen_names:
            raise SchemaError(f"duplicate record name {name}")
        seen_names.add(name)

        rt = record["record_type"]
        if rt in seen_types:
            raise SchemaError(f"record_type {rt} used by both {seen_types[rt]} and {name}")
        seen_types[rt] = name

        for field in record["fields"]:
            if field["name"] in header_names:
                raise SchemaError(f"{name}.{field['name']} collides with a header field")
            check_field(f"{name}.{field['name']}", field, types, enums)

    for field in schema["header"]["fields"]:
        if field.get("enum") == "RecordType":
            continue
        check_field(f"header.{field['name']}", field, types, enums)


def check_field(where: str, field: dict, types: dict, enums: dict) -> None:
    """Success Criterion 5 lives here: no float, no string, no variable-width field."""
    ftype = field["type"]
    if ftype not in types:
        raise SchemaError(f"{where}: type {ftype!r} is not in the closed integer vocabulary")
    if field["name"] != field["name"].lower():
        raise SchemaError(f"{where}: field names are snake_case")
    if "enum" in field:
        enum = enums.get(field["enum"])
        if enum is None:
            raise SchemaError(f"{where}: unknown enum {field['enum']}")
        if enum["underlying"] != ftype:
            raise SchemaError(
                f"{where}: declared {ftype} but enum {field['enum']} is {enum['underlying']}"
            )


# ---------------------------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------------------------


def layout(schema: dict, record: dict) -> list[dict]:
    """Header fields in declared order, then body fields widest-first.

    The header comes first because schema_version and record_type must sit at a fixed offset
    across every record — a consumer reads them before it knows what it is holding. Body fields
    are sorted widest-first (a stable sort, so declaration order breaks ties) purely so that
    layouts stay predictable; correctness rests on packing, not on alignment.
    """
    types = schema["types"]
    body = sorted(record["fields"], key=lambda f: -types[f["type"]]["size"])
    return list(schema["header"]["fields"]) + body


def offsets(schema: dict, fields: list[dict]) -> tuple[dict[str, int], int]:
    types = schema["types"]
    out: dict[str, int] = {}
    cursor = 0
    for field in fields:
        out[field["name"]] = cursor
        cursor += types[field["type"]]["size"]
    return out, cursor


def struct_format(schema: dict, fields: list[dict]) -> str:
    types = schema["types"]
    return "<" + "".join(types[f["type"]]["struct"] for f in fields)


def record_type_enum(schema: dict) -> dict:
    """Synthesised from [[records]] so it cannot drift from the records themselves."""
    return {
        "name": "RecordType",
        "underlying": "u16",
        "doc": "Record discriminator. Synthesised from the record list.",
        "values": [
            {"name": to_screaming_snake(r["name"]), "value": r["record_type"], "doc": r["name"]}
            for r in sorted(schema["records"], key=lambda r: r["record_type"])
        ],
    }


def to_screaming_snake(name: str) -> str:
    out: list[str] = []
    for i, ch in enumerate(name):
        if ch.isupper() and i:
            out.append("_")
        out.append(ch.upper())
    return "".join(out)


def wrap_doc(text: str | None, prefix: str, width: int = 96) -> list[str]:
    if not text:
        return []
    import textwrap

    lines: list[str] = []
    for para in text.strip().split("\n\n"):
        lines.extend(textwrap.wrap(" ".join(para.split()), width=width - len(prefix)))
    return [f"{prefix}{line}".rstrip() for line in lines]


# ---------------------------------------------------------------------------------------------
# C++ emitter
# ---------------------------------------------------------------------------------------------


def emit_hpp(schema: dict, sha: str) -> str:
    types = schema["types"]
    out: list[str] = [BANNER.format(c="//", sha=sha).rstrip(), ""]
    out += [
        "#pragma once",
        "",
        "#include <cstddef>",
        "#include <cstdint>",
        "#include <type_traits>",
        "",
        "namespace quant_arena::contracts::v1 {",
        "",
        f"inline constexpr std::uint16_t SCHEMA_VERSION = {schema['schema_version']};",
        "",
        "// A record's seq is the Redis stream id, which does not exist until XADD returns.",
        "// Producers write SEQ_UNASSIGNED; consumers fill it in from the message id on read.",
        "inline constexpr std::uint64_t SEQ_UNASSIGNED = 0;",
        "",
    ]

    for enum in [record_type_enum(schema)] + list(schema["enums"]):
        out += wrap_doc(enum.get("doc"), "// ")
        out.append(f"enum class {enum['name']} : {types[enum['underlying']]['cpp']} {{")
        for value in enum["values"]:
            comment = f"  // {value['doc']}" if value.get("doc") else ""
            out.append(f"  {value['name']} = {value['value']},{comment}")
        out += ["};", ""]

    out += [
        "// Packed, little-endian, no padding anywhere. This is what makes sizeof() here equal",
        "// struct.calcsize() of the '<'-prefixed format string in contracts.py.",
        "#pragma pack(push, 1)",
        "",
        "// The common prefix is enough to dispatch a record before its full type is known.",
        "struct RecordHeader {",
        "  std::uint16_t schema_version;",
        "  RecordType record_type;",
        "};",
        "static_assert(sizeof(RecordHeader) == 4, "
        '"RecordHeader must be 4 bytes — regenerate from schema.toml");',
        "static_assert(std::is_trivially_copyable_v<RecordHeader>, "
        '"RecordHeader must be trivially copyable");',
        "static_assert(offsetof(RecordHeader, schema_version) == 0, "
        '"RecordHeader.schema_version moved — regenerate from schema.toml");',
        "static_assert(offsetof(RecordHeader, record_type) == 2, "
        '"RecordHeader.record_type moved — regenerate from schema.toml");',
        "",
    ]

    for record in schema["records"]:
        fields = layout(schema, record)
        offs, size = offsets(schema, fields)
        out += wrap_doc(f"{record['doc']}", "// ")
        out.append(f"// Direction: {record['direction']}. record_type = {record['record_type']}.")
        out.append(f"struct {record['name']} {{")
        for field in fields:
            cpp = field["enum"] if "enum" in field else types[field["type"]]["cpp"]
            comment = f"  // {' '.join(field['doc'].split())}" if field.get("doc") else ""
            out.append(f"  {cpp} {field['name']};{comment}")
        out.append("};")
        out.append(
            f'static_assert(sizeof({record["name"]}) == {size}, '
            f'"{record["name"]} must be {size} bytes — regenerate from schema.toml");'
        )
        out.append(
            f"static_assert(std::is_trivially_copyable_v<{record['name']}>, "
            f'"{record["name"]} must be trivially copyable");'
        )
        for field in fields:
            out.append(
                f'static_assert(offsetof({record["name"]}, {field["name"]}) == {offs[field["name"]]}, '
                f'"{record["name"]}.{field["name"]} moved — regenerate from schema.toml");'
            )
        out.append("")

    out += ["#pragma pack(pop)", "", "}  // namespace quant_arena::contracts::v1", ""]
    return "\n".join(out)


# ---------------------------------------------------------------------------------------------
# Python emitter
# ---------------------------------------------------------------------------------------------


def emit_py(schema: dict, sha: str) -> str:
    out: list[str] = [BANNER.format(c="#", sha=sha).rstrip(), ""]
    out += [
        '"""Quant Arena wire records — generated from contracts/v1/schema.toml.',
        "",
        "Standard library only, deliberately: every service depends on this module, so it must",
        "not drag a dependency graph behind it.",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        "import struct",
        "from enum import IntEnum",
        "from typing import NamedTuple",
        "",
        f"SCHEMA_VERSION = {schema['schema_version']}",
        "",
        "#: A record's seq is the Redis stream id, which does not exist until XADD returns.",
        "#: Producers write SEQ_UNASSIGNED; consumers fill it in from the message id on read.",
        "SEQ_UNASSIGNED = 0",
        "",
        "",
    ]

    for enum in [record_type_enum(schema)] + list(schema["enums"]):
        out.append(f"class {enum['name']}(IntEnum):")
        doc = wrap_doc(enum.get("doc"), "    ")
        if doc:
            out.append('    """')
            out += doc
            out.append('    """')
            out.append("")
        for value in enum["values"]:
            comment = f"  # {value['doc']}" if value.get("doc") else ""
            out.append(f"    {value['name']} = {value['value']}{comment}")
        out += ["", ""]

    header_names = [f["name"] for f in schema["header"]["fields"]]
    dispatch: list[str] = []

    for record in schema["records"]:
        name = record["name"]
        fields = layout(schema, record)
        offs, size = offsets(schema, fields)
        fmt = struct_format(schema, fields)
        body = [f for f in fields if f["name"] not in header_names]

        out.append(f"class {name}(NamedTuple):")
        out.append('    """')
        out += wrap_doc(record["doc"], "    ")
        out.append("")
        out.append(f"    Direction: {record['direction']}. record_type = {record['record_type']}.")
        out.append('    """')
        out.append("")
        for field in fields:
            comment = f"  # {' '.join(field['doc'].split())}" if field.get("doc") else ""
            out.append(f"    {field['name']}: int{comment}")
        out += [
            "",
            f'    FORMAT = "{fmt}"',
            f"    SIZE = {size}",
            f"    RECORD_TYPE = {record['record_type']}",
            f"    OFFSETS = {{",
        ]
        for field in fields:
            out.append(f'        "{field["name"]}": {offs[field["name"]]},')
        out += [
            "    }",
            "",
            "    @classmethod",
            f"    def new(cls, *, timestamp_ns: int, "
            + ", ".join(f"{f['name']}: int" for f in body)
            + f") -> \"{name}\":",
            '        """Build a record with the header filled in correctly and seq unassigned."""',
            "        return cls(",
            f"            schema_version=SCHEMA_VERSION,",
            f"            record_type={record['record_type']},",
            "            seq_ms=SEQ_UNASSIGNED,",
            "            seq_ord=SEQ_UNASSIGNED,",
            "            timestamp_ns=timestamp_ns,",
        ]
        for field in body:
            out.append(f"            {field['name']}={field['name']},")
        out += [
            "        )",
            "",
            "    def pack(self) -> bytes:",
            f"        return _S_{name}.pack(*self)",
            "",
            "    @classmethod",
            f'    def unpack(cls, data: bytes) -> "{name}":',
            f"        return cls(*_S_{name}.unpack(data))",
            "",
            "",
            f"_S_{name} = struct.Struct({name}.FORMAT)",
            f'assert _S_{name}.size == {name}.SIZE, "{name}: format string and SIZE disagree"',
            "",
            "",
        ]
        dispatch.append(f"    {record['record_type']}: {name},")

    out += [
        "#: record_type -> record class.",
        "RECORD_BY_TYPE = {",
        *dispatch,
        "}",
        "",
        "ALL_RECORDS = tuple(RECORD_BY_TYPE.values())",
        "",
        "_RECORD_TYPE_OFFSET = "
        + str(offsets(schema, list(schema["header"]["fields"]))[0]["record_type"]),
        "",
        "",
        "def peek_record_type(data: bytes) -> int:",
        '    """Read the discriminator without knowing which record this is."""',
        '    return struct.unpack_from("<H", data, _RECORD_TYPE_OFFSET)[0]',
        "",
        "",
        "def unpack_any(data: bytes):",
        '    """Unpack a record of any type, dispatching on record_type."""',
        "    record_type = peek_record_type(data)",
        "    cls = RECORD_BY_TYPE.get(record_type)",
        "    if cls is None:",
        "        raise ValueError(f\"unknown record_type {record_type}\")",
        "    return cls.unpack(data)",
        "",
        "",
        "def parse_stream_id(stream_id: str | bytes) -> tuple[int, int]:",
        '    """Split a Redis stream id `<ms>-<ord>` into its two halves."""',
        "    if isinstance(stream_id, bytes):",
        '        stream_id = stream_id.decode("ascii")',
        '    ms, _, ordinal = stream_id.partition("-")',
        "    return int(ms), int(ordinal)",
        "",
        "",
        "def with_seq(record, stream_id: str | bytes):",
        '    """Stamp a record with the sequence number it was assigned on append.',
        "",
        "    Open Issue 003 fixes the Redis stream id AS the sequence number, and forbids a",
        "    parallel counter. A producer cannot know its own id before XADD returns, so the",
        "    number is applied here, on read, and re-derived identically on every replay.",
        '    """',
        "    seq_ms, seq_ord = parse_stream_id(stream_id)",
        "    return record._replace(seq_ms=seq_ms, seq_ord=seq_ord)",
        "",
    ]
    return "\n".join(out)


# ---------------------------------------------------------------------------------------------
# C++ size/offset probe — how Success Criterion 3 is actually proven rather than asserted
# ---------------------------------------------------------------------------------------------


def emit_size_check(schema: dict, sha: str) -> str:
    out = [BANNER.format(c="//", sha=sha).rstrip(), ""]
    out += [
        "// Prints the real sizeof and offsetof of every record, for",
        "// contracts/v1/tests/test_sizes.py to compare against the Python module.",
        "",
        "#include <cstddef>",
        "#include <cstdio>",
        "",
        '#include "contracts.hpp"',
        "",
        "using namespace quant_arena::contracts::v1;",
        "",
        "int main() {",
    ]
    for record in schema["records"]:
        name = record["name"]
        out.append(f'  std::printf("size {name} %zu\\n", sizeof({name}));')
        for field in layout(schema, record):
            out.append(
                f'  std::printf("offset {name} {field["name"]} %zu\\n", '
                f"offsetof({name}, {field['name']}));"
            )
    out += ["  return 0;", "}", ""]
    return "\n".join(out)


# ---------------------------------------------------------------------------------------------


def render(schema: dict, sha: str) -> dict[str, str]:
    return {
        "contracts.hpp": emit_hpp(schema, sha),
        "contracts.py": emit_py(schema, sha),
        "size_check.cpp": emit_size_check(schema, sha),
        "__init__.py": '"""Generated wire contracts. Do not edit by hand."""\n',
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--check", action="store_true", help="fail if the output is stale")
    args = parser.parse_args(argv)

    raw = SCHEMA_PATH.read_bytes().replace(b"\r\n", b"\n")
    sha = hashlib.sha256(raw).hexdigest()
    schema = load(SCHEMA_PATH)
    files = render(schema, sha)

    if args.check:
        stale = [
            name
            for name, body in files.items()
            if not (args.out / name).exists() or (
                args.out.joinpath(name).read_text(encoding="utf-8") != body
            )
        ]
        if stale:
            print("stale: " + ", ".join(sorted(stale)), file=sys.stderr)
            print("run: python contracts/v1/generate.py", file=sys.stderr)
            return 1
        print(f"up to date ({len(files)} files)")
        return 0

    args.out.mkdir(parents=True, exist_ok=True)
    for name, body in files.items():
        with (args.out / name).open("w", encoding="utf-8", newline="\n") as output:
            output.write(body)
    total = sum(
        offsets(schema, layout(schema, r))[1] for r in schema["records"]
    )
    print(
        f"generated {len(files)} files in {args.out} from schema.toml "
        f"({len(schema['records'])} records, {total} bytes total)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
