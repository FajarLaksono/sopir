# AGENTS.md - AI Agent Guidelines for Sopir

## Project Context
This is a **portfolio project** for an "AD/ADAS Software & Data Platform Engineer" role. The project demonstrates backend/data/cloud engineering for automated driving validation - NOT an autonomous driving AI model.

**Core Value Proposition**: "Built a validation platform that systematically generates scenarios, executes simulations, evaluates outcomes, identifies failures, and tracks them against software versions."

---

## Architecture Principles

### 1. Simplicity Over Cleverness
- Prefer boring, well-understood patterns over novel architectures
- Each service does ONE thing well
- Avoid premature abstraction - duplicate code before abstracting

### 2. Local-First Development
- All services run via `docker compose up` locally
- No external cloud dependencies in MVP
- Shared volume for file passing (simpler than object storage)

### 3. Type Safety Everywhere
- Pydantic for API contracts (request/response)
- SQLAlchemy models with type annotations
- MyPy strict mode in CI

### 4. Explicit Over Implicit
- Configuration via Pydantic Settings (env vars + .env)
- No magic globals or singleton patterns
- Dependency injection via FastAPI's `Depends()`

---

## Code Conventions

### Python Style
- **Formatter**: Ruff (replaces Black + isort + flake8)
- **Line length**: 100 chars
- **Imports**: Grouped (stdlib, third-party, local), sorted
- **Type hints**: Required for all public functions

### Naming
| Element | Convention |
|---------|------------|
| Files/modules | `snake_case.py` |
| Classes | `PascalCase` |
| Functions/variables | `snake_case` |
| Constants | `UPPER_SNAKE_CASE` |
| Private | `_leading_underscore` |
| Pydantic models | `PascalCase` + `Schema` suffix for requests |

### API Design
- RESTful endpoints with plural nouns: `/scenarios`, `/runs`, `/metrics`
- Version prefix: `/api/v1/`
- Consistent error format: `{ "detail": "message", "code": "ERROR_CODE" }`
- Pagination: `?page=1&page_size=20` with `X-Total-Count` header

### Database
- UUID primary keys (`uuid_generate_v4()`)
- `created_at` / `updated_at` on all tables
- Indexes on foreign keys and query filters
- Migrations via Alembic (add when schema stabilizes)

---

## Service-Specific Guidelines

### Backend (FastAPI)
```
backend/
├── main.py           # App factory, lifespan, middleware
├── config.py         # Pydantic Settings (env-driven)
├── database.py       # Engine, session, base
├── models.py         # SQLAlchemy models
├── schemas.py        # Pydantic request/response
└── api/
    ├── scenarios.py  # Scenario CRUD + generation trigger
    ├── runs.py       # Run lifecycle + telemetry
    ├── metrics.py    # Evaluation trigger + results
    └── failures.py   # Failure queries
```

**Rules**:
- Keep route handlers thin - delegate to service functions
- Use `Depends(get_db)` for DB sessions
- Return Pydantic models, not ORM objects
- Background tasks for long-running operations (generation, evaluation)

### Scenario Generator
```
scenario_gen/
├── generator.py      # Core logic: config → SUMO files
├── cli.py            # Typer CLI for manual generation
└── templates/        # .net.xml network files
```

**Rules**:
- Pure functions where possible (config dict → XML strings)
- Validate config with Pydantic before generation
- Template networks with Jinja2 or string substitution
- Output to `/data/scenarios/{scenario_id}/`

### Simulation Worker
```
simulation/
├── worker.py         # Main loop: poll → run → store
├── traci_client.py   # SUMO TraCI wrapper
└── telemetry.py      # Batch telemetry writer
```

**Rules**:
- Worker is stateless - all state in DB
- Poll interval: configurable (default 5s)
- Graceful shutdown on SIGTERM (finish current run)
- TraCI client: context manager for SUMO process lifecycle
- Telemetry: batch insert every N steps (configurable, default 100)

### Evaluation
```
evaluation/
├── metrics.py           # Pure metric computation functions
└── failure_detector.py  # Rule engine → Failure objects
```

**Rules**:
- Metrics = pure functions (telemetry list → metric dict)
- Failure detector = pure function (metrics → failure list)
- No DB access in evaluation module
- Easy to unit test with synthetic telemetry

### Dashboard (Streamlit)
```
dashboard/
├── app.py                    # Main entry, page config
└── components/
    ├── run_table.py          # AgGrid or st.dataframe
    ├── metric_charts.py      # Plotly charts
    └── failure_list.py       # Filterable failure view
```

**Rules**:
- Cache data queries with `@st.cache_data(ttl=30)`
- Separate data fetching from rendering
- Components = pure functions (data → Streamlit elements)
- No business logic in dashboard

