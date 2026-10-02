"""Data access for the pipeline reconciliation panel.

Kept out of ``dashboard/components/`` on purpose: components are pure renderers
(data in, Streamlit out) and this module does not render anything. It is also
kept out of ``app.py`` because unlike the four loaders there, this one reaches
Kafka and the object store, and burying three different client stacks in the
entry point would make it unreadable.

Every figure is read from the system that owns it rather than from a metric:

===================  ==========================================================
Produced             Kafka high watermarks per partition, summed per topic
Landed               Parquet row counts, read from the object store
Processed            Row counts in Postgres
Dead-lettered        Distinct keys on the compacted DLQ topic
===================  ==========================================================

Prometheus is deliberately *not* the source for any of these. Its counters
reset when a container restarts, so ``sopir_records_written_total`` reads zero
after a restart while the lake still holds its full history -- which would make
the accounting disagree with itself in exactly the situation where somebody is
checking whether a restart lost data.

The identity the panel reports is therefore the one that has to hold regardless
of restarts::

    produced == landed + dead_lettered

**This holds only while the pipeline is quiesced.** Bronze is append-only, so a
deliberate replay re-writes the records under different object keys and ``landed``
legitimately ends up *above* ``produced``. That is a duplicate, not a loss, and
the distinction matters enough to spell out here: a negative gap is expected
after a replay and is not an incident. The durable guarantee is the per-record
one -- every distinct ``event_id`` and its payload survive a replay, which is
what lets silver dedupe on ``event_id``. It is asserted end to end by
``tests/test_pipeline_integration.py`` and is not something a row count can show.

Anything still in flight shows up as ``processed`` lagging ``landed``, which is
the expected steady state rather than an error.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

import boto3
import pyarrow as pa
import pyarrow.parquet as pq
from confluent_kafka import Consumer, KafkaError, TopicPartition

from streaming.dlq import DLQ_TOPIC
from streaming.lake_writer import LakeConfig

logger = logging.getLogger(__name__)

# Compaction means the DLQ topic's message count can exceed its distinct-key
# count, and every retry of the same bad record reuses the same key. Counting
# keys rather than messages is what makes the number meaningful. This bounds
# the scan so a pathological topic cannot wedge a dashboard refresh.
MAX_DLQ_SCAN = 50_000


@dataclass(frozen=True)
class Reconciliation:
    """One snapshot of the four-way accounting. Every field is optional.

    ``None`` means "could not be read", which is not the same as zero. The
    panel renders the reason rather than pretending a broker outage means the
    pipeline produced nothing.
    """

    produced: Optional[int] = None
    produced_by_topic: dict[str, int] = field(default_factory=dict)
    landed: Optional[int] = None
    lake_objects: Optional[int] = None
    dead_lettered: Optional[int] = None
    unavailable: dict[str, str] = field(default_factory=dict)

    @property
    def gap(self) -> Optional[int]:
        """``produced - (landed + dead_lettered)``, if every figure was readable.

        Signed on purpose. A positive gap means records are unaccounted for and
        is an incident. A negative gap means the lake holds rows nobody produced
        this time round, which is what a replay looks like -- expected, and
        resolved by silver's ``event_id`` dedupe.
        """
        if self.produced is None or self.landed is None or self.dead_lettered is None:
            return None
        return self.produced - (self.landed + self.dead_lettered)

    @property
    def reconciles(self) -> Optional[bool]:
        """Whether ``produced == landed + dead_lettered`` holds, if knowable.

        True/false/None is too coarse to drive the panel on its own -- false
        conflates "we lost records" with "a replay duplicated them", which want
        opposite reactions. Use :attr:`gap` to tell them apart.
        """
        gap = self.gap
        if gap is None:
            return None
        return gap == 0

    @property
    def surplus(self) -> Optional[int]:
        """Rows in the lake that exceed the produced count, or None if unknown.

        Non-negative by construction; zero means no replay-duplication detected.
        """
        gap = self.gap
        return None if gap is None else max(0, -gap)


def _consumer(config: LakeConfig) -> Consumer:
    """A throwaway consumer used only for offset inspection.

    Never subscribes, so it joins no group and cannot rebalance or move anyone
    else's offsets. ``group.id`` is still required by librdkafka and is named
    apart from any real group so it can never collide with one.
    """
    return Consumer(
        {
            "bootstrap.servers": config.bootstrap_servers,
            "group.id": "sopir-dashboard-observer",
            "enable.auto.commit": False,
            "enable.auto.offset.store": False,
            "allow.auto.create.topics": False,
        }
    )


def _partition_ids(partitions: object) -> list[int]:
    """Normalise a topic's partitions to a list of partition ids.

    confluent-kafka changed the shape of this field in 2.x: it used to be a
    ``dict[int, PartitionMetadata]`` and is now a ``list[PartitionMetadata]``.
    Iterating ``.values()`` on the new shape raises TypeError at runtime, which
    is exactly the kind of failure no stubbed unit test catches -- so both forms
    are handled rather than pinned to whichever version is installed.

    Version-agnostic on purpose: this module is imported by the dashboard, which
    gets its ``confluent-kafka`` from requirements.txt, not from the broker.
    """
    if isinstance(partitions, dict):
        return list(partitions.keys())
    ids: list[int] = []
    for partition in partitions or []:
        # PartitionMetadata exposes .id; fall back to the mapping key form.
        ids.append(getattr(partition, "id", None) or partition["id"])
    return ids


def produced_counts(config: LakeConfig) -> dict[str, int]:
    """Records published per source topic, from partition high watermarks.

    High watermark is the next offset to be assigned, so the count of retained
    records in a partition is ``high - low``. Summing ``high`` alone would
    count offsets that compaction or retention has already discarded.
    """
    consumer = _consumer(config)
    counts: dict[str, int] = {}
    try:
        metadata = consumer.list_topics(timeout=10)
        for topic in config.topics:
            if topic not in metadata.topics:
                continue
            total = 0
            for partition in _partition_ids(metadata.topics[topic].partitions):
                low, high = consumer.get_watermark_offsets(
                    TopicPartition(topic, partition), timeout=10, cached=False
                )
                total += max(0, high - low)
            counts[topic] = total
    finally:
        consumer.close()
    return counts


def dlq_key_count(config: LakeConfig) -> int:
    """Distinct dead-letter keys currently retained on the DLQ topic.

    Counts keys, not messages: the DLQ is compacted on
    ``topic:partition:offset``, so a record that failed repeatedly occupies one
    key and many message slots.
    """
    consumer = _consumer(config)
    keys: set[bytes | None] = set()
    scanned = 0
    try:
        metadata = consumer.list_topics(topic=DLQ_TOPIC, timeout=10)
        topic_meta = metadata.topics.get(DLQ_TOPIC)
        if topic_meta is None or topic_meta.error is not None:
            return 0

        for partition in _partition_ids(topic_meta.partitions):
            low, high = consumer.get_watermark_offsets(
                TopicPartition(DLQ_TOPIC, partition), timeout=10, cached=False
            )
            if high <= low:
                continue
            consumer.assign([TopicPartition(DLQ_TOPIC, partition, low)])

            empties = 0
            while scanned < MAX_DLQ_SCAN:
                message = consumer.poll(0.5)
                if message is None:
                    # Compaction can leave holes, so an empty poll is not proof
                    # the end has been reached. Two in a row is close enough for
                    # a dashboard; the topic is tiny either way.
                    empties += 1
                    if empties >= 2:
                        break
                    continue
                if message.error():
                    if message.error().code() == KafkaError._PARTITION_EOF:
                        break
                    raise message.error()

                empties = 0
                scanned += 1
                keys.add(message.key())
                if message.offset() >= high - 1:
                    break
    finally:
        consumer.close()
    return len(keys)


def landed_record_count(config: LakeConfig) -> tuple[int, int]:
    """Return (rows, objects) in the raw lake.

    Counts rows from each Parquet footer rather than by scanning the data, so
    no column is ever decoded. The whole object is still fetched to reach the
    footer, which is acceptable only because lake files are capped at
    ``LAKE_BATCH_SIZE`` records; against a real S3 this would become a ranged
    tail read.

    The object count is reported alongside because a healthy lake with a
    surprising row count is usually a partitioning problem, and the two numbers
    together show whether files are being written sanely.
    """
    client = boto3.client("s3", endpoint_url=config.endpoint_url, region_name=config.region)
    rows = 0
    objects = 0
    token: Optional[str] = None
    while True:
        kwargs = {"Bucket": config.bucket, "MaxKeys": 1000}
        if token:
            kwargs["ContinuationToken"] = token
        listing = client.list_objects_v2(**kwargs)
        for item in listing.get("Contents", []):
            body = client.get_object(Bucket=config.bucket, Key=item["Key"])["Body"].read()
            rows += pq.read_metadata(pa.BufferReader(body)).num_rows
            objects += 1
        if not listing.get("IsTruncated"):
            return rows, objects
        token = listing.get("NextContinuationToken")


def collect(config: Optional[LakeConfig] = None) -> Reconciliation:
    """Gather all three figures, never raising.

    Each figure is attempted independently so one unavailable dependency does
    not blank out the others -- a broker outage should still show the state of
    the lake, which is exactly when that is worth knowing.
    """
    config = config or LakeConfig.from_env()
    unavailable: dict[str, str] = {}

    by_topic: dict[str, int] = {}
    try:
        by_topic = produced_counts(config)
    except Exception as exc:
        logger.warning("Could not read producer watermarks: %s", exc)
        unavailable["produced"] = str(exc)

    landed: Optional[int] = None
    objects: Optional[int] = None
    try:
        landed, objects = landed_record_count(config)
    except Exception as exc:
        logger.warning("Could not read the raw lake: %s", exc)
        unavailable["landed"] = str(exc)

    dead: Optional[int] = None
    try:
        dead = dlq_key_count(config)
    except Exception as exc:
        logger.warning("Could not read the DLQ topic: %s", exc)
        unavailable["dead_lettered"] = str(exc)

    return Reconciliation(
        produced=sum(by_topic.values()) if by_topic else None,
        produced_by_topic=by_topic,
        landed=landed,
        lake_objects=objects,
        dead_lettered=dead,
        unavailable=unavailable,
    )
