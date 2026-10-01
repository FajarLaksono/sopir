---
description: Run the whole validation loop end to end, from templates to failures
agent: build
---

Execute the full Sopir validation loop and report the outcome.

Scenarios to run: $ARGUMENTS (default: all four generated scenario types)

Steps:

1. Bring the stack up:
   `docker compose up --build -d`
2. Generate scenarios from the templates:
   `docker compose exec backend python -m scenario_gen.cli generate`
3. Queue one run per scenario. Collect the scenario IDs from
   `GET /api/v1/scenarios`, then for each:
   `curl -s -X POST http://localhost:8000/api/v1/runs -H "Content-Type: application/json" -d '{"scenario_id":"<SCENARIO_ID>"}'`
4. Scale workers to drain the queue:
   `docker compose up -d --scale worker=3`
5. Poll until every run leaves `queued`:
   `curl -s "http://localhost:8000/api/v1/runs?page_size=100"`
   Watch `docker compose logs -f worker` for TraCI or SUMO errors.
6. Evaluate each completed run:
   `curl -s -X POST http://localhost:8000/api/v1/metrics/runs/<RUN_ID>/evaluate`
7. Collect and rank failures:
   `curl -s "http://localhost:8000/api/v1/failures?page_size=100"`
8. Report a table of `scenario_type -> run_id -> status -> min_ttc ->
   collision_count -> failure severities`, then name the single most important
   finding.

Rules:

- Never skip step 2. Runs require generated SUMO artifacts on the shared
  `scenario_data` volume; a run without artifacts fails immediately with
  "Config file not found".
- Never delete the Postgres volume. Use `/reset-db` only when explicitly asked.
- If a run is `failed`, report `error_message` verbatim before attempting any
  repair, and do not retry silently.
- The report is the deliverable. This command is about producing an auditable
  validation result, not about editing code.
