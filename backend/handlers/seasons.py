from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend import models
from backend import schemas


def create_season(db: Session, season: schemas.SeasonCreate) -> models.Seasons:
    db_season = models.Seasons(
        os=season.os,
        sdk_version=season.sdk_version,
        simulator_version=season.simulator_version,
        track_id=season.track_id,
        car_config=season.car_config,
        status="running",
    )
    db.add(db_season)
    db.commit()
    db.refresh(db_season)
    return db_season

def complete_season_status(db: Session, season_id: UUID) -> models.Seasons | None:
    db_season = get_season(db, season_id)
    if db_season is None:
        return None
    
    db_season.status = "completed"
    db.commit()
    db.refresh(db_season)
    return db_season

def crash_season_status(db: Session, season_id: UUID) -> models.Seasons | None:
    db_season = get_season(db, season_id)
    if db_season is None:
        return None
    
    db_season.status = "crashed"
    db.commit()
    db.refresh(db_season)
    return db_season

def list_seasons(db: Session) -> list[models.Seasons]:
    return db.execute(select(models.Seasons)).scalars().all()

def get_season(db: Session, season_id: UUID) -> models.Seasons | None:
    return db.execute(select(models.Seasons).where(models.Seasons.id == season_id)).scalar_one_or_none()

