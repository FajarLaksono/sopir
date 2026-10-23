
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.functions import count

from backend.models import Telemetry

IN_LANE_CTE = 0.5      # |cte| < 0.5 counts as "in lane"
OFFTRACK_CTE = 1.0     # crossing <1.0 -> >=1.0 is an offtrack event


def _abs_cte(expr):
    return func.abs(expr)


def count_collisions(session: Session, run_id: UUID) -> int:
    """A collision = the START of a streak where hit != 'none'.

    Read the ticks in order; whenever the previous hit was 'none' (or there
    was no previous tick) and the current one isn't, that's one event. A hit
    lasting ten ticks is therefore ONE collision.
    """
    rows = session.execute(
        select(Telemetry.hit)
        .where(Telemetry.run_id == run_id)
        .order_by(Telemetry.tick)
    ).scalars().all()

    events = 0
    prev_hit = "none"
    for hit in rows:
        if hit != "none" and prev_hit == "none":
            events += 1
        prev_hit = hit
    return events


def count_offtrack(session: Session, run_id: UUID) -> int:
    """Times |cte| crosses from < OFFTRACK_CTE to >= OFFTRACK_CTE."""
    rows = session.execute(
        select(func.abs(Telemetry.cte))
        .where(Telemetry.run_id == run_id)
        .order_by(Telemetry.tick)
    ).scalars().all()

    events = 0
    prev_off = False
    for abs_cte in rows:
        off = abs_cte >= OFFTRACK_CTE
        if off and not prev_off:
            events += 1
        prev_off = off
    return events


def lap_times(session: Session, run_id: UUID) -> list[float]:
    """Lap times, in the order laps completed.

    `lap_count` increments when a lap finishes; the matching `last_lap_time`
    is that lap's duration. We walk ticks in order and append whenever
    `lap_count` grows (rounding to 1 decimal to match the JSON example).
    """
    rows = session.execute(
        select(Telemetry.lap_count, Telemetry.last_lap_time)
        .where(Telemetry.run_id == run_id)
        .order_by(Telemetry.tick)
    ).all()

    times: list[float] = []
    last_count = 0
    for lap_count, last_lap_time in rows:
        if lap_count > last_count and last_lap_time > 0:
            times.append(round(last_lap_time, 1))
        last_count = lap_count
    return times


def compute(session: Session, run_id: UUID) -> dict:
    """The full report card. Returns a plain dict for JSONB storage."""
    agg = session.execute(
        select(
            count().label("ticks"),
            func.min(Telemetry.ts).label("ts_min"),
            func.max(Telemetry.ts).label("ts_max"),
            func.max(Telemetry.lap_count).label("laps"),
            func.avg(_abs_cte(Telemetry.cte)).label("mean_abs_cte"),
            func.max(_abs_cte(Telemetry.cte)).label("max_abs_cte"),
            func.avg(Telemetry.speed).label("avg_speed"),
            func.max(Telemetry.speed).label("max_speed"),
        ).where(Telemetry.run_id == run_id)
    ).one()

    ticks = agg.ticks or 0
    if ticks == 0:
        # A run_end with no telemetry (e.g. crashed before the first batch).
        return {
            "duration_s": 0.0, 
            "ticks": 0, 
            "laps": 0, 
            "lap_times": [],
            "mean_abs_cte": 0.0, 
            "max_abs_cte": 0.0, 
            "pct_in_lane": 0.0,
            "avg_speed": 0.0, 
            "max_speed": 0.0,
            "collisions": 0, 
            "offtrack_events": 0,
        }

    in_lane = session.execute(
        select(count()).where(
            Telemetry.run_id == run_id,
            _abs_cte(Telemetry.cte) < IN_LANE_CTE,
        )
    ).scalar_one()
    pct_in_lane = round(100.0 * in_lane / ticks, 1)

    duration_s = round(float(agg.ts_max - agg.ts_min), 1) if agg.ts_max else 0.0

    return {
        "duration_s": duration_s,
        "ticks": ticks,
        "laps": agg.laps or 0,
        "lap_times": lap_times(session, run_id),
        "mean_abs_cte": round(float(agg.mean_abs_cte or 0.0), 3),
        "max_abs_cte": round(float(agg.max_abs_cte or 0.0), 3),
        "pct_in_lane": pct_in_lane,
        "avg_speed": round(float(agg.avg_speed or 0.0), 3),
        "max_speed": round(float(agg.max_speed or 0.0), 3),
        "collisions": count_collisions(session, run_id),
        "offtrack_events": count_offtrack(session, run_id),
    }
