# AI Configuration Plan

Project-local OpenCode configuration for **Sopir**: the eight slash commands,
five domain skills, and the permission model that keeps an agent useful on this
repository without letting it reach anything sensitive.

Everything here lives inside the repository. There is no global config, no MCP
server, no plugin and no network dependency.

---

## 1. Objectives

| # | Requirement | How it is met |
|---|-------------|---------------|
| 1 | The agent understands Sopir's purpose without being re-briefed | `instructions` loads `AGENTS.md`, `docs/architecture.md`, `docs/SUMO_RESEARCH.md` into every session |
| 2 | Common operations are one keystroke | 8 commands in `.opencode/commands/` |
| 3 | Domain knowledge is reusable and loadable on demand | 5 skills in `.opencode/skills/`, each with a trigger-rich `description` |
| 4 | No secrets are read or written | `*.env`, key/cert and credential patterns denied for `read`/`edit`/`glob`/`grep`/`list` |
| 5 | No access outside the project | `external_directory: deny` |
| 6 | No exfiltration path | `webfetch: deny`, `websearch: deny`, `curl`/`wget`/`ssh`/`scp`/`rsync`/`sudo` denied |
| 7 | Destructive operations cannot happen silently | `rm`, `del`, `rmdir`, `Remove-Item`, `docker ... down -v`, `git push`, `git reset --hard` denied |
| 8 | Default model, not a pinned one | No `model` key anywhere in the config |
| 9 | Portable across OSes | Dashboard command selects the opener per platform |
| 10 | Reversible | Every addition is a new file or an additive doc line; no code was modified |

---

## 2. Layout

```
opencode.json                              # permissions, instructions, share
.opencode/
├── commands/
│   ├── generate-scenarios.md
│   ├── run-workers.md
│   ├── evaluate-run.md
│   ├── open-dashboard.md
│   ├── full-validation-loop.md
│   ├── reset-db.md
│   ├── run-tests.md
│   └── lint.md
└── skills/
    ├── scenario-generation/SKILL.md
    ├── traci-debugging/SKILL.md
    ├── evaluation-metrics/SKILL.md
    ├── dashboard-components/SKILL.md
    └── sumo-documentation/SKILL.md
```

Formats follow the OpenCode docs exactly:

- config at `opencode.json`, validated against `https://opencode.ai/config.json`
- commands: `<dir>/commands/<name>.md`, filename becomes `/<name>`
- skills: `<dir>/skills/<name>/SKILL.md`, `name` matches the directory and
  matches `^[a-z0-9]+(-[a-z0-9]+)*$`

---

## 3. Commands

| Command | Does | Notes |
|---------|------|-------|
| `/generate-scenarios` | Runs `scenario_gen.cli generate`, verifies 4 scenarios | Optional `$ARGUMENTS` for a single type. Templates stay the source of truth; never hand-writes artifacts. |
| `/run-workers` | `docker compose up -d --scale worker=N` | Defaults to 3 workers. Reports queued-to-terminal transitions and `error_message` verbatim. |
| `/evaluate-run` | `POST /api/v1/metrics/runs/{id}/evaluate`, reads metrics + failures | Optional run ID; otherwise every completed run. Uses the real route, not an invented one. |
| `/open-dashboard` | Health-checks `:8501` then opens a browser | Picks `open` / `xdg-open` / `Start-Process` per platform. |
| `/full-validation-loop` | Templates to ranked failures, end to end | The interview demo script as a single command. |
| `/reset-db` | `docker compose down -v` then rebuild | Destructive. Requires explicit confirmation and reports the run count first. |
| `/run-tests` | `pytest -v --cov=...` | Both verbose and coverage, per decision. Distinguishes a missing `psycopg` from a real defect. |
| `/lint` | `ruff check` + `ruff format` + `mypy` | Reverts incidental reformatting rather than sweeping the repo. |

Every command carries the constraints that make it safe: no `down -v` as a
troubleshooting step, no hand-editing artifacts, no patching run status from the
evaluation path.

---

## 4. Skills

Each skill has a single purpose and a `description` written so the agent picks
it correctly.

| Skill | Owns | Trigger examples |
|-------|------|------------------|
| `scenario-generation` | `scenario_gen/`, template validation, adding a scenario type | "add a new scenario type", "ScenarioGenerationError", "Config file not found" |
| `traci-debugging` | `simulation/worker.py`, `traci_client.py`, SUMO process lifecycle | "run is stuck", "no telemetry", "sumo not found", "orphaned running run" |
| `evaluation-metrics` | `evaluation/metrics.py`, `failure_detector.py`, metric and threshold work | "add a metric", "why is min_ttc wrong", "add a failure rule", "test the metrics" |
| `dashboard-components` | `dashboard/app.py`, `dashboard/components/` | "add a metric chart", "dashboard is slow", "dashboard shows no rows" |
| `sumo-documentation` | `README.md`, `docs/PLANNING_phase_1_MVP.md`, `docs/PLANNING_phase_2_STREAMING_PIPELINE.md`, `docs/architecture.md`, `docs/SUMO_RESEARCH.md`, Sopir naming | "update the README", "explain what SUMO models", "rename in the docs" |

### What the skills encode that the repo does not

