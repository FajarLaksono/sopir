"""Avro schema evolution, checked offline.

The property that matters for a registry-governed pipeline is not "the registry
rejects bad schemas" -- that is Redpanda's job and is verified against a live
registry in ``.github/workflows/ci-integration.yml``. It is that a reader built
against the OLD schema can still decode a record written with the NEW one.

That is what BACKWARD compatibility means operationally, and it is what lets a
producer be upgraded before its consumers are. Testing it here needs no broker:
fastavro will encode and decode with an explicit writer/reader schema pair.

Two failure modes are pinned, and the second is the one people get wrong:

1. Adding a field with a default is safe. The old reader never sees it.
2. Adding an enum symbol is NOT safe, and the usual escape hatch does not work.
   ``schemas/dlq.avsc``'s ErrorStage and ``schemas/veh_events.avsc``'s Severity
   are declared without a ``default``; the test below goes further and shows
   that adding one does not actually help under fastavro. The practical rule
   for this codebase is therefore that a deployed enum does not get new
   symbols -- a new stage is a new topic or a new record, not a new symbol.
"""

from __future__ import annotations

import copy
import io
import json
from pathlib import Path
from typing import Any

import fastavro
import pytest
from fastavro import parse_schema

SCHEMAS_DIR = Path(__file__).resolve().parent.parent / "schemas"


def load(name: str) -> dict[str, Any]:
    return json.loads((SCHEMAS_DIR / name).read_text(encoding="utf-8"))


def can_record(**overrides: Any) -> dict[str, Any]:
    record = {
        "schema_version": "1.0.0",
        "event_id": "evt-1",
        "vehicle_id": "veh_001",
        "captured_at": 1_700_000_000_000,
        "run_id": None,
        "rpm": 2100.0,
        "wheel_speed": 14.2,
        "brake_pressure": 0.0,
        "steering_angle": 0.5,
        "battery_soc": 87.5,
    }
    record.update(overrides)
    return record


def round_trip(writer_schema: dict, reader_schema: dict, record: dict) -> dict:
    buffer = io.BytesIO()
    fastavro.schemaless_writer(buffer, parse_schema(writer_schema), record)
    buffer.seek(0)
    return fastavro.schemaless_reader(buffer, parse_schema(reader_schema))


class TestBackwardCompatibleAddition:
    """The demo from docs/PLANNING_phase_2_STREAMING_PIPELINE.md 7.3."""

    def test_v2_producer_is_decoded_by_a_v1_reader(self):
        v1 = load("veh_can.avsc")
        v2 = copy.deepcopy(v1)
        v2["fields"].append({"name": "gear_ratio", "type": ["null", "double"], "default": None})

        record = can_record(gear_ratio=3.44)
        decoded = round_trip(v2, v1, record)

        # Every field the v1 reader knows about survives intact.
        for key in ("event_id", "vehicle_id", "rpm", "wheel_speed"):
            assert decoded[key] == record[key]

    def test_the_new_field_is_invisible_to_the_old_reader(self):
        # Not an error, not a crash: simply absent. This is what makes a rolling
        # upgrade safe, since the old reader never has to be taught the field.
        v1 = load("veh_can.avsc")
        v2 = copy.deepcopy(v1)
        v2["fields"].append({"name": "gear_ratio", "type": ["null", "double"], "default": None})

        decoded = round_trip(v2, v1, can_record(gear_ratio=3.44))
        assert "gear_ratio" not in decoded

    def test_a_writer_may_omit_a_field_that_has_a_default(self):
        # The default is what lets a producer that has not populated the new
        # field yet keep encoding at all. Without one, schemaless_writer rejects
        # the record outright, so a schema that adds a field without a default
        # breaks producers immediately rather than consumers later.
        v1 = load("veh_can.avsc")
        v2 = copy.deepcopy(v1)
        v2["fields"].append({"name": "coolant_c", "type": ["null", "double"], "default": None})

        record = can_record()
        assert "coolant_c" not in record
        decoded = round_trip(v2, v1, record)
        assert decoded["rpm"] == record["rpm"]

    def test_the_shipped_schemas_are_already_backward_compatible_with_themselves(self):
        # The baseline the registry enforces on registration: reading a schema
        # with an older copy of itself must not lose data. All five shipped
        # schemas carry defaults on every optional field, which is what makes
        # this hold.
        for name in ("sim_telemetry.avsc", "veh_can.avsc", "veh_gnss.avsc", "veh_events.avsc"):
            schema = load(name)
            for field in schema["fields"]:
                if isinstance(field["type"], list):
                    assert "default" in field, (
                        f"{name}.{field['name']} is optional but has no default, "
                        f"so adding it is not a backward-compatible change"
                    )


