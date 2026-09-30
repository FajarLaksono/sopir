import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import (
    String, Text, Integer, Float, DateTime, ForeignKey, Index, BigInteger
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from backend.database import Base


class Scenario(Base):
    __tablename__ = "scenarios"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    type: Mapped[str] = mapped_column(String(50), nullable=False)
    config: Mapped[dict] = mapped_column(JSONB, nullable=False)
    net_file_path: Mapped[Optional[str]] = mapped_column(String(500))
    route_file_path: Mapped[Optional[str]] = mapped_column(String(500))
    config_file_path: Mapped[Optional[str]] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    runs: Mapped[list["SimulationRun"]] = relationship(back_populates="scenario", cascade="all, delete-orphan")

    __table_args__ = (Index("ix_scenarios_type", "type"),)


class SimulationRun(Base):
    __tablename__ = "simulation_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    scenario_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("scenarios.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    worker_id: Mapped[Optional[str]] = mapped_column(String(100))
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    scenario: Mapped["Scenario"] = relationship(back_populates="runs")
    telemetry: Mapped[list["Telemetry"]] = relationship(back_populates="run", cascade="all, delete-orphan")
    metrics: Mapped[Optional["Metrics"]] = relationship(back_populates="run", uselist=False, cascade="all, delete-orphan")
    failures: Mapped[list["Failure"]] = relationship(back_populates="run", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_runs_scenario", "scenario_id"),
        Index("ix_runs_status", "status"),
        Index("ix_runs_worker", "worker_id"),
    )


class Telemetry(Base):
    __tablename__ = "telemetry"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("simulation_runs.id", ondelete="CASCADE"), nullable=False)
    step: Mapped[int] = mapped_column(Integer, nullable=False)
    vehicle_id: Mapped[str] = mapped_column(String(100), nullable=False)
    x: Mapped[float] = mapped_column(Float, nullable=False)
    y: Mapped[float] = mapped_column(Float, nullable=False)
    speed: Mapped[float] = mapped_column(Float, nullable=False)
    angle: Mapped[float] = mapped_column(Float, nullable=False)
    lane_id: Mapped[str] = mapped_column(String(100), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)

    run: Mapped["SimulationRun"] = relationship(back_populates="telemetry")

    __table_args__ = (
        Index("ix_telemetry_run_step", "run_id", "step"),
        Index("ix_telemetry_run_vehicle", "run_id", "vehicle_id"),
    )


class Metrics(Base):
    __tablename__ = "metrics"

    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("simulation_runs.id", ondelete="CASCADE"), primary_key=True)
    collision_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    min_ttc: Mapped[Optional[float]] = mapped_column(Float)
    avg_speed: Mapped[float] = mapped_column(Float, nullable=False)
    speed_violations: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lane_deviations: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)

    run: Mapped["SimulationRun"] = relationship(back_populates="metrics")


class Failure(Base):
    __tablename__ = "failures"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("simulation_runs.id", ondelete="CASCADE"), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    rule: Mapped[str] = mapped_column(String(100), nullable=False)
    details: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)

    run: Mapped["SimulationRun"] = relationship(back_populates="failures")

    __table_args__ = (
        Index("ix_failures_run", "run_id"),
        Index("ix_failures_severity", "severity"),
    )