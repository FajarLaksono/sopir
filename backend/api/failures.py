from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models import Failure
from backend.schemas import ErrorResponse, FailureListResponse, FailureResponse

router = APIRouter()


@router.get("", response_model=FailureListResponse)
def list_failures(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    severity: str | None = Query(None),
    run_id: UUID | None = Query(None),
    db: Session = Depends(get_db),
):
    offset = (page - 1) * page_size
    query = select(Failure)
    if severity:
        query = query.where(Failure.severity == severity)
    if run_id:
        query = query.where(Failure.run_id == run_id)
    total = db.scalar(
        select(func.count(Failure.id)).where(
            query.whereclause if query.whereclause is not None else True
        )
    )
    items = (
        db.execute(query.offset(offset).limit(page_size).order_by(Failure.created_at.desc()))
        .scalars()
        .all()
    )
    return FailureListResponse(items=items, total=total, page=page, page_size=page_size)


@router.get(
    "/{failure_id}", response_model=FailureResponse, responses={404: {"model": ErrorResponse}}
)
def get_failure(failure_id: UUID, db: Session = Depends(get_db)):
    failure = db.get(Failure, failure_id)
    if not failure:
        raise HTTPException(
            status_code=404, detail="Failure not found", headers={"X-Error-Code": "NOT_FOUND"}
        )
    return failure