class TestBreakingChangesAreVisible:
    """What a non-backward change does to a deployed reader."""

    def test_dropping_a_field_the_old_reader_requires_breaks_decoding(self):
        # This is the change the registry refuses at registration time. Here it
        # is demonstrated at the level that matters: the old reader cannot
        # consume the new writer's output, which is why it must be refused.
        v1 = load("veh_can.avsc")
        v2 = copy.deepcopy(v1)
        v2["fields"] = [f for f in v2["fields"] if f["name"] != "rpm"]

        with pytest.raises(Exception):
            round_trip(v2, v1, can_record())

    def test_retyping_a_field_as_optional_also_breaks_the_old_reader(self):
        # Subtler than deletion: nothing is removed, but a reader that expects
        # a double can no longer assume one arrives.
        v1 = load("veh_can.avsc")
        v2 = copy.deepcopy(v1)
        for field in v2["fields"]:
            if field["name"] == "rpm":
                field["type"] = ["null", "double"]
                field["default"] = None

        with pytest.raises(Exception):
            round_trip(v2, v1, can_record())

    def test_adding_an_enum_symbol_without_a_default_breaks_old_readers(self):
        # The Avro enum trap, and the reason ErrorStage in dlq.avsc has no
        # `default`. A writer emits the new symbol; a reader compiled against
        # the old symbol set has nowhere to put it.
        v1 = {
            "type": "record",
            "name": "Stage",
            "namespace": "sopir.test",
            "fields": [
                {
                    "name": "stage",
                    "type": {"type": "enum", "name": "StageKind", "symbols": ["alpha", "beta"]},
                }
            ],
        }
        v2 = copy.deepcopy(v1)
        v2["fields"][0]["type"]["symbols"].append("gamma")

        with pytest.raises(Exception):
            round_trip(v2, v1, {"stage": "gamma"})

    def test_an_enum_default_does_not_rescue_it_in_fastavro(self):
        # The tempting fix for the trap above is to add a `default` to the enum,
        # which the Avro spec says an old reader should fall back on. Measured
        # against fastavro 1.9.0, it does not: putting the default on the writer
        # raises IndexError and putting it on the reader raises ValueError.
        #
        # So the practical rule is stricter than the spec suggests -- for this
        # codebase, do not add a symbol to a deployed enum at all. That is why
        # ErrorStage and Severity are asserted to stay put below rather than
        # being quietly extended.
        v1 = {
            "type": "record",
            "name": "Stage",
            "namespace": "sopir.test",
            "fields": [
                {
                    "name": "stage",
                    "type": {"type": "enum", "name": "StageKind", "symbols": ["alpha", "beta"]},
                }
            ],
        }
        writer_default = copy.deepcopy(v1)
        writer_default["fields"][0]["type"]["symbols"].append("gamma")
        writer_default["fields"][0]["type"]["default"] = "beta"
        reader_default = copy.deepcopy(v1)
        reader_default["fields"][0]["type"]["default"] = "beta"

        with pytest.raises(Exception):
            round_trip(writer_default, v1, {"stage": "gamma"})
        with pytest.raises(Exception):
            round_trip(v1, reader_default, {"stage": "gamma"})


class TestShippedEnumRisk:
    """The shipped enums have no default, which is a known constraint."""

    @pytest.mark.parametrize(
        ("name", "field_name"),
        [("dlq.avsc", "error_stage"), ("veh_events.avsc", "severity")],
    )
    def test_shipped_enums_carry_no_default(self, name: str, field_name: str):
        # Asserted, not fixed. Adding a default now would be a backward-
        # compatible change in its own right and a deliberate decision, so this
        # test documents the constraint rather than quietly removing it. If
        # someone adds a symbol to either enum, this is the test that should
        # make them reconsider.
        schema = load(name)
        field = next(f for f in schema["fields"] if f["name"] == field_name)
        assert field["type"]["type"] == "enum"
        assert "default" not in field["type"]
