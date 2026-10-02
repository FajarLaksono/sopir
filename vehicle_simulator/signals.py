"""Correlated vehicle signal generators."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Optional

from .config import VehicleSpec


@dataclass
class VehicleState:
    """Mutable vehicle state for simulation."""

    spec: VehicleSpec
    # Dynamic state
    speed_mps: float = 0.0
    heading_rad: float = 0.0
    lat: float = 0.0
    lon: float = 0.0
    alt: float = 0.0
    soc: float = 1.0
    rpm: float = 0.0
    wheel_speed_mps: float = 0.0
    brake_pressure_kpa: float = 0.0
    steering_angle_deg: float = 0.0
    throttle: float = 0.0
    # Derived / behavioral
    target_speed_mps: float = 0.0
    lane_offset_m: float = 0.0
    lane_change_progress: float = 0.0
    # Events
    last_event_step: int = -1000
    # RNG
    rng: Optional[random.Random] = None

    def __post_init__(self):
        if self.rng is None:
            self.rng = random.Random()
        self.speed_mps = self.spec.initial_speed_mps
        self.heading_rad = self.spec.initial_heading_rad
        self.lat = self.spec.initial_lat
        self.lon = self.spec.initial_lon
        self.alt = self.spec.initial_alt
        self.soc = self.spec.initial_soc
        self.target_speed_mps = self.spec.initial_speed_mps


def rpm_from_wheel_speed(wheel_speed_mps: float, spec: VehicleSpec, rng: random.Random) -> float:
    """Calculate engine RPM from wheel speed, with idle jitter when stationary."""
    if wheel_speed_mps <= 0:
        return rng.uniform(600, 900)  # idle RPM
    rpm = (wheel_speed_mps / (2 * math.pi * spec.wheel_radius_m)) * spec.gear_ratio * 60
    return min(max(rpm, 600), spec.max_rpm)


def wheel_speed_from_rpm(rpm: float, spec: VehicleSpec) -> float:
    """Calculate wheel speed from RPM (inverse of above)."""
    return (rpm / 60) * (2 * math.pi * spec.wheel_radius_m) / spec.gear_ratio


def update_powertrain(state: VehicleState, dt: float, config) -> None:
    """Update RPM, wheel speed, throttle, brake, SOC."""
    spec = state.spec

    # Throttle control toward target speed
    speed_error = state.target_speed_mps - state.speed_mps
    throttle_change = config.throttle_change_rate * dt
    if speed_error > 0:
        state.throttle = min(1.0, state.throttle + throttle_change)
        state.brake_pressure_kpa = max(
            0.0,
            state.brake_pressure_kpa - spec.max_brake_pressure_kpa * dt / config.brake_ramp_time,
        )
    elif speed_error < -0.5:
        state.throttle = max(0.0, state.throttle - throttle_change)
        # Apply brake if significantly over target
        if speed_error < -2.0:
            state.brake_pressure_kpa = min(
                spec.max_brake_pressure_kpa,
                state.brake_pressure_kpa
                + spec.max_brake_pressure_kpa * dt / config.brake_ramp_time,
            )
        else:
            state.brake_pressure_kpa = max(
                0.0,
                state.brake_pressure_kpa
                - spec.max_brake_pressure_kpa * dt / config.brake_ramp_time,
            )

    # Simple physics: acceleration from throttle/brake
    max_accel = 3.0  # m/s^2
    max_decel = 5.0  # m/s^2
    accel = (
        state.throttle * max_accel
        - (state.brake_pressure_kpa / spec.max_brake_pressure_kpa) * max_decel
    )

    # Add drag
    drag = 0.01 * state.speed_mps * abs(state.speed_mps)
    accel -= drag / spec.mass_kg

    state.speed_mps = max(0.0, state.speed_mps + accel * dt)

    # Update wheel speed and RPM
    state.wheel_speed_mps = state.speed_mps
    state.rpm = rpm_from_wheel_speed(state.wheel_speed_mps, spec, state.rng)

    # Battery SOC drain based on power
    power_kw = max(0.0, state.rpm / spec.max_rpm * 100)  # rough power estimate
    energy_kwh = power_kw * dt / 3600
    state.soc = max(0.0, state.soc - energy_kwh / spec.battery_capacity_kwh)


def update_steering(state: VehicleState, dt: float, config) -> None:
    """Update steering angle for lane changes."""
    spec = state.spec
    if state.lane_change_progress > 0:
        elapsed = config.lane_change_duration - state.lane_change_progress
        progress = min(1.0, elapsed / config.lane_change_duration)
        # S-curve steering profile
        angle = math.sin(progress * math.pi) * spec.max_steering_angle_deg
        state.steering_angle_deg = angle
        state.lane_offset_m = angle / spec.max_steering_angle_deg * 3.5  # ~3.5m lane width
        state.lane_change_progress = max(0.0, state.lane_change_progress - dt)
    else:
        state.steering_angle_deg = 0.0
        state.lane_offset_m = 0.0


def maybe_initiate_lane_change(state: VehicleState, step: int, config) -> None:
    """Randomly initiate a lane change."""
    if state.lane_change_progress == 0 and state.rng.random() < config.lane_change_prob_per_step:
        state.lane_change_progress = config.lane_change_duration


def update_gnss(state: VehicleState, dt: float) -> None:
    """Update GNSS position from speed and heading."""
    # Distance traveled
    dist = state.speed_mps * dt
    # Update lat/lon (approximate: 1 deg lat ~= 111km, 1 deg lon ~= 111km * cos(lat))
    dlat = (dist * math.cos(state.heading_rad)) / 111000
    dlon = (dist * math.sin(state.heading_rad)) / (111000 * math.cos(math.radians(state.lat)))
    state.lat += dlat
    state.lon += dlon

    # Add GPS noise
    state.lat += state.rng.gauss(0, 0.00001)
    state.lon += state.rng.gauss(0, 0.00001)
    state.alt += state.rng.gauss(0, 0.5)


def generate_can_signals(state: VehicleState, step: int, captured_at: int) -> dict:
    """Generate CAN bus signals for current state."""
    return {
        "schema_version": "1.0.0",
        "event_id": f"{state.spec.vehicle_id}_{step}_{state.rng.getrandbits(32):08x}",
        "vehicle_id": state.spec.vehicle_id,
        "captured_at": captured_at,
        "run_id": None,
        "rpm": round(state.rpm, 1),
        "wheel_speed": round(state.wheel_speed_mps, 3),
        "brake_pressure": round(state.brake_pressure_kpa, 1),
        "steering_angle": round(state.steering_angle_deg, 2),
        "battery_soc": round(state.soc, 4) if state.soc > 0 else None,
    }


def generate_gnss_signals(state: VehicleState, step: int, captured_at: int) -> dict:
    """Generate GNSS signals for current state."""
    return {
        "schema_version": "1.0.0",
        "event_id": f"{state.spec.vehicle_id}_{step}_{state.rng.getrandbits(32):08x}",
        "vehicle_id": state.spec.vehicle_id,
        "captured_at": captured_at,
        "run_id": None,
        "latitude": round(state.lat, 7),
        "longitude": round(state.lon, 7),
        "altitude": round(state.alt, 2),
        "fix_quality": 1 if state.soc > 0.05 else 0,
        "hdop": round(1.0 + state.rng.random() * 0.5, 2),
        "satellites_tracked": state.rng.randint(8, 14),
    }


def _build_event(
    state: VehicleState,
    step: int,
    captured_at: int,
    code: str,
    severity: str,
    ecu: str,
    details: Optional[str] = None,
) -> dict:
    state.last_event_step = step
    return {
        "schema_version": "1.0.0",
        "event_id": f"{state.spec.vehicle_id}_{step}_{state.rng.getrandbits(32):08x}",
        "vehicle_id": state.spec.vehicle_id,
        "captured_at": captured_at,
        "run_id": None,
        "event_code": code,
        "severity": severity,
        "source_ecu": ecu,
        "details": details,
    }


def detect_events(state: VehicleState, step: int, config, captured_at: int) -> list[dict]:
    """Derive events from vehicle state, with a small random injection rate.

    State-driven events make the event stream correlated with the CAN/GNSS
    streams, which is what a real ECU-side event feed looks like. The random
    component exists to exercise downstream failure/alert paths.
    """
    spec = state.spec
    events: list[dict] = []
    on_cooldown = step - state.last_event_step <= config.event_cooldown_steps

    if state.brake_pressure_kpa >= spec.max_brake_pressure_kpa * 0.5:
        events.append(
            _build_event(
                state,
                step,
                captured_at,
                "HARD_BRAKE",
                "warning",
                "ECU_BRAKE",
                f"brake_pressure={state.brake_pressure_kpa:.0f}kPa",
            )
        )
    if abs(state.steering_angle_deg) >= spec.max_steering_angle_deg * 0.6:
        events.append(
            _build_event(
                state,
                step,
                captured_at,
                "LANE_DEPARTURE",
                "info",
                "ECU_LKA",
                f"steering_angle={state.steering_angle_deg:.1f}deg",
            )
        )
    if state.soc <= config.low_battery_threshold:
        events.append(
            _build_event(
                state,
                step,
                captured_at,
                "LOW_BATTERY",
                "critical",
                "ECU_BMS",
                f"soc={state.soc:.3f}",
            )
        )

    if on_cooldown:
        return events

    if state.rng.random() < config.event_prob_per_step:
        code, severity, ecu = state.rng.choice(
            [
                ("TRACTION_LOSS", "warning", "ECU_ESP"),
                ("ENGINE_OVERHEAT", "critical", "ECU_ENGINE"),
                ("IMU_DRIFT", "info", "ECU_IMU"),
            ]
        )
        events.append(_build_event(state, step, captured_at, code, severity, ecu))

    return events
