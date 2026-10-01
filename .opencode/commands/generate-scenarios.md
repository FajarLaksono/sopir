---
description: Generate the four SUMO scenarios from templates via the CLI
agent: build
---

Regenerate the SUMO scenarios for this project.

Target scenario type: $ARGUMENTS (default: all four)

Steps:

1. Confirm the Postgres service is reachable:
   `docker compose ps db`
2. Generate scenarios using the CLI (the canonical, template-driven path):
   `docker compose exec backend python -m scenario_gen.cli generate`
   If the user asked for a specific type, note that the CLI regenerates all
   registered types and then report only the requested subset.
3. Verify the result with:
   `curl -s http://localhost:8000/api/v1/scenarios | python -m json.tool`
   Expect 4 scenarios: `intersection`, `highway_merge`, `pedestrian_crossing`,
   `lane_change`.
4. Report the scenario IDs and the artifact directories under
   `scenario_data:/data/scenarios/<scenario_id>/`.

Constraints:

- Never hand-write `.net.xml`, `.rou.xml` or `.sumocfg` into `/data/scenarios`.
  Templates in `scenario_gen/templates/` are the single source of truth.
- Do not delete the Postgres volume to "fix" generation. Generation is
  idempotent; re-run the command instead.
- If generation fails with `ScenarioGenerationError`, report the template
  validation message verbatim (missing template, malformed XML, shapeless lane,
  or a route with no vehicle/person/flow) before attempting any fix.
