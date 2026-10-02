"""Hive-style partition path construction for the raw lake.

Layout, per the Phase 2 plan:

    s3://sopir-raw/source=can/dt=2026-10-01/hour=09/part-00000-<id>.parquet

Partitioning is by ``source`` and event time only. ``vehicle_id`` stays a column
rather than a partition key: partitioning by a high-cardinality field is how you
end up with one Parquet file per vehicle, which is the classic small-files
mistake and makes the lake unqueryable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

_SAFE_KEY = re.compile(r"[^A-Za-z0-9_=.-]")


def event_datetime(captured_at_ms: int) -> datetime:
    """Convert an epoch-millisecond timestamp to a UTC datetime."""
    return datetime.fromtimestamp(captured_at_ms / 1000, tz=timezone.utc)


def normalize_key(value: str) -> str:
    """Make a value safe to embed in an object key."""
    return _SAFE_KEY.sub("_", value) or "_unknown_"


@dataclass(frozen=True)
class LakePartition:
    """A resolved partition and the key it lives under."""

    source: str
    dt: str
    hour: str

    @property
    def prefix(self) -> str:
        return f"source={self.source}/dt={self.dt}/hour={self.hour}"

    @property
    def hive_values(self) -> dict[str, str]:
        """Partition columns, so Parquet readers can prune on them."""
        return {"source": self.source, "dt": self.dt, "hour": self.hour}


def build_partition(source: str, captured_at_ms: int) -> LakePartition:
    """Build the partition for a record from its source and event time."""
    moment = event_datetime(captured_at_ms)
    return LakePartition(
        source=normalize_key(source),
        dt=moment.strftime("%Y-%m-%d"),
        hour=moment.strftime("%H"),
    )


def object_key(partition: LakePartition, batch_id: str) -> str:
    """Build the full object key for one Parquet file.

    ``batch_id`` carries the Kafka offset range that produced the file, which
    makes the key stable for a *given* batch and therefore makes writes
    traceable back to the offsets they came from.

    It does not make the lake idempotent. The range reflects whatever was
    buffered when the flush fired, so the same records replayed under different
    timing produce a different ``batch_id`` and a second object. Two batches in
    the lake can legitimately cover the same offsets. Bronze is append-only and
    duplication is resolved downstream on ``event_id``, not here.
    """
    return f"{partition.prefix}/part-{batch_id}.parquet"
