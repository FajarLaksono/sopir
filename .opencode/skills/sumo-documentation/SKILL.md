---
name: sumo-documentation
description: Keep project documentation accurate and explain SUMO/TraCI concepts precisely. Use when updating README.md, docs/PLANNING_phase_1_MVP.md, docs/PLANNING_phase_2_STREAMING_PIPELINE.md, docs/architecture.md or docs/SUMO_RESEARCH.md, when explaining what SUMO does or does not model, or when a doc claim no longer matches the code.
compatibility: opencode
metadata:
  project: sopir
  layer: docs
---

## What I do

Documentation is part of the deliverable for this portfolio project, not an
afterthought. This skill covers both writing the docs and stating SUMO facts
correctly.

Doc map:

| file | owns |
|------|------|
| `README.md` | pitch, quickstart, architecture summary, env table, demo script |
| `docs/PLANNING_phase_1_MVP.md` | MVP roadmap, scope, phase gates, deliberate omissions |
| `docs/PLANNING_phase_2_STREAMING_PIPELINE.md` | streaming ingestion roadmap: topics, schemas, phases |
| `docs/architecture.md` | components, data flow, decisions and their rationale, Phase 2 |
| `docs/SUMO_RESEARCH.md` | SUMO/TraCI reference, observed-vs-available gap |
| `AGENTS.md` | conventions and rules the agent follows |
| `opencode.json`, `.opencode/**` | agent configuration, commands, skills |

## Naming

The project is **Sopir**. Use "Sopir" in prose. `OpenDriveLab` survives only
as a runtime identifier that is out of scope for documentation edits:

- Postgres database name and volume `opendrivelab` (`.env.example`,
  `docker-compose.yml`, `backend/config.py`, `alembic.ini`)
- container names `opendrivelab-db` / `-backend` / `-dashboard`
- `health` response `service: opendrivelab-backend`
- `page_title="OpenDriveLab Dashboard"` and `title="OpenDriveLab API"`

Do not document `opendrivelab` as if it were `sopir`. When a doc shows a
`DATABASE_URL`, copy the real value. If the identifier is renamed later, that
is a separate code change with its own migration and its own doc pass.

## Framing SUMO correctly

State this plainly whenever it is relevant:

- SUMO is a **traffic** simulator with behavioural driver models (Krauss,
  Gipps, IDM, LC2013, SL2015). It is not an ADAS stack.
- It gives no perception model, no occupancy grid, no sensor model, no
  planner. Validating a controller here means exercising it against scripted
  human drivers.
- TraCI is the socket control interface; `libsumo` is the faster in-process
  alternative. This project uses TraCI.
- Integration paths and version details live in `docs/SUMO_RESEARCH.md`. Cite
  it rather than restating version numbers from memory.

## Accuracy rules

1. **Every command in a doc must run.** Verify against `docker-compose.yml`,
   `scenario_gen/cli.py`, `backend/main.py` and the CLI help before publishing.
2. **Every endpoint must exist.** Cross-check the router in
   `backend/main.py` for prefixes and `backend/api/*.py` for paths and methods.
   The evaluation route is `POST /api/v1/metrics/runs/{run_id}/evaluate`, and
   failures are at `/api/v1/failures`, not `/api/v1/fails`.
3. **No aspirational claims.** Write what the code does today. Put future work
   in the roadmap, labelled as future work.
4. **Document known limitations.** The TTC caveat, the lane-deviation
   over-count, the hard `SIGTERM` exit and the per-step telemetry rescan are
   real and already written down. Do not delete them to make the project look
   cleaner; naming them is the engineering signal.
5. **One source of truth per fact.** Config lives in `backend/config.py`.
   Templates live in `scenario_gen/templates/`. Do not duplicate values into a
   second doc that can drift.

## Style

- Markdown, sentence case headings, tables for anything with three or more
  items.
- Commands in fenced blocks with the language tag. No screenshots for
  text-reproducible steps.
- Write for an interviewer reading cold: state what the system does, then how,
  then what it deliberately does not do.
- ASCII only in committed docs. Box-drawing and arrow characters render as
  mojibake in some terminals.
- UTF-8 without a BOM.
