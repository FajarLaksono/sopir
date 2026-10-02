"""Raw lake writer: Kafka -> Parquet -> S3-compatible object storage.

This is the bronze layer. Records land exactly as received, partitioned by
source and event time, and are never mutated. Nothing downstream is allowed to
change these bytes; a correction is a new object.

The one correctness rule that matters here:

    Write Parquet to the object store FIRST, commit the Kafka offset SECOND.

Reverse that and a crash between the two loses the batch permanently, with no
trace in the topic. Doing it in this order guarantees at-least-once delivery.

**It does not guarantee exactly-once, and the object keys do not save it.** An
object key embeds the offset range that was buffered when the flush fired, and
that range is a function of *timing*, not of the data: the batch-size trigger
and the ``LAKE_FLUSH_INTERVAL_SEC`` timer can split the same records differently
on a replay. So a replayed batch produces a *new* key and the records land
twice. This was measured, not assumed -- a full replay under a fresh consumer
group took the lake from 520 rows to 1040. See ``tests/test_lake_writer.py``
for the object-key tests and ``tests/test_pipeline_integration.py`` for the
end-to-end proof.

Bronze is append-only by design, so duplication is tolerated rather than
prevented. The guarantee that actually holds is the one silver relies on:

    a replay adds rows, never loses them, and never changes a payload

which is why ``event_id`` is in every schema and unique in ``stream_events``.
"""

from __future__ import annotations

import io
import logging
import os
import signal
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

import boto3
import pyarrow as pa
import pyarrow.parquet as pq
from confluent_kafka import Consumer, KafkaError, TopicPartition
from confluent_kafka.schema_registry import SchemaRegistryClient
from confluent_kafka.schema_registry.avro import AvroDeserializer
from confluent_kafka.serialization import MessageField, SerializationContext

from streaming.dlq import (
    DESERIALIZATION,
    PROCESSING,
    SINK_WRITE,
    DlqRouter,
    FailureContext,
)
from streaming.lake_paths import build_partition, object_key
from streaming.observability import KafkaLagCollector, Observability
from streaming.producer import create_producer

logger = logging.getLogger(__name__)

# topic -> lake `source` value
DEFAULT_SOURCES = {
    "sopir.sim.telemetry.v1": "sim",
    "sopir.veh.can.v1": "can",
    "sopir.veh.gnss.v1": "gnss",
    "sopir.veh.events.v1": "events",
}


@dataclass
class LakeConfig:
    """Configuration for the lake writer."""

    bootstrap_servers: str = "redpanda:9092"
    schema_registry_url: str = "http://redpanda:8081"
    bucket: str = "sopir-raw"
    group_id: str = "lake-writer"
    endpoint_url: Optional[str] = None
    region: str = "us-east-1"
    batch_size: int = 500
    flush_interval_sec: float = 10.0
    poll_timeout_sec: float = 1.0
    topics: tuple[str, ...] = tuple(DEFAULT_SOURCES)
    sources: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_SOURCES))

    @classmethod
    def from_env(cls) -> "LakeConfig":
        """Build a config from environment variables, falling back to defaults.

        Credentials accept either the repo-wide ``S3_*`` names or the standard
        ``AWS_*`` pair, so the lake writer drops into an existing compose block
        without renaming variables.
        """
        topics = os.getenv("LAKE_TOPICS")
        access_key = os.getenv("AWS_ACCESS_KEY_ID") or os.getenv("S3_ACCESS_KEY")
        secret_key = os.getenv("AWS_SECRET_ACCESS_KEY") or os.getenv("S3_SECRET_KEY")
        if access_key:
            os.environ.setdefault("AWS_ACCESS_KEY_ID", access_key)
        if secret_key:
            os.environ.setdefault("AWS_SECRET_ACCESS_KEY", secret_key)

        return cls(
            bootstrap_servers=os.getenv("KAFKA_BOOTSTRAP_SERVERS", cls.bootstrap_servers),
            schema_registry_url=os.getenv("SCHEMA_REGISTRY_URL", cls.schema_registry_url),
            bucket=os.getenv("S3_BUCKET", cls.bucket),
            group_id=os.getenv("LAKE_CONSUMER_GROUP", cls.group_id),
            endpoint_url=os.getenv("S3_ENDPOINT_URL") or os.getenv("S3_ENDPOINT") or None,
            region=os.getenv("AWS_DEFAULT_REGION", cls.region),
            batch_size=int(os.getenv("LAKE_BATCH_SIZE", cls.batch_size)),
            flush_interval_sec=float(os.getenv("LAKE_FLUSH_INTERVAL_SEC", cls.flush_interval_sec)),
            topics=tuple(t.strip() for t in topics.split(",") if t.strip())
            if topics
            else cls.topics,
        )


