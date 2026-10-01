---
name: traci-debugging
description: Debug SUMO and TraCI problems in the simulation worker - process leaks, missing sumo binary, stuck runs, empty telemetry, duplicate run claims, and telemetry batch sizing. Use when investigating simulation/worker.py, simulation/traci_client.py, or a run stuck in running/failed status.
compatibility: opencode
metadata:
  project: sopir
  layer: simulation
---

## What I do

`simulation/worker.py` is a stateless poll loop; all state lives in Postgres.

- Poll interval: `POLL_INTERVAL` (default 5s).
- Claim: `SELECT ... WHERE status='queued' ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED`.
  `SKIP LOCKED` is what makes horizontal scaling safe.
- Execute: `simulation/traci_client.py` `sumo_connection` context manager.
- Store: telemetry batched with `bulk_save_objects` every
  `TELEMETRY_BATCH_SIZE` rows (default 100).

Per step the worker records, for every vehicle:
`step`, `vehicle_id`, `x`, `y`, `speed`, `angle`, `lane_id`.

## The lifecycle I must respect

`queued -> running -> completed | failed`

- `worker_id` is `HOSTNAME` or `worker-<uuid8>`.
- `started_at` on `running`, `completed_at` on `completed`/`failed`.
- `error_message` is the only place a failure reason lives. Report it verbatim.
- The worker owns the run status. Evaluation is read-only and must never patch
  it back to `completed`.

## Known leaks and how to spot them

`traci_client.sumo_connection` must always be used as a context manager:

```python
with sumo_connection(config_path):
    ...
```

The `finally` block calls `traci.close()` and swallows exceptions. If you see
stray `sumo` processes or `ConnectionRefusedError` on the next poll, someone
bypassed the context manager. Never call `traci.start` directly.

SIGTERM/SIGINT call `sys.exit(0)` immediately, so an in-flight run can be left
`running` forever. That is the known sharp edge: a graceful "finish current run"
handler is Phase 2 work. If you see orphaned `running` rows after a scale-down,
that is why.

## Diagnostic checklist

1. `docker compose logs worker` - read the actual exception first.
2. `sumo not found` or similar -> the worker image lacks SUMO, or `SUMO_HOME` is
   wrong. This is an image problem, not a scenario problem.
3. Run is `failed` with `Scenario has no config file` -> generation never
   produced artifacts. Use the `scenario-generation` skill.
4. Run is `failed` with `Config file not found: <path>` -> the path in the DB
   does not exist inside the container. Check the `scenario_data` mount.
5. Run stays `queued` -> no worker is running, or the worker's DB session
   cannot see the row.
6. Telemetry empty but run `completed` -> the scenario ended before any vehicle
   entered. Inspect the route file's `depart` times against the 100s window.
7. Duplicate claims -> `SKIP LOCKED` was removed from the claim query. Restore it.

## Constraints

- Never bump `TELEMETRY_BATCH_SIZE` above a few hundred to "fix" slowness. It
  is an insert-rate tradeoff, and losing the batch on a crash is worse.
- Never add a second engine or store worker state in memory.
- Report the log line, not a paraphrase of it.
