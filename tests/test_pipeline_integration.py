"""End-to-end reconciliation of the raw lake against Kafka.

Marked ``integration``, so pytest deselects it by default (see ``pytest.ini``).
It needs the real streaming stack -- Redpanda, the Schema Registry, LocalStack
and a running lake writer -- because the claims it makes are only meaningful
against real brokers and a real object store.

What it asserts, and why none of it is redundant with the unit tests in
``tests/test_lake_writer.py``:

1. **Nothing is lost.** Every ``event_id`` produced is readable back out of the
   lake. The unit tests stub the object store; only this can catch a topic that
   was never subscribed, a record that serializes but deserializes to nothing, or
   a DLQ route that silently drops.

2. **A replay is safe.** A second consumer group with no committed offsets starts
   from ``earliest`` and reconsumes every record ever produced.

   This test originally asserted that a replay rewrites the same object keys and
   therefore leaves the row count untouched. **That was wrong, and running this
   test is how it was found.** Object keys embed the offset range buffered when
   the flush fired, so a replay re-batches on its own timing, writes a *different*
   key, and duplicates every record -- measured here at 520 rows growing to 1040.
   Bronze is append-only and is *expected* to gain rows.

   So the assertion is now the invariant that genuinely holds, and the reason
   silver can rely on it:

       a replay adds rows, never loses them, never invents an ``event_id``,
       and never changes the payload behind one

   That is what makes ``event_id`` a safe dedupe key downstream, and it is a
   stronger statement than "the count did not move", because a silent payload
   rewrite would also leave the count unchanged.

The replay is driven by pointing a second ``LakeWriter`` at a different
``group_id`` rather than by restarting the first one. A restart is the weaker
test -- if the writer stopped cleanly it had already committed, so there is
nothing to replay and the assertion passes trivially. A fresh group guarantees
the whole topic is re-read, which is the case that actually exercises this.

Counts are measured as deltas against a baseline taken before this module
produces anything, so the suite is safe to re-run against a stack that already
has data in it (see ``scripts/integration_check.sh --keep``).
"""

from __future__ import annotations

import signal
import threading
import time
import uuid
from dataclasses import replace
from typing import Any

import boto3
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

pytestmark = pytest.mark.integration

TOPICS = [
    "sopir.veh.can.v1",
    "sopir.veh.gnss.v1",
    "sopir.veh.events.v1",
    "sopir.sim.telemetry.v1",
]

# Small on purpose. These tests are about the ordering and identity guarantees,
# not throughput, and a few hundred records keep a run to well under a minute.
RECORDS_PER_TOPIC = 40

# Generous, because the writer flushes on a timer and CI machines are slow, but
# still bounded so a genuine failure reports instead of hanging.
SETTLE_TIMEOUT_SEC = 90.0


def _payloads(topic: str, run_id: str) -> list[dict[str, Any]]:
    """Deterministic, schema-valid records for one topic."""
    now_ms = 1_700_000_000_000
    records = []
    for i in range(RECORDS_PER_TOPIC):
        if topic == "sopir.veh.can.v1":
            records.append(
                {
                    "schema_version": "1.0.0",
                    "event_id": f"can-{run_id}-{i}",
                    "vehicle_id": f"veh_{i % 5:03d}",
                    "captured_at": now_ms + i,
                    "rpm": 1000.0 + i,
                    "wheel_speed": 12.0 + i * 0.1,
                    "brake_pressure": 0.0,
                    "steering_angle": 1.0,
                    "battery_soc": 90.0,
                }
            )
        elif topic == "sopir.veh.gnss.v1":
            records.append(
                {
                    "schema_version": "1.0.0",
                    "event_id": f"gnss-{run_id}-{i}",
                    "vehicle_id": f"veh_{i % 5:03d}",
                    "captured_at": now_ms + i,
                    "latitude": -6.2 + i * 1e-5,
                    "longitude": 106.8 + i * 1e-5,
                    "altitude": 50.0,
                    "fix_quality": 3,
                    "hdop": 0.8,
                    "satellites_tracked": 14,
                }
            )
        elif topic == "sopir.veh.events.v1":
            records.append(
                {
                    "schema_version": "1.0.0",
                    "event_id": f"evt-{run_id}-{i}",
                    "vehicle_id": f"veh_{i % 5:03d}",
                    "captured_at": now_ms + i,
                    "run_id": run_id,
                    "event_code": "lane_change_start",
                    # Enum, not a free string. Adding a symbol here would break
                    # every deployed reader, which is why
                    # tests/test_schema_evolution.py pins the symbol set.
                    "severity": "info",
                    "source_ecu": "adas_ctrl",
                    "details": "deterministic integration event",
                }
            )
        else:
            records.append(
                {
                    "schema_version": "1.0.0",
                    "event_id": f"sim-{run_id}-{i}",
                    "vehicle_id": f"veh_{i % 5:03d}",
                    "captured_at": now_ms + i,
                    "run_id": run_id,
                    "step": i,
                    "x": 100.0 + i,
                    "y": 2.0,
                    "speed": 13.0,
                    "angle": 90.0,
                    "lane_id": "edge_0",
                }
            )
    return records


