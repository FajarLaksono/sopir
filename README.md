# OpenDriveLab - Automated Driving Simulation & Validation Platform

A cloud-native platform for AD/ADAS validation that systematically generates scenarios, executes SUMO simulations, records telemetry, evaluates outcomes, identifies failures, and tracks them against software versions.

**Target Role**: AD/ADAS Software & Data Platform Engineer (Woven by Toyota)

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                      Docker Compose                          │
├─────────────┬─────────────┬─────────────┬───────────────────┤
│  PostgreSQL │  Backend    │  Worker     │  Dashboard        │
│  (Data)     │  (FastAPI)  │  (SUMO)     │  (Streamlit)      │
└─────────────┴─────────────┴─────────────┴───────────────────┘
```

---

## Quick Start

### Prerequisites
- Docker Desktop (running)
- Python 3.10+ (for local development)
- SUMO 1.27+ (for local testing)

### Local Development Setup

```bash
# 1. Clone and setup
cd sopir
cp .env.example .env  # Edit if needed

# 2. Install Python dependencies
pip install -r requirements.txt

# 3. Run SUMO TraCI integration spike (validates SUMO + TraCI)
python spike_sumo.py                    # Minimal generated test
python spike_sumo.py --project /path/to/your/sumo/project  # Your custom project
```

### Docker Spike Test

```bash
# Build and run spike in Docker (uses the official Eclipse SUMO image)
docker build -f Dockerfile.sumo -t sumo-spike .
docker run --rm sumo-spike                           # Minimal test
docker run --rm -v /host/path/to/project:/data sumo-spike --project /data  # Your project

# On Windows (PowerShell), use forward slashes and quote the mount:
docker run --rm -v "D:/path/to/project:/data" sumo-spike --project /data
```

### Full Platform (After Day 1-5 Implementation)

```bash
# 1. Start all services
docker compose up --build -d

# 2. Generate scenarios (creates 4 scenario types)
docker compose exec backend python -m scenario_gen.cli generate

# 3. Start workers (scale for parallel simulation)
docker compose up --scale worker=3 -d

