"""Tests that the Avro schemas and the Python code that depends on them agree.

Every schema in ``schemas/`` is a contract, and three pieces of Python restate
parts of it by hand: the DLQ router's error-stage set, the lake writer's and
processor's required-field tuple, and the subject mapping. Nothing in the build
checked those restatements against the schemas of record, which means the two
sides could drift apart silently:

- a fifth ``ErrorStage`` symbol added to ``dlq.avsc`` would leave
  ``DlqRouter.route`` raising ``ValueError`` on a schema-valid symbol;
- ``REQUIRED_FIELDS`` is duplicated in two modules and neither reads the
  schema, so a renamed field would be rejected as "missing" while the record
  was in fact fine.

No broker, registry or database is needed: these are file-level checks.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import fastavro
import pytest
from fastavro import parse_schema

from streaming.dlq import VALID_ERROR_STAGES
from streaming.lake_writer import REQUIRED_FIELDS as LAKE_REQUIRED_FIELDS
from streaming.processor import REQUIRED_FIELDS as PROCESSOR_REQUIRED_FIELDS

SCHEMAS_DIR = Path(__file__).resolve().parent.parent / "schemas"

SOURCE_SCHEMAS = [
    "sim_telemetry.avsc",
    "veh_can.avsc",
    "veh_gnss.avsc",
    "veh_events.avsc",
]
DLQ_SCHEMA = "dlq.avsc"


def load(name: str) -> dict[str, Any]:
    return json.loads((SCHEMAS_DIR / name).read_text(encoding="utf-8"))


def field_names(schema: dict[str, Any]) -> set[str]:
    return {field["name"] for field in schema["fields"]}


class TestSchemasParse:
    @pytest.mark.parametrize("name", SOURCE_SCHEMAS + [DLQ_SCHEMA])
    def test_every_schema_is_valid_avro(self, name: str):
        # parse_schema raises on malformed schemas, so reaching the assert is
        # the test. The schemas are named explicitly rather than globbed so a
        # stray file in schemas/ does not silently become untested.
        assert parse_schema(load(name))["name"]

    def test_the_five_schemas_are_the_ones_that_exist(self):
        on_disk = {p.name for p in SCHEMAS_DIR.glob("*.avsc")}
        assert on_disk == set(SOURCE_SCHEMAS + [DLQ_SCHEMA])

    @pytest.mark.parametrize("name", SOURCE_SCHEMAS + [DLQ_SCHEMA])
    def test_every_schema_carries_the_replay_envelope(self, name: str):
        # event_id is the dedupe key and captured_at is what drives Hive
        # partitioning and window placement. A schema without either cannot be
        # replayed or filed correctly, whatever else it carries.
        names = field_names(load(name))
        assert {"schema_version", "event_id", "captured_at"} <= names


class TestErrorStageEnumMatchesTheRouter:
    """`VALID_ERROR_STAGES` hand-mirrors an enum in the schema."""

    def test_router_stages_match_the_avro_enum_exactly(self):
        schema = load(DLQ_SCHEMA)
        field = next(f for f in schema["fields"] if f["name"] == "error_stage")
        symbols = set(field["type"]["symbols"])
        assert VALID_ERROR_STAGES == symbols

    def test_every_router_stage_round_trips_through_avro(self):
        # The membership check above compares two sets and would pass even if
        # both were misspelled the same way. Encoding and decoding closes that
        # gap: a symbol the encoder does not accept fails here.
        schema = parse_schema(load(DLQ_SCHEMA))
        for stage in VALID_ERROR_STAGES:
            record = {
                "schema_version": "1.0.0",
                "event_id": "e1",
                "original_topic": "t",
                "original_key": None,
                "original_value": b"\x00",
                "error_reason": "r",
                "error_stage": stage,
                "captured_at": 1_700_000_000_000,
            }
            buffer = io.BytesIO()
            fastavro.schemaless_writer(buffer, schema, record)
            buffer.seek(0)
            decoded = fastavro.schemaless_reader(buffer, schema)
            assert decoded["error_stage"] == stage


class TestRequiredFieldsExistInEverySchema:
    """The consumer-side envelope check must not name fields that do not exist."""

    @pytest.mark.parametrize("name", SOURCE_SCHEMAS)
    def test_required_fields_are_present(self, name: str):
        assert set(LAKE_REQUIRED_FIELDS) <= field_names(load(name))

    def test_lake_writer_and_processor_agree_on_required_fields(self):
        # These are the same tuple written twice, which is the defect. If they
        # ever need to differ the duplication has to become a shared constant
        # rather than two lists that drift.
        assert LAKE_REQUIRED_FIELDS == PROCESSOR_REQUIRED_FIELDS

    def test_required_fields_are_required_not_optional(self):
        # A field declared as a nullable union would satisfy the membership
        # check above while still being able to arrive absent, which would
        # make the envelope check pass on records it cannot actually use.
        for name in SOURCE_SCHEMAS:
            schema = load(name)
            for field in schema["fields"]:
                if field["name"] in LAKE_REQUIRED_FIELDS:
                    assert not isinstance(field["type"], list), (
                        f"{name}.{field['name']} is a union but is treated as required"
                    )


class TestSubjectMapping:
    def test_every_mapped_file_exists(self):
        # Mirrors the loop in scripts/register_schemas.py, which fails the run
        # on a missing file. Asserting it here means a renamed schema is caught
        # by pytest rather than by a container that refuses to start.
        from scripts.register_schemas import SUBJECT_MAPPING

        for filename in SUBJECT_MAPPING:
            assert (SCHEMAS_DIR / filename).exists(), filename

    def test_every_schema_file_is_mapped(self):
        from scripts.register_schemas import SUBJECT_MAPPING

        on_disk = {p.name for p in SCHEMAS_DIR.glob("*.avsc")}
        assert on_disk == set(SUBJECT_MAPPING)
