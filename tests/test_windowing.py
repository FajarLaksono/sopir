"""Unit tests for streaming/windowing.py.

Pure logic, no Kafka or Postgres, so these run fast and pin down the
aggregation rules the parity test depends on.
"""

from datetime import datetime, timezone

import pytest

from streaming.windowing import (
    WINDOW_IDLE_TIMEOUT_SEC,
    Window,
    WindowManager,
    floor_to_window,
    window_end_for,
)


def sim_record(run_id: str, step: int, captured_at_ms: int, vehicle_id: str = "v0") -> dict:
    return {
        "run_id": run_id,
        "vehicle_id": vehicle_id,
        "step": step,
        "x": 1.0,
        "y": 2.0,
        "speed": 10.0,
        "angle": 0.0,
        "lane_id": "lane_0",
        "captured_at": captured_at_ms,
    }


def veh_record(vehicle_id: str, captured_at_ms: int) -> dict:
    return {"vehicle_id": vehicle_id, "captured_at": captured_at_ms}


class TestFloorToWindow:
    def test_aligns_down_to_boundary(self):
        ts = datetime(2026, 10, 1, 9, 0, 45, tzinfo=timezone.utc)
        assert floor_to_window(ts, 30) == datetime(2026, 10, 1, 9, 0, 30, tzinfo=timezone.utc)

    def test_exact_boundary_is_unchanged(self):
        ts = datetime(2026, 10, 1, 9, 0, 30, tzinfo=timezone.utc)
        assert floor_to_window(ts, 30) == ts

    def test_is_epoch_aligned_not_relative(self):
        """Two records minutes apart land in windows keyed off absolute time."""
        early = datetime(2026, 10, 1, 9, 0, 5, tzinfo=timezone.utc)
        late = datetime(2026, 10, 1, 9, 5, 55, tzinfo=timezone.utc)
        assert floor_to_window(early, 30) != floor_to_window(late, 30)

    def test_window_end_follows_start(self):
        start = floor_to_window(datetime(2026, 10, 1, 9, 0, 5, tzinfo=timezone.utc), 60)
        assert window_end_for(start, 60) == datetime(2026, 10, 1, 9, 1, tzinfo=timezone.utc)


class TestRunWindows:
    def test_records_for_one_run_share_a_window(self):
        manager = WindowManager("run")
        base = 1_000_000
        for step in range(5):
            manager.add(sim_record("run-a", step, base), "sim", 0.0, "t", 0, step)
        assert manager.open_count == 1

    def test_different_runs_get_separate_windows(self):
        manager = WindowManager("run")
        manager.add(sim_record("run-a", 0, 1000), "sim", 0.0, "t", 0, 0)
        manager.add(sim_record("run-b", 0, 1000), "sim", 0.0, "t", 1, 0)
        assert manager.open_count == 2

    def test_missing_run_id_is_rejected(self):
        manager = WindowManager("run")
        record = sim_record("run-a", 0, 1000)
        del record["run_id"]
        with pytest.raises(ValueError, match="run_id"):
            manager.add(record, "sim", 0.0, "t", 0, 0)

    def test_idle_timeout_closes_window(self):
        manager = WindowManager("run", idle_timeout_sec=10.0)
        manager.add(
            sim_record("run-a", 0, 1000), "sim", now=100.0, topic="t", partition=0, offset=0
        )
        assert manager.close_due(now=105.0) == []
        closed = manager.close_due(now=111.0)
        assert len(closed) == 1
        assert closed[0].key == "run:run-a"

    def test_default_idle_timeout_is_used(self):
        manager = WindowManager("run")
        manager.add(sim_record("run-a", 0, 1000), "sim", now=0.0, topic="t", partition=0, offset=0)
        just_under = WINDOW_IDLE_TIMEOUT_SEC - 1
        assert manager.close_due(now=just_under) == []
        assert len(manager.close_due(now=WINDOW_IDLE_TIMEOUT_SEC + 1)) == 1

    def test_active_window_stays_open(self):
        """A run still producing telemetry must not be evaluated mid-flight."""
        manager = WindowManager("run", idle_timeout_sec=10.0)
        manager.add(sim_record("run-a", 0, 1000), "sim", 0.0, "t", 0, 0)
        manager.add(sim_record("run-a", 1, 2000), "sim", 9.0, "t", 0, 1)
        assert manager.close_due(now=15.0) == []

    def test_force_close_removes_specific_run(self):
        manager = WindowManager("run")
        manager.add(sim_record("run-a", 0, 1000), "sim", 0.0, "t", 0, 0)
        manager.add(sim_record("run-b", 0, 1000), "sim", 0.0, "t", 1, 0)
        window = manager.force_close_run("run-a")
        assert window is not None and window.key == "run:run-a"
        assert manager.open_count == 1
        assert manager.force_close_run("run-a") is None

    def test_offsets_track_high_water_mark(self):
        manager = WindowManager("run")
        manager.add(sim_record("run-a", 0, 1000), "sim", 0.0, "t", 0, 5)
        manager.add(sim_record("run-a", 1, 2000), "sim", 0.0, "t", 0, 9)
        manager.add(sim_record("run-a", 2, 3000), "sim", 0.0, "t", 0, 3)
        window = manager.force_close_run("run-a")
        assert window.last_offsets == {"t:0": 9}


