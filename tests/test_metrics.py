"""Unit tests for compute_metrics function."""

from __future__ import annotations

from evaluation.metrics import compute_metrics


def test_empty_telemetry_returns_zero_metrics():
    result = compute_metrics([])

    assert result == {
        "collision_count": 0,
        "min_ttc": None,
        "avg_speed": 0.0,
        "speed_violations": 0,
        "lane_deviations": 0,
        "ttc_per_step": [],
    }


def test_ego_telemetry_computes_avg_speed(ego_telemetry):
    result = compute_metrics(ego_telemetry)

    assert result["avg_speed"] == 10.0
    assert result["collision_count"] == 0
    assert result["speed_violations"] == 0
    assert result["lane_deviations"] == 0
    assert len(result["ttc_per_step"]) == 100
    assert all(v is None for v in result["ttc_per_step"])


def test_collision_telemetry_detects_collision(collision_telemetry):
    result = compute_metrics(collision_telemetry)

    assert result["collision_count"] > 0
    assert result["min_ttc"] is not None
    assert result["min_ttc"] < 1.0
    assert len(result["ttc_per_step"]) == 50


def test_low_ttc_telemetry_detects_low_ttc(low_ttc_telemetry):
    result = compute_metrics(low_ttc_telemetry)

    assert result["collision_count"] == 0
    assert result["min_ttc"] is not None
    assert result["min_ttc"] < 1.0
    assert len(result["ttc_per_step"]) == 50


def test_speed_violation_telemetry_counts_violations(speed_violation_telemetry):
    result = compute_metrics(speed_violation_telemetry)

    assert result["speed_violations"] == 20
    assert result["avg_speed"] > 10.0
    assert len(result["ttc_per_step"]) == 100


def test_lane_deviation_telemetry_counts_deviations(lane_deviation_telemetry):
    result = compute_metrics(lane_deviation_telemetry)

    assert result["lane_deviations"] > 0
    assert len(result["ttc_per_step"]) == 100


def test_clean_telemetry_no_failures(clean_telemetry):
    result = compute_metrics(clean_telemetry)

    assert result["collision_count"] == 0
    assert result["min_ttc"] is None
    assert result["speed_violations"] == 0
    assert result["lane_deviations"] == 0
    assert result["avg_speed"] == 10.0
    assert len(result["ttc_per_step"]) == 100


def test_ttc_per_step_length_matches_max_step():
    telemetry = [
        {
            "step": 0,
            "vehicle_id": "ego",
            "x": 0.0,
            "y": 0.0,
            "speed": 10.0,
            "angle": 90.0,
            "lane_id": "E0_0",
        },
        {
            "step": 5,
            "vehicle_id": "ego",
            "x": 50.0,
            "y": 0.0,
            "speed": 10.0,
            "angle": 90.0,
            "lane_id": "E0_0",
        },
    ]
    result = compute_metrics(telemetry)
    assert len(result["ttc_per_step"]) == 6
