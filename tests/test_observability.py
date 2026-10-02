"""Tests for the shared observability primitives.

These cover the parts that can silently lie: lag arithmetic, the liveness
timeout, readiness gating, and the HTTP contract Prometheus scrapes against.
The collectors themselves are thin wrappers, so the tests focus on behaviour
rather than on asserting that prometheus_client increments numbers.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest

from streaming.observability import (
    DEFAULT_METRICS_PORT,
    OFFSET_UNSET,
    HealthState,
    KafkaLagCollector,
    MetricsServer,
    Observability,
    ObservabilityConfig,
    ServiceMetrics,
)


@pytest.fixture
def metrics() -> ServiceMetrics:
    return ServiceMetrics("test-service")


@pytest.fixture
def health() -> HealthState:
    return HealthState("test-service", heartbeat_max_age_sec=60.0)


@pytest.fixture
def server(metrics, health):
    """A metrics server bound to an ephemeral port."""
    srv = MetricsServer(metrics, health, host="127.0.0.1", port=0)
    assert srv.start() is True
    yield srv
    srv.stop()


def fetch(url: str) -> tuple[int, str, str]:
    """GET a URL, returning (status, content-type, body). HTTP errors included."""
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            return (
                response.status,
                response.headers.get("Content-Type", ""),
                response.read().decode(),
            )
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers.get("Content-Type", ""), exc.read().decode()


def gauge_line(text: str, name: str) -> str:
    """Return the single sample line for a gauge, labels included.

    Gauges here carry service and instance labels, and the instance comes from
    $HOSTNAME, so it is whatever the container or shell happens to be named.
    Tests must not assume it: hardcoding "local" passes on a developer machine
    and fails inside a container.
    """
    samples = [
        line
        for line in text.splitlines()
        if line.startswith(name + "{") or line.startswith(name + " ")
    ]
    assert len(samples) == 1, f"expected exactly one {name} sample, got {samples}"
    return samples[0]


def gauge_value(text: str, name: str) -> float:
    return float(gauge_line(text, name).rsplit(" ", 1)[1])


class TestHealthState:
    def test_starts_not_ready_until_marked_started(self, health):
        # A process that has not finished booting must not accept traffic.
        assert health.alive is True
        assert health.ready is False
        assert health.snapshot()["started"] is False

        health.mark_started()
        assert health.ready is True

    def test_failing_dependency_makes_unready(self, health):
        health.mark_started()
        health.set_check("postgres", False, detail="connection refused")

        assert health.ready is False
        snapshot = health.snapshot()
        assert snapshot["failing"] == ["postgres"]
        assert snapshot["checks"]["postgres"]["detail"] == "connection refused"

    def test_recovers_when_dependency_returns(self, health):
        health.mark_started()
        health.set_check("postgres", False)
        assert health.ready is False

        health.set_check("postgres", True)
        assert health.ready is True
        assert health.snapshot()["failing"] == []

    def test_stale_heartbeat_reports_unalive(self, health):
        health.mark_started()
        assert health.alive is True

        # Simulate a main loop that stopped checking in.
        health._last_heartbeat -= 120.0
        assert health.age_since_heartbeat > 60.0
        assert health.alive is False
        assert health.ready is False

    def test_heartbeat_refreshes_liveness(self, health):
        health._last_heartbeat -= 120.0
        assert health.alive is False

        health.heartbeat()
        assert health.alive is True

    def test_remove_check_restores_readiness(self, health):
        health.mark_started()
        health.set_check("s3", False)
        assert health.ready is False

        health.remove_check("s3")
        assert health.ready is True

    def test_snapshot_reports_all_failing_checks_sorted(self, health):
        health.mark_started()
        health.set_check("zeta", False)
        health.set_check("alpha", False)
        health.set_check("postgres", True)

        assert health.snapshot()["failing"] == ["alpha", "zeta"]


class TestLagArithmetic:
    def test_lag_is_high_water_mark_minus_committed(self):
        assert KafkaLagCollector.compute_lag(committed=90, low=0, high=100) == 10

    def test_fully_caught_up_is_zero(self):
        assert KafkaLagCollector.compute_lag(committed=100, low=0, high=100) == 0

    def test_no_committed_offset_reports_whole_backlog(self):
        # A fresh group reads from `low`, so the retained backlog is real lag.
        # Reporting 0 here would make a cold consumer look healthy.
        assert KafkaLagCollector.compute_lag(OFFSET_UNSET, low=40, high=100) == 60

    def test_negative_committed_offset_is_treated_as_unset(self):
        assert KafkaLagCollector.compute_lag(-1, low=0, high=25) == 25

    def test_retention_past_committed_offset_measures_from_low_watermark(self):
        # Retention moved the low watermark to 95, past the committed 90.
        # Records 90-94 are gone, so lag is measured from 95: the 5 records
        # that still exist. Measuring from the stale committed offset would
        # report 10, including 5 the consumer can never re-read.
        assert KafkaLagCollector.compute_lag(committed=90, low=95, high=100) == 5

    def test_fully_compacted_partition_is_zero(self):
        # low == high means nothing left to read, whatever the committed offset.
        assert KafkaLagCollector.compute_lag(committed=90, low=100, high=100) == 0
        assert KafkaLagCollector.compute_lag(OFFSET_UNSET, low=100, high=100) == 0

    def test_lag_is_never_negative(self):
        # Defensive: high below low should never occur, but must not go negative.
        assert KafkaLagCollector.compute_lag(committed=90, low=100, high=50) == 0

    def test_empty_partition_is_zero(self):
        assert KafkaLagCollector.compute_lag(committed=0, low=0, high=0) == 0


class TestServiceMetrics:
    def test_counters_are_exposed_with_service_and_instance_labels(self, metrics):
        metrics.record_consumed("sopir.sim.telemetry.v1", 3)
        metrics.record_written("parquet_records", 3)
        metrics.record_dlq("deserialization")
        metrics.record_error("sink_write")

        from prometheus_client import generate_latest

        text = generate_latest(metrics.registry).decode()

        assert 'sopir_records_consumed_total{instance="' in text
        assert 'topic="sopir.sim.telemetry.v1"' in text
        assert "sopir_records_consumed_total" in text
        assert 'stage="deserialization"' in text
        assert 'kind="sink_write"' in text

    def test_registries_are_independent_per_service(self):
        # Two services in one process must not collide on the global registry.
        first = ServiceMetrics("lake_writer")
        second = ServiceMetrics("stream-processor")

        assert first.registry is not second.registry

        first.record_consumed("topic-a")
        from prometheus_client import generate_latest

        second_text = generate_latest(second.registry).decode()
        assert "topic-a" not in second_text

    def test_set_lag_clamps_negative_values(self, metrics):
        metrics.set_lag("group", "topic", 0, -5)
        from prometheus_client import generate_latest

        text = generate_latest(metrics.registry).decode()
        assert "sopir_consumer_lag{" in text
        assert "-5" not in text

    def test_open_window_and_pending_event_gauges(self, metrics):
        metrics.set_open_windows(3)
        metrics.set_pending_events(250)

        from prometheus_client import generate_latest

        text = generate_latest(metrics.registry).decode()
        assert gauge_value(text, "sopir_open_windows") == 3.0
        assert gauge_value(text, "sopir_pending_events") == 250.0
        assert f'service="{metrics.service}"' in gauge_line(text, "sopir_open_windows")

    def test_run_duration_ignores_negative_values(self, metrics):
        from prometheus_client import generate_latest

        metrics.record_run("completed", duration_sec=-3.0)
        text = generate_latest(metrics.registry).decode()
        assert 'outcome="completed"' in text

    def test_build_info_is_always_one(self, metrics):
        from prometheus_client import generate_latest

        text = generate_latest(metrics.registry).decode()
        assert "sopir_build_info" in text
        assert " 1.0" in text


class TestMetricsEndpoints:
    def test_metrics_endpoint_returns_prometheus_format(self, server):
        status, content_type, body = fetch(f"http://127.0.0.1:{server.port}/metrics")

        assert status == 200
        assert "text/plain" in content_type
        assert "sopir_service_up" in body

    def test_healthz_returns_200_with_service_identity(self, server):
        status, content_type, body = fetch(f"http://127.0.0.1:{server.port}/healthz")

        assert status == 200
        assert "application/json" in content_type
        payload = json.loads(body)
        assert payload["service"] == "test-service"
        assert payload["alive"] is True

    def test_readyz_returns_200_once_started(self, server, health):
        health.mark_started()
        status, _, body = fetch(f"http://127.0.0.1:{server.port}/readyz")

        assert status == 200
        assert json.loads(body)["ready"] is True

    def test_readyz_returns_503_when_dependency_down(self, server, health):
        health.mark_started()
        health.set_check("postgres", False, "connection refused")

        status, _, body = fetch(f"http://127.0.0.1:{server.port}/readyz")

        assert status == 503
        payload = json.loads(body)
        assert payload["ready"] is False
        assert payload["failing"] == ["postgres"]

    def test_healthz_returns_503_when_loop_is_stuck(self, server, health):
        health.mark_started()
        health._last_heartbeat -= 600.0

        status, _, body = fetch(f"http://127.0.0.1:{server.port}/healthz")

        assert status == 503
        assert json.loads(body)["alive"] is False

    def test_unknown_path_returns_404(self, server):
        status, _, _ = fetch(f"http://127.0.0.1:{server.port}/nope")
        assert status == 404

    def test_trailing_slash_is_tolerated(self, server):
        status, _, _ = fetch(f"http://127.0.0.1:{server.port}/healthz/")
        assert status == 200

    def test_stop_releases_the_port(self, metrics, health):
        srv = MetricsServer(metrics, health, host="127.0.0.1", port=0)
        srv.start()
        port = srv.port
        srv.stop()

        # Rebinding the same port proves the listener really went away.
        again = MetricsServer(ServiceMetrics("other"), health, host="127.0.0.1", port=port)
        assert again.start() is True
        again.stop()


class TestObservabilityFacade:
    def test_disabled_by_zero_port_skips_http(self):
        obs = Observability("svc", ObservabilityConfig(service="svc", metrics_port=0))
        obs.start()

        assert obs.server is None
        obs.stop()

    def test_disabled_by_env_flag_skips_http(self, monkeypatch):
        monkeypatch.setenv("OBSERVABILITY_ENABLED", "false")
        config = ObservabilityConfig.from_env("svc")

        assert config.enabled is False
        assert config.serves_http is False

    def test_start_marks_service_up_and_started(self):
        obs = Observability("svc", ObservabilityConfig(service="svc", metrics_port=0))
        obs.start()

        from prometheus_client import generate_latest

        text = generate_latest(obs.metrics.registry).decode()
        assert gauge_value(text, "sopir_service_up") == 1.0
        assert gauge_value(text, "sopir_service_ready") == 1.0
        assert obs.health.ready is True
        obs.stop()

    def test_stop_marks_service_down(self):
        obs = Observability("svc", ObservabilityConfig(service="svc", metrics_port=0))
        obs.start()
        obs.stop()

        from prometheus_client import generate_latest

        text = generate_latest(obs.metrics.registry).decode()
        assert gauge_value(text, "sopir_service_up") == 0.0

    def test_port_is_confined_to_ephemeral_when_zero(self, metrics, health):
        srv = MetricsServer(metrics, health, host="127.0.0.1", port=0)
        srv.start()
        assert srv.port is not None and srv.port > 0
        srv.stop()


class TestConfigFromEnv:
    def test_reads_metrics_port_from_env(self, monkeypatch):
        monkeypatch.setenv("METRICS_PORT", "9999")
        assert ObservabilityConfig.from_env("svc").metrics_port == 9999

    def test_default_port_is_shared_across_services(self, monkeypatch):
        monkeypatch.delenv("METRICS_PORT", raising=False)
        assert ObservabilityConfig.from_env("svc").metrics_port == DEFAULT_METRICS_PORT

    @pytest.mark.parametrize("raw", ["0", "false", "no", "off", "FALSE"])
    def test_falsy_flags_disable(self, monkeypatch, raw):
        monkeypatch.setenv("OBSERVABILITY_ENABLED", raw)
        assert ObservabilityConfig.from_env("svc").enabled is False

    @pytest.mark.parametrize("raw", ["1", "true", "yes", "on"])
    def test_truthy_flags_enable(self, monkeypatch, raw):
        monkeypatch.setenv("OBSERVABILITY_ENABLED", raw)
        assert ObservabilityConfig.from_env("svc").enabled is True

    def test_lag_interval_has_a_floor(self, monkeypatch):
        # A zero interval would spin the lag thread as fast as the CPU allows.
        from streaming.observability import KafkaLagCollector as K

        collector = K(
            ServiceMetrics("svc"),
            bootstrap_servers="localhost:9092",
            group_id="g",
            topics=("t",),
            interval_sec=0.0,
        )
        assert collector._interval_sec >= 1.0

    def test_lag_collector_without_topics_does_not_start_thread(self):
        collector = KafkaLagCollector(
            ServiceMetrics("svc"),
            bootstrap_servers="localhost:9092",
            group_id="g",
            topics=(),
        )
        collector.start()
        assert collector._thread is None
        collector.stop()
