import logging
import os
import signal
import sys
import time
import uuid
from datetime import datetime

import traci
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend.config import settings
from backend.models import SimulationRun
from simulation.telemetry_sink import TelemetryRecord, create_sink
from simulation.traci_client import sumo_connection
from streaming.observability import Observability

logger = logging.getLogger(__name__)

POLL_INTERVAL = settings.poll_interval
BATCH_SIZE = settings.telemetry_batch_size
WORKER_ID = os.environ.get("HOSTNAME", f"worker-{uuid.uuid4().hex[:8]}")

obs = Observability("worker")


engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_next_run(db) -> SimulationRun | None:
    return db.execute(
        select(SimulationRun)
        .where(SimulationRun.status == "queued")
        .order_by(SimulationRun.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    ).scalar_one_or_none()


def _fail(run: SimulationRun, db, message: str) -> None:
    run.status = "failed"
    run.error_message = message
    db.commit()
    obs.metrics.record_run("failed")
    obs.metrics.record_error("run_setup")


def run_simulation(run: SimulationRun, db) -> None:
    started = time.monotonic()

    if not run.scenario.config_file_path:
        _fail(run, db, "Scenario has no config file")
        return

    config_path = run.scenario.config_file_path
    if not os.path.exists(config_path):
        _fail(run, db, f"Config file not found: {config_path}")
        return

    run.status = "running"
    run.worker_id = WORKER_ID
    run.started_at = datetime.utcnow()
    db.commit()

    sink = create_sink(db)
    step = 0
    failed = False

    try:
        with sumo_connection(config_path):
            while traci.simulation.getMinExpectedNumber() > 0:
                traci.simulationStep()
                step += 1

                captured_at_ms = int(time.time() * 1000)
                vehicles = traci.vehicle.getIDList()
                records: list = []

                for vid in vehicles:
                    pos = traci.vehicle.getPosition(vid)
                    speed = traci.vehicle.getSpeed(vid)
                    angle = traci.vehicle.getAngle(vid)
                    lane = traci.vehicle.getLaneID(vid)

                    records.append(
                        TelemetryRecord(
                            run_id=run.id,
                            step=step,
                            vehicle_id=vid,
                            x=pos[0],
                            y=pos[1],
                            speed=speed,
                            angle=angle,
                            lane_id=lane,
                            captured_at_ms=captured_at_ms,
                        )
                    )

                if records:
                    sink.write_batch(records)
                    obs.metrics.record_written("telemetry", len(records))
                    obs.metrics.touch_progress()

    except Exception as e:
        failed = True
        run.status = "failed"
        run.error_message = str(e)
        db.commit()
        obs.metrics.record_run("failed", time.monotonic() - started)
        obs.metrics.record_error("simulation")
        raise
    finally:
        try:
            sink.close()
        finally:
            run.completed_at = datetime.utcnow()
            if not failed and run.status == "running":
                run.status = "completed"
            db.commit()
            if not failed:
                obs.metrics.record_run("completed", time.monotonic() - started)


def main():
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format=f"%(asctime)s %(levelname)s [{WORKER_ID}] %(message)s",
    )
    obs.start()
    logger.info("Starting simulation worker, poll_interval=%ss", POLL_INTERVAL)
    logger.info("Telemetry sink: %s", os.getenv("TELEMETRY_SINK", "postgres"))

    def shutdown(signum, frame):
        logger.info("Shutdown signal received")
        obs.stop()
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    while True:
        db = SessionLocal()
        try:
            run = get_next_run(db)
            if run:
                logger.info("Picked up run %s", run.id)
                run_simulation(run, db)
            else:
                time.sleep(POLL_INTERVAL)
        except Exception:
            # A failed run must not take the worker down; the next poll retries.
            obs.metrics.record_error("run_cycle")
            obs.health.set_check("last_run", True)
            logger.exception("Error during run cycle")
            time.sleep(POLL_INTERVAL)
        finally:
            db.close()
        # A poll that found no work is still a live loop; a worker waiting on an
        # empty queue must not look dead to a liveness probe.
        obs.health.heartbeat()


if __name__ == "__main__":
    main()
