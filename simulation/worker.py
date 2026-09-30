import os
import sys
import time
import uuid
import signal
from datetime import datetime

import traci
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from backend.models import SimulationRun, Telemetry
from backend.config import settings
from simulation.traci_client import sumo_connection


POLL_INTERVAL = settings.poll_interval
BATCH_SIZE = settings.telemetry_batch_size
WORKER_ID = os.environ.get("HOSTNAME", f"worker-{uuid.uuid4().hex[:8]}")


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


def run_simulation(run: SimulationRun, db) -> None:
    if not run.scenario.config_file_path:
        run.status = "failed"
        run.error_message = "Scenario has no config file"
        db.commit()
        return

    config_path = run.scenario.config_file_path
    if not os.path.exists(config_path):
        run.status = "failed"
        run.error_message = f"Config file not found: {config_path}"
        db.commit()
        return

    run.status = "running"
    run.worker_id = WORKER_ID
    run.started_at = datetime.utcnow()
    db.commit()

    telemetry_batch = []
    step = 0

    try:
        with sumo_connection(config_path):
            while traci.simulation.getMinExpectedNumber() > 0:
                traci.simulationStep()
                step += 1

                vehicles = traci.vehicle.getIDList()
                for vid in vehicles:
                    pos = traci.vehicle.getPosition(vid)
                    speed = traci.vehicle.getSpeed(vid)
                    angle = traci.vehicle.getAngle(vid)
                    lane = traci.vehicle.getLaneID(vid)

                    telemetry_batch.append(Telemetry(
                        run_id=run.id,
                        step=step,
                        vehicle_id=vid,
                        x=pos[0],
                        y=pos[1],
                        speed=speed,
                        angle=angle,
                        lane_id=lane,
                    ))

                if len(telemetry_batch) >= BATCH_SIZE:
                    db.bulk_save_objects(telemetry_batch)
                    db.commit()
                    telemetry_batch.clear()

        if telemetry_batch:
            db.bulk_save_objects(telemetry_batch)
            db.commit()

        run.status = "completed"
    except Exception as e:
        run.status = "failed"
        run.error_message = str(e)
        db.commit()
        raise
    finally:
        run.completed_at = datetime.utcnow()
        db.commit()


def main():
    print(f"[{WORKER_ID}] Starting simulation worker, poll_interval={POLL_INTERVAL}s")

    def shutdown(signum, frame):
        print(f"[{WORKER_ID}] Shutdown signal received")
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    while True:
        db = SessionLocal()
        try:
            run = get_next_run(db)
            if run:
                print(f"[{WORKER_ID}] Picked up run {run.id}")
                run_simulation(run, db)
            else:
                time.sleep(POLL_INTERVAL)
        except Exception as e:
            print(f"[{WORKER_ID}] Error: {e}")
            time.sleep(POLL_INTERVAL)
        finally:
            db.close()


if __name__ == "__main__":
    main()