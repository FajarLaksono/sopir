"""Unit tests for raw lake path construction, batch id determinism, and the
write-then-commit ordering.

Three layers, deliberately:

- pure path and batch-id logic;
- the ordering of the two operations that make at-least-once delivery safe,
  tested with hand-rolled stubs so no broker or object store is involved;
- the envelope validation both consumers apply before buffering a record.

The ordering tests are the ones worth having. Everything else in Phase 2.3 was
verified by hand against a live stack, and the interesting claim -- that the
object write happens strictly before the offset commit -- was never asserted
anywhere. Nothing about that ordering is visible from reading the function; it
is only visible from watching what happens when the write fails.

Note what these tests deliberately do *not* claim. Object keys are not an
idempotency mechanism; see ``TestBatchIdDeterminism`` and
``tests/test_pipeline_integration.py``.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from streaming.lake_paths import (
    build_partition,
    event_datetime,
    normalize_key,
    object_key,
)
from streaming.lake_writer import REQUIRED_FIELDS, Batch, LakeConfig, LakeWriter, _validate


class TestEventTime:
    def test_epoch_millis_becomes_utc_datetime(self):
        moment = event_datetime(1_700_000_000_000)
        assert moment == datetime(2023, 11, 14, 22, 13, 20, tzinfo=timezone.utc)

    def test_sub_second_precision_is_preserved(self):
        moment = event_datetime(1_700_000_000_123)
        assert moment.microsecond == 123_000


class TestNormalizeKey:
    def test_unsafe_characters_are_replaced(self):
        assert normalize_key("veh/../etc") == "veh_.._etc"

    def test_empty_value_falls_back(self):
        assert normalize_key("") == "_unknown_"

    def test_safe_value_is_unchanged(self):
        assert normalize_key("veh_001") == "veh_001"


class TestBuildPartition:
    def test_source_and_hour_are_derived_from_event_time(self):
        partition = build_partition("can", 1_700_000_000_000)
        assert partition.source == "can"
        assert partition.dt == "2023-11-14"
        assert partition.hour == "22"

    def test_prefix_is_hive_style(self):
        partition = build_partition("gnss", 1_700_000_000_000)
        assert partition.prefix == "source=gnss/dt=2023-11-14/hour=22"

    def test_hive_values_are_exposed_for_column_pruning(self):
        partition = build_partition("sim", 1_700_000_000_000)
        assert partition.hive_values == {
            "source": "sim",
            "dt": "2023-11-14",
            "hour": "22",
        }

    def test_records_in_different_hours_land_in_different_partitions(self):
        # 22:13:20 UTC, then 46m41s later, which crosses into the next hour.
        base = 1_700_000_000_000
        before = build_partition("can", base)
        after = build_partition("can", base + 2_800_100)
        assert before.hour == "22"
        assert after.hour == "23"
        assert before.prefix != after.prefix

    def test_same_hour_same_day_shares_a_partition(self):
        base = 1_700_000_000_000
        first = build_partition("can", base)
        second = build_partition("can", base + 60_000)
        assert first.prefix == second.prefix


class TestObjectKey:
    def test_key_contains_partition_and_batch_id(self):
        partition = build_partition("can", 1_700_000_000_000)
        key = object_key(partition, "00-000000000001-000000000200")
        assert key == ("source=can/dt=2023-11-14/hour=22/part-00-000000000001-000000000200.parquet")


class TestBatchIdDeterminism:
    """What a deterministic batch id does and does not buy.

    The id is a function of the offset range, so it is stable for a given range
    and traceable back to the offsets it covers. It is *not* an idempotency
    guarantee: a replay re-batches on its own timing, so the same records can
    arrive under a different range and therefore a different key.
    """

    def _batch(self, first: int, last: int, rows: int = 1) -> Batch:
        return Batch(
            topic="sopir.veh.can.v1",
            source="can",
            partition=3,
            rows=[{} for _ in range(rows)],
            first_offset=first,
            last_offset=last,
        )

    def test_batch_id_is_stable_for_the_same_offset_range(self):
        assert self._batch(10, 20).batch_id == self._batch(10, 20).batch_id

    def test_batch_id_differs_for_a_different_offset_range(self):
        assert self._batch(10, 20).batch_id != self._batch(10, 21).batch_id

    def test_partition_is_included_so_different_partitions_do_not_collide(self):
        partition = build_partition("can", 1_700_000_000_000)
        left = Batch("t", "can", 0, [], 10, 20).batch_id
        right = Batch("t", "can", 1, [], 10, 20).batch_id
        assert object_key(partition, left) != object_key(partition, right)

    def test_count_reflects_rows(self):
        assert self._batch(0, 0, rows=17).count == 17

    def test_rebatching_the_same_records_under_a_different_range_changes_the_key(self):
        """The duplication mechanism, pinned as a unit test.

        This is the behaviour that makes bronze at-least-once rather than
        exactly-once, and it was previously asserted to be the opposite. Flush
        timing decides where a batch boundary falls, so the same 40 records can
        be written as offsets 0..39 on one pass and 1..39 on the next, landing in
        two objects. ``tests/test_pipeline_integration.py`` shows the same thing
        end to end; this pins the key-level mechanism without a broker.

        If a future change makes batching deterministic (for example, flushing
        only on offset-aligned strides), this test is the thing that fails and
        says so.
        """
        partition = build_partition("can", 1_700_000_000_000)

        first_pass = object_key(partition, self._batch(0, 39, rows=40).batch_id)
        # Offset 0 already landed in an earlier, separately flushed object.
        replay = object_key(partition, self._batch(1, 39, rows=39).batch_id)

        assert first_pass != replay


# --- Write-then-commit ordering -------------------------------------------------
#
# _flush_one is the only place in the pipeline where an offset becomes durable,
# so the sequence of its side effects is the whole correctness story: if the
# commit ever moved above the write, a crash between them would skip records
# and nothing downstream could detect it.


class _RecordingLake:
    """Stands in for ParquetLakeWriter, recording when it was called."""

    def __init__(self, calls: list, fail: bool = False):
        self.calls = calls
        self._fail = fail

    def write(self, batch: Batch) -> list[str]:
        self.calls.append("write")
        if self._fail:
            raise OSError("object store refused the write")
        return [f"key/{batch.batch_id}.parquet"]


class _RecordingConsumer:
    """Stands in for the Kafka consumer's offset machinery."""

    def __init__(self, calls: list):
        self.calls = calls
        self.stored: list = []
        self.commits: list = []

    def store_offsets(self, offsets=None):
        self.calls.append("store_offsets")
        self.stored.extend(offsets or [])

    def commit(self, asynchronous: bool = True):
        self.calls.append("commit")
        self.commits.append(asynchronous)


