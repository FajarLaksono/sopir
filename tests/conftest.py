"""Shared test fixtures for evaluation module."""

from __future__ import annotations

from typing import Any

import pytest


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
