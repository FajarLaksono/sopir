"""Shared test fixtures for evaluation module."""

from __future__ import annotations

import os
from typing import Any

import pytest


def _database_reachable() -> bool:
    """True when a Postgres is actually there, without raising."""
    url = os.getenv("DATABASE_URL")
    if not url:
        return False
    try:
        from sqlalchemy import create_engine

        engine = create_engine(url, pool_pre_ping=True, connect_args={"connect_timeout": 3})
        try:
            with engine.connect() as connection:
                connection.exec_driver_sql("SELECT 1")
        finally:
            engine.dispose()
    except Exception:
        return False
    return True


@pytest.fixture(scope="session", autouse=True)
def database_schema():
    """Create the SQLAlchemy tables once per session, if a database exists.

    This used to happen by accident: ``tests/test_backend_observability.py``
    enters the FastAPI lifespan, which calls ``create_all``, and because that
    file sorts before ``test_processor.py`` alphabetically the tables happened to
    be there by the time the processor tests ran. Run the processor tests alone
    against an empty database and every one of them errored on a missing table.

    Making it explicit here means the suite does not depend on collection order,
    and it degrades to a no-op when ``DATABASE_URL`` is unset or unreachable --
    which is what lets the fast CI tier run the pure unit tests with no database
    at all.
    """
    if not _database_reachable():
        yield None
        return

    from backend.database import Base, engine

    # create_all only, never drop_all: DATABASE_URL may well be pointing at a
    # developer's local compose Postgres holding real scenarios and runs, and a
    # teardown that dropped every table would destroy it. create_all is
    # idempotent, so leaving the schema in place costs nothing and the next run
    # still gets what it needs.
    Base.metadata.create_all(bind=engine)
    yield engine


@pytest.fixture
def ego_telemetry() -> list[dict[str, Any]]:
    """Ego vehicle driving straight at 10 m/s for 100 steps."""
    return [
        {
            "step": i,
            "vehicle_id": "ego",
            "x": float(i),
            "y": 0.0,
            "speed": 10.0,
            "angle": 90.0,
            "lane_id": "E0_0",
        }
        for i in range(100)
    ]


@pytest.fixture
def collision_telemetry() -> list[dict[str, Any]]:
    """Two vehicles on collision course - ego at x=0..49, traffic at x=50..1.
    Different speeds so TTC can be computed (ego 15 m/s, traffic 10 m/s)."""
    return [
        {
            "step": i,
            "vehicle_id": "ego",
            "x": float(i * 1.5),
            "y": 0.0,
            "speed": 15.0,
            "angle": 90.0,
            "lane_id": "E0_0",
        }
        for i in range(50)
    ] + [
        {
            "step": i,
            "vehicle_id": "traffic",
            "x": 50.0 - float(i * 1.0),
            "y": 0.0,
            "speed": 10.0,
            "angle": 270.0,
            "lane_id": "E0_1",
        }
        for i in range(50)
    ]


@pytest.fixture
def low_ttc_telemetry() -> list[dict[str, Any]]:
    """Close following at high speed - TTC ~0.5s (dist=10m, v_rel=20m/s).
    Lead at 30 m/s, ego at 10 m/s -> v_rel = 20 m/s, dist = 10m -> TTC = 0.5s"""
    return [
        {
            "step": i,
            "vehicle_id": "ego",
            "x": float(i * 10),
            "y": 0.0,
            "speed": 10.0,
            "angle": 90.0,
            "lane_id": "E0_0",
        }
        for i in range(50)
    ] + [
        {
            "step": i,
            "vehicle_id": "lead",
            "x": float(i * 10) + 10.0,
            "y": 0.0,
            "speed": 30.0,
            "angle": 90.0,
            "lane_id": "E0_0",
        }
        for i in range(50)
    ]


@pytest.fixture
def speed_violation_telemetry() -> list[dict[str, Any]]:
    """Ego vehicle exceeding 15 m/s for 20 steps."""
    return [
        {
            "step": i,
            "vehicle_id": "ego",
            "x": float(i * 20),
            "y": 0.0,
            "speed": 20.0,
            "angle": 90.0,
            "lane_id": "E0_0",
        }
        for i in range(20)
    ] + [
        {
            "step": i + 20,
            "vehicle_id": "ego",
            "x": float((i + 20) * 10),
            "y": 0.0,
            "speed": 10.0,
            "angle": 90.0,
            "lane_id": "E0_0",
        }
        for i in range(80)
    ]


@pytest.fixture
def lane_deviation_telemetry() -> list[dict[str, Any]]:
    """Ego vehicle changing lanes frequently without signaling."""
    telemetry = []
    for i in range(100):
        lane = "E0_0" if i % 10 < 5 else "E0_1"
        telemetry.append(
            {
                "step": i,
                "vehicle_id": "ego",
                "x": float(i),
                "y": 0.0 if lane == "E0_0" else 3.5,
                "speed": 10.0,
                "angle": 90.0,
                "lane_id": lane,
            }
        )
    return telemetry


@pytest.fixture
def clean_telemetry() -> list[dict[str, Any]]:
    """Clean scenario - ego at safe speed, no conflicts."""
    return [
        {
            "step": i,
            "vehicle_id": "ego",
            "x": float(i * 10),
            "y": 0.0,
            "speed": 10.0,
            "angle": 90.0,
            "lane_id": "E0_0",
        }
        for i in range(100)
    ] + [
        {
            "step": i,
            "vehicle_id": "traffic",
            "x": float(i * 10) + 100.0,
            "y": 0.0,
            "speed": 10.0,
            "angle": 90.0,
            "lane_id": "E0_0",
        }
        for i in range(100)
    ]
