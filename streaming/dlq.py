"""Dead-letter routing for records the pipeline cannot process.

A message that fails deserialization or validation is never silently dropped. It
is republished to ``sopir.dlq.v1`` with the reason, the originating coordinates
and the original payload, so the failure can be diagnosed and replayed.

The DLQ topic is compacted and keyed on the original ``(topic, partition,
offset)``, so repeatedly failing on the same message overwrites its own DLQ entry
instead of filling the topic with duplicates.

Records here match ``schemas/dlq.avsc`` exactly; changing either without
registering a compatible schema version will break the writer.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Optional

from streaming.producer import AvroProducer

logger = logging.getLogger(__name__)

DLQ_TOPIC = "sopir.dlq.v1"
MAX_RAW_PAYLOAD_BYTES = 8192

# Mirrors the ErrorStage enum in schemas/dlq.avsc.
SCHEMA_VALIDATION = "schema_validation"
DESERIALIZATION = "deserialization"
PROCESSING = "processing"
SINK_WRITE = "sink_write"

VALID_ERROR_STAGES = frozenset({SCHEMA_VALIDATION, DESERIALIZATION, PROCESSING, SINK_WRITE})


class RecordError(Exception):
    """Raised when a record cannot be deserialized or validated."""

    def __init__(self, message: str, stage: str = DESERIALIZATION):
        if stage not in VALID_ERROR_STAGES:
            raise ValueError(f"Unknown error stage: {stage}")
        super().__init__(message)
        self.stage = stage


@dataclass(frozen=True)
class FailureContext:
    """Where a bad record came from."""

    source_topic: str
    partition: int
    offset: int
    key: Optional[str] = None
    raw_payload: Optional[bytes] = None

    @property
    def dedup_key(self) -> str:
        """Stable identity of the source record, used as the compacted key."""
        return f"{self.source_topic}:{self.partition}:{self.offset}"


def build_dlq_record(
    context: FailureContext,
    reason: str,
    error_stage: str,
    captured_at: int,
) -> dict:
    """Build the payload written to the DLQ topic."""
    if error_stage not in VALID_ERROR_STAGES:
        raise ValueError(f"Unknown error stage: {error_stage}")

    payload = context.raw_payload or b""
    if len(payload) > MAX_RAW_PAYLOAD_BYTES:
        payload = payload[:MAX_RAW_PAYLOAD_BYTES]

    return {
        "schema_version": "1.0.0",
        "event_id": str(uuid.uuid4()),
        "original_topic": context.source_topic,
        "original_key": context.key,
        "original_value": payload,
        "error_reason": reason[:1024],
        "error_stage": error_stage,
        "captured_at": captured_at,
    }


class DlqRouter:
    """Publishes failed records to the DLQ topic."""

    def __init__(self, producer: AvroProducer, topic: str = DLQ_TOPIC):
        self._producer = producer
        self._topic = topic
        self.published = 0

    def route(
        self,
        context: FailureContext,
        reason: str,
        error_stage: str,
        captured_at: int,
    ) -> None:
        """Send one failed record to the DLQ.

        Failures here are logged but never raised: a DLQ that cannot be written
        must not take the consumer down, otherwise a broker hiccup turns into
        total data loss.
        """
        try:
            record = build_dlq_record(context, reason, error_stage, captured_at)
            self._producer.produce(self._topic, context.dedup_key, record)
            self.published += 1
        except Exception:
            logger.exception(
                "Failed to route offset %s of %s to the DLQ",
                context.offset,
                context.source_topic,
            )