@pytest.fixture(scope="module")
def accounting() -> dict[str, Any]:
    """Produce a known batch, wait for the lake, and return before/after deltas.

    Measured as a *delta* rather than an absolute count. CI always brings up a
    fresh stack so absolute totals would work there, but
    ``scripts/integration_check.sh --keep`` deliberately leaves the stack
    running, and a second run against the same topics would then see its own
    records plus the previous run's. Comparing deltas keeps the assertion about
    what this test did rather than about the history of the bucket.
    """
    from dashboard.pipeline_stats import collect
    from streaming.producer import create_producer

    expected = len(TOPICS) * RECORDS_PER_TOPIC

    # Read the baseline first, so nothing this fixture produces can be counted
    # as pre-existing.
    before = collect()

    run_id = f"integ{uuid.uuid4().hex[:16]}"
    produced_event_ids: set[str] = set()
    producer = create_producer()
    try:
        for topic in TOPICS:
            for record in _payloads(topic, run_id):
                # Keyed by run_id, as the architecture specifies, so all of this
                # run's records land in one partition and windowing stays sane.
                producer.produce(topic, run_id, record)
                produced_event_ids.add(record["event_id"])
            producer.flush(timeout=20.0)
    finally:
        producer.close()

    deadline = time.monotonic() + SETTLE_TIMEOUT_SEC
    while time.monotonic() < deadline:
        after = collect()
        landed = (after.landed or 0) - (before.landed or 0)
        if landed >= expected:
            break
        time.sleep(2.0)
    else:
        pytest.fail(
            f"lake did not gain {expected} records within {SETTLE_TIMEOUT_SEC}s; "
            f"baseline={before} last={collect()}"
        )

    assert not after.unavailable, f"could not read the pipeline: {after.unavailable}"

    return {
        "run_id": run_id,
        "expected": expected,
        "produced_event_ids": produced_event_ids,
        "produced": (after.produced or 0) - (before.produced or 0),
        "landed": (after.landed or 0) - (before.landed or 0),
        "dead_lettered": (after.dead_lettered or 0) - (before.dead_lettered or 0),
        "absolute": after,
    }


class TestReconciliation:
    def test_every_produced_record_is_accounted_for(self, accounting):
        # Nothing sits in a gap between "we sent it" and "it is durably
        # somewhere". Measured as a delta over a clean produce, so this holds
        # exactly; after a replay the raw row count legitimately exceeds the
        # produced count, which is why this is a delta and why
        # TestReplayIsSafe asserts on distinct event_ids instead.
        assert accounting["produced"] == accounting["landed"] + accounting["dead_lettered"]

    def test_the_lake_holds_every_produced_record(self, accounting):
        # Stricter than the identity above. DLQ routes are expected to be zero
        # for well-formed input, so any record landing there means something
        # that should have been valid was rejected.
        assert accounting["dead_lettered"] == 0
        assert accounting["landed"] == accounting["produced"]

    def test_exactly_the_expected_number_of_records_was_produced(self, accounting):
        # Catches a producer silently dropping a topic, which would otherwise
        # show up only as a lower landed count.
        assert accounting["produced"] == accounting["expected"]

    def test_the_lake_is_partitioned_by_source_and_hour(self, accounting):
        # A lake that reconciles but writes one undifferentiated blob is not the
        # layout docs/architecture.md describes. Cheap to check, and it catches
        # a Hive-partitioning regression that the counts alone would miss.

        from streaming.lake_writer import LakeConfig

        config = LakeConfig.from_env()
        client = boto3.client("s3", endpoint_url=config.endpoint_url, region_name=config.region)
        keys = [
            item["Key"] for item in client.list_objects_v2(Bucket=config.bucket).get("Contents", [])
        ]
        assert keys, "lake is empty"
        for key in keys:
            assert key.startswith("source=")
            assert "/dt=" in key
            assert "/hour=" in key
            assert key.endswith(".parquet")