class _RecordingDlq:
    def __init__(self, calls: list):
        self.calls = calls
        self.routed: list = []

    def route(self, context, reason: str, stage: str, captured_at: int) -> None:
        self.calls.append("dlq")
        self.routed.append((context, reason, stage))


class _StubMetrics:
    def __init__(self):
        self.calls: list = []

    def record_written(self, kind, count=1):
        self.calls.append(("written", kind, count))

    def record_dlq(self, stage, count=1):
        self.calls.append(("dlq_metric", stage))

    def record_error(self, kind, count=1):
        self.calls.append(("error", kind))

    def record_window(self, records):
        self.calls.append(("window", records))

    def touch_progress(self):
        self.calls.append(("progress",))


class _StubObs:
    def __init__(self):
        self.metrics = _StubMetrics()


def _writer(lake: _RecordingLake, consumer: _RecordingConsumer, dlq: _RecordingDlq) -> LakeWriter:
    """A LakeWriter wired to stubs.

    Built with __new__ rather than the constructor on purpose: the constructor
    opens a real Consumer and Schema Registry client, and an ordering test that
    needs a broker running is not a unit test.
    """
    writer = LakeWriter.__new__(LakeWriter)
    writer.config = LakeConfig(batch_size=500, flush_interval_sec=10.0)
    writer.stats = _stub_stats()
    writer.obs = _StubObs()
    writer._lake = lake
    writer._consumer = consumer
    writer._dlq = dlq
    return writer


def _stub_stats():
    class _Stats:
        consumed = 0
        written = 0
        files_written = 0
        dlq_routed = 0
        batches = 0

    return _Stats()


def _pending(first: int = 10, last: int = 20, rows: int = 1):
    key = ("sopir.veh.can.v1", 0)
    batch = Batch(
        topic="sopir.veh.can.v1",
        source="can",
        partition=0,
        rows=[{} for _ in range(rows)],
        first_offset=first,
        last_offset=last,
    )
    return key, {key: batch}


