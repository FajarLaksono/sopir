---
description: Trigger evaluation for a completed run and summarise metrics plus failures
agent: build
---

Evaluate a simulation run.

Run ID: $ARGUMENTS (if omitted, evaluate every completed run that has no metrics yet)

Actual API surface (from `backend/main.py`):

- `POST /api/v1/metrics/runs/{run_id}/evaluate`
- `GET  /api/v1/metrics/runs/{run_id}`
- `GET  /api/v1/failures?run_id={run_id}`

Steps:

1. Resolve the target run(s):
   `curl -s "http://localhost:8000/api/v1/runs?status=completed&page_size=100"`
   When an ID was supplied, confirm it exists and is `completed` first.
2. Trigger evaluation for each target:
   `curl -s -X POST http://localhost:8000/api/v1/metrics/runs/<RUN_ID>/evaluate`
3. Read the stored results:
   `curl -s http://localhost:8000/api/v1/metrics/runs/<RUN_ID>`
   `curl -s "http://localhost:8000/api/v1/failures?run_id=<RUN_ID>"`
4. Report per run: `min_ttc`, `collision_count`, `avg_speed`,
   `speed_violations`, `lane_deviations`, and every failure with its severity
   and rule name.

Rules of interpretation:

- `collision_count > 0` raises a `critical` `collision` failure. This is the most
  important signal in the whole platform; surface it first.
- `min_ttc < 1.0` raises a `high` severity `min_ttc_lt_1s` failure.
- Evaluation is a read-only analysis of stored telemetry. It deliberately does
  not rewrite the run status, which the worker owns. Never patch the run status
  from this flow, and never recompute metrics by hand in SQL.
- `evaluation/metrics.py` has a known correctness caveat: TTC is derived from
  the scalar speed difference rather than the relative velocity vector. Mention
  it once when a `min_ttc` looks surprising, but do not fix it unless asked.
