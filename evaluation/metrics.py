from typing import Any
from collections import defaultdict
import math


def compute_metrics(telemetry: list[dict[str, Any]]) -> dict[str, Any]:
    if not telemetry:
        return {
            "collision_count": 0,
            "min_ttc": None,
            "avg_speed": 0.0,
            "speed_violations": 0,
            "lane_deviations": 0,
        }

    vehicles = defaultdict(list)
    for t in telemetry:
        vehicles[t["vehicle_id"]].append(t)

    collision_count = 0
    min_ttc = float("inf")
    speed_sum = 0.0
    speed_count = 0
    speed_violations = 0
    lane_deviations = 0

    for vid, states in vehicles.items():
        for i, s in enumerate(states):
            speed_sum += s["speed"]
            speed_count += 1
            if s["speed"] > 15.0:
                speed_violations += 1

        if vid == "ego" or "ego" in vid.lower():
            pass

    for i, s1 in enumerate(telemetry):
        for s2 in telemetry[i + 1:]:
            if s1["step"] != s2["step"]:
                continue
            dx = s1["x"] - s2["x"]
            dy = s1["y"] - s2["y"]
            dist = math.hypot(dx, dy)
            if dist < 2.5:
                collision_count += 1
            if s1["speed"] > 0 or s2["speed"] > 0:
                v_rel = abs(s1["speed"] - s2["speed"])
                if v_rel > 0 and dist > 0:
                    ttc = dist / v_rel
                    if ttc < min_ttc:
                        min_ttc = ttc

    if min_ttc == float("inf"):
        min_ttc = None

    return {
        "collision_count": collision_count,
        "min_ttc": min_ttc,
        "avg_speed": speed_sum / speed_count if speed_count else 0.0,
        "speed_violations": speed_violations,
        "lane_deviations": lane_deviations,
    }