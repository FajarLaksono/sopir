"""Telemetry sink interface for switching between Postgres and Kafka."""

from __future__ import annotations

import logging
import os
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from sqlalchemy.orm import Session

from backend.models import Telemetry
from streaming.producer import AvroProducer, create_producer

logger = logging.getLogger(__name__)


@dataclass
class TelemetryRecord:
    """Immutable telemetry record for sink consumption."""

    run_id: uuid.UUID
    step: int
    vehicle_id: str
    x: float
    y: float
    speed: float
    angle: float
    lane_id: str
    captured_at_ms: Optional[int] = None


class TelemetrySink(ABC):
    """Abstract sink for telemetry records."""

    @abstractmethod
    def write_batch(self, records: list[TelemetryRecord]) -> None:
        """Write a batch of telemetry records."""
        ...

    @abstractmethod
    def flush(self) -> None:
        """Flush any buffered records."""
        ...

    @abstractmethod
    def close(self) -> None:
        """Release resources."""
        ...


class PostgresSink(TelemetrySink):
    """Write telemetry directly to PostgreSQL via SQLAlchemy."""

    def __init__(self, db: Session, batch_size: int = 100):
        self._db = db
        self._batch_size = batch_size
        self._buffer: list[Telemetry] = []

    def write_batch(self, records: list[TelemetryRecord]) -> None:
        for r in records:
            self._buffer.append(
                Telemetry(
                    run_id=r.run_id,
                    step=r.step,
                    vehicle_id=r.vehicle_id,
                    x=r.x,
                    y=r.y,
                    speed=r.speed,
                    angle=r.angle,
                    lane_id=r.lane_id,
                )
            )

        if len(self._buffer) >= self._batch_size:
            self.flush()

    def flush(self) -> None:
        if self._buffer:
            self._db.bulk_save_objects(self._buffer)
            self._db.commit()
            self._buffer.clear()

    def close(self) -> None:
        self.flush()


class KafkaSink(TelemetrySink):
    """Write telemetry to Kafka as Avro records."""

    def __init__(
        self,
        producer: Optional[AvroProducer] = None,
        topic: str = "sopir.sim.telemetry.v1",
        batch_size: int = 100,
    ):
        self._producer = producer or create_producer()
        self._topic = topic
        self._batch_size = batch_size
        self._buffer: list[tuple[str, dict]] = []

    def write_batch(self, records: list[TelemetryRecord]) -> None:
        for r in records:
            value = {
                "schema_version": "1.0.0",
                "event_id": str(uuid.uuid4()),
                "run_id": str(r.run_id),
                "vehicle_id": r.vehicle_id,
                "captured_at": (
                    r.captured_at_ms if r.captured_at_ms is not None else int(time.time() * 1000)
                ),
                "step": r.step,
                "x": r.x,
                "y": r.y,
                "speed": r.speed,
                "angle": r.angle,
                "lane_id": r.lane_id,
            }
            self._buffer.append((r.vehicle_id, value))

        if len(self._buffer) >= self._batch_size:
            self.flush()

    def flush(self) -> None:
        """Enqueue buffered records and block until the broker acknowledges them."""
        if not self._buffer:
            return
        batch, self._buffer = self._buffer, []
        self._producer.produce_batch(self._topic, batch)
        remaining = self._producer.flush(30.0)
        if remaining:
            logger.error("Kafka sink flush timed out with %d undelivered messages", remaining)

    def close(self) -> None:
        self.flush()
        self._producer.close()


def create_sink(db: Optional[Session] = None) -> TelemetrySink:
    """Factory for creating the appropriate sink based on env var."""
    sink_type = os.getenv("TELEMETRY_SINK", "postgres").lower()

    if sink_type == "kafka":
        producer = create_producer()
        return KafkaSink(producer=producer)

    if sink_type == "postgres":
        if db is None:
            raise ValueError("PostgresSink requires a database session")
        return PostgresSink(db)

    raise ValueError(f"Unknown TELEMETRY_SINK: {sink_type}")
