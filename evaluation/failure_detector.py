from typing import Any


def detect_failures(metrics: dict[str, Any]) -> list[dict[str, Any]]:
    failures = []

    if metrics.get("collision_count", 0) > 0:
        failures.append(
            {
                "severity": "critical",
                "rule": "collision",
                "details": {"count": metrics["collision_count"]},
            }
        )

    min_ttc = metrics.get("min_ttc")
    if min_ttc is not None and min_ttc < 1.0:
        failures.append(
            {
                "severity": "high",
                "rule": "min_ttc_lt_1s",
                "details": {"ttc": min_ttc},
            }
        )

    if metrics.get("speed_violations", 0) > 10:
        failures.append(
            {
                "severity": "medium",
                "rule": "speed_violations_gt_10",
                "details": {"count": metrics["speed_violations"]},
            }
        )

    if metrics.get("lane_deviations", 0) > 5:
        failures.append(
            {
                "severity": "medium",
                "rule": "lane_deviations_gt_5",
                "details": {"count": metrics["lane_deviations"]},
            }
        )

    return failures
