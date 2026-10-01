---
description: Scale up simulation workers to drain the queued run backlog
agent: build
---

Start simulation workers for this project.

Worker count: $ARGUMENTS (default: 3)

Steps:

1. Show current run backlog per status:
   `curl -s "http://localhost:8000/api/v1/runs?status=queued&page_size=100"`
2. Scale the worker pool:
   `docker compose up -d --scale worker=$ARGUMENTS`
   When no argument is supplied use `--scale worker=3`.
3. Follow progress:
   `docker compose logs -f worker`
   and `curl -s "http://localhost:8000/api/v1/runs?page_size=50"`
4. Report how many runs moved `queued -> running -> completed|failed`, and list
   any `failed` runs with their `error_message`.

Notes:

- The worker image must contain SUMO. If a run fails with a `sumo` not found
  error, the Dockerfile is missing `SUMO_HOME` rather than the run being bad.
- Workers are stateless; all state lives in Postgres. Scaling is safe at any
  time.
- Runs are claimed with `SELECT ... FOR UPDATE SKIP LOCKED`, so no two workers
  pick up the same run.
- Never run `docker compose down -v`; it deletes the Postgres volume and all
  run history.
