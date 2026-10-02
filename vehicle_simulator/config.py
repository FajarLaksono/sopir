"""Configuration for vehicle log simulator."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class VehicleSpec:
    """Specification for a simulated vehicle."""

    vehicle_id: str
    # Physical parameters
    mass_kg: float = 1500.0
    wheel_radius_m: float = 0.3
    gear_ratio: float = 10.0
    max_rpm: float = 6000.0
    max_brake_pressure_kpa: float = 10000.0
    max_steering_angle_deg: float = 35.0
    battery_capacity_kwh: float = 60.0
    # Initial state
    initial_speed_mps: float = 0.0
    initial_heading_rad: float = 0.0
    initial_lat: float = 37.7749
    initial_lon: float = -122.4194
    initial_alt: float = 10.0
    initial_soc: float = 1.0


@dataclass
class SimConfig:
    """Simulation configuration."""

    vehicles: list[VehicleSpec] = field(default_factory=list)
    duration_sec: float = 120.0
    rate_hz: float = 10.0
    seed: int = 42
    # Kafka topics
    can_topic: str = "sopir.veh.can.v1"
    gnss_topic: str = "sopir.veh.gnss.v1"
    events_topic: str = "sopir.veh.events.v1"
    # Behavioral parameters
    throttle_change_rate: float = 0.5  # per second
    brake_ramp_time: float = 0.3  # seconds
    lane_change_duration: float = 3.0  # seconds
    lane_change_prob_per_step: float = 0.002
    event_prob_per_step: float = 0.0005
    event_cooldown_steps: int = 50
    low_battery_threshold: float = 0.2


def create_default_config(
    num_vehicles: int = 5,
    duration_sec: float = 120.0,
    rate_hz: float = 10.0,
    seed: int = 42,
) -> SimConfig:
    """Create a default config with N vehicles."""
    vehicles = []
    for i in range(num_vehicles):
        vehicles.append(
            VehicleSpec(
                vehicle_id=f"veh_{i:03d}",
                initial_speed_mps=10.0 + i * 2.0,
                initial_heading_rad=i * 0.1,
                initial_lat=37.7749 + i * 0.001,
                initial_lon=-122.4194 + i * 0.001,
            )
        )
    return SimConfig(
        vehicles=vehicles,
        duration_sec=duration_sec,
        rate_hz=rate_hz,
        seed=seed,
    )