# 4. Create a simulation run (pick a scenario_id from step 2)
SCENARIO_ID=$(curl -s http://localhost:8000/api/v1/scenarios | jq -r '.items[0].id')
curl -X POST http://localhost:8000/api/v1/runs \
  -H "Content-Type: application/json" \
  -d "{\"scenario_id\": \"$SCENARIO_ID\"}"

# 5. Wait for worker to complete (check logs), then evaluate
RUN_ID=<run_id_from_step_4_response>
curl -X POST http://localhost:8000/api/v1/metrics/runs/$RUN_ID/evaluate

# 6. Open dashboard
open http://localhost:8501
```

### Quick Test (All-in-One)
```bash
# After docker compose up --build -d and scenario generation:
SCENARIO_ID=$(curl -s http://localhost:8000/api/v1/scenarios | jq -r '.items[0].id')
RUN_ID=$(curl -s -X POST http://localhost:8000/api/v1/runs \
  -H "Content-Type: application/json" \
  -d "{\"scenario_id\": \"$SCENARIO_ID\"}" | jq -r '.id')
echo "Run created: $RUN_ID"

# Wait 30-60s for worker to complete simulation, then:
curl -X POST http://localhost:8000/api/v1/metrics/runs/$RUN_ID/evaluate
```

---

## SUMO TraCI Integration Spike

### Purpose
Validates that SUMO and TraCI can communicate properly before building the full platform.

### Run Locally (Windows)

```bash
# Minimal generated test (default)
python spike_sumo.py

# Your custom SUMO project
python spike_sumo.py --project "D:\Projects\MySUMOProject"
python spike_sumo.py -p "D:\Projects\MySUMOProject"

# Custom simulation steps
python spike_sumo.py --steps 200
```

### Run in Docker

```bash
# Build image (one-time)
docker build -f Dockerfile.sumo -t sumo-spike .

# Minimal test
docker run --rm sumo-spike

# Your project (volume mount)
docker run --rm -v "D:/Projects/MySUMOProject:/data" sumo-spike --project /data
```

### SUMO Versions

The image is pinned to `ghcr.io/eclipse-sumo/sumo:1.27.1`, matching the local SUMO install and the `traci`/`sumolib` pins in `requirements.txt`. Keep these in lockstep: a scenario written by a current netedit can use flow attributes that older binaries reject outright. `perHour` is the clearest example - it is documented in the current spec and is what netedit emits by default, but a 2019 SUMO build does not recognise it and fails the run with `At least one of 'period', 'vehsPerHour', 'probability', and 'number' is needed in flow`.

TraCI and `sumolib` are **not** pip-installed in the image. They are taken from `$SUMO_HOME/tools` in the base image, built from the same source tree as the `sumo` binary, so the TraCI client and server cannot drift apart. The `traci==1.27.1` / `sumolib==1.27.1` entries in `requirements.txt` describe the local Windows environment only.

The base image already sets `SUMO_HOME=/usr/share/sumo`; the `PYTHONPATH` in `Dockerfile.sumo` is what makes `$SUMO_HOME/tools` importable.

> Note: the commonly referenced `dlrts/sumo` Docker Hub image is abandoned. Its single `latest` tag was last pushed in June 2019 (SUMO 1.5.0) and cannot load scenarios produced by current netedit.

### Expected Output (Success)

```
============================================================
SUMO TraCI Integration Spike
============================================================

[1/5] Creating minimal scenario in /tmp/tmpxxx
    Created: /tmp/tmpxxx/spike.sumocfg

[2/5] Starting SUMO headless via TraCI
    Command: sumo -c /tmp/tmpxxx/spike.sumocfg
    [OK] SUMO started successfully

[3/5] Running simulation loop (100 steps)
    Step   0: ego @ (5.1, 0.0) speed=10.0 lane=edge1_0
    Step  20: ego @ (29.2, 0.0) speed=14.0 lane=edge1_0
    ...

[4/5] Checking results
    Total vehicle readings: ~150
    Unique vehicles seen: {'traffic', 'ego'}
    Collision detection: SKIPPED (TraCI version specific)

[5/5] Shutting down SUMO
    [OK] Clean shutdown

============================================================
SPIKE RESULT: SUCCESS
============================================================
```

### Expected Output (`--project`)

With `--project DIR` no scenario is generated; the spike runs your `.sumocfg` directly, so step `[1/5]` and the vehicle IDs differ:

```
[INFO] Found config: /data/Simple-traffic.sumocfg
============================================================
SUMO TraCI Integration Spike
============================================================

[1/5] Using scenario: /data/Simple-traffic.sumocfg
    Scenario dir: /data

[2/5] Starting SUMO headless via TraCI
    Command: sumo -c /data/Simple-traffic.sumocfg
    [OK] SUMO started successfully

[3/5] Running simulation loop (100 steps)
    Step   0: f_0.0 @ (-694.9, -1.6) speed=13.9 lane=-E22_2
    Step  20: f_0.0 @ (-498.4, 57.5) speed=13.5 lane=-E13_0
    ...

[4/5] Checking results
    Total vehicle readings: ~1300
    Unique vehicles seen: {'f_0.0', 'f_0.1', ...}
    Collision detection: SKIPPED (TraCI version specific)

[5/5] Shutting down SUMO
    [OK] Clean shutdown

============================================================
SPIKE RESULT: SUCCESS
============================================================
```

The reading count is driven by the flow rate in your route file; `assets/SUMO` uses 1800 veh/h, which yields ~46 vehicles across the 100 steps.

---

## Project Structure

```
sopir/
├── docker-compose.yml           # Service orchestration
├── Dockerfile.sumo              # SUMO spike image
├── spike_sumo.py                # TraCI integration test
├── requirements.txt             # Python dependencies
├── .env.example                 # Environment template
├── PLANNING.md                  # Implementation roadmap
├── AGENTS.md                    # AI agent guidelines
├── backend/                     # FastAPI backend
│   ├── main.py                  # App entry point
│   ├── config.py                # Pydantic settings
│   ├── database.py              # SQLAlchemy setup
│   ├── models.py                # ORM models
│   ├── schemas.py               # Pydantic schemas
│   └── api/                     # REST endpoints
├── scenario_gen/                # Scenario generator
│   ├── generator.py
│   ├── cli.py
│   ├── templates/               # .net.xml templates
│   └── configs/                 # Scenario YAML configs
├── simulation/                  # SUMO worker
│   ├── worker.py
│   ├── traci_client.py
│   └── telemetry.py
├── evaluation/                  # Metrics & failure detection
│   ├── metrics.py
│   └── failure_detector.py
├── dashboard/                   # Streamlit dashboard
│   ├── app.py
│   └── components/
└── tests/                       # Unit tests
```

---

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `DATABASE_URL` | PostgreSQL connection string | `postgresql://postgres:postgres@db:5432/opendrivelab` |
| `SUMO_HOME` | SUMO installation path | Auto-detected |
| `POLL_INTERVAL` | Worker poll interval (seconds) | `5` |
| `TELEMETRY_BATCH_SIZE` | Telemetry batch insert size | `100` |

---

## Development Workflow

```bash
# 1. Make changes
# 2. Run linter
ruff check . && ruff format .

# 3. Run tests
pytest tests/ -v

# 4. Verify manually
docker compose up --build -d
```

---

## Portfolio Talking Points

- **End-to-end validation loop**: Scenario gen → Simulation → Telemetry → Evaluation → Dashboard
- **Failure detection**: TTC thresholds, collision classification, rule violations
- **Cloud-native design**: Docker, async workers, stateless services, shared volumes
- **Engineering metrics**: Not just "it drives" - TTC, violations, lane deviations
- **Scalable architecture**: Horizontal worker scaling, batch telemetry, polling-based job queue

---

## License

MIT License - Portfolio project for Woven by Toyota application.


# 1. Start workers (scale for parallel)
docker compose up --scale worker=3 -d

# 2. Create a run (pick a scenario_id from the list above)
curl -X POST http://localhost:8000/api/v1/runs \
  -H "Content-Type: application/json" \
  -d '{"scenario_id": "71e09fd8-bea8-4baa-8eda-0bb16f0ebb54"}'

# 3. Wait ~30-60s for worker to complete, then evaluate
curl -X POST http://localhost:8000/api/v1/metrics/runs/{run_id}/evaluate

# 4. View results
open http://localhost:8501