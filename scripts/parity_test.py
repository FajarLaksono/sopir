"""Parity test: streamed evaluation must equal batch evaluation.

This is the gate for retiring ``TELEMETRY_SINK=postgres``. It runs one seeded
SUMO scenario down both paths and compares the results field by field:

    Path A (batch)    worker -> telemetry table -> compute_metrics
    Path B (streaming) worker -> Kafka -> processor -> stream_metrics

The comparison deliberately goes through ``evaluation/`` in both cases. The
batch path reads the SQL rows and calls the same pure function the processor
calls, so a disagreement can only come from the stream adapter: a dropped
record, a mis-projected field, or a window that closed early.

Usage:
    docker compose exec backend python scripts/parity_test.py
    docker compose exec backend python scripts/parity_test.py --tolerance 1e-9
"""

from __future__ import annotations

import argparse
import sys
import time
import uuid
from typing import Any, Optional

from sqlalchemy import delete, func, select

from backend.database import SessionLocal
from backend.models import StreamFailure, StreamMetrics, Telemetry
from evaluation.failure_detector import detect_failures
from evaluation.metrics import compute_metrics

FLOAT_FIELDS = ("min_ttc", "avg_speed")
INT_FIELDS = ("collision_count", "speed_violations", "lane_deviations")


def batch_metrics(run_id: uuid.UUID) -> dict[str, Any]:
    """Recompute metrics the way the evaluate endpoint does.

    Reads the ``telemetry`` table and feeds it to the pure function, which is
    what ``POST /metrics/runs/{id}/evaluate`` does internally.
    """
    db = SessionLocal()
    try:
        rows = (
            db.execute(
                select(Telemetry)
                .where(Telemetry.run_id == run_id)
                .order_by(Telemetry.step, Telemetry.vehicle_id)
            )
            .scalars()
            .all()
        )
    finally:
        db.close()

    telemetry = [
        {
            "vehicle_id": r.vehicle_id,
            "step": r.step,
            "x": r.x,
            "y": r.y,
            "speed": r.speed,
            "angle": r.angle,
            "lane_id": r.lane_id,
        }
        for r in rows
    ]
    return compute_metrics(telemetry)


def stream_metrics(run_id: uuid.UUID) -> Optional[dict[str, Any]]:
    """Read the processor's silver row for this run."""
    db = SessionLocal()
    try:
        row = db.execute(
            select(StreamMetrics).where(
                StreamMetrics.run_id == run_id, StreamMetrics.source == "sim"
            )
        ).scalar_one_or_none()
    finally:
        db.close()

    if row is None:
        return None
    return {
        "collision_count": row.collision_count,
        "min_ttc": row.min_ttc,
        "avg_speed": row.avg_speed,
        "speed_violations": row.speed_violations,
        "lane_deviations": row.lane_deviations,
        "ttc_per_step": row.ttc_per_step,
        "record_count": row.record_count,
    }


def stream_failures(run_id: uuid.UUID) -> list[dict[str, Any]]:
    db = SessionLocal()
    try:
        rows = (
            db.execute(
                select(StreamFailure).where(
                    StreamFailure.run_id == run_id, StreamFailure.source == "sim"
                )
            )
            .scalars()
            .all()
        )
    finally:
        db.close()
    return [{"severity": r.severity, "rule": r.rule, "details": r.details} for r in rows]


def telemetry_count(run_id: uuid.UUID) -> int:
    db = SessionLocal()
    try:
        return db.execute(
            select(func.count()).select_from(Telemetry).where(Telemetry.run_id == run_id)
        ).scalar_one()
    finally:
        db.close()


def compare(
    batch: dict[str, Any],
    streamed: dict[str, Any],
    tolerance: float,
) -> list[str]:
    """Return a list of human-readable differences; empty means parity."""
    problems: list[str] = []

    for name in INT_FIELDS:
        if batch[name] != streamed[name]:
            problems.append(f"{name}: batch={batch[name]} stream={streamed[name]}")

    for name in FLOAT_FIELDS:
        expected, actual = batch[name], streamed[name]
        if expected is None or actual is None:
            if expected is not actual:
                problems.append(f"{name}: batch={expected} stream={actual}")
        elif abs(expected - actual) > tolerance:
            problems.append(
                f"{name}: batch={expected!r} stream={actual!r} "
                f"delta={abs(expected - actual):.3e} > tol={tolerance:.1e}"
            )

    if batch["ttc_per_step"] != streamed["ttc_per_step"]:
        expected_len = len(batch["ttc_per_step"])
        actual_len = len(streamed["ttc_per_step"] or [])
        problems.append(f"ttc_per_step differs in length: batch={expected_len} stream={actual_len}")

    return problems


