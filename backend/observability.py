"""Metrics and health endpoints for the FastAPI backend.

The backend already has a web server, so it mounts routes rather than starting
a second listener like the daemons do. It shares the collectors and the health
model with them, which means one Prometheus query covers all four services.

The database is the backend's only hard dependency, so readiness probes it with
a trivial statement. That check is cached briefly: a readiness endpoint that
runs a query on every scrape turns a load problem into an outage.
"""

from __future__ import annotations

import json
import logging
import time

from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text

from backend.database import engine
from streaming.observability import HealthState, ServiceMetrics

logger = logging.getLogger(__name__)

SERVICE_NAME = "backend"

metrics = ServiceMetrics(SERVICE_NAME)

# The backend has no consume loop to heartbeat from: it is driven entirely by
# inbound requests. Liveness is therefore refreshed by the request middleware
# below, and given a generous timeout so an idle API is healthy rather than
# declared dead. Requiring a heartbeat here without a producer would have the
# container report unhealthy after two minutes of no traffic, which is wrong.
health = HealthState(SERVICE_NAME, heartbeat_max_age_sec=300.0)

router = APIRouter(tags=["observability"])
observability_router = router

# How long a successful database probe stays valid.
_DB_CHECK_TTL_SEC = 5.0
_last_db_check: tuple[float, bool] = (0.0, False)

_STARTED_AT = time.time()

# HTTP-specific collectors. These exist rather than reusing the Kafka counters
# so that stream volume and request volume stay separate series: summing them
# into one query would answer neither question.
http_requests = metrics.http_requests
http_duration = metrics.http_duration

__all__ = [
    "health",
    "http_duration",
    "http_requests",
    "mark_started",
    "metrics",
    "normalize_route",
    "observability_router",
    "record_http_request",
    "router",
]


def _database_ready() -> bool:
    """Probe the database, caching the answer briefly."""
    global _last_db_check

    checked_at, healthy = _last_db_check
    now = time.monotonic()
    if now - checked_at < _DB_CHECK_TTL_SEC:
        return healthy

    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        healthy = True
        detail = "ok"
    except Exception as exc:
        healthy = False
        detail = str(exc).splitlines()[0][:200]
        metrics.record_error("database_probe")
        logger.warning("Database readiness probe failed: %s", detail)

    _last_db_check = (now, healthy)
    health.set_check("database", healthy, detail)
    metrics.set_ready(healthy)
    metrics.set_up(True)
    return healthy


@router.get("/metrics")
def prometheus_metrics() -> Response:
    """Prometheus exposition format."""
    return Response(content=generate_latest(metrics.registry), media_type=CONTENT_TYPE_LATEST)


@router.get("/healthz")
def healthz() -> Response:
    """Liveness: the process is up and able to serve."""
    # Serving this request is itself proof of liveness, so refresh here too:
    # an API with no traffic must not age out and restart-loop.
    health.heartbeat()
    snapshot = health.snapshot()
    status = 200 if snapshot["alive"] else 503
    return Response(
        content=json.dumps(snapshot, indent=2),
        status_code=status,
        media_type="application/json",
    )


@router.get("/readyz")
def readyz() -> Response:
    """Readiness: the database is reachable."""
    health.heartbeat()
    _database_ready()
    snapshot = health.snapshot()
    status = 200 if snapshot["ready"] else 503
    return Response(
        content=json.dumps(snapshot, indent=2),
        status_code=status,
        media_type="application/json",
    )


def mark_started() -> None:
    """Called from the app lifespan once the schema is ready."""
    health.mark_started()
    metrics.set_up(True)
    _database_ready()
    logger.info(
        "Observability ready: /metrics /healthz /readyz, uptime=%.1fs",
        time.time() - _STARTED_AT,
    )


def normalize_route(path: str) -> str:
    """Collapse identifier segments so cardinality stays bounded.

    ``/api/v1/runs/<uuid>/telemetry`` must not create a new time series per run.
    A UUID is longer than 8 characters and contains digits, which is enough to
    tell one apart from a fixed route segment like ``metrics``.
    """
    route = path.split("?", 1)[0]
    parts = route.split("/")
    for index, part in enumerate(parts):
        if len(part) > 8 and any(char.isdigit() for char in part):
            parts[index] = "{id}"
            break
    return "/".join(parts)


def record_http_request(method: str, path: str, status_code: int, duration_sec: float) -> None:
    """Record one served request.

    HTTP traffic gets its own counter rather than riding on
    ``sopir_records_consumed_total``: that metric is labelled by Kafka topic, and
    stuffing ``"GET /readyz"`` into a topic label would make one sum() query mix
    request volume with stream volume.
    """
    labels = {"service": SERVICE_NAME, "instance": metrics.instance}
    metrics.http_requests.labels(**labels, route=normalize_route(path), method=method).inc()
    metrics.http_duration.labels(**labels, route=normalize_route(path)).observe(
        max(0.0, duration_sec)
    )
    if status_code >= 500:
        metrics.record_error("http_5xx")
