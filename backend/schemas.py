from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ScenarioBase(BaseModel):
    type: str = Field(..., min_length=1, max_length=50)
    config: dict


class ScenarioCreate(ScenarioBase):
    pass


class ScenarioResponse(ScenarioBase):
    id: UUID
    net_file_path: Optional[str] = None
    route_file_path: Optional[str] = None
    config_file_path: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ScenarioListResponse(BaseModel):
    items: list[ScenarioResponse]
    total: int
    page: int
    page_size: int


class SimulationRunBase(BaseModel):
    scenario_id: UUID


class SimulationRunCreate(SimulationRunBase):
    pass


class SimulationRunResponse(SimulationRunBase):
    id: UUID
    status: str
    worker_id: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    error_message: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SimulationRunListResponse(BaseModel):
    items: list[SimulationRunResponse]
    total: int
    page: int
    page_size: int


class TelemetryRecord(BaseModel):
    step: int
    vehicle_id: str
    x: float
    y: float
    speed: float
    angle: float
    lane_id: str


class TelemetryBatch(BaseModel):
    run_id: UUID
    records: list[TelemetryRecord]


class MetricsResponse(BaseModel):
    run_id: UUID
    collision_count: int
    min_ttc: Optional[float] = None
    avg_speed: float
    speed_violations: int
    lane_deviations: int
    ttc_per_step: Optional[list[Optional[float]]] = None
    computed_at: datetime

    model_config = ConfigDict(from_attributes=True)


class FailureResponse(BaseModel):
    id: UUID
    run_id: UUID
    severity: str
    rule: str
    details: dict
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class FailureListResponse(BaseModel):
    items: list[FailureResponse]
    total: int
    page: int
    page_size: int


class EvaluateRequest(BaseModel):
    pass


class ErrorResponse(BaseModel):
    detail: str
    code: str
