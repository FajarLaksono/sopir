---
description: Run ruff check, ruff format and mypy, then fix or report findings
agent: build
---

Run the project's lint and type gate.

Steps:

1. Lint:
   `ruff check .`
2. Autofix safe findings:
   `ruff check . --fix`
3. Format:
   `ruff format .`
4. Types (strict mode per `AGENTS.md`):
   `mypy backend scenario_gen simulation evaluation`
5. Re-run `ruff check .` and `mypy ...` to confirm both are clean.
6. Report every file that was reformatted, and every remaining error verbatim.

Conventions to enforce (from `AGENTS.md`):

- Line length 100, Ruff as the single formatter (Black/isort/flake8 are not
  used directly).
- Type hints on all public functions.
- Imports grouped stdlib, third-party, local, and sorted.
- No new comments unless explicitly requested.

If `ruff format` rewrites a file you did not intend to touch, revert that file
with `git checkout -- <path>` and re-run `ruff check .` to see the real issue.
Do not reformat unrelated files just to make the gate green.
