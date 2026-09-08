"""Success Criteria 4 and 5, machine-checked against schema.toml.

These are the two criteria that are cheapest to believe and easiest to break by accident, so
they are asserted over the definition file rather than eyeballed in review.
"""

import pytest

from _schema import load_schema

SCHEMA = load_schema()
INTEGER_TYPES = set(SCHEMA["types"])
RECORDS = SCHEMA["records"]
HEADER_FIELDS = SCHEMA["header"]["fields"]

FORBIDDEN_TYPE_HINTS = (
    "float", "double", "f32", "f64", "decimal",
    "str", "string", "char", "bytes", "text",
    "list", "array", "vector", "map", "dict", "optional", "variant", "union",
)


def all_fields():
    for field in HEADER_FIELDS:
        yield "header", field
    for record in RECORDS:
        for field in record["fields"]:
            yield record["name"], field


# --- Criterion 4: every record carries schema_version --------------------------------------


def test_header_carries_schema_version():
    assert any(f["name"] == "schema_version" for f in HEADER_FIELDS)


@pytest.mark.parametrize("record", RECORDS, ids=lambda r: r["name"])
def test_every_record_carries_the_full_header(record):
    """The generator prepends the header, so this proves no record can opt out of it."""
    from contracts.v1.generated import contracts

    cls = getattr(contracts, record["name"])
    for field in HEADER_FIELDS:
        assert field["name"] in cls._fields, f"{record['name']} is missing {field['name']}"
        assert cls.OFFSETS[field["name"]] == contracts.SubmitOrder.OFFSETS[field["name"]], (
            f"{record['name']}.{field['name']} sits at a different offset than in other records; "
            "a consumer could not dispatch on record_type"
        )


def test_schema_version_is_populated_not_left_zero():
    from contracts.v1.generated import contracts

    assert contracts.SCHEMA_VERSION == SCHEMA["schema_version"]
    for cls in contracts.ALL_RECORDS:
        record = cls.new(timestamp_ns=0, **{f: 0 for f in cls._fields[len(HEADER_FIELDS):]})
        assert record.schema_version == contracts.SCHEMA_VERSION
        assert record.record_type == cls.RECORD_TYPE


# --- Criterion 5: no field is a float, a string, or variable-width ---------------------------


@pytest.mark.parametrize("owner,field", list(all_fields()), ids=lambda x: str(x))
def test_every_field_is_a_fixed_width_integer(owner, field):
    assert field["type"] in INTEGER_TYPES, (
        f"{owner}.{field['name']} has type {field['type']!r}, which is not in the closed "
        f"integer vocabulary {sorted(INTEGER_TYPES)}"
    )


@pytest.mark.parametrize("owner,field", list(all_fields()), ids=lambda x: str(x))
def test_no_field_name_smells_of_a_forbidden_type(owner, field):
    lowered = field["name"].lower()
    for hint in ("_str", "_text", "_name", "_json", "_blob"):
        assert not lowered.endswith(hint), f"{owner}.{field['name']} looks like a string field"


def test_the_type_vocabulary_admits_nothing_but_integers():
    """The grammar itself is the guarantee: there is no way to spell a float or a string."""
    import struct

    for name, spec in SCHEMA["types"].items():
        assert name not in FORBIDDEN_TYPE_HINTS
        assert struct.calcsize("<" + spec["struct"]) == spec["size"]
        assert spec["struct"] in "BHhQq", f"{name} uses a non-integer struct code"


def test_units_are_in_the_field_names():
    """price_ticks, not price — Open Issue 016 section 6. The ambiguity this removes is exactly
    how a unit-conversion error reaches the ledger."""
    for owner, field in all_fields():
        name = field["name"]
        assert name not in ("price", "amount", "cash", "timestamp"), (
            f"{owner}.{name} must carry its unit in the name"
        )


# --- Structural rules the schema relies on ---------------------------------------------------


def test_record_types_are_unique():
    seen = {}
    for record in RECORDS:
        assert record["record_type"] not in seen, (
            f"record_type {record['record_type']} reused by {record['name']} and "
            f"{seen[record['record_type']]}"
        )
        seen[record["record_type"]] = record["name"]


def test_every_record_is_inbound_or_outbound():
    for record in RECORDS:
        assert record["direction"] in ("inbound", "outbound")


def test_the_expected_record_set_is_present():
    """The frozen records plus the agreed replay-configuration amendment are all present."""
    expected = {
        "SubmitOrder", "CancelOrder", "CreateAccount", "CreditCash",
        "ConfigureReplay",
        "OrderAccepted", "OrderRejected", "Fill", "OrderCancelled", "BookChanged",
        "AccountCreated", "CashCredited", "ReplayConfigured",
    }
    assert {r["name"] for r in RECORDS} == expected


def test_fill_carries_aggressor_side():
    """Required for maker/taker fees and impossible to derive after the fact."""
    fill = next(r for r in RECORDS if r["name"] == "Fill")
    assert any(f["name"] == "aggressor_side" for f in fill["fields"])


def test_submit_order_carries_tif_and_only_gtc_and_ioc_exist():
    submit = next(r for r in RECORDS if r["name"] == "SubmitOrder")
    assert any(f["name"] == "tif" for f in submit["fields"])
    tif = next(e for e in SCHEMA["enums"] if e["name"] == "Tif")
    assert {v["name"] for v in tif["values"]} == {"GTC", "IOC"}
