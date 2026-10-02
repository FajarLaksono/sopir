"""Stream processor: Kafka -> windowed evaluation -> PostgreSQL silver layer.

This is the adapter between the stream and the Phase 1 evaluation logic. It
imports ``compute_metrics`` and ``detect_failures`` unchanged: those stay pure
functions with no I/O, and this module is the thing that has to know about
Kafka offsets, database transactions, and windows. That split is why the parity
test in ``scripts/parity_test.py`` can compare a streamed result against a batch
result field by field.

Delivery semantics follow the lake writer, for the same reason:

    Commit the database transaction FIRST, advance the Kafka offset SECOND.

A crash between the two replays the window, and replay is safe because writes
are idempotent: ``stream_metrics.stream_key`` is unique, ``stream_failures`` is
unique per (key, rule), and ``stream_events.event_id`` is unique.
"""

from __future__ import annotations

import logging
import os
import signal
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from confluent_kafka import Consumer, TopicPartition
from confluent_kafka.schema_registry import SchemaRegistryClient
from confluent_kafka.schema_registry.avro import AvroDeserializer
from confluent_kafka.serialization import MessageField, SerializationContext
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from backend.database import SessionLocal
from backend.models import StreamEvent, StreamFailure, StreamMetrics
from evaluation.failure_detector import detect_failures
from evaluation.metrics import compute_metrics
from streaming.dlq import DESERIALIZATION, PROCESSING, DlqRouter, FailureContext
from streaming.observability import KafkaLagCollector, Observability
from streaming.producer import create_producer
from streaming.windowing import WINDOW_IDLE_TIMEOUT_SEC, Window, WindowManager

logger = logging.getLogger(__name__)

SIM_TOPIC = "sopir.sim.telemetry.v1"
VEHICLE_TOPICS = ("sopir.veh.can.v1", "sopir.veh.gnss.v1", "sopir.veh.events.v1")

SOURCE_NAMES = {
    SIM_TOPIC: "sim",
    "sopir.veh.can.v1": "can",
    "sopir.veh.gnss.v1": "gnss",
    "sopir.veh.events.v1": "events",
}


@dataclass
class ProcessorConfig:
    """Configuration for the stream processor."""

    bootstrap_servers: str = "redpanda:9092"
    schema_registry_url: str = "http://redpanda:8081"
    database_url: str = "postgresql://postgres:postgres@db:5432/opendrivelab"
    group_id: str = "stream-processor"
    window_sec: int = 30
    idle_timeout_sec: float = WINDOW_IDLE_TIMEOUT_SEC
    poll_timeout_sec: float = 1.0
    max_poll_records: int = 500
    retain_events: bool = True
    max_open_windows: int = 500
    topics: tuple[str, ...] = (SIM_TOPIC,) + VEHICLE_TOPICS

    @classmethod
    def from_env(cls) -> "ProcessorConfig":
        topics = os.getenv("PROCESSOR_TOPICS")
        return cls(
            bootstrap_servers=os.getenv("KAFKA_BOOTSTRAP_SERVERS", cls.bootstrap_servers),
            schema_registry_url=os.getenv("SCHEMA_REGISTRY_URL", cls.schema_registry_url),
            database_url=os.getenv("DATABASE_URL", cls.database_url),
            group_id=os.getenv("PROCESSOR_CONSUMER_GROUP", cls.group_id),
            window_sec=int(os.getenv("PROCESSOR_WINDOW_SEC", cls.window_sec)),
            idle_timeout_sec=float(os.getenv("PROCESSOR_IDLE_TIMEOUT_SEC", cls.idle_timeout_sec)),
            retain_events=os.getenv("PROCESSOR_RETAIN_EVENTS", "1") not in ("0", "false"),
            max_open_windows=int(os.getenv("PROCESSOR_MAX_WINDOWS", cls.max_open_windows)),
            topics=tuple(t.strip() for t in topics.split(",") if t.strip())
            if topics
            else cls.topics,
        )


@dataclass
class ProcessorStats:
    """Counters for the reconciliation view."""

    consumed: int = 0
    events_written: int = 0
    events_skipped: int = 0
    windows_closed: int = 0
    metrics_written: int = 0
    failures_written: int = 0
    dlq_routed: int = 0
    db_errors: int = 0
    processing_errors: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "consumed": self.consumed,
            "events_written": self.events_written,
            "events_skipped": self.events_skipped,
            "windows_closed": self.windows_closed,
            "metrics_written": self.metrics_written,
            "failures_written": self.failures_written,
            "dlq_routed": self.dlq_routed,
            "db_errors": self.db_errors,
            "processing_errors": self.processing_errors,
        }


