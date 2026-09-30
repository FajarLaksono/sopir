from uuid import UUID
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session
from sqlalchemy import select, func
from backend.database import get_db
from backend.models import SimulationRun, Telemetry
from backend.schemas import (
    SimulationRunCreate, SimulationRunResponse, SimulationRunListResponse,
    TelemetryBatch, TelemetryRecord, ErrorResponse
)

router = APIRouter()


@router.post("", response_model=SimulationRunResponse, status_code=201, responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}})
def create_run(payload: SimulationRunCreate, db: Session = Depends(get_db)):
    from backend.models import Scenario
    scenario = db.get(Scenario, payload.scenario_id)
    if not scenario:
        raise HTTPException(status_code=404, detail="Scenario not found", headers={"X-Error-Code": "NOT_FOUND"})
    run = SimulationRun(scenario_id=payload.scenario_id)
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


@router.get("", response_model=SimulationRunListResponse)
def list_runs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: str | None = Query(None),
    db: Session = Depends(get_db),
):
    offset = (page - 1) * page_size
    query = select(SimulationRun)
    if status:
        query = query.where(SimulationRun.status == status)
    total = db.scalar(select(func.count(SimulationRun.id)).where(query.whereclause if query.whereclause is not None else True))
    items = db.execute(
        query.offset(offset).limit(page_size).order_by(SimulationRun.created_at.desc())
    ).scalars().all()
    return SimulationRunListResponse(items=items, total=total, page=page, page_size=page_size)


@router.get("/{run_id}", response_model=SimulationRunResponse, responses={404: {"model": ErrorResponse}})
def get_run(run_id: UUID, db: Session = Depends(get_db)):
    run = db.get(SimulationRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found", headers={"X-Error-Code": "NOT_FOUND"})
    return run


@router.post("/{run_id}/telemetry", response_model=dict, responses={404: {"model": ErrorResponse}})
def ingest_telemetry(run_id: UUID, payload: TelemetryBatch, db: Session = Depends(get_db)):
    run = db.get(SimulationRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found", headers={"X-Error-Code": "NOT_FOUND"})

    records = [
        Telemetry(
            run_id=run_id,
            step=r.step,
            vehicle_id=r.vehicle_id,
            x=r.x,
            y=r.y,
            speed=r.speed,
            angle=r.angle,
            lane_id=r.lane_id,
        )
        for r in payload.records
    ]
    db.bulk_save_objects(records)
    db.commit()
    return {"ingested": len(records)}


@router.patch("/{run_id}/status", response_model=SimulationRunResponse, responses={404: {"model": ErrorResponse}})
def update_run_status(run_id: UUID, status: str, worker_id: str | None = None, db: Session = Depends(get_db)):
    run = db.get(SimulationRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found", headers={"X-Error-Code": "NOT_FOUND"})
    run.status = status
    if worker_id:
        run.worker_id = worker_id
    if status == "running" and not run.started_at:
        run.started_at = datetime.utcnow()
    if status in ("completed", "failed") and not run.completed_at:
        run.completed_at = datetime.utcnow()
    db.commit()
    db.refresh(run)
    return run