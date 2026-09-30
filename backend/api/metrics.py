from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models import Failure, Metrics, SimulationRun, Telemetry
from backend.schemas import ErrorResponse, EvaluateRequest, MetricsResponse

router = APIRouter()


@router.post(
    "/runs/{run_id}/evaluate",
    response_model=MetricsResponse,
    responses={404: {"model": ErrorResponse}},
)
def evaluate_run(
    run_id: UUID, payload: EvaluateRequest | None = None, db: Session = Depends(get_db)
):
    run = db.get(SimulationRun, run_id)
    if not run:
        raise HTTPException(
            status_code=404, detail="Run not found", headers={"X-Error-Code": "NOT_FOUND"}
        )

    from evaluation.failure_detector import detect_failures
    from evaluation.metrics import compute_metrics

    telemetry = (
        db.execute(
            select(Telemetry)
            .where(Telemetry.run_id == run_id)
            .order_by(Telemetry.step, Telemetry.vehicle_id)
        )
        .scalars()
        .all()
    )

    if not telemetry:
        raise HTTPException(
            status_code=400,
            detail="No telemetry data for run",
            headers={"X-Error-Code": "NO_TELEMETRY"},
        )

    telemetry_dicts = [
        {
            "step": t.step,
            "vehicle_id": t.vehicle_id,
            "x": t.x,
            "y": t.y,
            "speed": t.speed,
            "angle": t.angle,
            "lane_id": t.lane_id,
        }
        for t in telemetry
    ]

    metrics_dict = compute_metrics(telemetry_dicts)
    failures = detect_failures(metrics_dict)

    metrics_obj = Metrics(
        run_id=run_id,
        collision_count=metrics_dict.get("collision_count", 0),
        min_ttc=metrics_dict.get("min_ttc"),
        avg_speed=metrics_dict.get("avg_speed", 0.0),
        speed_violations=metrics_dict.get("speed_violations", 0),
        lane_deviations=metrics_dict.get("lane_deviations", 0),
    )
    metrics_obj = db.merge(metrics_obj)

    db.query(Failure).filter(Failure.run_id == run_id).delete(synchronize_session=False)
    for f in failures:
        failure = Failure(
            run_id=run_id,
            severity=f["severity"],
            rule=f["rule"],
            details=f["details"],
        )
        db.add(failure)

    # Evaluation is a read-only analysis of stored telemetry: it must not
    # rewrite the run's lifecycle status, which the worker owns. Overwriting it
    # here would silently relabel a failed run as completed and hide the very
    # signal this platform exists to surface.
    db.commit()
    db.refresh(metrics_obj)
    return metrics_obj


@router.get(
    "/runs/{run_id}", response_model=MetricsResponse, responses={404: {"model": ErrorResponse}}
)
def get_metrics(run_id: UUID, db: Session = Depends(get_db)):
    metrics = db.get(Metrics, run_id)
    if not metrics:
        raise HTTPException(
            status_code=404, detail="Metrics not found", headers={"X-Error-Code": "NOT_FOUND"}
        )
    return metrics
