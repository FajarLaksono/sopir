"""Avro producer wrapper for Kafka with Schema Registry integration."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Callable, Optional

from confluent_kafka import KafkaError, Producer
from confluent_kafka.schema_registry import SchemaRegistryClient
from confluent_kafka.schema_registry.avro import AvroSerializer
from confluent_kafka.serialization import (
    MessageField,
    SerializationContext,
    StringSerializer,
)

logger = logging.getLogger(__name__)

DeliveryCallback = Callable[[Optional[KafkaError], Any], None]


@dataclass
class ProducerConfig:
    bootstrap_servers: str = "redpanda:9092"
    schema_registry_url: str = "http://redpanda:8081"
    max_retries: int = 3
    retry_backoff_base: float = 0.5  # seconds
    linger_ms: int = 5
    batch_size: int = 16384


class AvroProducer:
    """Kafka producer that serializes records as Avro via the Schema Registry.

    Serialization is performed explicitly per message so that a single producer
    instance can serve multiple topics, each with its own registered schema.
    """

    def __init__(self, config: Optional[ProducerConfig] = None):
        self.config = config or ProducerConfig()
        self._schema_registry: Optional[SchemaRegistryClient] = None
        self._producer: Optional[Producer] = None
        self._serializers: dict[str, AvroSerializer] = {}
        self._key_serializer = StringSerializer("utf_8")
        self._closed = False

    def _get_schema_registry(self) -> SchemaRegistryClient:
        if self._schema_registry is None:
            self._schema_registry = SchemaRegistryClient({"url": self.config.schema_registry_url})
        return self._schema_registry

    def _load_schema(self, topic: str) -> str:
        """Load the latest value schema registered for a topic."""
        registry = self._get_schema_registry()
        subject = f"{topic}-value"
        try:
            return registry.get_latest_version(subject).schema.schema_str
        except Exception:
            logger.exception("Failed to load schema for subject %s", subject)
            raise

    def _get_serializer(self, topic: str) -> AvroSerializer:
        if topic not in self._serializers:
            registry = self._get_schema_registry()
            self._serializers[topic] = AvroSerializer(
                registry,
                self._load_schema(topic),
                lambda obj, ctx: obj,
            )
        return self._serializers[topic]

    def _get_producer(self) -> Producer:
        if self._producer is None:
            self._producer = Producer(
                {
                    "bootstrap.servers": self.config.bootstrap_servers,
                    "linger.ms": self.config.linger_ms,
                    "batch.size": self.config.batch_size,
                    "acks": "all",
                    "retries": self.config.max_retries,
                    "retry.backoff.ms": int(self.config.retry_backoff_base * 1000),
                    "enable.idempotence": True,
                    "max.in.flight.requests.per.connection": 5,
                }
            )
        return self._producer

    def _context(self, topic: str, field: MessageField) -> SerializationContext:
        return SerializationContext(topic, field)

    def produce(
        self,
        topic: str,
        key: str,
        value: dict[str, Any],
        on_delivery: Optional[DeliveryCallback] = None,
    ) -> None:
        """Serialize and enqueue a single record.

        Args:
            topic: Kafka topic name.
            key: Message key, used for partition affinity.
            value: Record body, must conform to the registered schema.
            on_delivery: Optional callback invoked with (error, message).
        """
        if self._closed:
            raise RuntimeError("Producer is closed")

        producer = self._get_producer()
        serializer = self._get_serializer(topic)
        payload = serializer(value, self._context(topic, MessageField.VALUE))
        message_key = self._key_serializer(key, self._context(topic, MessageField.KEY))

        def delivery_callback(err: Optional[KafkaError], msg: Any) -> None:
            if on_delivery is not None:
                on_delivery(err, msg)
            elif err is not None:
                logger.error("Delivery failed for topic=%s key=%s: %s", topic, key, err)
            else:
                logger.debug(
                    "Delivered to topic=%s partition=%d offset=%d key=%s",
                    msg.topic(),
                    msg.partition(),
                    msg.offset(),
                    key,
                )

        try:
            producer.produce(
                topic=topic,
                key=message_key,
                value=payload,
                on_delivery=delivery_callback,
            )
        except BufferError:
            producer.poll(0.1)
            producer.produce(
                topic=topic,
                key=message_key,
                value=payload,
                on_delivery=delivery_callback,
            )
        producer.poll(0)

    def produce_batch(
        self,
        topic: str,
        records: list[tuple[str, dict[str, Any]]],
        on_delivery: Optional[DeliveryCallback] = None,
    ) -> int:
        """Enqueue multiple records for a single topic.

        Args:
            topic: Kafka topic name.
            records: List of (key, value) tuples.
            on_delivery: Optional delivery callback.

        Returns:
            Number of records enqueued.
        """
        for key, value in records:
            self.produce(topic, key, value, on_delivery)
        return len(records)

    def flush(self, timeout: float = 10.0) -> int:
        """Block until in-flight messages are delivered.

        Args:
            timeout: Maximum seconds to wait.

        Returns:
            Number of messages still undelivered when the timeout expired.
        """
        if self._producer is None:
            return 0
        return self._producer.flush(timeout)

    def close(self, timeout: float = 10.0) -> None:
        """Flush pending messages and release producer resources."""
        if self._closed:
            return
        if self._producer is not None:
            remaining = self._producer.flush(timeout)
            if remaining:
                logger.error("Producer closed with %d undelivered messages", remaining)
            self._producer = None
        self._schema_registry = None
        self._serializers.clear()
        self._closed = True


def create_producer(
    bootstrap_servers: Optional[str] = None,
    schema_registry_url: Optional[str] = None,
) -> AvroProducer:
    """Create an Avro producer using explicit overrides or environment defaults."""
    return AvroProducer(
        ProducerConfig(
            bootstrap_servers=bootstrap_servers
            or os.getenv("KAFKA_BOOTSTRAP_SERVERS", "redpanda:9092"),
            schema_registry_url=schema_registry_url
            or os.getenv("SCHEMA_REGISTRY_URL", "http://redpanda:8081"),
        )
    )
