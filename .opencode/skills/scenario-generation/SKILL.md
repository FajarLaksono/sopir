---
name: scenario-generation
description: Generate and repair SUMO scenario artifacts from the version-controlled templates in scenario_gen/templates. Use when adding a new scenario type, editing a .net.xml or .rou.xml template, debugging ScenarioGenerationError, or when a run fails with "Config file not found".
compatibility: opencode
metadata:
  project: sopir
  layer: scenario_gen
---

## What I do

`scenario_gen/` materialises SUMO artifacts on the shared `scenario_data`
volume from templates that live in git. Generation is **idempotent**: one
`Scenario` row per scenario type, artifacts re-synced from templates on every
call so a stale or truncated file is repaired rather than reused.

Registered scenario types live in `SCENARIO_TEMPLATES` in `scenario_gen/cli.py`:

| type | purpose |
|------|---------|
| `intersection` | 4-way priority intersection, conflicting left and through traffic |
| `highway_merge` | 3-lane highway, low-priority on-ramp merging into an occupied lane |
| `pedestrian_crossing` | two-lane road, generated footway, marked pedestrian crossing |
| `lane_change` | two congested lanes against one free lane, forcing a two-stage overtake |

Each entry maps to three files: `net_file`, `route_file`, `config_file`. The
`.sumocfg` is **rendered**, not templated: `_render_sumocfg` writes
`begin=0`, `end=100`, `step-length=0.1`, `collision.action=warn`.

## Invariants I must not break

- Templates are the single source of truth. Never hand-write artifacts into
  `/data/scenarios`.
- The DB row is committed **before** artifacts are written, and rolled back if
  the write fails, so a scenario directory never outlives its row.
- Generation must stay safe to re-run. Never make it append duplicates.
- Never run `docker compose down -v` to fix a generation error.

## Validation the CLI already performs

`_validate_net_template` rejects a network that:
- is not well-formed XML, or
- declares no `<lane>` at all, or
- has any lane without a `shape` attribute (SUMO rejects shapeless lanes;
  regenerate with `netconvert` rather than hand-adding a shape).

`_validate_route_template` rejects a route that defines no `<vehicle>`,
`<person>` or `<flow>`.

## Adding a new scenario type

1. Add the three files to `scenario_gen/templates/`.
2. Verify the network in the SUMO GUI before committing.
3. Register the type in `SCENARIO_TEMPLATES`.
4. Run `docker compose exec backend python -m scenario_gen.cli generate`.
5. Confirm via `GET /api/v1/scenarios` and that the run actually completes.

## Common failures

| symptom | cause | action |
|---------|-------|--------|
| `missing template <file>` | template not committed or wrong name | fix the filename or the registry entry |
| `<file> is not valid XML` | hand-edited template broke well-formedness | repair the XML |
| `lanes without a shape attribute` | lane built without `netconvert` | regenerate the network |
| `<file> defines no vehicle, person or flow` | empty or wrong route file | add a vehicle/flow |
| run fails with `Config file not found` | artifact missing on the shared volume | re-run generation, check the volume mount |
