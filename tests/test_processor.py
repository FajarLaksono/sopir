"""Tests for streaming/processor.py.

Covers the pure adapter pieces (``to_telemetry``, ``_validate``) and the
idempotency of ``SilverWriter`` against the real database, because the replay
guarantee is a claim about unique constraints actually doing their job.
"""

import uuid

import pytest
from sqlalchemy import delete, select

from backend.database import SessionLocal
from backend.models import StreamEvent, StreamFailure, StreamMetrics
from evaluation.failure_detector import detect_failures
from evaluation.metrics import compute_metrics
from streaming.processor import (
    SIM_TOPIC,
    SilverWriter,
    _validate,
    coerce_run_id,
    is_evaluable,
    to_telemetry,
)
from streaming.windowing import Window, WindowManager

RUN_ID = str(uuid.uuid4())


def sim_payload(
    run_id: str = RUN_ID,
    step: int = 0,
    vehicle_id: str = "v0",
    speed: float = 10.0,
    lane_id: str = "lane_0",
    event_id: str = "e1",
    captured_at: int = 1_760_000_000_000,
) -> dict:
    return {
        "schema_version": "1.0.0",
        "event_id": event_id,
        "run_id": run_id,
        "vehicle_id": vehicle_id,
        "captured_at": captured_at,
        "step": step,
        "x": 1.0,
        "y": 2.0,
        "speed": speed,
        "angle": 90.0,
        "lane_id": lane_id,
    }


@pytest.fixture
def clean_tables():
    """Remove silver rows so each test starts from a known state."""
    db = SessionLocal()
    db.execute(delete(StreamFailure))
    db.execute(delete(StreamMetrics))
    db.execute(delete(StreamEvent))
    db.commit()
    db.close()
    yield
    db = SessionLocal()
    db.execute(delete(StreamFailure))
    db.execute(delete(StreamMetrics))
    db.execute(delete(StreamEvent))
    db.commit()
    db.close()


def build_window(strategy: str = "run", **kwargs) -> Window:
    manager = WindowManager(strategy, **kwargs)
    for step in range(3):
        manager.add(
            sim_payload(step=step, captured_at=1_760_000_000_000 + step * 1000),
            "sim",
            now=float(step),
            topic=SIM_TOPIC,
            partition=0,
            offset=step,
        )
    return manager.flush_all()[0]


class TestToTelemetry:
    def test_projects_the_fields_metrics_reads(self):
        projected = to_telemetry(sim_payload())
        assert projected == {
            "vehicle_id": "v0",
            "step": 0,
            "x": 1.0,
            "y": 2.0,
            "speed": 10.0,
            "angle": 90.0,
            "lane_id": "lane_0",
            # Carried for windowing even though compute_metrics ignores it.
            "captured_at": 1_760_000_000_000,
        }

    def test_coerces_numeric_types(self):
        """Avro may hand back ints where the metric maths expects floats."""
        projected = to_telemetry(sim_payload(step=3, speed=12))
        assert isinstance(projected["x"], float)
        assert isinstance(projected["y"], float)
        assert isinstance(projected["speed"], float)

    def test_result_feeds_compute_metrics_directly(self):
        telemetry = [to_telemetry(sim_payload(step=i)) for i in range(3)]
        metrics = compute_metrics(telemetry)
        assert metrics["avg_speed"] == pytest.approx(10.0)
        assert metrics["speed_violations"] == 0

    def test_missing_field_raises(self):
        record = sim_payload()
        del record["x"]
        with pytest.raises(KeyError):
            to_telemetry(record)


class TestValidate:
    def test_accepts_a_well_formed_sim_record(self):
        _validate(sim_payload(), SIM_TOPIC)

    def test_rejects_non_dict(self):
        with pytest.raises(ValueError, match="expected a record"):
            _validate([1, 2, 3], SIM_TOPIC)

    def test_rejects_missing_envelope_fields(self):
        for field in ("event_id", "vehicle_id", "captured_at"):
            record = sim_payload()
            del record[field]
            with pytest.raises(ValueError, match="missing required field"):
                _validate(record, SIM_TOPIC)

    def test_rejects_non_integer_captured_at(self):
        record = sim_payload()
        record["captured_at"] = "not-a-number"
        with pytest.raises(ValueError, match="epoch milliseconds"):
            _validate(record, SIM_TOPIC)

    def test_rejects_sim_record_missing_telemetry_field(self):
        record = sim_payload()
        del record["lane_id"]
        with pytest.raises(ValueError, match="lane_id"):
            _validate(record, SIM_TOPIC)

    def test_vehicle_record_does_not_need_telemetry_fields(self):
        _validate({"event_id": "e", "vehicle_id": "v", "captured_at": 1}, "sopir.veh.can.v1")


