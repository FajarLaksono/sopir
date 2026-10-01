import math
from collections import defaultdict
from typing import Any


def compute_metrics(telemetry: list[dict[str, Any]]) -> dict[str, Any]:
    if not telemetry:
        return {
            "collision_count": 0,
            "min_ttc": None,
            "avg_speed": 0.0,
            "speed_violations": 0,
            "lane_deviations": 0,
            "ttc_per_step": [],
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

    max_step = max(t["step"] for t in telemetry)
    ttc_per_step = []

    for step in range(max_step + 1):
        step_telemetry = [t for t in telemetry if t["step"] == step]
        step_min_ttc = float("inf")
        for i, s1 in enumerate(step_telemetry):
            for s2 in step_telemetry[i + 1 :]:
                dx = s1["x"] - s2["x"]
                dy = s1["y"] - s2["y"]
                dist = math.hypot(dx, dy)
                if dist < 2.5:
                    collision_count += 1
                if s1["speed"] > 0 or s2["speed"] > 0:
                    v_rel = abs(s1["speed"] - s2["speed"])
                    if v_rel > 0 and dist > 0:
                        ttc = dist / v_rel
                        if ttc < step_min_ttc:
                            step_min_ttc = ttc
        if step_min_ttc == float("inf"):
            step_min_ttc = None
        else:
            if step_min_ttc < min_ttc:
                min_ttc = step_min_ttc
        ttc_per_step.append(step_min_ttc)

    for vid, states in vehicles.items():
        prev_lane = None
        for i, s in enumerate(states):
            speed_sum += s["speed"]
            speed_count += 1
            if s["speed"] > 15.0:
                speed_violations += 1
            if prev_lane is not None and s["lane_id"] != prev_lane:
                lane_deviations += 1
            prev_lane = s["lane_id"]

    if min_ttc == float("inf"):
        min_ttc = None

    return {
        "collision_count": collision_count,
        "min_ttc": min_ttc,
        "avg_speed": speed_sum / speed_count if speed_count else 0.0,
        "speed_violations": speed_violations,
        "lane_deviations": lane_deviations,
        "ttc_per_step": ttc_per_step,
    }
