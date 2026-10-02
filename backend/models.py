import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    BigInteger,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
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
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    runs: Mapped[list["SimulationRun"]] = relationship(
        back_populates="scenario", cascade="all, delete-orphan"
    )

    __table_args__ = (Index("ix_scenarios_type", "type"),)


class SimulationRun(Base):
    __tablename__ = "simulation_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    scenario_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scenarios.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    worker_id: Mapped[Optional[str]] = mapped_column(String(100))
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    scenario: Mapped["Scenario"] = relationship(back_populates="runs")
    telemetry: Mapped[list["Telemetry"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    metrics: Mapped[Optional["Metrics"]] = relationship(
        back_populates="run", uselist=False, cascade="all, delete-orphan"
    )
    failures: Mapped[list["Failure"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_runs_scenario", "scenario_id"),
        Index("ix_runs_status", "status"),
        Index("ix_runs_worker", "worker_id"),
    )


class Telemetry(Base):
    __tablename__ = "telemetry"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("simulation_runs.id", ondelete="CASCADE"), nullable=False
    )
    step: Mapped[int] = mapped_column(Integer, nullable=False)
    vehicle_id: Mapped[str] = mapped_column(String(100), nullable=False)
    x: Mapped[float] = mapped_column(Float, nullable=False)
    y: Mapped[float] = mapped_column(Float, nullable=False)
    speed: Mapped[float] = mapped_column(Float, nullable=False)
    angle: Mapped[float] = mapped_column(Float, nullable=False)
    lane_id: Mapped[str] = mapped_column(String(100), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )

    run: Mapped["SimulationRun"] = relationship(back_populates="telemetry")

    __table_args__ = (
        Index("ix_telemetry_run_step", "run_id", "step"),
        Index("ix_telemetry_run_vehicle", "run_id", "vehicle_id"),
    )


class Metrics(Base):
    __tablename__ = "metrics"

    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("simulation_runs.id", ondelete="CASCADE"), primary_key=True
    )
    collision_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    min_ttc: Mapped[Optional[float]] = mapped_column(Float)
    avg_speed: Mapped[float] = mapped_column(Float, nullable=False)
    speed_violations: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lane_deviations: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ttc_per_step: Mapped[Optional[list[float]]] = mapped_column(JSONB)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )

    run: Mapped["SimulationRun"] = relationship(back_populates="metrics")


class Failure(Base):
    __tablename__ = "failures"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("simulation_runs.id", ondelete="CASCADE"), nullable=False
    )
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    rule: Mapped[str] = mapped_column(String(100), nullable=False)
    details: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )

    run: Mapped["SimulationRun"] = relationship(back_populates="failures")

    __table_args__ = (
        Index("ix_failures_run", "run_id"),
        Index("ix_failures_severity", "severity"),
    )


class StreamMetrics(Base):
    """Silver layer: metrics computed from the Kafka stream.

    Deliberately separate from ``Metrics``. The batch path evaluates an
    on-demand read of the ``telemetry`` table; this table is written by a
    consumer group as windows close. Keeping them apart means the two paths can
    be diffed against each other, which is the whole point of the parity test,
    and it means a replay cannot silently overwrite a batch result.

    ``stream_key`` identifies the aggregation unit: a ``run_id`` for simulation
    telemetry, or ``<vehicle_id>:<window_start>`` for the unbounded vehicle
    streams.
    """

    __tablename__ = "stream_metrics"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    stream_key: Mapped[str] = mapped_column(String(200), nullable=False)
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    run_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True))
    vehicle_id: Mapped[Optional[str]] = mapped_column(String(100))
    window_start: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    window_end: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    record_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    collision_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    min_ttc: Mapped[Optional[float]] = mapped_column(Float)
    avg_speed: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    speed_violations: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lane_deviations: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ttc_per_step: Mapped[Optional[list]] = mapped_column(JSONB)
    # Offsets of the last record folded into this row. Lets a replay prove it
    # covered the same records instead of double counting.
    last_offsets: Mapped[Optional[dict]] = mapped_column(JSONB)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )

    __table_args__ = (
        UniqueConstraint("stream_key", name="uq_stream_metrics_key"),
        Index("ix_stream_metrics_source", "source"),
        Index("ix_stream_metrics_run", "run_id"),
        Index("ix_stream_metrics_vehicle", "vehicle_id"),
    )


class StreamFailure(Base):
    """Silver layer: failures detected from the Kafka stream."""

    __tablename__ = "stream_failures"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    stream_key: Mapped[str] = mapped_column(String(200), nullable=False)
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    run_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True))
    vehicle_id: Mapped[Optional[str]] = mapped_column(String(100))
    window_start: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    rule: Mapped[str] = mapped_column(String(100), nullable=False)
    details: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )

    __table_args__ = (
        # One row per (window, rule): re-running the same window replaces the
        # prior verdicts instead of stacking duplicates.
        UniqueConstraint("stream_key", "rule", name="uq_stream_failures_key_rule"),
        Index("ix_stream_failures_run", "run_id"),
        Index("ix_stream_failures_severity", "severity"),
    )


class StreamEvent(Base):
    """Silver layer: raw vehicle-log records, retained for traceability.

    The raw lake in the object store is the system of record. This table exists
    so a specific event can be joined against telemetry and metrics in SQL
    without leaving Postgres, which is what makes failure investigation fast.
    """

    __tablename__ = "stream_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(100), nullable=False)
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    topic: Mapped[str] = mapped_column(String(100), nullable=False)
    vehicle_id: Mapped[str] = mapped_column(String(100), nullable=False)
    run_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True))
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    kafka_partition: Mapped[int] = mapped_column(Integer, nullable=False)
    kafka_offset: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )

    __table_args__ = (
        # The idempotency guard. event_id is unique per record, so a replayed
        # window collides on insert instead of duplicating the row.
        UniqueConstraint("event_id", name="uq_stream_events_event_id"),
        Index("ix_stream_events_vehicle", "vehicle_id"),
        Index("ix_stream_events_run", "run_id"),
        Index("ix_stream_events_source_time", "source", "captured_at"),
        Index("ix_stream_events_topic_offset", "topic", "kafka_offset"),
    )
