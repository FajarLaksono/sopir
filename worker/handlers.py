import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from backend.database import session_local
from backend.models import Runs, Telemetry

TELEMETRY_COLUMNS = (
    "tick", "ts", "steering", "throttle", "speed", "forward_vel", "cte",
    "pos_x", "pos_y", "pos_z",
    "vel_x", "vel_y", "vel_z",
    "accel_x", "accel_y", "accel_z",
    "gyro_x", "gyro_y", "gyro_z",
    "hit", "reward", "lap_count", "last_lap_time", "terminated",
)

def _decode(msg) -> dict:
    return json.loads(msg.value().decode("utf-8"))

def _handle_run_start(session: Session, record: dict) -> None:
    """Create the run row. Idempotent: a replayed marker is a no-op."""
    stmt = (
        pg_insert(Runs)
        .values(
            id=uuid.UUID(record["run_id"]),
            season_id=uuid.UUID(record["season_id"]),
            driver=record.get("driver", "random"),
            status="running",
        )
        .on_conflict_do_nothing(index_elements=["id"])
    )
    session.execute(stmt)

def _telemetry_row(record: dict) -> dict:
    row = {"run_id": uuid.UUID(record["run_id"])}
    row.update({col: record[col] for col in TELEMETRY_COLUMNS})
    return row

def _handle_run_end(session: Session, record: dict) -> int:
    run_id = uuid.UUID(record["run_id"])
    status = record.get("status", "completed")
    result = session.execute(
        update(Runs)
        .where(Runs.id == run_id, Runs.status == "running")
        .values(status=status, ended_at=datetime.now(timezone.utc))
    )
    if result.rowcount == 0:
        print(f"[ingest] run_end for unknown or already-closed run {str(run_id)[:8]}")
    return result.rowcount

def handle_batch(batch: list) -> dict:
    """One transaction for the whole batch. Returns per-type counts."""
    counts = {"run_start": 0, "telemetry": 0, "run_end": 0, "unknown": 0}
    session = session_local()
    buffer: list[dict] = []          # telemetry rows waiting for one bulk INSERT

    def flush() -> None:
        if not buffer:
            return
        stmt = (
            pg_insert(Telemetry)
            .values(buffer)
            .on_conflict_do_nothing(index_elements=["run_id", "tick"])
        )
        session.execute(stmt)
        buffer.clear()

    try:
        for msg in batch:
            record = _decode(msg)
            msg_type = record.get("type")
            if msg_type == "telemetry":
                buffer.append(_telemetry_row(record))
                counts["telemetry"] += 1
            else:
                flush()
                if msg_type == "run_start":
                    _handle_run_start(session, record)
                    counts["run_start"] += 1
                elif msg_type == "run_end":
                    _handle_run_end(session, record)
                    counts["run_end"] += 1
                else:
                    counts["unknown"] += 1
        flush()
        session.commit()             
    except Exception:
        session.rollback()
        raise                       
    finally:
        session.close()
    return counts