class TestReplayIsSafe:
    """A replay must never lose data or corrupt a payload.

    It *will* add rows, and that is the accepted design: bronze is append-only
    and the lake writer's object keys are not an idempotency mechanism. What
    silver depends on is weaker and more specific -- nothing disappears, nothing
    new is invented, and nothing already written is silently rewritten. Those
    three are what make ``event_id`` safe to dedupe on.
    """

    @staticmethod
    def _lake_rows(config) -> dict[str, list[dict]]:
        """Read every landed row, grouped by ``event_id``.

        Byte-level comparison matters here. A duplicate row that differs from the
        original would pass an event-id check and still be a real bug, so the
        whole payload is compared, not just the key.
        """

        client = boto3.client("s3", endpoint_url=config.endpoint_url, region_name=config.region)
        rows: dict[str, list[dict]] = {}
        for item in client.list_objects_v2(Bucket=config.bucket).get("Contents", []):
            body = client.get_object(Bucket=config.bucket, Key=item["Key"])["Body"].read()
            # read_table wants a file-like source; the buffer is a Parquet file
            # written to S3, not a bare byte string.
            table = pq.read_table(pa.BufferReader(body))
            for record in table.to_pylist():
                rows.setdefault(record["event_id"], []).append(record)
        return rows

    def _replay_everything(self, target: int) -> None:
        """Reconsume every record on the topics under a fresh consumer group."""
        from streaming.lake_writer import LakeConfig, LakeWriter
        from streaming.observability import Observability

        config = LakeConfig.from_env()
        replay_config = replace(config, group_id=f"{config.group_id}-replay-{uuid.uuid4().hex[:8]}")

        # start() installs SIGTERM/SIGINT handlers, which Python only permits on
        # the main thread, so the writer runs here and a watchdog thread asks it
        # to stop. Driving it through the real signal path also exercises the
        # drain-on-shutdown branch.
        #
        # METRICS_PORT=0 in the test service's environment is what keeps this
        # second writer from fighting the real lake_writer for :9101; the
        # observability contract is that losing the listener degrades to
        # in-process collection rather than failing the writer.
        writer = LakeWriter(config=replay_config, obs=Observability("lake_writer"))

        # Replayed against the topics' own high watermarks, not this run's
        # expected count: a fresh group starts at `earliest`, so it re-reads
        # every record ever produced, including those from earlier runs.
        assert target > 0, "no records on the topics, so a replay proves nothing"

        def watchdog() -> None:
            deadline = time.monotonic() + SETTLE_TIMEOUT_SEC
            while time.monotonic() < deadline:
                # consumed counts every record the replay read, buffered or not,
                # so reaching the target means the whole topic was re-read and
                # only the flush is outstanding.
                if writer.stats.consumed >= target:
                    break
                time.sleep(1.0)
            signal.raise_signal(signal.SIGTERM)

        thread = threading.Thread(target=watchdog, daemon=True)
        thread.start()
        writer.start()
        thread.join(timeout=30)

        assert writer.stats.consumed >= target, (
            f"replay consumed {writer.stats.consumed} of {target} records"
        )
        assert writer.stats.written > 0, "replay wrote nothing, so it proved nothing"

    def test_a_replay_never_loses_or_corrupts_a_record(self, accounting):
        from streaming.lake_writer import LakeConfig

        config = LakeConfig.from_env()
        before = self._lake_rows(config)
        assert before, "lake is empty, so a replay proves nothing"

        target = sum((accounting["absolute"].produced_by_topic or {}).values())
        self._replay_everything(target)

        after = self._lake_rows(config)

        # 1. Nothing disappeared. Every event_id present before is still there.
        missing = set(before) - set(after)
        assert not missing, f"replay lost {len(missing)} records, e.g. {sorted(missing)[:3]}"

        # 2. Nothing new was invented. A replay can only re-derive ids that were
        #    already on a topic, so this catches a writer fabricating rows or
        #    attributing a batch to the wrong partition.
        assert set(after) - set(before) <= set(accounting["produced_event_ids"]), (
            "replay introduced event_ids that were never produced"
        )

        # 3. Nothing already written was rewritten. Every surviving copy of an
        #    id must be byte-identical to the original; this is the condition
        #    that makes first-write-wins dedupe on event_id safe.
        divergent = [
            event_id
            for event_id in set(before) & set(after)
            if any(candidate != before[event_id][0] for candidate in after[event_id])
        ]
        assert not divergent, (
            f"replay produced divergent payloads for {len(divergent)} event_ids, "
            f"e.g. {sorted(divergent)[:3]}"
        )

    def test_a_replay_only_ever_adds_duplicate_rows(self, accounting):
        """Documents the accepted cost, so a future regression cannot hide it.

        If this ever starts failing because the row count *stopped* growing, that
        is not a win -- it would mean flush timing became deterministic, which
        would be a genuine behaviour change worth knowing about rather than a
        passing test to enjoy.
        """
        from streaming.lake_writer import LakeConfig

        config = LakeConfig.from_env()
        before = self._lake_rows(config)
        distinct_before = len(before)

        target = sum((accounting["absolute"].produced_by_topic or {}).values())
        self._replay_everything(target)

        after = self._lake_rows(config)

        # Distinct ids are unchanged: that is the guarantee silver relies on.
        assert len(after) == distinct_before

        # Raw rows do grow, because object keys embed the offset range buffered
        # at flush time and a replay re-batches differently.
        duplicated = sum(len(copies) - 1 for copies in after.values())
        assert duplicated > 0, (
            "expected the replay to duplicate rows; if it did not, object keys "
            "have become deterministic and the idempotency discussion in "
            "docs/architecture.md needs revisiting"
        )
