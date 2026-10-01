---
name: evaluation-metrics
description: Compute and interpret validation metrics and failure rules in evaluation/. Use when adding a metric, changing a failure threshold, debugging min_ttc or collision_count, writing tests for evaluation/metrics.py or evaluation/failure_detector.py, or explaining a failure to a reader.
compatibility: opencode
metadata:
  project: sopir
  layer: evaluation
---

## What I do

`evaluation/` is deliberately pure: no database, no network, no I/O. That is
what makes it testable with synthetic telemetry.

- `evaluation/metrics.py` - `compute_metrics(telemetry: list[dict]) -> dict`
- `evaluation/failure_detector.py` - `detect_failures(metrics: dict) -> list[dict]`

A telemetry row is `{vehicle_id, step, x, y, speed, angle, lane_id}`.

## Metrics produced

| key | meaning | empty-input value |
|-----|---------|-------------------|
| `collision_count` | pairs closer than 2.5 m, summed per step | `0` |
| `min_ttc` | smallest time-to-collision across all steps, or `None` | `None` |
| `avg_speed` | mean speed over all samples | `0.0` |
| `speed_violations` | samples above 15.0 m/s | `0` |
| `lane_deviations` | consecutive-sample lane changes per vehicle | `0` |
| `ttc_per_step` | per-step minimum TTC, `None` where undefined | `[]` |

Empty input must return exactly the empty-input shape above, not raise.

## Failure rules

| rule | condition | severity |
|------|-----------|----------|
| `collision` | `collision_count > 0` | `critical` |
| `min_ttc_lt_1s` | `min_ttc is not None and min_ttc < 1.0` | `high` |
| `speed_violations_gt_10` | `speed_violations > 10` | `medium` |
| `lane_deviations_gt_5` | `lane_deviations > 5` | `medium` |

Each failure is `{severity, rule, details}`. Order in the returned list is
collision, then TTC, then the two medium rules. Keep that order stable; the
dashboard ranks by it.

## Known correctness caveats - surface these, do not silently patch them

1. **TTC ignores geometry.** `ttc = dist / abs(speed1 - speed2)` uses the
   scalar speed difference, not the relative velocity vector along the line of
   travel. Lateral separation is treated the same as longitudinal, and two
   vehicles at identical speeds produce `None` even when they are 1 m apart.
2. **The speed guard is weak.** `if s1["speed"] > 0 or s2["speed"] > 0` lets a
   stationary vehicle pair produce an enormous TTC.
3. **`lane_deviations` counts normal lane changes.** Any `lane_id` change is
   counted, including legitimate overtakes. In `lane_change` this metric is
   expected to be high and is not by itself a defect.
4. **Complexity.** `compute_metrics` rescans the full telemetry list per step.
   Fine at current volumes, not at 10k+ runs. See `docs/architecture.md`.

When a metric looks wrong, say which caveat applies. Only change the maths when
the user explicitly asks, and update `tests/test_metrics.py` in the same change.

## Adding a metric

1. Pure function in `evaluation/metrics.py`, fully type hinted.
2. Add the field to the `Metrics` model and to the Pydantic response schema.
3. Add a failure rule only if the metric is threshold based.
4. Add cases to `tests/test_metrics.py`, including the empty-input case.
5. Update `dashboard/components/metric_charts.py`.

## Adding a failure rule

Keep `detect_failures` a single pure function. Use `.get()` with the same
defaults the metric function returns, guard `None` explicitly, and never import
from `backend/` or `simulation/`.
