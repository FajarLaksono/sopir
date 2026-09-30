# OpenDriveLab - Automated Driving Simulation & Validation Platform

## Project Overview
**Target**: Woven by Toyota portfolio project  
**Positioning**: AD/ADAS Software & Data Platform Engineer (not Autonomous Driving Engineer)  
**Core Story**: "Built a cloud-native validation platform that systematically generates scenarios, executes SUMO simulations, records telemetry, evaluates outcomes, identifies failures, and tracks them against software versions."

## MVP Scope (1 Week / 5 Days)

### Core Loop
```
Scenario Generation → SUMO Simulation → Telemetry Collection → Evaluation Metrics → Failure Detection → Dashboard
```

### Architecture (Docker Compose)
| Service | Tech | Purpose |
|---------|------|---------|
| **API Gateway** | FastAPI | REST API for scenario mgmt, job queue, results |
| **Scenario Generator** | Python | Generate SUMO .rou.xml / .net.xml from config |
| **Simulation Worker** | Python + SUMO | Run simulations, emit telemetry (position, speed, collisions) |
| **Evaluation Engine** | Python | Compute metrics (collision rate, avg speed, rule violations) |
| **PostgreSQL** | Postgres | Store scenarios, runs, metrics, failures |
| **Dashboard** | Streamlit | View runs, compare metrics, flag failures |

---

## Day-by-Day Implementation Plan

### Day 0: SUMO TraCI Integration Spike (Critical Risk Mitigation)
**Goal**: Verify SUMO + TraCI communication works in Docker before building infrastructure.

**Deliverables**:
- Dockerfile with SUMO + TraCI installed
- Spike script testing: start SUMO headless, step simulation, read vehicle data, detect collisions, clean shutdown
- Verified `traci_client.py` context manager pattern

**Files**:
```
Dockerfile.sumo          # Base image with SUMO
spike_sumo.py            # Throwaway test script
simulation/traci_client.py  # Reusable context manager (output of spike)
```

**Success Criteria**:
- [ ] `docker build -f Dockerfile.sumo .` succeeds
- [ ] `docker run --rm sumo-spike` prints vehicle positions/speeds for 100 steps
- [ ] Collision detection works (`traci.simulation.getCollisions()`)
- [ ] No orphan SUMO processes after script exits
- [ ] Headless mode works (no display required)

**Timebox**: 1.5 hours max. If blocked >30 min, escalate.

---

### Day 1: Foundation & Infrastructure
**Deliverables**:
- `docker-compose.yml` with all services
- PostgreSQL schema (Scenario, SimulationRun, Telemetry, Metrics, Failure)
- FastAPI skeleton with CORS, lifespan, health check
- Database connection + SQLAlchemy models
- Pydantic schemas for API contracts
- Basic API routers structure

**Files**:
```
docker-compose.yml
backend/
  ├── main.py
  ├── config.py
  ├── database.py
  ├── models.py
  ├── schemas.py
  └── api/
      ├── __init__.py
      ├── scenarios.py
      ├── runs.py
      ├── metrics.py
      └── failures.py
```

### Day 2: Scenario Generation
**Deliverables**:
- 4 SUMO network templates (.net.xml)
- Scenario generator from YAML config
- CLI to generate scenarios
- API endpoints for scenario CRUD

**Scenario Types** (in `scenario_gen/configs/scenarios.yaml`):
1. **4-way intersection** - 2 vehicles crossing paths, 1 pedestrian
2. **Highway merge** - ego vehicle merging, 3 traffic vehicles
3. **Pedestrian crossing** - ego approaching crosswalk, pedestrian crossing
4. **Lane change** - ego changing lanes with adjacent traffic

**Files**:
```
scenario_gen/
  ├── __init__.py
  ├── generator.py
  ├── cli.py
  ├── templates/
  │   ├── intersection.net.xml
  │   ├── highway_merge.net.xml
  │   ├── pedestrian_crossing.net.xml
  │   └── lane_change.net.xml
  └── configs/
      └── scenarios.yaml
```

### Day 3: Simulation Worker + Telemetry
**Deliverables**:
- SUMO TraCI wrapper (headless mode)
- Simulation worker polling for queued runs
- Telemetry collection (batch inserts every 100 steps)
- Run lifecycle management (queued → running → completed/failed)

**Files**:
```
simulation/
  ├── __init__.py
  ├── worker.py
  ├── traci_client.py
  └── telemetry.py
```

