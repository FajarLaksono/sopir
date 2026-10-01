---
description: Run pytest verbosely with coverage, then report failures
agent: build
---

Run the test suite.

Scope: $ARGUMENTS (default: `tests/`, e.g. `tests/test_metrics.py`)

Steps:

1. Verbose run with coverage over the pure-Python modules that do not need a
   live database:
   `pytest tests/ -v --cov=evaluation --cov=scenario_gen --cov=backend --cov-report=term-missing`
2. If a scope was supplied, narrow the path but keep both flags:
   `pytest $ARGUMENTS -v --cov=evaluation --cov-report=term-missing`
3. For each failure, show the traceback, name the responsible module and line,
   and propose the smallest fix. Do not change behaviour outside the failing
   test.
4. Report the coverage table and whether `evaluation/` stays above the 90%
   target from `AGENTS.md`.

Notes:

- `tests/test_metrics.py` and `tests/test_failure_detector.py` are pure unit
  tests and must pass without Postgres or SUMO.
- `tests/test_evaluate_endpoint.py` needs `psycopg`. If collection fails with
  `ModuleNotFoundError: No module named 'psycopg'`, that is a missing local
  dependency, not a code defect. Report it as such and run the suite inside the
  container instead: `docker compose exec backend pytest tests/ -v`.
- Never skip the verbose flag; failures here are the fastest signal that a
  pure function in `evaluation/` drifted.