class TestWriteBeforeCommit:
    def test_the_write_happens_before_the_commit(self):
        calls: list = []
        consumer = _RecordingConsumer(calls)
        writer = _writer(_RecordingLake(calls), consumer, _RecordingDlq(calls))
        key, pending = _pending()

        writer._flush_one(key, pending)

        durability_ops = [c for c in calls if c in ("write", "commit")]
        assert durability_ops == ["write", "commit"]

    def test_the_commit_is_synchronous(self):
        # An asynchronous commit could return before the broker had the offset,
        # which would reintroduce the same loss window under a different name.
        calls: list = []
        consumer = _RecordingConsumer(calls)
        writer = _writer(_RecordingLake(calls), consumer, _RecordingDlq(calls))
        key, pending = _pending()

        writer._flush_one(key, pending)

        assert consumer.commits == [False]

    def test_the_committed_offset_is_one_past_the_last_record(self):
        calls: list = []
        consumer = _RecordingConsumer(calls)
        writer = _writer(_RecordingLake(calls), consumer, _RecordingDlq(calls))
        key, pending = _pending(first=10, last=20)

        writer._flush_one(key, pending)

        stored = consumer.stored[0]
        assert stored.offset == 21
        assert stored.partition == 0
        assert stored.topic == "sopir.veh.can.v1"

    def test_a_failed_write_leaves_the_offsets_uncommitted(self):
        # The at-least-once guarantee, stated as an assertion: when the sink
        # refuses, the offset must not advance, so the batch replays.
        calls: list = []
        consumer = _RecordingConsumer(calls)
        writer = _writer(_RecordingLake(calls, fail=True), consumer, _RecordingDlq(calls))
        key, pending = _pending()

        writer._flush_one(key, pending)

        assert "write" in calls
        assert "commit" not in calls
        assert consumer.stored == []

    def test_a_failed_write_is_routed_to_the_dlq_as_sink_write(self):
        calls: list = []
        dlq = _RecordingDlq(calls)
        writer = _writer(_RecordingLake(calls, fail=True), _RecordingConsumer(calls), dlq)
        key, pending = _pending()

        writer._flush_one(key, pending)

        assert len(dlq.routed) == 1
        _context, reason, stage = dlq.routed[0]
        assert stage == "sink_write"
        assert "sink_write failed" in reason

    def test_a_failed_write_is_counted_as_an_error(self):
        calls: list = []
        writer = _writer(
            _RecordingLake(calls, fail=True), _RecordingConsumer(calls), _RecordingDlq(calls)
        )
        key, pending = _pending()

        writer._flush_one(key, pending)

        assert ("error", "sink_write") in writer.obs.metrics.calls

    def test_a_failed_write_does_not_advance_the_counters(self):
        # The reconcilable numbers must not claim records landed when they did
        # not, or the accounting on the dashboard drifts by exactly the amount
        # that was lost.
        calls: list = []
        writer = _writer(
            _RecordingLake(calls, fail=True), _RecordingConsumer(calls), _RecordingDlq(calls)
        )
        key, pending = _pending(rows=5)

        writer._flush_one(key, pending)

        assert writer.stats.written == 0
        assert writer.stats.batches == 0
        assert not any(call[0] == "written" for call in writer.obs.metrics.calls)

    def test_a_successful_flush_records_what_it_landed(self):
        calls: list = []
        writer = _writer(_RecordingLake(calls), _RecordingConsumer(calls), _RecordingDlq(calls))
        key, pending = _pending(rows=5)

        writer._flush_one(key, pending)

        assert writer.stats.written == 5
        assert writer.stats.files_written == 1
        assert writer.stats.batches == 1

    def test_an_empty_batch_neither_writes_nor_commits(self):
        calls: list = []
        writer = _writer(_RecordingLake(calls), _RecordingConsumer(calls), _RecordingDlq(calls))
        key = ("sopir.veh.can.v1", 0)

        writer._flush_one(key, {key: Batch("sopir.veh.can.v1", "can", 0, [], 10, 10)})

        assert "write" not in calls
        assert "commit" not in calls

    def test_the_batch_is_removed_from_pending_when_the_write_fails(self):
        # It is popped before the write is attempted, so a retry after a sink
        # outage has to come from the consumer replaying the offsets rather
        # than from this process still holding the rows.
        calls: list = []
        writer = _writer(
            _RecordingLake(calls, fail=True), _RecordingConsumer(calls), _RecordingDlq(calls)
        )
        key, pending = _pending()

        writer._flush_one(key, pending)

        assert pending == {}


class TestEnvelopeValidation:
    """Records missing the fields the lake depends on never reach the bucket."""

    def _record(self):
        return {
            "event_id": "evt-1",
            "vehicle_id": "veh_001",
            "captured_at": 1_700_000_000_000,
            "rpm": 2100.0,
        }

    def test_a_complete_record_is_accepted(self):
        assert _validate(self._record()) is None

    @pytest.mark.parametrize("field", REQUIRED_FIELDS)
    def test_a_missing_required_field_is_rejected(self, field):
        record = self._record()
        del record[field]
        with pytest.raises(ValueError, match=field):
            _validate(record)

    def test_a_non_dict_record_is_rejected(self):
        with pytest.raises(ValueError):
            _validate([1, 2, 3])

    def test_validation_mentions_the_field_by_name(self):
        # The reason string is what lands in the DLQ envelope's error_reason,
        # so an unlabelled message would be useless to whoever reads it later.
        record = self._record()
        del record["captured_at"]
        with pytest.raises(ValueError) as excinfo:
            _validate(record)
        assert "captured_at" in str(excinfo.value)