---

## Development Workflow

### Starting Fresh
```bash
# 1. Clone & setup
cd sopir
cp .env.example .env  # edit if needed

# 2. Start services
docker compose up --build -d

# 3. Generate scenarios
docker compose exec backend python -m scenario_gen.cli generate

# 4. Run simulations
docker compose up --scale worker=3

# 5. Open dashboard
open http://localhost:8501
```

### Making Changes
1. **Read existing code** in the target module first
2. **Write failing test** for new behavior (TDD preferred)
3. **Implement** following conventions above
4. **Run linter**: `ruff check . && ruff format .`
5. **Run tests**: `pytest tests/ -v`
6. **Verify manually** via API docs (localhost:8000/docs) or dashboard

### Adding a New Scenario Type
1. Add `.net.xml` template to `scenario_gen/templates/`
2. Add config entry to `scenario_gen/configs/scenarios.yaml`
3. Update generator to handle new type
4. Test via CLI: `python -m scenario_gen.cli generate --type=new_type`

### Adding a New Metric
1. Add function to `evaluation/metrics.py` with type hints
2. Add column to `Metrics` model + migration
3. Update failure detector if threshold-based
4. Add test in `tests/test_metrics.py`
5. Update dashboard metric charts component

---

## Testing Strategy

### Unit Tests (Fast, No External Deps)
- `tests/test_metrics.py` - metric calculations with synthetic data
- `tests/test_failure_detector.py` - rule engine with metric fixtures
- Target: >90% coverage on evaluation module

### Integration Tests (Require Services)
- API contract tests via `httpx.AsyncClient` against test DB
- Worker simulation with minimal SUMO config
- Run in CI with `docker compose -f docker-compose.test.yml up`

### Manual Verification Checklist
- [ ] `docker compose up` starts cleanly
- [ ] POST /scenarios/generate returns 4 scenario IDs
- [ ] GET /scenarios lists all with correct types
- [ ] POST /runs creates run, status transitions work
- [ ] Worker picks up queued run, completes, stores telemetry
- [ ] POST /runs/{id}/evaluate computes metrics + failures
- [ ] Dashboard loads, shows data, filters work

---

## Common Pitfalls to Avoid

| Pitfall | Prevention |
|---------|------------|
| SUMO process leaks | Context manager in `traci_client.py`, worker SIGTERM handler |
| Telemetry DB overload | Batch inserts, limit to ego + 3 nearest vehicles |
| Dashboard slow with 10k+ runs | Pagination, cached aggregates, indexed queries |
| Circular imports | Shared schemas in `backend/schemas.py`, no cross-service imports |
| Config drift | Single source of truth: `backend/config.py` (Pydantic Settings) |

---

## Portfolio-Specific Notes

### What Makes This Impressive
1. **End-to-end validation loop** - not just simulation
2. **Failure detection + prioritization** - shows safety mindset
3. **Cloud-native design** - Docker, async workers, scalable architecture
4. **Engineering metrics** - TTC, violations, not just "it drives"
5. **Professional repo** - README, architecture diagram, tests, CI

### Demo Script for Interviews
```bash
# 1. Show architecture diagram in README
# 2. Generate scenarios
docker compose exec backend python -m scenario_gen.cli generate

# 3. Run parallel simulations
docker compose up --scale worker=3 -d

# 4. Show live dashboard
open http://localhost:8501
# - Runs table updating in real-time
# - Failure detection working
# - Metric comparison across scenarios
```

### Talking Points
- "Designed for scale: async workers, batch telemetry, stateless services"
- "Failure detection mirrors V&V: TTC thresholds, collision classification"
- "Active learning ready: failure priority → scenario prioritization queue"
- "My SAA-C03 applied: this architecture maps to ECS/SQS/S3/CloudWatch"

---

## File Ownership Map

| Area | Primary Files | Agent Focus |
|------|---------------|-------------|
| Infrastructure | `docker-compose.yml`, `backend/config.py` | Day 1 |
| API/Backend | `backend/` | Day 1-2 |
| Scenario Gen | `scenario_gen/` | Day 2 |
| Simulation | `simulation/` | Day 3 |
| Evaluation | `evaluation/` | Day 4 |
| Dashboard | `dashboard/` | Day 5 |
| Tests | `tests/` | Ongoing |

---

## Questions for Human Before Major Changes
- Changing database schema → needs migration plan
- Adding external dependency → justify vs stdlib
- Changing API contract → version or deprecation strategy
- Modifying SUMO network templates → verify in SUMO GUI first
- Dashboard UX changes → confirm with user before implementing