def compare_failures(batch: list[dict[str, Any]], streamed: list[dict[str, Any]]) -> list[str]:
    problems: list[str] = []
    batch_rules = sorted(f["rule"] for f in batch)
    stream_rules = sorted(f["rule"] for f in streamed)
    if batch_rules != stream_rules:
        problems.append(f"failure rules differ: batch={batch_rules} stream={stream_rules}")
        return problems

    by_rule_batch = {f["rule"]: f for f in batch}
    by_rule_stream = {f["rule"]: f for f in streamed}
    for rule, expected in by_rule_batch.items():
        actual = by_rule_stream[rule]
        if expected["severity"] != actual["severity"]:
            problems.append(
                f"{rule} severity: batch={expected['severity']} stream={actual['severity']}"
            )
        if expected["details"] != actual["details"]:
            problems.append(
                f"{rule} details: batch={expected['details']} stream={actual['details']}"
            )
    return problems


def wait_for_stream_row(
    run_id: uuid.UUID, timeout_sec: float, interval_sec: float = 2.0
) -> Optional[dict[str, Any]]:
    """Poll until the processor writes this run's silver row.

    The processor closes a run window on an idle timeout, so the row appears
    after the stream goes quiet rather than when the run finishes. Polling with
    a bounded timeout keeps this a test rather than a hang.
    """
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        row = stream_metrics(run_id)
        if row is not None:
            return row
        time.sleep(interval_sec)
    return None


def cleanup(run_id: uuid.UUID) -> None:
    """Remove this run's rows so the script can be re-run."""
    db = SessionLocal()
    try:
        db.execute(delete(StreamFailure).where(StreamFailure.run_id == run_id))
        db.execute(delete(StreamMetrics).where(StreamMetrics.run_id == run_id))
        db.execute(delete(Telemetry).where(Telemetry.run_id == run_id))
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", help="existing run_id to compare (skips waiting)")
    parser.add_argument(
        "--timeout",
        type=float,
        default=180.0,
        help="seconds to wait for the processor to close the window",
    )
    parser.add_argument(
        "--tolerance",
        type=float,
        default=1e-6,
        help="allowed float difference between batch and streamed metrics",
    )
    parser.add_argument(
        "--telemetry",
        type=int,
        default=0,
        help="expected telemetry rows; used to assert the stream saw them all",
    )
    args = parser.parse_args()

    if not args.run_id:
        print(
            "No --run-id supplied.\n"
            "Create a run with TELEMETRY_SINK=postgres so the telemetry table is "
            "populated,\nthen re-run the worker with TELEMETRY_SINK=kafka for the same "
            "run_id, and\npass that run_id here. See scripts/run_parity.sh.",
            file=sys.stderr,
        )
        return 2

    run_id = uuid.UUID(args.run_id)
    print(f"Run under test: {run_id}")

    expected_rows = args.telemetry or telemetry_count(run_id)
    if expected_rows == 0:
        print(
            "The telemetry table is empty for this run, so there is nothing to compare. "
            "The batch path must be populated first.",
            file=sys.stderr,
        )
        return 2

    print(f"Batch telemetry rows: {expected_rows}")
    batch = batch_metrics(run_id)
    batch_failures = detect_failures(batch)

    print(f"Waiting up to {args.timeout:.0f}s for the processor to close the window...")
    streamed = wait_for_stream_row(run_id, args.timeout)

    if streamed is None:
        print(
            "FAIL: no stream_metrics row appeared. Either the processor is not "
            "running,\nit never received records for this run, or the idle timeout "
            f"({args.timeout:.0f}s) has not elapsed.",
            file=sys.stderr,
        )
        cleanup(run_id)
        return 1

    print(f"Stream record_count: {streamed['record_count']}")
    if streamed["record_count"] != expected_rows:
        print(
            f"FAIL: record count differs: telemetry={expected_rows} "
            f"stream={streamed['record_count']}",
            file=sys.stderr,
        )
        cleanup(run_id)
        return 1

    problems = compare(batch, streamed, args.tolerance)
    streamed_failures = stream_failures(run_id)
    problems += compare_failures(batch_failures, streamed_failures)

    print("\n--- batch ---")
    for name in INT_FIELDS + FLOAT_FIELDS:
        print(f"  {name}: {batch[name]}")
    print(f"  failures: {[f['rule'] for f in batch_failures]}")

    print("\n--- stream ---")
    for name in INT_FIELDS + FLOAT_FIELDS:
        print(f"  {name}: {streamed[name]}")
    print(f"  failures: {[f['rule'] for f in streamed_failures]}")

    if problems:
        print("\nFAIL: batch and stream disagree", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        cleanup(run_id)
        return 1

    print("\nPASS: streamed evaluation matches batch evaluation exactly")
    cleanup(run_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
