from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import Column, Integer, String, Float, Uuid, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from worker.aggregates import kpi


TestBase = declarative_base()


class Telemetry(TestBase):
    __tablename__ = "telemetry"

    run_id = Column(Uuid(as_uuid=True), primary_key=True)  # portable UUID
    tick = Column(Integer, primary_key=True)
    ts = Column(Float, nullable=False)
    speed = Column(Float, nullable=False)
    cte = Column(Float, nullable=False)
    hit = Column(String(20), nullable=False)
    lap_count = Column(Integer, nullable=False)
    last_lap_time = Column(Float, nullable=False)


@pytest.fixture()
def session():
    engine = create_engine("sqlite://")          # in-memory, thrown away after
    TestBase.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        yield db
    finally:
        db.close()


def _add_run(session, run_id, ticks):
    """`ticks` is a list of dicts; missing keys get sane defaults."""
    for i, t in enumerate(ticks):
        session.add(Telemetry(
            run_id=run_id,
            tick=i,
            ts=t.get("ts", i * 0.1),
            speed=t.get("speed", 1.0),
            cte=t.get("cte", 0.0),
            hit=t.get("hit", "none"),
            lap_count=t.get("lap_count", 0),
            last_lap_time=t.get("last_lap_time", 0.0),
        ))
    session.commit()


def test_empty_run_is_zeros(session):
    result = kpi.compute(session, uuid4())
    assert result["ticks"] == 0
    assert result["pct_in_lane"] == 0.0
    assert result["collisions"] == 0
    assert result["offtrack_events"] == 0


def test_pct_in_lane_uses_magnitude(session):
    run_id = uuid4()
    # 3 ticks in lane (|cte| < 0.5), 1 out of lane -> 75.0 %
    _add_run(session, run_id, [
        {"cte": 0.1}, {"cte": -0.4}, {"cte": 0.49}, {"cte": -0.6},
    ])
    result = kpi.compute(session, run_id)
    assert result["ticks"] == 4
    assert result["pct_in_lane"] == 75.0


def test_collision_streak_counts_once(session):
    run_id = uuid4()
    # one hit lasting three ticks  ->  1 collision
    _add_run(session, run_id, [
        {"hit": "none"}, {"hit": "pole"}, {"hit": "pole"}, {"hit": "pole"},
        {"hit": "none"}, {"hit": "cone"},   # second, separate collision
    ])
    assert kpi.count_collisions(session, run_id) == 2


def test_offtrack_crossings(session):
    run_id = uuid4()
    # in -> out (1) -> in -> out (2)
    _add_run(session, run_id, [
        {"cte": 0.2}, {"cte": 1.5}, {"cte": 1.6}, {"cte": 0.3}, {"cte": 1.1},
    ])
    assert kpi.count_offtrack(session, run_id) == 2


def test_lap_times_recorded_on_increase(session):
    run_id = uuid4()
    _add_run(session, run_id, [
        {"lap_count": 0, "last_lap_time": 0.0},
        {"lap_count": 1, "last_lap_time": 12.34},
        {"lap_count": 1, "last_lap_time": 12.34},
        {"lap_count": 2, "last_lap_time": 11.10},
    ])
    result = kpi.compute(session, run_id)
    assert result["laps"] == 2
    assert result["lap_times"] == [12.3, 11.1]