# compute_metrics reads exactly these keys, so the adapter's job is to hand it
# a dict with them and nothing surprising.
TELEMETRY_FIELDS = ("vehicle_id", "step", "x", "y", "speed", "angle", "lane_id")


def coerce_run_id(value: Any) -> Optional[uuid.UUID]:
    """Parse a run_id into a UUID, or None when it is absent or malformed.

    ``run_id`` arrives as a string over Avro but is stored in a UUID column. A
    malformed value must not take the window down: a write error here would
    leave the offsets uncommitted and the same poison window would be retried
    on every poll, forever. Nulling it keeps the window, and the original value
    is still visible in ``stream_key``.
    """
    if value in (None, ""):
        return None
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        logger.warning("Unparseable run_id %r, storing NULL", value)
        return None


def is_evaluable(window: Window) -> bool:
    """True when a window's records carry the fields ``compute_metrics`` needs.

    Only simulation telemetry qualifies. Vehicle logs have a ``vehicle_id`` and
    a timestamp but no simulation step or position, so there is nothing for the
    collision and TTC maths to work with. Those records are retained in
    ``stream_events`` instead of being scored, which is the honest answer rather
    than a silent zero.
    """
    if window.is_empty:
        return False
    first = window.records[0]
    return all(name in first for name in TELEMETRY_FIELDS)


def require_evaluable(window: Window) -> None:
    """Guard against scoring a window that has no telemetry geometry."""
    if not is_evaluable(window):
        missing = [
            name for name in TELEMETRY_FIELDS if window.is_empty or name not in window.records[0]
        ]
        raise ValueError(
            f"window {window.key} (source={window.source}) is not evaluable; "
            f"missing {missing or 'records'}"
        )


def to_telemetry(record: dict[str, Any]) -> dict[str, Any]:
    """Project an Avro sim record onto the shape ``compute_metrics`` expects.

    ``evaluate`` in the batch path reads straight out of the ``telemetry``
    table, so this is the one place where the stream schema and the SQL schema
    have to be reconciled. Keeping it an explicit projection means a schema
    change shows up here as a test failure rather than as a silently wrong
    metric.

    ``captured_at`` is carried through even though ``compute_metrics`` ignores
    it: the windowing layer needs it to place a record in time.
    """
    return {
        "vehicle_id": record["vehicle_id"],
        "step": int(record["step"]),
        "x": float(record["x"]),
        "y": float(record["y"]),
        "speed": float(record["speed"]),
        "angle": float(record.get("angle", 0.0)),
        "lane_id": record["lane_id"],
        "captured_at": int(record["captured_at"]),
    }


