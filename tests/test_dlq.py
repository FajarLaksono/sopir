"""Unit tests for DLQ routing.

The DLQ payload has to match schemas/dlq.avsc exactly, including the ErrorStage
enum, because the writer publishes it with the registered Avro serializer. These
tests pin the contract so a careless field rename fails here rather than in the
broker.
"""

from __future__ import annotations

import pytest

from streaming.dlq import (
    DESERIALIZATION,
    MAX_RAW_PAYLOAD_BYTES,
    PROCESSING,
    SINK_WRITE,
    VALID_ERROR_STAGES,
    FailureContext,
    RecordError,
    build_dlq_record,
)


class TestFailureContext:
    def test_dedup_key_identifies_the_source_record(self):
        context = FailureContext("sopir.veh.can.v1", partition=3, offset=42)
        assert context.dedup_key == "sopir.veh.can.v1:3:42"

    def test_same_offsets_across_partitions_are_distinct(self):
        left = FailureContext("t", partition=0, offset=7).dedup_key
        right = FailureContext("t", partition=1, offset=7).dedup_key
        assert left != right


class TestBuildDlqRecord:
    def _context(self) -> FailureContext:
        return FailureContext(
            source_topic="sopir.veh.can.v1",
            partition=3,
            offset=42,
            key="veh_001",
            raw_payload=b"\xde\xad\xbe\xef",
        )

    def test_record_carries_the_original_coordinate(self):
        record = build_dlq_record(
            self._context(), "boom", DESERIALIZATION, captured_at=1_700_000_000_000
        )
        assert record["original_topic"] == "sopir.veh.can.v1"
        assert record["original_key"] == "veh_001"

    def test_raw_payload_is_preserved_for_replay(self):
        record = build_dlq_record(
            self._context(), "boom", DESERIALIZATION, captured_at=1_700_000_000_000
        )
        assert record["original_value"] == b"\xde\xad\xbe\xef"

    def test_error_stage_is_one_of_the_registered_enum_symbols(self):
        for stage in (DESERIALIZATION, PROCESSING, SINK_WRITE):
            record = build_dlq_record(self._context(), "boom", stage, captured_at=1_700_000_000_000)
            assert record["error_stage"] in VALID_ERROR_STAGES

    def test_unknown_stage_is_rejected(self):
        with pytest.raises(ValueError, match="Unknown error stage"):
            build_dlq_record(self._context(), "boom", "not_a_stage", captured_at=1_700_000_000_000)

    def test_long_reason_is_truncated_to_fit_the_schema(self):
        record = build_dlq_record(self._context(), "x" * 5000, DESERIALIZATION, captured_at=1)
        assert len(record["error_reason"]) == 1024

    def test_oversized_payload_is_truncated(self):
        context = FailureContext("t", 0, 0, raw_payload=b"y" * (MAX_RAW_PAYLOAD_BYTES + 500))
        record = build_dlq_record(context, "boom", DESERIALIZATION, captured_at=1)
        assert len(record["original_value"]) == MAX_RAW_PAYLOAD_BYTES

    def test_missing_payload_is_allowed(self):
        context = FailureContext("t", 0, 0, raw_payload=None)
        record = build_dlq_record(context, "boom", DESERIALIZATION, captured_at=1)
        assert record["original_value"] == b""


class TestRecordError:
    def test_default_stage_is_deserialization(self):
        assert RecordError("boom").stage == DESERIALIZATION

    def test_stage_can_be_overridden(self):
        assert RecordError("boom", stage=PROCESSING).stage == PROCESSING

    def test_unknown_stage_is_rejected_at_construction(self):
        with pytest.raises(ValueError, match="Unknown error stage"):
            RecordError("boom", stage="nonsense")
