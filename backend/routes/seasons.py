# pyright: reportAttributeAccessIssue=false, reportGeneralTypeIssues=false
# pylint: disable=no-member

import os
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi import Query
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from backend import schemas
from backend import handlers
from backend.models import Users
from backend.handlers import seasons as handlers
from backend.handlers import auth as auth

from backend.database import get_db

router = APIRouter(prefix="/seasons", tags=["seasons"])


@router.post("", response_model=schemas.SeasonRead, status_code=201)
def create_season(
    data: schemas.SeasonCreate,
    db: Session = Depends(get_db),
    user: Users = Depends(auth.get_current_user),
):
    return handlers.create_season(db, data)


@router.get("", response_model=list[schemas.SeasonRead])
def list_seasons(
    driver: str | None = Query(default=None),
    db: Session = Depends(get_db),
    user: Users = Depends(auth.get_current_user),
):
    return handlers.list_seasons(db, driver=driver)


@router.get("/{season_id}", response_model=schemas.SeasonDetail)
def get_season(
    season_id: UUID,
    db: Session = Depends(get_db),
    user: Users = Depends(auth.get_current_user),
):
    season = handlers.get_season_detail(db, season_id)
    if season is None:
        raise HTTPException(status_code=404, detail="Season not found")
    return season

@router.get("/{season_id}/runs", response_model=list[schemas.RunRead])
def list_runs(
    season_id: UUID,
    db: Session = Depends(get_db),
    user: Users = Depends(auth.get_current_user),
):
    season = handlers.get_season_detail(db, season_id)
    if season is None:
        raise HTTPException(status_code=404, detail="Season not found")
    return season.runs          # ordered newest-first by the relationship

@router.get(
    "/{season_id}/runs/{run_id}/telemetry",
    response_model=list[schemas.TelemetryPoint],
)
def run_telemetry(
    season_id: UUID,
    run_id: UUID,
    max_points: int = Query(default=1000, ge=10, le=10000),
    db: Session = Depends(get_db),
    user: Users = Depends(auth.get_current_user),
):
    run = handlers.get_run(db, season_id, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return handlers.get_telemetry(db, run_id, max_points)

@router.get("/{season_id}/runs/{run_id}/frame")
def run_frame(
    season_id: UUID,
    run_id: UUID,
    at_tick: int = Query(default=0, ge=0),
    user: Users = Depends(auth.get_current_user),
):
    path = _find_frame(season_id, run_id, at_tick)
    if path is None:
        raise HTTPException(status_code=404, detail="No frame saved for this run")
    return FileResponse(path, media_type="image/jpeg")

def _find_frame(season_id: UUID, run_id: UUID, at_tick: int) -> str | None:
    """Nearest saved frame with tick <= at_tick (filenames: {tick:06d}.jpg)."""
    folder = os.path.join(
        "data", "seasons", str(season_id), "runs", str(run_id), "frames"
    )
    if not os.path.isdir(folder):
        return None
    best: int | None = None
    for name in os.listdir(folder):
        if not name.endswith(".jpg"):
            continue
        try:
            tick = int(name[:-4])
        except ValueError:
            continue
        if tick <= at_tick and (best is None or tick > best):
            best = tick
    return os.path.join(folder, f"{best:06d}.jpg") if best is not None else None