@dataclass
class LakeStats:
    """Counters exposed for the reconciliation view."""

    consumed: int = 0
    written: int = 0
    files_written: int = 0
    dlq_routed: int = 0
    batches: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "consumed": self.consumed,
            "written": self.written,
            "files_written": self.files_written,
            "dlq_routed": self.dlq_routed,
            "batches": self.batches,
        }


@dataclass
class Batch:
    """A set of records grouped into one Parquet file."""

    topic: str
    source: str
    partition: int
    rows: list[dict[str, Any]]
    first_offset: int
    last_offset: int

    @property
    def batch_id(self) -> str:
        """Deterministic id from the offset range, so a replay rewrites the same key."""
        return f"{self.partition:02d}-{self.first_offset:012d}-{self.last_offset:012d}"

    @property
    def count(self) -> int:
        return len(self.rows)


class ParquetLakeWriter:
    """Writes Parquet objects to an S3-compatible bucket."""

    def __init__(self, config: LakeConfig):
        self.config = config
        self._client = boto3.client(
            "s3",
            endpoint_url=config.endpoint_url,
            region_name=config.region,
        )

    def ensure_bucket(self) -> None:
        """Create the bucket if it does not exist."""
        try:
            self._client.head_bucket(Bucket=self.config.bucket)
            logger.info("Lake bucket %s exists", self.config.bucket)
        except Exception:
            self._client.create_bucket(Bucket=self.config.bucket)
            logger.info("Created lake bucket %s", self.config.bucket)

    def write(self, batch: Batch) -> list[str]:
        """Serialize a batch to Parquet and put the objects.

        A batch is grouped by Kafka partition and offset range, which does not
        respect hour boundaries: a slow consumer or a long backlog can hold
        records from two different hours in one batch. Rows are therefore split
        by their own event time before writing, so no object ends up filed under
        an hour it does not belong to. Partition columns are carried inside each
        Parquet file as well, so a reader that ignores the key still knows.

        Returns the object keys written.
        """
        grouped: dict[str, list[dict[str, Any]]] = {}
        partitions: dict[str, Any] = {}

        for row in batch.rows:
            partition = build_partition(batch.source, row["captured_at"])
            grouped.setdefault(partition.prefix, []).append(row)
            partitions[partition.prefix] = partition

        written: list[str] = []
        for prefix, rows in grouped.items():
            partition = partitions[prefix]
            table = pa.Table.from_pylist(rows)
            for name, value in partition.hive_values.items():
                table = table.append_column(
                    name, pa.array([value] * table.num_rows, type=pa.string())
                )

            buffer = io.BytesIO()
            pq.write_table(table, buffer, compression="snappy", use_dictionary=True)

            key = object_key(partition, batch.batch_id)
            self._client.put_object(
                Bucket=self.config.bucket,
                Key=key,
                Body=buffer.getvalue(),
                ContentType="application/vnd.apache.parquet",
            )
            written.append(key)

        return written

    def count_objects(self) -> int:
        """Count objects currently in the lake, for reconciliation."""
        total = 0
        token: Optional[str] = None
        while True:
            kwargs = {"Bucket": self.config.bucket, "MaxKeys": 1000}
            if token:
                kwargs["ContinuationToken"] = token
            response = self._client.list_objects_v2(**kwargs)
            total += response.get("KeyCount", 0)
            if not response.get("IsTruncated"):
                return total
            token = response.get("NextContinuationToken")