class TestTumblingWindows:
    def test_window_key_includes_vehicle_and_time(self):
        manager = WindowManager("tumbling", window_sec=30)
        base = 1_760_000_000_000
        manager.add(veh_record("v0", base), "can", 0.0, "t", 0, 0)
        manager.add(veh_record("v1", base), "can", 0.0, "t", 0, 1)
        assert manager.open_count == 2

    def test_records_in_same_window_merge(self):
        manager = WindowManager("tumbling", window_sec=60)
        base = 1_760_000_000_000
        manager.add(veh_record("v0", base), "can", 0.0, "t", 0, 0)
        manager.add(veh_record("v0", base + 500), "can", 0.0, "t", 0, 1)
        assert manager.open_count == 1

    def test_boundary_splits_windows(self):
        manager = WindowManager("tumbling", window_sec=30)
        start_ms = 1_760_000_010_000
        manager.add(veh_record("v0", start_ms), "can", 0.0, "t", 0, 0)
        manager.add(veh_record("v0", start_ms + 30_000), "can", 0.0, "t", 0, 1)
        assert manager.open_count == 2

    def test_window_metadata_is_set(self):
        manager = WindowManager("tumbling", window_sec=30)
        manager.add(veh_record("v0", 1_760_000_000_000), "gnss", 0.0, "t", 0, 0)
        window = next(iter(manager._windows.values()))
        assert window.window_start is not None and window.window_end is not None
        assert (window.window_end - window.window_start).total_seconds() == 30

    def test_closes_when_clock_passes_boundary(self):
        manager = WindowManager("tumbling", window_sec=30)
        record_ms = 1_760_000_000_000
        manager.add(veh_record("v0", record_ms), "can", 0.0, "t", 0, 0)
        window = next(iter(manager._windows.values()))
        just_before = window.window_end.timestamp() - 1
        assert manager.close_due(now=just_before) == []
        closed = manager.close_due(now=window.window_end.timestamp() + 1)
        assert len(closed) == 1

    def test_missing_vehicle_id_is_rejected(self):
        manager = WindowManager("tumbling")
        record = veh_record("v0", 1000)
        del record["vehicle_id"]
        with pytest.raises(ValueError, match="vehicle_id"):
            manager.add(record, "can", 0.0, "t", 0, 0)

    def test_missing_captured_at_is_rejected(self):
        """A tumbling key cannot be derived without a timestamp."""
        manager = WindowManager("tumbling")
        with pytest.raises(ValueError, match="captured_at"):
            manager.add({"vehicle_id": "v0"}, "can", 0.0, "t", 0, 0)

    def test_run_window_does_not_require_captured_at(self):
        """Run windows are keyed by run_id alone, so time is irrelevant."""
        manager = WindowManager("run")
        record = sim_record("run-a", 0, 1000)
        del record["captured_at"]
        window = manager.add(record, "sim", 0.0, "t", 0, 0)
        assert window.key == "run:run-a"
        assert window.window_start is None


class TestWindowLifecycle:
    def test_flush_all_returns_everything(self):
        manager = WindowManager("run")
        manager.add(sim_record("run-a", 0, 1000), "sim", 0.0, "t", 0, 0)
        manager.add(sim_record("run-b", 0, 1000), "sim", 0.0, "t", 1, 0)
        assert len(manager.flush_all()) == 2
        assert manager.open_count == 0

    def test_oversized_window_set_is_evicted(self):
        manager = WindowManager("run", max_open_windows=3)
        for i in range(5):
            manager.add(sim_record(f"run-{i}", 0, 1000), "sim", 0.0, "t", i, 0)
        closed = manager.close_due(now=0.0)
        assert len(closed) == 2
        assert manager.open_count == 3

    def test_closed_windows_are_not_returned_twice(self):
        manager = WindowManager("run", idle_timeout_sec=1.0)
        manager.add(sim_record("run-a", 0, 1000), "sim", 0.0, "t", 0, 0)
        assert len(manager.close_due(now=10.0)) == 1
        assert manager.close_due(now=20.0) == []

    def test_eviction_preserves_newest_windows(self):
        manager = WindowManager("run", max_open_windows=2)
        for i in range(4):
            manager.add(sim_record(f"run-{i}", 0, 1000), "sim", 0.0, "t", i, 0)
        manager.close_due(now=0.0)
        remaining = set(manager._windows)
        assert remaining == {"run:run-2", "run:run-3"}


class TestWindow:
    def test_empty_window_is_never_closed(self):
        assert Window(key="k", source="sim").closed(now=1e9) is False

    def test_is_empty_reflects_record_count(self):
        window = Window(key="k", source="sim")
        assert window.is_empty
        window.add({"x": 1}, 0.0, "t", 0, 0)
        assert not window.is_empty
        assert window.count == 1


class TestManagerValidation:
    def test_unknown_strategy_rejected(self):
        with pytest.raises(ValueError, match="unknown window strategy"):
            WindowManager("session")