class SilverWriter:
    """Writes closed windows to PostgreSQL. All writes are idempotent."""

    def __init__(self, session_factory, retain_events: bool = True):
        self._session_factory = session_factory
        self._retain_events = retain_events

    def write_window(self, window: Window) -> tuple[int, int]:
        """Persist one window's metrics and failures.

        Returns ``(metrics_rows, failure_rows)``. Uses upserts keyed on the
        window identity so that replaying the same window overwrites its own
        prior result rather than accumulating a second verdict.
        """
        require_evaluable(window)
        metrics = compute_metrics(window.records)
        failures = detect_failures(metrics)

        db = self._session_factory()
        try:
            values = {
                "stream_key": window.key,
                "source": window.source,
                "run_id": coerce_run_id(window.run_id),
                "vehicle_id": window.vehicle_id,
                "window_start": window.window_start,
                "window_end": window.window_end,
                "record_count": window.count,
                "collision_count": metrics["collision_count"],
                "min_ttc": metrics["min_ttc"],
                "avg_speed": metrics["avg_speed"],
                "speed_violations": metrics["speed_violations"],
                "lane_deviations": metrics["lane_deviations"],
                "ttc_per_step": metrics["ttc_per_step"],
                "last_offsets": window.last_offsets,
            }

            # on_conflict_do_update keeps the row identity stable so re-running
            # a window is a correction, not a duplicate.
            statement = pg_insert(StreamMetrics).values(**values)
            statement = statement.on_conflict_do_update(
                index_elements=[StreamMetrics.stream_key], set_=values
            )
            db.execute(statement)

            # Failures are derived, so the previous derivation is replaced
            # wholesale rather than merged.
            db.execute(delete(StreamFailure).where(StreamFailure.stream_key == window.key))
            for failure in failures:
                db.execute(
                    pg_insert(StreamFailure)
                    .values(
                        stream_key=window.key,
                        source=window.source,
                        run_id=coerce_run_id(window.run_id),
                        vehicle_id=window.vehicle_id,
                        window_start=window.window_start,
                        severity=failure["severity"],
                        rule=failure["rule"],
                        details=failure["details"],
                    )
                    .on_conflict_do_update(
                        index_elements=[StreamFailure.stream_key, StreamFailure.rule],
                        set_={
                            "severity": failure["severity"],
                            "details": failure["details"],
                        },
                    )
                )

            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

        return 1, len(failures)

    def write_events(self, events: list[tuple[dict[str, Any], str, int, int]]) -> int:
        """Bulk insert vehicle-log records, ignoring ones already stored.

        Returns the number of newly inserted rows. The ``ON CONFLICT DO NOTHING``
        on ``event_id`` is what makes a replay free: the offset batch is
        re-delivered and every row collides, so nothing is duplicated.
        """
        if not events or not self._retain_events:
            return 0

        rows = []
        for record, topic, partition, offset in events:
            rows.append(
                {
                    "event_id": record["event_id"],
                    "source": SOURCE_NAMES.get(topic, topic),
                    "topic": topic,
                    "vehicle_id": record["vehicle_id"],
                    "run_id": coerce_run_id(record.get("run_id")),
                    "captured_at": datetime.fromtimestamp(
                        record["captured_at"] / 1000.0, tz=timezone.utc
                    ),
                    "payload": record,
                    "kafka_partition": partition,
                    "kafka_offset": offset,
                }
            )

        db = self._session_factory()
        try:
            result = db.execute(
                pg_insert(StreamEvent)
                .values(rows)
                .on_conflict_do_nothing(index_elements=[StreamEvent.event_id])
            )
            db.commit()
            return result.rowcount or 0
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def count_events(self) -> int:
        """Count retained events, for reconciliation against the raw lake."""
        session = self._session_factory()
        try:
            return session.execute(select(func.count()).select_from(StreamEvent)).scalar_one()
        finally:
            session.close()