class LakeWriter:
    """Consumes Kafka topics and lands them in the raw lake."""

    def __init__(
        self,
        config: Optional[LakeConfig] = None,
        obs: Optional[Observability] = None,
    ):
        self.config = config or LakeConfig.from_env()
        self.stats = LakeStats()
        self.obs = obs or Observability("lake_writer")

        self._consumer = Consumer(
            {
                "bootstrap.servers": self.config.bootstrap_servers,
                "group.id": self.config.group_id,
                "auto.offset.reset": "earliest",
                # Manual commit: the whole point is that we control when an
                # offset becomes durable.
                "enable.auto.commit": False,
                "enable.auto.offset.store": False,
            }
        )
        self._registry = SchemaRegistryClient({"url": self.config.schema_registry_url})
        self._deserializers: dict[str, AvroDeserializer] = {}
        self._lake = ParquetLakeWriter(self.config)
        self._dlq = DlqRouter(
            create_producer(
                bootstrap_servers=self.config.bootstrap_servers,
                schema_registry_url=self.config.schema_registry_url,
            )
        )
        self._running = False
        self._last_write = time.monotonic()

    def _deserializer(self, topic: str) -> AvroDeserializer:
        if topic not in self._deserializers:
            registered = self._registry.get_latest_version(f"{topic}-value")
            self._deserializers[topic] = AvroDeserializer(
                self._registry, registered.schema.schema_str
            )
        return self._deserializers[topic]

    def start(self) -> None:
        """Subscribe and run the consume loop until stopped."""
        self.obs.start(
            lag=KafkaLagCollector(
                self.obs.metrics,
                bootstrap_servers=self.config.bootstrap_servers,
                group_id=self.config.group_id,
                topics=self.config.topics,
                interval_sec=self.obs.config.lag_interval_sec,
            )
        )
        try:
            self._lake.ensure_bucket()
            self.obs.health.set_check("object_store", True, self.config.bucket)
        except Exception:
            # Without the bucket nothing can be landed, so this instance should
            # not receive traffic and the healthcheck should fail loudly.
            self.obs.health.set_check("object_store", False, "bucket unavailable")
            self.obs.stop()
            raise

        self._consumer.subscribe(list(self.config.topics))
        self._running = True

        handler = self._handle_signal
        signal.signal(signal.SIGTERM, handler)
        signal.signal(signal.SIGINT, handler)

        logger.info(
            "Lake writer started: group=%s topics=%s bucket=%s",
            self.config.group_id,
            ",".join(self.config.topics),
            self.config.bucket,
        )

        pending: dict[tuple[str, int], Batch] = {}
        try:
            while self._running:
                message = self._consumer.poll(self.config.poll_timeout_sec)
                if message is not None:
                    self._handle_message(message, pending)

                # Heartbeat every iteration, not only when a record arrives. An
                # idle topic means the lake writer is healthy and waiting, not
                # stuck; tying liveness to traffic produces false alarms.
                self.obs.health.heartbeat()

                if self._should_flush(pending):
                    self._flush_all(pending)
                    self.obs.metrics.set_pending_events(sum(len(b.rows) for b in pending.values()))

                if message is None:
                    continue
        finally:
            # Drain whatever is buffered so a clean stop does not discard a
            # partial batch.
            self._flush_all(pending)
            self._consumer.close()
            self.obs.stop()
            logger.info("Lake writer stopped: %s", self.stats.as_dict())

    def _handle_signal(self, signum, frame) -> None:
        logger.info("Shutdown requested, finishing current batch")
        self._running = False

    def _handle_message(self, message, pending: dict[tuple[str, int], Batch]) -> None:
        if message.error():
            if message.error().code() == KafkaError._PARTITION_EOF:
                return
            logger.error("Consumer error: %s", message.error())
            return

        context = FailureContext(
            source_topic=message.topic(),
            partition=message.partition(),
            offset=message.offset(),
            key=message.key().decode("utf-8", errors="replace") if message.key() else None,
            raw_payload=message.value(),
        )
        self.stats.consumed += 1
        self.obs.metrics.record_consumed(message.topic())

        try:
            record = self._deserializer(message.topic())(
                message.value(),
                SerializationContext(message.topic(), MessageField.VALUE),
            )
            _validate(record)
        except Exception as exc:
            stage = PROCESSING if isinstance(exc, ValueError) else DESERIALIZATION
            self._dlq.route(context, str(exc), stage, int(time.time() * 1000))
            self.stats.dlq_routed += 1
            self.obs.metrics.record_dlq(stage)
            # The bad record will never succeed, so its offset must still be
            # committed or the consumer would retry it forever.
            self._commit(message)
            return

        key = (message.topic(), message.partition())
        batch = pending.get(key)
        if batch is None:
            batch = Batch(
                topic=message.topic(),
                source=self.config.sources.get(message.topic(), message.topic()),
                partition=message.partition(),
                rows=[],
                first_offset=message.offset(),
                last_offset=message.offset(),
            )
            pending[key] = batch
        batch.rows.append(record)
        batch.last_offset = message.offset()

        if sum(len(b.rows) for b in pending.values()) >= self.config.batch_size:
            self._flush_all(pending)

    def _should_flush(self, pending: dict[tuple[str, int], Batch]) -> bool:
        if not pending:
            return False
        if sum(len(b.rows) for b in pending.values()) >= self.config.batch_size:
            return True
        return (time.monotonic() - self._last_write) >= self.config.flush_interval_sec

    def _flush_all(self, pending: dict[tuple[str, int], Batch]) -> None:
        for key in list(pending):
            self._flush_one(key, pending)
        self._last_write = time.monotonic()

    def _flush_one(self, key: tuple[str, int], pending: dict[tuple[str, int], Batch]) -> None:
        batch = pending.pop(key)
        if not batch.rows:
            return

        try:
            written_keys = self._lake.write(batch)
        except Exception as exc:
            # The write failed, so the offsets stay uncommitted and the records
            # are replayed on restart. This is the at-least-once behaviour we
            # want; dropping them here is how data disappears.
            logger.exception(
                "Failed to write batch %s of %s, offsets left uncommitted",
                batch.batch_id,
                batch.topic,
            )
            self._dlq.route(
                FailureContext(
                    source_topic=batch.topic,
                    partition=batch.partition,
                    offset=batch.first_offset,
                    raw_payload=None,
                ),
                f"sink_write failed: {exc}",
                SINK_WRITE,
                int(time.time() * 1000),
            )
            self.stats.dlq_routed += 1
            self.obs.metrics.record_dlq(SINK_WRITE)
            self.obs.metrics.record_error("sink_write")
            return

        self.stats.written += batch.count
        self.stats.files_written += len(written_keys)
        self.stats.batches += 1
        self.obs.metrics.record_written("parquet_records", batch.count)
        self.obs.metrics.record_window(batch.count)
        self.obs.metrics.touch_progress()
        logger.info(
            "Landed %d records to %s",
            batch.count,
            ", ".join(written_keys) if written_keys else "nothing",
        )

        # Durability achieved: only now is it safe to advance the offset.
        self._consumer.store_offsets(
            offsets=[TopicPartition(batch.topic, batch.partition, batch.last_offset + 1)]
        )
        self._consumer.commit(asynchronous=False)

    def _commit(self, message) -> None:
        """Commit past a message that was routed to the DLQ."""
        self._consumer.store_offsets(
            offsets=[TopicPartition(message.topic(), message.partition(), message.offset() + 1)]
        )
        self._consumer.commit(asynchronous=False)


REQUIRED_FIELDS = ("event_id", "vehicle_id", "captured_at")


def _validate(record: dict[str, Any]) -> None:
    """Reject records missing the envelope fields the lake depends on."""
    if not isinstance(record, dict):
        raise ValueError(f"expected a record, got {type(record).__name__}")

    for field_name in REQUIRED_FIELDS:
        if record.get(field_name) in (None, ""):
            raise ValueError(f"missing required field: {field_name}")

    if not isinstance(record["captured_at"], int):
        raise ValueError("captured_at must be epoch milliseconds")


def main() -> None:
    """Entry point for the lake_writer service."""
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logger.info("lake_writer starting, instance=%s", uuid.uuid4().hex[:8])
    LakeWriter(obs=Observability("lake_writer")).start()


if __name__ == "__main__":
    main()
