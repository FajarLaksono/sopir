"""Unit tests for detect_failures function."""

from __future__ import annotations

from evaluation.failure_detector import detect_failures


def test_collision_triggers_critical_failure():
    metrics = {
        "collision_count": 1,
        "min_ttc": 0.5,
        "avg_speed": 10.0,
        "speed_violations": 0,
        "lane_deviations": 0,
    }

    failures = detect_failures(metrics)

    # Both collision (critical) and low TTC (high) should be reported
    assert len(failures) == 2
    severities = {f["severity"] for f in failures}
    assert severities == {"critical", "high"}
    rules = {f["rule"] for f in failures}
    assert rules == {"collision", "min_ttc_lt_1s"}


def test_low_ttc_triggers_high_failure():
    metrics = {
        "collision_count": 0,
        "min_ttc": 0.5,
        "avg_speed": 10.0,
        "speed_violations": 0,
        "lane_deviations": 0,
    }

    failures = detect_failures(metrics)

    assert len(failures) == 1
    assert failures[0]["severity"] == "high"
    assert failures[0]["rule"] == "min_ttc_lt_1s"
    assert failures[0]["details"]["ttc"] == 0.5


def test_speed_violations_triggers_medium_failure():
    metrics = {
        "collision_count": 0,
        "min_ttc": 2.0,
        "avg_speed": 18.0,
        "speed_violations": 15,
        "lane_deviations": 0,
    }

    failures = detect_failures(metrics)

    assert len(failures) == 1
    assert failures[0]["severity"] == "medium"
    assert failures[0]["rule"] == "speed_violations_gt_10"
    assert failures[0]["details"]["count"] == 15


def test_lane_deviations_triggers_medium_failure():
    metrics = {
        "collision_count": 0,
        "min_ttc": 2.0,
        "avg_speed": 10.0,
        "speed_violations": 0,
        "lane_deviations": 10,
    }

    failures = detect_failures(metrics)

    assert len(failures) == 1
    assert failures[0]["severity"] == "medium"
    assert failures[0]["rule"] == "lane_deviations_gt_5"
    assert failures[0]["details"]["count"] == 10


def test_multiple_failures_all_reported():
    metrics = {
        "collision_count": 2,
        "min_ttc": 0.3,
        "avg_speed": 20.0,
        "speed_violations": 20,
        "lane_deviations": 10,
    }

    failures = detect_failures(metrics)

    assert len(failures) == 4
    severities = {f["severity"] for f in failures}
    assert severities == {"critical", "high", "medium"}
    rules = {f["rule"] for f in failures}
    assert rules == {"collision", "min_ttc_lt_1s", "speed_violations_gt_10", "lane_deviations_gt_5"}


def test_clean_metrics_no_failures():
    metrics = {
        "collision_count": 0,
        "min_ttc": 5.0,
        "avg_speed": 10.0,
        "speed_violations": 0,
        "lane_deviations": 0,
    }

    failures = detect_failures(metrics)

    assert failures == []


def test_exact_threshold_no_failure():
    metrics = {
        "collision_count": 0,
        "min_ttc": 1.0,
        "avg_speed": 15.0,
        "speed_violations": 10,
        "lane_deviations": 5,
    }

    failures = detect_failures(metrics)

    assert failures == []


def test_none_min_ttc_no_high_failure():
    metrics = {
        "collision_count": 0,
        "min_ttc": None,
        "avg_speed": 10.0,
        "speed_violations": 0,
        "lane_deviations": 0,
    }

    failures = detect_failures(metrics)

    assert failures == []