### Day 4: Evaluation & Failure Detection
**Deliverables**:
- Metrics computation module
- Rule-based failure detector
- API endpoints for evaluation trigger + results

**Metrics**:
| Metric | Calculation |
|--------|-------------|
| `collision_count` | SUMO collision output |
| `min_ttc` | Min time-to-collision across all vehicle pairs |
| `avg_speed` | Mean speed of ego vehicle |
| `speed_violations` | Count timesteps ego > speed_limit * 1.1 |
| `lane_deviations` | Count lane changes without signaling / off-lane |

**Failure Rules**:
- Critical: collision_count > 0
- High: min_ttc < 1.0s
- Medium: speed_violations > 10
- Medium: lane_deviations > 5

**Files**:
```
evaluation/
  ├── __init__.py
  ├── metrics.py
  └── failure_detector.py
```

### Day 5: Dashboard & Polish
**Deliverables**:
- Streamlit dashboard with:
  - Runs table (scenario, status, duration, collisions, failure count)
  - Metric charts (bar charts per metric, TTC trend line)
  - Failure list (filterable by severity)
  - Run detail view with telemetry summary
- README with architecture diagram, quickstart, demo GIF
- Unit tests for metrics + failure detection
- Demo script

**Files**:
```
dashboard/
  ├── __init__.py
  ├── app.py
  └── components/
      ├── __init__.py
      ├── run_table.py
      ├── metric_charts.py
      └── failure_list.py

tests/
  ├── __init__.py
  ├── test_metrics.py
  └── test_failure_detector.py

README.md
docs/architecture.md
```

---

## Repository Structure
```
sopir/
├── docker-compose.yml
├── README.md
├── docs/
│   └── architecture.md
├── backend/
│   ├── main.py
│   ├── models.py
│   ├── schemas.py
│   ├── database.py
│   ├── config.py
│   └── api/
│       ├── __init__.py
│       ├── scenarios.py
│       ├── runs.py
│       ├── metrics.py
│       └── failures.py
├── scenario_gen/
│   ├── __init__.py
│   ├── generator.py
│   ├── cli.py
│   ├── templates/
│   │   ├── intersection.net.xml
│   │   ├── highway_merge.net.xml
│   │   ├── pedestrian_crossing.net.xml
│   │   └── lane_change.net.xml
│   └── configs/
│       └── scenarios.yaml
├── simulation/
│   ├── __init__.py
│   ├── worker.py
│   ├── traci_client.py
│   └── telemetry.py
├── evaluation/
│   ├── __init__.py
│   ├── metrics.py
│   └── failure_detector.py
├── dashboard/
│   ├── __init__.py
│   ├── app.py
│   └── components/
│       ├── __init__.py
│       ├── run_table.py
│       ├── metric_charts.py
│       └── failure_list.py
└── tests/
    ├── __init__.py
    ├── test_metrics.py
    └── test_failure_detector.py
```

---

## Key Technical Decisions

| Decision | Rationale |
|----------|-----------|
| SUMO `--no-gui` headless | CI-friendly, faster, no display needed |
| Batch telemetry inserts (100 steps) | Avoid DB bottleneck at 10Hz/vehicle |
| Polling worker (not queue) | Simpler for MVP; Redis/SQS in Phase 2 |
| Streamlit over React | 10x faster for data dashboard |
| SQLAlchemy + Pydantic | Type safety, auto API docs (Swagger) |
| Shared Docker volume | Simple file passing between services |

---

## Success Criteria (MVP Complete)
- [ ] `docker compose up` starts all services without errors
- [ ] Generate 4 scenarios via API (`POST /scenarios/generate`)
- [ ] Run 3+ simulations in parallel (`docker compose up --scale worker=3`)
- [ ] Dashboard shows runs table with metrics + failures
- [ ] At least 1 failure detected per scenario type
- [ ] README with Mermaid architecture diagram + demo GIF

---

## Phase 2+ (Post-MVP)
| Feature | Description |
|---------|-------------|
| Active Learning Loop | Prioritize rare/dangerous scenarios for re-simulation |
| LLM Failure Assistant | Generate investigation notes from telemetry + metrics |
| Regression Testing | CI/CD blocks deployment on metric regressions |
| AWS Deployment | Terraform, ECS, SQS, S3, CloudWatch |
| Scenario Versioning | Git-like scenario library with tags |
| Horizontal Scaling | Redis queue, spot instances, cost optimization |