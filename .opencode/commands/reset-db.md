---
description: Tear down the stack and delete the database volume
agent: build
---

Reset the local Sopir database.

This is destructive. Every scenario, run, telemetry row, metrics row and
failure is lost. Confirm with the user before running step 2.

Steps:

1. Show what will be destroyed first:
   `curl -s "http://localhost:8000/api/v1/runs?page_size=1"` to report the
   current run count, and `docker compose ps` to show running services.
2. After explicit confirmation:
   `docker compose down -v`
   This removes the `postgres_data` volume. The `scenario_data` volume is also
   removed, so re-run `/generate-scenarios` afterwards.
3. Bring the stack back:
   `docker compose up -d db && docker compose up --build -d`
4. Verify:
   `curl -s http://localhost:8000/health`

Safety rules:

- Never run this command without an explicit confirmation in the conversation.
- Never use `docker compose down -v` as a troubleshooting step for a failing
  run. Diagnose the run first; the data is the point of the project.
- Schema is recreated by `Base.metadata.create_all` on backend startup. Do not
  hand-write SQL to truncate tables.