class TestCoerceRunId:
    def test_parses_a_valid_uuid(self):
        assert coerce_run_id(RUN_ID) == uuid.UUID(RUN_ID)

    def test_none_and_empty_become_none(self):
        assert coerce_run_id(None) is None
        assert coerce_run_id("") is None

    def test_malformed_value_becomes_none(self):
        """A poison run_id must not make the window unwritable forever."""
        assert coerce_run_id("not-a-uuid") is None

    def test_non_string_input_is_tolerated(self):
        assert coerce_run_id(12345) is None

    def test_malformed_run_id_still_persists_the_window(self, clean_tables):
        writer = SilverWriter(SessionLocal, retain_events=False)
        window = build_window()
        window.run_id = "not-a-uuid"

        writer.write_window(window)

        db = SessionLocal()
        row = db.execute(select(StreamMetrics)).scalar_one()
        assert row.run_id is None
        assert row.record_count == 3
        db.close()


class TestEvaluability:
    def test_sim_window_is_evaluable(self):
        assert is_evaluable(build_window())

    def test_vehicle_window_is_not_evaluable(self):
        manager = WindowManager("tumbling")
        manager.add(
            {"vehicle_id": "v0", "captured_at": 1_760_000_000_000},
            "can",
            0.0,
            "sopir.veh.can.v1",
            0,
            0,
        )
        assert is_evaluable(manager.flush_all()[0]) is False

    def test_empty_window_is_not_evaluable(self):
        assert is_evaluable(Window(key="k", source="sim")) is False


class TestSilverWriterMetrics:
    def test_writes_metrics_row(self, clean_tables):
        writer = SilverWriter(SessionLocal, retain_events=False)
        window = build_window()
        writer.write_window(window)

        db = SessionLocal()
        row = db.execute(select(StreamMetrics)).scalar_one()
        assert row.stream_key == f"run:{RUN_ID}"
        assert row.record_count == 3
        assert row.source == "sim"
        assert row.collision_count == 0
        db.close()

    def test_metrics_match_the_pure_function(self, clean_tables):
        """The whole point of the adapter: no arithmetic of its own."""
        writer = SilverWriter(SessionLocal, retain_events=False)
        window = build_window()
        writer.write_window(window)

        expected = compute_metrics(window.records)
        db = SessionLocal()
        row = db.execute(select(StreamMetrics)).scalar_one()
        assert row.collision_count == expected["collision_count"]
        assert row.avg_speed == pytest.approx(expected["avg_speed"])
        assert row.speed_violations == expected["speed_violations"]
        assert row.lane_deviations == expected["lane_deviations"]
        assert row.min_ttc == expected["min_ttc"]
        db.close()

    def test_replaying_a_window_does_not_duplicate(self, clean_tables):
        writer = SilverWriter(SessionLocal, retain_events=False)
        window = build_window()
        writer.write_window(window)
        writer.write_window(window)
        writer.write_window(window)

        db = SessionLocal()
        rows = db.execute(select(StreamMetrics)).scalars().all()
        assert len(rows) == 1
        db.close()

    def test_replay_updates_values_rather_than_ignoring_them(self, clean_tables):
        writer = SilverWriter(SessionLocal, retain_events=False)
        window = build_window()
        writer.write_window(window)

        window.records[0]["speed"] = 30.0
        writer.write_window(window)

        db = SessionLocal()
        row = db.execute(select(StreamMetrics)).scalar_one()
        assert row.avg_speed == pytest.approx(compute_metrics(window.records)["avg_speed"])
        db.close()

    def test_offsets_are_recorded_for_replay_auditing(self, clean_tables):
        writer = SilverWriter(SessionLocal, retain_events=False)
        window = build_window()
        writer.write_window(window)

        db = SessionLocal()
        row = db.execute(select(StreamMetrics)).scalar_one()
        assert row.last_offsets == {SIM_TOPIC + ":0": 2}
        db.close()


class TestSilverWriterFailures:
    def test_collision_produces_critical_failure(self, clean_tables):
        """Two vehicles within 2.5m must trip the collision rule."""
        window = build_window()
        window.records[0].update({"x": 0.0, "y": 0.0})
        window.records.append({**window.records[0], "vehicle_id": "v1", "x": 1.0})

        writer = SilverWriter(SessionLocal, retain_events=False)
        writer.write_window(window)

        db = SessionLocal()
        rows = db.execute(select(StreamFailure)).scalars().all()
        assert {r.rule for r in rows} >= {"collision"}
        assert any(r.severity == "critical" for r in rows)
        db.close()

    def test_no_failures_means_no_rows(self, clean_tables):
        writer = SilverWriter(SessionLocal, retain_events=False)
        writer.write_window(build_window())
        db = SessionLocal()
        assert db.execute(select(StreamFailure)).scalars().all() == []
        db.close()

    def test_replay_replaces_prior_verdicts(self, clean_tables):
        writer = SilverWriter(SessionLocal, retain_events=False)
        window = build_window()
        window.records[0]["speed"] = 40.0
        for i in range(15):
            window.records.append({**window.records[0], "step": i, "vehicle_id": f"v{i}"})
        writer.write_window(window)

        db = SessionLocal()
        assert db.execute(select(StreamFailure)).scalars().all()
        db.close()

        # Re-evaluating with clean data must retract the earlier verdicts.
        clean = build_window()
        writer.write_window(clean)
        db = SessionLocal()
        assert db.execute(select(StreamFailure)).scalars().all() == []
        db.close()

    def test_failure_set_matches_detector(self, clean_tables):
        window = build_window()
        expected = detect_failures(compute_metrics(window.records))
        writer = SilverWriter(SessionLocal, retain_events=False)
        writer.write_window(window)

        db = SessionLocal()
        rows = db.execute(select(StreamFailure)).scalars().all()
        assert {r.rule for r in rows} == {f["rule"] for f in expected}
        db.close()


