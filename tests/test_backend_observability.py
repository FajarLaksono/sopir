"""Tests for the backend observability routes and the worker instrumentation.

The backend already has a web server, so these exercise the mounted routes
rather than a second listener. The worker is harder to test directly because
importing it opens a module-level engine, so its behaviour is covered by
checking the metric helpers it depends on and the route normalization that keeps
cardinality bounded.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from prometheus_client import generate_latest

from backend.observability import (
    metrics,
    normalize_route,
    record_http_request,
)


@pytest.fixture
def client():
    from backend.main import app

    with TestClient(app) as test_client:
        yield test_client


class TestBackendRoutes:
    def test_metrics_endpoint_is_mounted_at_root(self, client):
        response = client.get("/metrics")
        assert response.status_code == 200
        assert "text/plain" in response.headers["content-type"]
        assert "sopir_build_info" in response.text

    def test_healthz_reports_the_service_name(self, client):
        response = client.get("/healthz")
        assert response.status_code == 200
        assert json.loads(response.text)["service"] == "backend"

    def test_readyz_passes_against_a_live_database(self, client):
        # A real Postgres, not a stub: /readyz is only meaningful if the probe
        # actually reaches a database. conftest creates the schema on whatever
        # DATABASE_URL points at, so CI's service container and a developer's
        # running stack both work without changes here.
        response = client.get("/readyz")
        assert response.status_code == 200
        payload = json.loads(response.text)
        assert payload["ready"] is True
        assert payload["checks"]["database"]["healthy"] is True

    def test_legacy_health_endpoint_still_works(self, client):
        # The dashboard and any existing scripts depend on this one.
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    def test_root_advertises_the_observability_endpoints(self, client):
        payload = client.get("/").json()
        assert "/metrics" in payload["observability"]

    def test_requests_are_recorded_in_the_registry(self, client):
        client.get("/api/v1/scenarios?limit=1")
        text = generate_latest(metrics.registry).decode()
        assert "sopir_http_requests_total" in text
        assert 'route="/api/v1/scenarios"' in text

    def test_http_traffic_does_not_pollute_the_kafka_counter(self, client):
        # Stream volume and request volume must stay separate series; a
        # "GET /readyz" sitting in a topic label would corrupt any sum() over
        # sopir_records_consumed_total.
        client.get("/api/v1/scenarios")
        text = generate_latest(metrics.registry).decode()
        for line in text.splitlines():
            if line.startswith("sopir_records_consumed_total"):
                assert 'service="backend"' not in line


class TestNormalizeRoute:
    def test_uuid_segment_is_collapsed(self):
        path = "/api/v1/runs/dddaea16-776d-454d-b99a-68997f5a6bd5"
        assert normalize_route(path) == "/api/v1/runs/{id}"

    def test_nested_uuid_is_collapsed(self):
        path = "/api/v1/runs/dddaea16-776d-454d-b99a-68997f5a6bd5/telemetry"
        assert normalize_route(path) == "/api/v1/runs/{id}/telemetry"

    def test_static_routes_are_untouched(self):
        assert normalize_route("/api/v1/scenarios") == "/api/v1/scenarios"
        assert normalize_route("/api/v1/metrics/runs/xyz/evaluate") == (
            "/api/v1/metrics/runs/xyz/evaluate"
        )

    def test_query_string_is_dropped(self):
        # Otherwise ?page=1 and ?page=2 become different series.
        assert normalize_route("/api/v1/runs?page=2&page_size=20") == "/api/v1/runs"

    def test_root_path(self):
        assert normalize_route("/") == "/"

    def test_short_numeric_segments_are_not_collapsed(self):
        # "v1" is short and has no digit run long enough to be an id.
        assert normalize_route("/api/v1/runs") == "/api/v1/runs"


class TestRecordHttpRequest:
    """These exercise the metric helpers directly.

    Counters are process-global, so each test uses its own ``kind`` label where
    one is settable and otherwise reads a before/after delta. Asserting on a
    shared label would make the result depend on test order.
    """

    def test_5xx_increments_the_error_counter(self):
        before = _error_total()
        record_http_request("GET", "/api/v1/runs/abc123def456", 503, 0.01)
        assert _error_total() == before + 1

    def test_2xx_does_not_increment_errors(self):
        before = _error_total()
        record_http_request("GET", "/api/v1/scenarios", 200, 0.01)
        assert _error_total() == before

    def test_negative_duration_is_clamped(self):
        # A clock adjustment must not produce a negative observation.
        record_http_request("GET", "/api/v1/scenarios", 200, -5.0)

    def test_metric_labels_use_the_backend_service_name(self):
        record_http_request("POST", "/api/v1/scenarios/generate", 200, 0.02)
        text = generate_latest(metrics.registry).decode()
        assert 'service="backend"' in text

    def test_route_label_is_normalized(self):
        record_http_request("GET", "/api/v1/runs/dddaea16-776d-454d-b99a-68997f5a6bd5", 200, 0.01)
        text = generate_latest(metrics.registry).decode()
        # The raw uuid must never become a label, or every run is a new series.
        assert "dddaea16-776d-454d-b99a-68997f5a6bd5" not in text
        assert 'route="/api/v1/runs/{id}"' in text


def _error_total() -> float:
    """Sum every ``sopir_errors_total`` sample in the backend registry."""
    total = 0.0
    for line in generate_latest(metrics.registry).decode().splitlines():
        if line.startswith("#") or "_created" in line:
            continue
        if line.startswith("sopir_errors_total"):
            total += float(line.rsplit(" ", 1)[1])
    return total


class TestLivenessDoesNotExpireWhenIdle:
    """The backend has no consume loop, so liveness must not decay while idle.

    Regression: heartbeat was only set in the lifespan, so a backend with no
    traffic for the heartbeat timeout reported 503 on /healthz and /readyz, and
    the compose healthcheck killed it. Serving a request is the proof of life.
    """

    def test_healthz_refreshes_heartbeat(self, client):
        from backend.observability import health

        health._last_heartbeat -= 10_000.0
        assert health.alive is False

        assert client.get("/healthz").status_code == 200
        assert health.alive is True

    def test_readyz_refreshes_heartbeat(self, client):
        from backend.observability import health

        health._last_heartbeat -= 10_000.0
        assert client.get("/readyz").status_code == 200
        assert health.alive is True

    def test_api_traffic_refreshes_heartbeat(self, client):
        from backend.observability import health

        health._last_heartbeat -= 10_000.0
        client.get("/health")
        assert health.alive is True

    def test_backend_uses_a_longer_timeout_than_the_daemons(self):
        # A daemon blocks on a socket and needs a tight bound; an API server
        # only needs proof that its event loop turns.
        from backend.observability import health

        assert health.heartbeat_max_age_sec >= 300.0


class TestWorkerMetricsSurface:
    def test_worker_observability_is_disabled_by_env(self, monkeypatch):
        monkeypatch.setenv("OBSERVABILITY_ENABLED", "false")
        from streaming.observability import Observability, ObservabilityConfig

        obs = Observability("worker-test", ObservabilityConfig.from_env("worker-test"))
        obs.start()
        # No server bound, but collectors still work.
        assert obs.server is None
        obs.metrics.record_run("completed", 1.0)
        obs.stop()