class StreamProcessor:
    """Consumes the stream topics and maintains the silver layer."""

    def __init__(
        self,
        config: Optional[ProcessorConfig] = None,
        obs: Optional[Observability] = None,
    ):
        self.config = config or ProcessorConfig.from_env()
        self.stats = ProcessorStats()
        self.obs = obs or Observability("stream_processor")

        self._consumer = Consumer(
            {
                "bootstrap.servers": self.config.bootstrap_servers,
                "group.id": self.config.group_id,
                "auto.offset.reset": "earliest",
                "enable.auto.commit": False,
                "enable.auto.offset.store": False,
            }
        )
        self._registry = SchemaRegistryClient({"url": self.config.schema_registry_url})
        self._deserializers: dict[str, AvroDeserializer] = {}
        self._dlq = DlqRouter(
            create_producer(
                bootstrap_servers=self.config.bootstrap_servers,
                schema_registry_url=self.config.schema_registry_url,
            )
        )
        self._writer = SilverWriter(SessionLocal, retain_events=self.config.retain_events)
        # Simulation telemetry is evaluated per run; vehicle logs are only
        # retained, since they lack the fields compute_metrics needs.
        self._windows = WindowManager(
            "run",
            idle_timeout_sec=self.config.idle_timeout_sec,
            max_open_windows=self.config.max_open_windows,
        )
        self._pending_events: list[tuple[dict[str, Any], str, int, int]] = []
        # Offsets are tracked separately per path on purpose. Simulation
        # offsets belong to a window and are committed when that window is
        # written; vehicle offsets belong to the event buffer and are committed
        # when that buffer is flushed. Sharing one map would let an event flush
        # commit an offset past a simulation record still sitting in an open
        # window, which skips it silently on the next rebalance.
        self._pending_event_offsets: dict[tuple[str, int], int] = {}
        self._running = False

    def _deserializer(self, topic: str) -> AvroDeserializer:
        if topic not in self._deserializers:
            registered = self._registry.get_latest_version(f"{topic}-value")
            self._deserializers[topic] = AvroDeserializer(
                self._registry, registered.schema.schema_str
            )
        return self._deserializers[topic]

    def start(self) -> None:
        self.obs.start(
            lag=KafkaLagCollector(
                self.obs.metrics,
                bootstrap_servers=self.config.bootstrap_servers,
                group_id=self.config.group_id,
                topics=self.config.topics,
                interval_sec=self.obs.config.lag_interval_sec,
            )
        )
        self._consumer.subscribe(list(self.config.topics))
        self._running = True

        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)

        logger.info(
            "Stream processor started: group=%s topics=%s window=%ds",
            self.config.group_id,
            ",".join(self.config.topics),
            self.config.window_sec,
        )

        try:
            while self._running:
                message = self._consumer.poll(self.config.poll_timeout_sec)
                if message is not None and not message.error():
                    # One poisoned record must not take the consumer down: the
                    # offsets stay uncommitted and it would be retried on every
                    # poll, so an escaping exception here is an outage that
                    # never recovers on its own.
                    try:
                        self._handle_message(message)
                        self.obs.health.heartbeat()
                    except Exception:
                        self.stats.processing_errors += 1
                        self.obs.metrics.record_error("processing")
                        logger.exception(
                            "Failed to handle %s[%s]@%s; leaving it uncommitted",
                            message.topic(),
                            message.partition(),
                            message.offset(),
                        )

                self._flush_due_windows()
                self._flush_events()
                self._report_window_state()
                # Heartbeat on every iteration, not only when a record arrives.
                # A consumer with an idle topic is healthy; tying liveness to
                # traffic would report a working service as dead whenever
                # nothing is being produced.
                self.obs.health.heartbeat()
        finally:
            # Drain before closing so a clean stop does not lose a window that
            # was nearly complete.
            self._flush_due_windows(force=True)
            self._flush_events(force=True)
            self._consumer.close()
            self.obs.stop()
            logger.info("Stream processor stopped: %s", self.stats.as_dict())

    def _report_window_state(self) -> None:
        """Publish in-memory buffer sizes so a stuck flush is visible."""
        self.obs.metrics.set_open_windows(len(self._windows))
        self.obs.metrics.set_pending_events(len(self._pending_events))

    def _handle_signal(self, signum, frame) -> None:
        logger.info("Shutdown requested, flushing open windows")
        self._running = False

    def _handle_message(self, message) -> None:
        topic = message.topic()
        partition = message.partition()
        offset = message.offset()
        self.stats.consumed += 1
        self.obs.metrics.record_consumed(topic)

        context = FailureContext(
            source_topic=topic,
            partition=partition,
            offset=offset,
            key=message.key().decode("utf-8", errors="replace") if message.key() else None,
            raw_payload=message.value(),
        )

        try:
            record = self._deserializer(topic)(
                message.value(), SerializationContext(topic, MessageField.VALUE)
            )
            _validate(record, topic)
        except Exception as exc:
            stage = PROCESSING if isinstance(exc, ValueError) else DESERIALIZATION
            self._dlq.route(context, str(exc), stage, int(time.time() * 1000))
            self.stats.dlq_routed += 1
            self.obs.metrics.record_dlq(stage)
            self._commit(message)
            return

        if topic == SIM_TOPIC:
            try:
                telemetry = to_telemetry(record)
                self._windows.add(
                    telemetry | {"run_id": record.get("run_id")},
                    source="sim",
                    now=time.monotonic(),
                    topic=topic,
                    partition=partition,
                    offset=offset,
                )
            except Exception as exc:
                # A record that cannot be windowed (missing run_id, unparseable
                # fields) would otherwise stay uncommitted and be redelivered
                # on every poll forever. Route it to the DLQ and move past it.
                self._dlq.route(
                    context,
                    f"windowing failed: {exc}",
                    PROCESSING,
                    int(time.time() * 1000),
                )
                self.stats.dlq_routed += 1
                self.obs.metrics.record_dlq(PROCESSING)
                self._commit(message)
                return
        else:
            self._pending_events.append((record, topic, partition, offset))
            self._pending_event_offsets[(topic, partition)] = offset

        if len(self._pending_events) >= self.config.max_poll_records:
            self._flush_events()
            self._flush_due_windows()

    def _flush_due_windows(self, force: bool = False) -> None:
        now = time.monotonic()
        windows = self._windows.flush_all() if force else self._windows.close_due(now)
        for window in windows:
            self._write_window(window)

    def _write_window(self, window: Window) -> None:
        if window.is_empty:
            return
        if not is_evaluable(window):
            # Vehicle-log windows have no simulation geometry, so there is
            # nothing to score. They are retained via _flush_events instead.
            logger.debug(
                "Skipping non-evaluable window %s (source=%s, %d records)",
                window.key,
                window.source,
                window.count,
            )
            return

        try:
            metrics_rows, failure_rows = self._writer.write_window(window)
        except Exception:
            # Leave the offsets uncommitted so the window is replayed. The raw
            # lake already holds these records, so nothing is lost either way.
            self.stats.db_errors += 1
            self.obs.metrics.record_error("db_window")
            logger.exception(
                "Failed to write window %s (%d records); will replay", window.key, window.count
            )
            return

        self.stats.windows_closed += 1
        self.stats.metrics_written += metrics_rows
        self.stats.failures_written += failure_rows
        self.obs.metrics.record_window(window.count)
        self.obs.metrics.record_written("stream_metrics", metrics_rows)
        self.obs.metrics.record_written("stream_failures", failure_rows)
        self.obs.metrics.touch_progress()
        logger.info(
            "Window %s evaluated: %d records -> %d failure(s)",
            window.key,
            window.count,
            failure_rows,
        )
        self._commit_offsets(window.last_offsets)

    def _flush_events(self, force: bool = False) -> None:
        if not self._pending_events:
            return
        if not force and len(self._pending_events) < self.config.max_poll_records:
            return

        batch, self._pending_events = self._pending_events, []
        try:
            inserted = self._writer.write_events(batch)
        except Exception:
            self.stats.db_errors += 1
            self.obs.metrics.record_error("db_events")
            logger.exception("Failed to write %d events; will replay", len(batch))
            self._pending_events = batch + self._pending_events
            return

        self.stats.events_written += inserted
        self.stats.events_skipped += len(batch) - inserted
        self.obs.metrics.record_written("stream_events", inserted)
        self.obs.metrics.touch_progress()
        if len(batch) > inserted:
            logger.info(
                "Event replay: %d of %d rows already present, skipped",
                len(batch) - inserted,
                len(batch),
            )
        self._commit_event_offsets()

    def _commit_offsets(self, last_offsets: dict[str, int]) -> None:
        offsets = []
        for key, offset in last_offsets.items():
            topic, partition = key.rsplit(":", 1)
            offsets.append(TopicPartition(topic, int(partition), offset + 1))
        if not offsets:
            return
        self._consumer.store_offsets(offsets=offsets)
        self._consumer.commit(asynchronous=False)

    def _commit_event_offsets(self) -> None:
        """Commit past the vehicle records that are now durable in Postgres."""
        if not self._pending_event_offsets:
            return
        offsets = [
            TopicPartition(topic, partition, offset + 1)
            for (topic, partition), offset in self._pending_event_offsets.items()
        ]
        self._pending_event_offsets.clear()
        self._consumer.store_offsets(offsets=offsets)
        self._consumer.commit(asynchronous=False)

    def _commit(self, message) -> None:
        """Commit past a record that was routed to the DLQ."""
        self._consumer.store_offsets(
            offsets=[TopicPartition(message.topic(), message.partition(), message.offset() + 1)]
        )
        self._consumer.commit(asynchronous=False)


REQUIRED_FIELDS = ("event_id", "vehicle_id", "captured_at")


def _validate(record: dict[str, Any], topic: str) -> None:
    """Reject records the silver layer cannot use."""
    if not isinstance(record, dict):
        raise ValueError(f"expected a record, got {type(record).__name__}")

    for name in REQUIRED_FIELDS:
        if record.get(name) in (None, ""):
            raise ValueError(f"missing required field: {name}")

    if not isinstance(record["captured_at"], int):
        raise ValueError("captured_at must be epoch milliseconds")

    if topic == SIM_TOPIC:
        for name in TELEMETRY_FIELDS:
            if record.get(name) is None:
                raise ValueError(f"simulation telemetry missing field: {name}")


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logger.info("stream_processor starting, instance=%s", uuid.uuid4().hex[:8])
    StreamProcessor(obs=Observability("stream_processor")).start()


if __name__ == "__main__":
    main()
