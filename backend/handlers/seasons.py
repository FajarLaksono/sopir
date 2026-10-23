import math

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.sql.functions import count
from sqlalchemy.orm import Session, selectinload

from backend import models
from backend import schemas


def create_season(db: Session, season: schemas.SeasonCreate) -> models.Seasons:
    db_season = models.Seasons(
        os=season.os,
        sdk_version=season.sdk_version,
        simulator_version=season.simulator_version,
        track_id=season.track_id,
        car_config=season.car_config,
        driver=season.driver,
    )
    db.add(db_season)
    db.commit()
    db.refresh(db_season)
    return db_season


def list_seasons(db: Session, driver: str | None = None) -> list[models.Seasons]:
    stmt = select(models.Seasons)
    if driver:
        # "seasons that have at least one run with this driver" (spec §8)
        stmt = stmt.where(
            models.Seasons.runs.any(models.Runs.driver == driver)
        )
    return db.execute(stmt).scalars().all()

def get_season(db: Session, season_id: UUID) -> models.Seasons | None:
    return db.execute(
        select(models.Seasons).where(models.Seasons.id == season_id)
    ).scalar_one_or_none()

def get_season_detail(db: Session, season_id: UUID) -> models.Seasons | None:
    """Load the season AND its runs in one go.

    `selectinload` issues a second SELECT ... WHERE season_id IN (...) rather
    than one query per run (the classic N+1). Essential because the API ships
    the runs list to the client — lazy loading would fire after the session
    closed, raising DetachedInstanceError.
    """
    return db.execute(
        select(models.Seasons)
        .where(models.Seasons.id == season_id)
        .options(selectinload(models.Seasons.runs))
    ).scalar_one_or_none()

def get_run(db: Session, season_id: UUID, run_id: UUID) -> models.Runs | None:
    return db.execute(
        select(models.Runs).where(models.Runs.id == run_id, models.Runs.season_id == season_id)
    ).scalar_one_or_none()

def get_telemetry(
    db: Session, run_id: UUID, max_points: int = 1000
) -> list[models.Telemetry]:
    total = db.execute(
        select(count()).where(models.Telemetry.run_id == run_id)
    ).scalar_one()
    if total == 0:
        return []
    stride = max(1, math.ceil(total / max_points))
    return db.execute(
        select(models.Telemetry)
        .where(models.Telemetry.run_id == run_id, func.mod(models.Telemetry.tick, stride) == 0)
        .order_by(models.Telemetry.tick)
    ).scalars().all()
