"""CLI entry point for vehicle simulator."""

from __future__ import annotations

import os

import typer

from .config import create_default_config
from .simulator import run_simulation

app = typer.Typer(help="Vehicle log simulator for Sopir streaming pipeline")


def _env_int(name: str, default: int) -> int:
    return int(os.getenv(name, default))


def _env_float(name: str, default: float) -> float:
    return float(os.getenv(name, default))


@app.command()
def run(
    vehicles: int = typer.Option(None, "--vehicles", "-n", help="Number of vehicles to simulate"),
    duration: float = typer.Option(None, "--duration", "-d", help="Simulation duration in seconds"),
    rate: float = typer.Option(None, "--rate", "-r", help="Simulation rate in Hz"),
    seed: int = typer.Option(None, "--seed", "-s", help="Random seed for reproducibility"),
) -> None:
    """Run the vehicle log simulator.

    Options fall back to VEHICLE_COUNT, SIM_DURATION, SIM_RATE and SIM_SEED.
    """
    run_simulation(
        create_default_config(
            num_vehicles=vehicles if vehicles is not None else _env_int("VEHICLE_COUNT", 5),
            duration_sec=(duration if duration is not None else _env_float("SIM_DURATION", 120.0)),
            rate_hz=rate if rate is not None else _env_float("SIM_RATE", 10.0),
            seed=seed if seed is not None else _env_int("SIM_SEED", 42),
        )
    )


if __name__ == "__main__":
    app()