- **Known-correctness caveats are written down, not hidden.** TTC uses the
  scalar speed difference rather than the relative velocity vector; the
  stationary-vehicle guard is weak; `lane_deviations` counts legitimate lane
  changes; `compute_metrics` rescans per step. The agent is told to *surface*
  these rather than silently patch them, which keeps a "fix" from quietly
  changing metric semantics.
- **Known sharp edges.** The worker's `SIGTERM` handler calls `sys.exit(0)`
  immediately, so an in-flight run can be left `running`. Evaluation is
  deliberately read-only and must not relabel a run as completed.
- **The TraCI context manager is not optional.** Calling `traci.start` directly
  is what leaks SUMO processes.
- **Naming.** "Sopir" in prose. `opendrivelab` is documented as the runtime
  identifier it actually is, because the code still uses it and docs that lie
  about `DATABASE_URL` are worse than no docs.

---

## 5. Permission model

Baseline is `ask`, not `allow`. OpenCode's own defaults are permissive, so an
explicit inverted baseline is what actually buys the guarantee.

```
permission:
  read / glob / grep / list  "*": allow, then deny secrets, keys, *.env, .git/*
  edit                       "*": ask, allow *.md, .opencode/*, docs/*
  bash                       "*": ask, allow the safe dev verbs, deny the rest
  external_directory         deny
  webfetch / websearch       deny
```

Notes on specific rules:

- `*.env.*` is denied **before** `*.env.example` is allowed. OpenCode evaluates
  the last matching rule, so ordering is what makes the template readable while
  the real file stays closed.
- Deny `*.git/*` as well: no index, no objects, no config.
- `python*` is `ask` rather than `allow`. `python -m pytest` is legitimate, but
  `python -c` is arbitrary code execution.
- `docker compose*` is `ask`; `docker compose down -v*` is denied. Stopping the
  stack is fine, deleting the volume is not.
- `doom_loop` is `ask` so a failing loop surfaces instead of burning tokens.

### What was deliberately not restricted

`git add` / `git commit` stay `ask` rather than `deny`. Committing is a normal
part of the workflow and the user should be in the loop, but blocking it would
be paternalistic. `git push` is denied: this is a local-first portfolio project
with no release flow, and an agent that can push to a remote is a supply-chain
risk with no upside here.

---

## 6. Decisions taken

| Decision | Choice | Reason |
|----------|--------|--------|
| Model | not set | OpenCode default; pinning a model in a committed repo ages badly and fights the user's own setup |
| Command names | short, no prefix | `/generate-scenarios` reads better than `/sopir-generate-scenarios` in a single-project repo |
| Skill grouping | 5 separate skills | Progressive disclosure: only the relevant domain knowledge is loaded, and each can be edited independently |
| Dashboard opener | per-platform | The repo is developed on Windows but the stack runs in Linux containers; assuming one OS breaks the demo |
| Tests | `pytest -v --cov` | Verbosity for diagnosis, coverage for the `evaluation/` >90% target in `AGENTS.md` |
| Permission baseline | `ask` | Inverting the permissive default is the only way the security requirement is real |
| Config location | `opencode.json` at root | Schema-validated, editor-autocompleted, and the documented project path |

---

## 7. Naming change

Documentation was rebranded from OpenDriveLab to Sopir:

| File | Change |
|------|--------|
| `AGENTS.md` | title |
| `README.md` | title |
| `PLANNING.md` (now `docs/PLANNING_phase_1_MVP.md`) | title |
| `docs/architecture.md` | title |

Left as `OpenDriveLab` / `opendrivelab` on purpose, because these are runtime
identifiers defined by code that was not in scope:

`backend/config.py` (default `DATABASE_URL`), `.env.example`, `alembic.ini`,
`docker-compose.yml` (database name, volume, network, three container names),
`backend/main.py` (API title, health service name), `dashboard/app.py`
(`page_title`). README's env table still shows the real
`postgresql://postgres:postgres@db:5432/opendrivelab` so the doc matches what
the stack actually does.

Renaming the identifiers is a follow-up: it needs a database rename or a
rebuild, a volume migration, and a coordinated pass over compose, Alembic, the
API title and the dashboard page title.

---

## 8. Verify it works

1. Restart OpenCode in the repo root. Config and skills load on startup.
2. Type `/` and confirm all eight commands are listed with descriptions.
3. Ask: "what can you do here?" - the answer should reference scenario
   generation, workers, evaluation, failures and the dashboard without being
   prompted.
4. Ask: "how is TTC computed?" and confirm the caveat is stated rather than
   glossed.
5. Try to read `.env`. It should be refused.
6. Try to read a file in the home directory. It should be refused as an
   external directory.
7. Run `/run-tests` and confirm it reports the `psycopg` collection error as a
   missing local dependency rather than a code defect.

---

## 9. Maintenance

- **Adding a command:** drop a `.md` file in `.opencode/commands/`. Filename is
  the command name; frontmatter needs `description`; the body is the prompt.
  Use `$ARGUMENTS` for parameters and `@path/to/file` to inline a file.
- **Adding a skill:** `.opencode/skills/<name>/SKILL.md` with `name` (matching
  the directory) and `description` (1-1024 chars). The description is the only
  thing the agent sees before loading, so it must state *when* to use it, not
  just what it is.
- **Loaders or components change:** update the matching command so the endpoint
  path stays truthful. A stale command is worse than no command.
- **Schema drift:** re-validate against `https://opencode.ai/config.json`. The
  `permission` object rejects unknown keys.
