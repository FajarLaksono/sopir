# pyright: reportAttributeAccessIssue=false, reportGeneralTypeIssues=false
# pylint: disable=no-member

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
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
    db: Session = Depends(get_db),
    user: Users = Depends(auth.get_current_user),
):
    return handlers.list_seasons(db)


@router.get("/{season_id}", response_model=schemas.SeasonRead)
def get_season(
    season_id: UUID,
    db: Session = Depends(get_db),
    user: Users = Depends(auth.get_current_user),
):
    season = handlers.get_season(db, season_id)
    if season is None:
        raise HTTPException(status_code=404, detail="Season not found")
    return season
