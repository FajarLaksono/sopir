import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.api import failures, metrics, runs, scenarios
from backend.database import Base, engine
from backend.observability import (
    health,
    mark_started,
    observability_router,
    record_http_request,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    mark_started()
    yield


app = FastAPI(
    title="OpenDriveLab API",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def observe_requests(request: Request, call_next):
    """Time every request, publish it, and refresh liveness.

    There is no consume loop to heartbeat from here, so inbound traffic is what
    proves this process is alive. Without this an idle API would age past the
    heartbeat timeout and the container healthcheck would kill it during a quiet
    period.
    """
    health.heartbeat()
    started = time.monotonic()
    try:
        response = await call_next(request)
    except Exception:
        # An unhandled exception still counts as a served request; recording it
        # before re-raising keeps 5xx visible in the error rate.
        record_http_request(request.method, request.url.path, 500, time.monotonic() - started)
        raise
    record_http_request(
        request.method, request.url.path, response.status_code, time.monotonic() - started
    )
    return response


app.include_router(scenarios.router, prefix="/api/v1/scenarios", tags=["scenarios"])
app.include_router(runs.router, prefix="/api/v1/runs", tags=["runs"])
app.include_router(metrics.router, prefix="/api/v1/metrics", tags=["metrics"])
app.include_router(failures.router, prefix="/api/v1/failures", tags=["failures"])

# Mounted at the root: Prometheus and container healthchecks expect
# /metrics, /healthz, /readyz rather than versioned API paths.
app.include_router(observability_router)


@app.get("/health")
def health_check():
    return {"status": "ok", "service": "opendrivelab-backend"}


@app.get("/")
def root():
    return JSONResponse(
        {
            "service": "opendrivelab-backend",
            "docs": "/docs",
            "observability": ["/metrics", "/healthz", "/readyz"],
        }
    )
