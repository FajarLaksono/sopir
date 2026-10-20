import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SeasonCreate(BaseModel):
    os: str = Field(max_length=10)
    sdk_version: str = Field(max_length=10)
    simulator_version: str = Field(max_length=10)
    track_id: str = Field(max_length=100)
    car_config: dict[str, Any] | None = None

class SeasonRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID | None
    os: str
    sdk_version: str
    simulator_version: str
    track_id: str
    car_config: dict[str, Any] | None
    status: str
    ended_at: datetime | None
    created_at: datetime
    updated_at: datetime

class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=6, max_length=72)

class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    username: str
    created_at: datetime

class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
