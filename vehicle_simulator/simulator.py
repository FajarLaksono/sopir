"""Main vehicle log simulator loop."""

from __future__ import annotations

import os
import random
import signal
import time
import zlib
from typing import Optional

from streaming.producer import AvroProducer, create_producer

from .config import SimConfig, create_default_config
from .signals import (
    VehicleState,
    detect_events,
    generate_can_signals,
    generate_gnss_signals,
    maybe_initiate_lane_change,
    update_gnss,
    update_powertrain,
    update_steering,
)


class VehicleSimulator:
    """Runs N vehicles and publishes their signals to Kafka."""

    def __init__(self, config: SimConfig):
        self.config = config
        self.producer: Optional[AvroProducer] = None
        self.states: list[VehicleState] = []
        self._running = False

    def _init_producer(self):
        self.producer = create_producer(
            bootstrap_servers=os.getenv("KAFKA_BOOTSTRAP_SERVERS"),
            schema_registry_url=os.getenv("SCHEMA_REGISTRY_URL"),
        )

    def _init_states(self):
        for spec in self.config.vehicles:
            # zlib.crc32 is stable across processes, unlike hash() which is
            # randomized per interpreter and would break replay determinism.
            seed = (self.config.seed + zlib.crc32(spec.vehicle_id.encode())) % (2**32)
            self.states.append(VehicleState(spec=spec, rng=random.Random(seed)))

    def start(self):
        """Initialize and start the simulation."""
        self._init_producer()
        self._init_states()
        self._running = True

        dt = 1.0 / self.config.rate_hz
        total_steps = int(self.config.duration_sec * self.config.rate_hz)

        print(
            f"Starting vehicle simulator: {len(self.states)} vehicles, "
            f"{self.config.duration_sec}s at {self.config.rate_hz}Hz"
        )

        try:
            for step in range(total_steps):
                if not self._running:
                    break

                step_start = time.time()
                captured_at = int(step_start * 1000)

                for state in self.states:
                    # Update vehicle physics
                    update_powertrain(state, dt, self.config)
                    maybe_initiate_lane_change(state, step, self.config)
                    update_steering(state, dt, self.config)
                    update_gnss(state, dt)

                    # Generate and publish signals
                    self.producer.produce(
                        self.config.can_topic,
                        key=state.spec.vehicle_id,
                        value=generate_can_signals(state, step, captured_at),
                    )
                    self.producer.produce(
                        self.config.gnss_topic,
                        key=state.spec.vehicle_id,
                        value=generate_gnss_signals(state, step, captured_at),
                    )
                    for event in detect_events(state, step, self.config, captured_at):
                        self.producer.produce(
                            self.config.events_topic,
                            key=state.spec.vehicle_id,
                            value=event,
                        )

                # Flush periodically
                if step % 100 == 0:
                    self.producer.flush(1.0)

                # Rate limiting
                elapsed = time.time() - step_start
                sleep_time = max(0, dt - elapsed)
                if sleep_time > 0:
                    time.sleep(sleep_time)

        finally:
            self.stop()

    def stop(self):
        """Stop the simulator and clean up."""
        self._running = False
        if self.producer is not None:
            remaining = self.producer.flush(10.0)
            if remaining:
                print(f"WARNING: {remaining} messages undelivered on shutdown")
            self.producer.close()
            self.producer = None
        print("Vehicle simulator stopped")


def run_simulation(config: Optional[SimConfig] = None) -> None:
    """Entry point for running the simulator."""
    if config is None:
        config = create_default_config(
            num_vehicles=int(os.getenv("VEHICLE_COUNT", "5")),
            duration_sec=float(os.getenv("SIM_DURATION", "120")),
            rate_hz=float(os.getenv("SIM_RATE", "10")),
            seed=int(os.getenv("SIM_SEED", "42")),
        )
    simulator = VehicleSimulator(config)

    def shutdown(signum, frame) -> None:
        """Ask the run loop to stop after the current step."""
        print("Shutdown signal received, finishing current step")
        simulator._running = False

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    simulator.start()


if __name__ == "__main__":
    run_simulation()