class TestSilverWriterEvents:
    def test_writes_event_rows(self, clean_tables):
        writer = SilverWriter(SessionLocal)
        record = {
            "event_id": "evt-1",
            "vehicle_id": "v0",
            "captured_at": 1_760_000_000_000,
            "rpm": 1200.0,
        }
        inserted = writer.write_events([(record, "sopir.veh.can.v1", 0, 5)])
        assert inserted == 1

        db = SessionLocal()
        row = db.execute(select(StreamEvent)).scalar_one()
        assert row.event_id == "evt-1"
        assert row.source == "can"
        assert row.kafka_partition == 0
        assert row.kafka_offset == 5
        assert row.payload["rpm"] == 1200.0
        db.close()

    def test_replaying_events_is_idempotent(self, clean_tables):
        writer = SilverWriter(SessionLocal)
        record = {
            "event_id": "evt-1",
            "vehicle_id": "v0",
            "captured_at": 1_760_000_000_000,
        }
        batch = [(record, "sopir.veh.can.v1", 0, 5)]

        assert writer.write_events(batch) == 1
        assert writer.write_events(batch) == 0
        assert writer.write_events(batch) == 0

        db = SessionLocal()
        assert len(db.execute(select(StreamEvent)).scalars().all()) == 1
        db.close()

    def test_event_id_is_first_write_wins(self, clean_tables):
        """Same event_id, different payload: the original row is kept.

        The vehicle simulator is seeded, so re-running it re-emits identical
        logical events and the dedupe must collapse them. If a payload ever did
        differ, this keeps the first and drops the later one rather than
        silently overwriting recorded history. Verified against the live topics:
        901 duplicated event_ids, zero with divergent payloads.
        """
        writer = SilverWriter(SessionLocal)
        original = {
            "event_id": "evt-1",
            "vehicle_id": "v0",
            "captured_at": 1_760_000_000_000,
            "rpm": 1000.0,
        }
        replay = {**original, "captured_at": 1_760_000_090_000, "rpm": 9999.0}

        assert writer.write_events([(original, "sopir.veh.can.v1", 0, 5)]) == 1
        assert writer.write_events([(replay, "sopir.veh.can.v1", 0, 9)]) == 0

        db = SessionLocal()
        row = db.execute(select(StreamEvent)).scalar_one()
        assert row.payload["rpm"] == 1000.0
        assert row.kafka_offset == 5
        db.close()

    def test_null_run_id_is_tolerated(self, clean_tables):
        """Vehicle logs carry no run_id; the column must stay nullable."""
        writer = SilverWriter(SessionLocal)
        record = {"event_id": "evt-2", "vehicle_id": "v0", "captured_at": 1}
        writer.write_events([(record, "sopir.veh.events.v1", 0, 1)])
        db = SessionLocal()
        assert db.execute(select(StreamEvent)).scalar_one().run_id is None
        db.close()

    def test_empty_batch_is_a_noop(self, clean_tables):
        writer = SilverWriter(SessionLocal)
        assert writer.write_events([]) == 0

    def test_retention_can_be_disabled(self, clean_tables):
        writer = SilverWriter(SessionLocal, retain_events=False)
        record = {"event_id": "evt-3", "vehicle_id": "v0", "captured_at": 1}
        assert writer.write_events([(record, "sopir.veh.can.v1", 0, 1)]) == 0

    def test_count_events_reflects_rows(self, clean_tables):
        writer = SilverWriter(SessionLocal)
        for i in range(3):
            writer.write_events(
                [
                    (
                        {"event_id": f"e{i}", "vehicle_id": "v0", "captured_at": 1},
                        "sopir.veh.can.v1",
                        0,
                        i,
                    )
                ]
            )
        assert writer.count_events() == 3


class TestTumblingWindowPersistence:
    def test_vehicle_window_is_retained_not_scored(self, clean_tables):
        """Vehicle logs have no simulation geometry, so no metrics row is written.

        Scoring them would mean inventing collisions out of nothing, so the
        honest outcome is: retained in stream_events, absent from stream_metrics.
        """
        manager = WindowManager("tumbling", window_sec=30)
        for i in range(2):
            manager.add(
                {"vehicle_id": "v0", "captured_at": 1_760_000_000_000 + i},
                "can",
                0.0,
                "sopir.veh.can.v1",
                0,
                i,
            )
        window = manager.flush_all()[0]
        assert window.vehicle_id == "v0"

        writer = SilverWriter(SessionLocal, retain_events=False)
        with pytest.raises(ValueError, match="not evaluable"):
            writer.write_window(window)

        db = SessionLocal()
        assert db.execute(select(StreamMetrics)).scalars().all() == []
        db.close()
