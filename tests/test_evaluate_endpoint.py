"""Regression tests for POST /metrics/runs/{run_id}/evaluate.

The evaluation endpoint used to have two defects that are invisible until
someone actually calls it, so both are pinned here:

1. It declared a required ``EvaluateRequest`` body even though the model is
   empty and the value was never read. Any body-less POST -- the obvious thing
   to send, and what both README.md and AGENTS.md documented -- was rejected
   with HTTP 422. The tests below call the handler with no payload at all, which
   fails at call time if the body ever becomes mandatory again.

2. It assigned ``run.status = "completed"``, so evaluating a run that had failed
   silently relabelled it as completed. On a platform whose purpose is failure
   detection that erases the signal the dashboard is supposed to show.

A fake session keeps these tests free of database and SUMO dependencies, in the
spirit of the rest of the suite.
"""

from __future__ import annotations

import inspect
from typing import Any
from uuid import UUID, uuid4

from backend.api.metrics import evaluate_run

RUN_ID = uuid4()

TELEMETRY = [
    {
        "step": 0,
        "vehicle_id": "ego",
        "x": 0.0,
        "y": 0.0,
        "speed": 10.0,
        "angle": 90.0,
        "lane_id": "E0_0",
    },
    {
        "step": 1,
        "vehicle_id": "ego",
        "x": 1.0,
        "y": 0.0,
        "speed": 10.0,
        "angle": 90.0,
        "lane_id": "E0_1",
    },
]


class _FakeRun:
    def __init__(self, status: str = "failed") -> None:
        self.id = RUN_ID
        self.status = status


class _FakeTelemetry:
    def __init__(self, row: dict[str, Any]) -> None:
        self.step = row["step"]
        self.vehicle_id = row["vehicle_id"]
        self.x = row["x"]
        self.y = row["y"]
        self.speed = row["speed"]
        self.angle = row["angle"]
        self.lane_id = row["lane_id"]


class _FakeQuery:
    def filter(self, *args: Any, **kwargs: Any) -> _FakeQuery:
        return self

    def delete(self, *args: Any, **kwargs: Any) -> int:
        return 0


class _FakeScalars:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows


class _FakeResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self) -> _FakeScalars:
        return _FakeScalars(self._rows)


class _FakeSession:
    """Minimal stand-in for the SQLAlchemy session the handler touches."""

    def __init__(self, run: _FakeRun | None, telemetry: list[dict[str, Any]]) -> None:
        self._run = run
        self._telemetry = [_FakeTelemetry(t) for t in telemetry]
        self.added: list[Any] = []
        self.committed = 0
        self.deleted_queries = 0

    def get(self, model: Any, pk: UUID) -> Any:
        return self._run

    def execute(self, *args: Any, **kwargs: Any) -> _FakeResult:
        return _FakeResult(self._telemetry)

    def merge(self, obj: Any) -> Any:
        return obj

    def query(self, *args: Any, **kwargs: Any) -> _FakeQuery:
        return _FakeQuery()

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    def commit(self) -> None:
        self.committed += 1

    def refresh(self, obj: Any) -> None:
        return None


def test_payload_is_optional() -> None:
    """The body must stay optional; EvaluateRequest is empty and unused."""
    params = inspect.signature(evaluate_run).parameters
    assert "payload" in params
    assert params["payload"].default is not inspect.Parameter.empty, (
        "evaluate_run still requires a request body, so a body-less POST returns 422"
    )


def test_evaluating_a_failed_run_does_not_mark_it_completed() -> None:
    """Evaluation analyses stored telemetry; it must not touch run lifecycle."""
    run = _FakeRun(status="failed")
    db = _FakeSession(run=run, telemetry=TELEMETRY)

    metrics = evaluate_run(RUN_ID, db=db)

    assert run.status == "failed", (
        "evaluate_run mutated run.status from 'failed' to "
        f"{run.status!r}; that hides failed runs from the dashboard"
    )
    assert metrics.run_id == RUN_ID
    assert metrics.collision_count == 0
    assert db.committed == 1


def test_evaluating_a_completed_run_leaves_status_untouched() -> None:
    run = _FakeRun(status="completed")
    db = _FakeSession(run=run, telemetry=TELEMETRY)

    metrics = evaluate_run(RUN_ID, db=db)

    assert run.status == "completed"
    assert metrics.avg_speed > 0


def test_failures_are_replaced_not_duplicated() -> None:
    """Re-evaluating clears prior failures so counts stay stable."""
    db = _FakeSession(run=_FakeRun(), telemetry=TELEMETRY)
    evaluate_run(RUN_ID, db=db)
    first = len(db.added)
    db.added.clear()
    evaluate_run(RUN_ID, db=db)
    assert len(db.added) == first


def test_missing_run_raises_404() -> None:
    from fastapi import HTTPException

    db = _FakeSession(run=None, telemetry=TELEMETRY)
    try:
        evaluate_run(RUN_ID, db=db)
    except HTTPException as exc:
        assert exc.status_code == 404
    else:
        raise AssertionError("expected HTTPException for an unknown run_id")


def test_run_without_telemetry_raises_400() -> None:
    from fastapi import HTTPException

    db = _FakeSession(run=_FakeRun(), telemetry=[])
    try:
        evaluate_run(RUN_ID, db=db)
    except HTTPException as exc:
        assert exc.status_code == 400
    else:
        raise AssertionError("expected HTTPException when no telemetry is stored")
