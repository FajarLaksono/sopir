import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base


class Seasons(Base):
    __tablename__ = "seasons"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    os: Mapped[str] = mapped_column(String(10), nullable=False)
    sdk_version: Mapped[str] = mapped_column(String(10), nullable=False)
    simulator_version: Mapped[str] = mapped_column(String(10), nullable=False)
    track_id: Mapped[str] = mapped_column(String(100), nullable=False)
    car_config: Mapped[dict] = mapped_column(JSONB, nullable=True)
    driver: Mapped[str] = mapped_column(String(10), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        server_default=text("now()"), onupdate=text("now()"),
    )

    runs: Mapped[list["Runs"]] = relationship(
        back_populates="season",
        cascade="all, delete-orphan",
        order_by="Runs.started_at.desc()",
    )

class Users(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    username: Mapped[str] = mapped_column(String(50), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )

class Runs(Base):
    """One continuous drive: start -> crash or Ctrl+C."""

    __tablename__ = "runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True) # No Default, Must use producer genetared UUID.
    season_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("seasons.id", ondelete="CASCADE"),
        nullable=False,
    )
    driver: Mapped[str] = mapped_column(String(10), nullable=False)   # random / rl
    status: Mapped[str] = mapped_column(String(10), nullable=False)   # running / completed / crashed
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    summary: Mapped[dict | None] = mapped_column(JSONB, nullable=True)  # KPI report card (Phase 4)

    season: Mapped["Seasons"] = relationship(back_populates="runs")


class Telemetry(Base):
    """One tick of driving data. PK (run_id, tick) = the dedupe key."""

    __tablename__ = "telemetry"

    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE"),
        primary_key=True,
    )
    tick: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[float] = mapped_column(Float, nullable=False)
    steering: Mapped[float] = mapped_column(Float, nullable=False)
    throttle: Mapped[float] = mapped_column(Float, nullable=False)
    speed: Mapped[float] = mapped_column(Float, nullable=False)
    forward_vel: Mapped[float] = mapped_column(Float, nullable=False)
    cte: Mapped[float] = mapped_column(Float, nullable=False)
    pos_x: Mapped[float] = mapped_column(Float, nullable=False)
    pos_y: Mapped[float] = mapped_column(Float, nullable=False)
    pos_z: Mapped[float] = mapped_column(Float, nullable=False)
    vel_x: Mapped[float] = mapped_column(Float, nullable=False)
    vel_y: Mapped[float] = mapped_column(Float, nullable=False)
    vel_z: Mapped[float] = mapped_column(Float, nullable=False)
    accel_x: Mapped[float] = mapped_column(Float, nullable=False)
    accel_y: Mapped[float] = mapped_column(Float, nullable=False)
    accel_z: Mapped[float] = mapped_column(Float, nullable=False)
    gyro_x: Mapped[float] = mapped_column(Float, nullable=False)
    gyro_y: Mapped[float] = mapped_column(Float, nullable=False)
    gyro_z: Mapped[float] = mapped_column(Float, nullable=False)
    hit: Mapped[str] = mapped_column(String(20), nullable=False)
    reward: Mapped[float] = mapped_column(Float, nullable=False)
    lap_count: Mapped[int] = mapped_column(Integer, nullable=False)
    last_lap_time: Mapped[float] = mapped_column(Float, nullable=False)
    terminated: Mapped[bool] = mapped_column(Boolean, nullable=False)
