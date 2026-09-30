from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models import Scenario
from backend.schemas import ErrorResponse, ScenarioCreate, ScenarioListResponse, ScenarioResponse

router = APIRouter()


@router.post(
    "", response_model=ScenarioResponse, status_code=201, responses={400: {"model": ErrorResponse}}
)
def create_scenario(payload: ScenarioCreate, db: Session = Depends(get_db)):
    scenario = Scenario(type=payload.type, config=payload.config)
    db.add(scenario)
    db.commit()
    db.refresh(scenario)
    return scenario


@router.get("", response_model=ScenarioListResponse)
def list_scenarios(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    offset = (page - 1) * page_size
    total = db.scalar(select(func.count(Scenario.id)))
    items = (
        db.execute(
            select(Scenario).offset(offset).limit(page_size).order_by(Scenario.created_at.desc())
        )
        .scalars()
        .all()
    )
    return ScenarioListResponse(items=items, total=total, page=page, page_size=page_size)


@router.get(
    "/{scenario_id}", response_model=ScenarioResponse, responses={404: {"model": ErrorResponse}}
)
def get_scenario(scenario_id: UUID, db: Session = Depends(get_db)):
    scenario = db.get(Scenario, scenario_id)
    if not scenario:
        raise HTTPException(
            status_code=404, detail="Scenario not found", headers={"X-Error-Code": "NOT_FOUND"}
        )
    return scenario


@router.post(
    "/generate",
    response_model=list[ScenarioResponse],
    status_code=201,
    responses={400: {"model": ErrorResponse}},
)
def generate_scenarios(db: Session = Depends(get_db)):
    from scenario_gen.cli import generate_scenarios as gen_scenarios

    scenario_ids = gen_scenarios(db=db)
    created = []
    for sid in scenario_ids:
        scenario = db.get(Scenario, sid)
        if scenario:
            created.append(scenario)
    return created
