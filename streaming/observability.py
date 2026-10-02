"""Observability primitives shared by every long-running service.

The pipeline has four daemons that fail in four different ways, and the symptom
is the same each time: something stopped and nobody noticed until the data was
missing. Each one needs the same three answers, so they live here rather than
being reimplemented per service:

    /metrics   Prometheus exposition for throughput, lag, and error counters
    /healthz   liveness: is the main loop still turning?
    /readyz    readiness: are the dependencies it needs actually reachable?

Two design choices are deliberate:

**Observability must never take down the service it observes.** Every failure
path here logs and degrades. A metrics port that will not bind, a lag poll that
raises, a scrape that throws: none of those may kill a consumer. Losing
visibility is annoying; losing the pipeline is worse.

**Each service owns its own registry.** Prometheus counters live in a global
default registry that raises ``Duplicated timeseries`` when a metric name is
defined twice, which is exactly what happens when tests import two services in
one process. A per-service registry keeps that failure mode away, and
``generate_latest`` does not care which registry it is handed.

Usage is three lines in a service's ``main()``::

    obs = Observability("lake_writer")
    obs.start()
    ...
    obs.metrics.record_consumed(topic)
    obs.health.heartbeat()

and nothing more. If ``METRICS_PORT`` is unset or ``0``, or
``OBSERVABILITY_ENABLED`` is false, the HTTP server is skipped entirely and the
service behaves exactly as it did before.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional

from confluent_kafka import Consumer, TopicPartition
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

logger = logging.getLogger(__name__)

# Chosen to sit above the app ports (8000 backend, 8501 dashboard) so nothing
# collides, and to be identical across services since each container has its own
# network namespace.
DEFAULT_METRICS_PORT = 9101

# Kafka reports "no committed offset" as a negative sentinel rather than null.
OFFSET_UNSET = -1001

# Batch size buckets span the real working range: vehicle topics flush in the
# hundreds, simulation telemetry in the low thousands.
_BATCH_BUCKETS = (1, 10, 50, 100, 200, 500, 1_000, 5_000, 10_000)


def _instance_id() -> str:
    """Identify this process among replicas of the same service."""
    return os.getenv("HOSTNAME") or os.getenv("POD_NAME") or "local"


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off")


@dataclass
class ObservabilityConfig:
    """Where and whether to expose observability endpoints."""

    service: str
    metrics_host: str = "0.0.0.0"
    metrics_port: int = DEFAULT_METRICS_PORT
    lag_interval_sec: float = 10.0
    heartbeat_max_age_sec: float = 60.0
    enabled: bool = True

    @classmethod
    def from_env(cls, service: str) -> "ObservabilityConfig":
        """Read config from the environment.

        ``METRICS_PORT=0`` disables the HTTP server without disabling metric
        collection, which is useful for one-shot CLI runs that should not try to
        bind a port.
        """
        return cls(
            service=service,
            metrics_host=os.getenv("METRICS_HOST", cls.metrics_host),
            metrics_port=int(os.getenv("METRICS_PORT", cls.metrics_port)),
            lag_interval_sec=float(os.getenv("LAG_REFRESH_SEC", cls.lag_interval_sec)),
            heartbeat_max_age_sec=float(
                os.getenv("HEARTBEAT_MAX_AGE_SEC", cls.heartbeat_max_age_sec)
            ),
            enabled=_env_flag("OBSERVABILITY_ENABLED", True),
        )

    @property
    def serves_http(self) -> bool:
        """Whether to bind an HTTP port at all."""
        return self.enabled and self.metrics_port > 0


class ServiceMetrics:
    """Prometheus collectors for one service.

    Every service registers the same metric names, so one dashboard query works
    across all four daemons instead of needing a per-service query. Labels carry
    the service name and the instance id, which is what makes ``--scale
    worker=3`` visible as three distinct series rather than one ambiguous one.
    """

    def __init__(self, service: str, version: str = "1.0.0"):
        self.service = service
        self.instance = _instance_id()
        self.registry = CollectorRegistry()

        common = ["service", "instance"]

        self.build_info = Gauge(
            "sopir_build_info",
            "Build metadata; always 1, labels carry the values",
            common + ["version"],
            registry=self.registry,
        )
        self.build_info.labels(service=service, instance=self.instance, version=version).set(1)

        self.service_up = Gauge(
            "sopir_service_up",
            "1 while the service main loop is running",
            common,
            registry=self.registry,
        )
        self.service_ready = Gauge(
            "sopir_service_ready",
            "1 when every registered dependency check passes",
            common,
            registry=self.registry,
        )

        self.records_consumed = Counter(
            "sopir_records_consumed_total",
            "Records read from Kafka",
            common + ["topic"],
            registry=self.registry,
        )
        self.records_written = Counter(
            "sopir_records_written_total",
            "Records durably written downstream",
            common + ["kind"],
            registry=self.registry,
        )
        self.dlq_routed = Counter(
            "sopir_dlq_routed_total",
            "Records routed to the dead letter topic",
            common + ["stage"],
            registry=self.registry,
        )
        self.errors = Counter(
            "sopir_errors_total",
            "Errors by kind; a rising sink_write or db_errors is a real problem",
            common + ["kind"],
            registry=self.registry,
        )
        self.runs = Counter(
            "sopir_runs_total",
            "Simulation runs by terminal outcome",
            common + ["outcome"],
            registry=self.registry,
        )
        self.windows_closed = Counter(
            "sopir_windows_closed_total",
            "Stream windows evaluated",
            common,
            registry=self.registry,
        )

        self.consumer_lag = Gauge(
            "sopir_consumer_lag",
            "Records between the committed offset and the high watermark",
            common + ["group", "topic", "partition"],
            registry=self.registry,
        )
        self.open_windows = Gauge(
            "sopir_open_windows",
            "Windows currently held in memory",
            common,
            registry=self.registry,
        )
        self.pending_events = Gauge(
            "sopir_pending_events",
            "Vehicle records buffered but not yet written",
            common,
            registry=self.registry,
        )
        self.last_progress = Gauge(
            "sopir_last_progress_timestamp_seconds",
            "Unix time of the last successful forward step; flat means stuck",
            common,
            registry=self.registry,
        )

        self.batch_records = Histogram(
            "sopir_batch_records",
            "Records per flush",
            common,
            buckets=_BATCH_BUCKETS,
            registry=self.registry,
        )
        self.run_duration = Histogram(
            "sopir_run_duration_seconds",
            "Wall time per simulation run",
            common,
            buckets=(1, 5, 10, 30, 60, 120, 300, 600, float("inf")),
            registry=self.registry,
        )

        # HTTP request collectors, used by the FastAPI backend. Registered here
        # so every service exposes the same metric set even though only one of
        # them serves HTTP traffic.
        self.http_requests = Counter(
            "sopir_http_requests_total",
            "HTTP requests served, by normalized route",
            common + ["method", "route"],
            registry=self.registry,
        )
        self.http_duration = Histogram(
            "sopir_http_request_duration_seconds",
            "HTTP request latency",
            common + ["route"],
            buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, float("inf")),
            registry=self.registry,
        )

    def _labels(self, **extra: str) -> dict[str, str]:
        return {"service": self.service, "instance": self.instance, **extra}

    def record_consumed(self, topic: str, count: int = 1) -> None:
        self.records_consumed.labels(**self._labels(topic=topic)).inc(count)

    def record_written(self, kind: str, count: int = 1) -> None:
        self.records_written.labels(**self._labels(kind=kind)).inc(count)

    def record_dlq(self, stage: str, count: int = 1) -> None:
        self.dlq_routed.labels(**self._labels(stage=stage)).inc(count)

    def record_error(self, kind: str, count: int = 1) -> None:
        self.errors.labels(**self._labels(kind=kind)).inc(count)

    def record_run(self, outcome: str, duration_sec: Optional[float] = None) -> None:
        self.runs.labels(**self._labels(outcome=outcome)).inc()
        if duration_sec is not None:
            self.run_duration.labels(**self._labels()).observe(max(0.0, duration_sec))

    def record_window(self, records: int) -> None:
        self.windows_closed.labels(**self._labels()).inc()
        self.batch_records.labels(**self._labels()).observe(records)

    def set_lag(self, group: str, topic: str, partition: int, lag: int) -> None:
        self.consumer_lag.labels(
            **self._labels(group=group, topic=topic, partition=str(partition))
        ).set(max(0, lag))

    def set_open_windows(self, count: int) -> None:
        self.open_windows.labels(**self._labels()).set(count)

    def set_pending_events(self, count: int) -> None:
        self.pending_events.labels(**self._labels()).set(count)

    def touch_progress(self) -> None:
        """Mark that the service just made forward progress."""
        self.last_progress.labels(**self._labels()).set(time.time())

    def set_up(self, up: bool) -> None:
        self.service_up.labels(**self._labels()).set(1 if up else 0)

    def set_ready(self, ready: bool) -> None:
        self.service_ready.labels(**self._labels()).set(1 if ready else 0)


class HealthState:
    """Liveness and readiness for one service.

    Liveness answers "is the main loop still turning?" and is driven by an
    explicit heartbeat, because a consumer blocked on a broker socket stays
    process-alive while doing nothing. Readiness answers "can this instance
    serve right now?" and is driven by dependency checks.
    """

    def __init__(self, service: str, heartbeat_max_age_sec: float = 60.0):
        self.service = service
        self.heartbeat_max_age_sec = heartbeat_max_age_sec
        self._lock = threading.Lock()
        self._checks: dict[str, dict[str, Any]] = {}
        self._last_heartbeat = time.monotonic()
        self._started = False

    def heartbeat(self) -> None:
        """Called from the main loop. Also satisfies liveness by itself."""
        with self._lock:
            self._last_heartbeat = time.monotonic()

    @property
    def age_since_heartbeat(self) -> float:
        with self._lock:
            return time.monotonic() - self._last_heartbeat

    @property
    def alive(self) -> bool:
        """True while the loop has checked in recently enough."""
        return self.age_since_heartbeat <= self.heartbeat_max_age_sec

    @property
    def ready(self) -> bool:
        """True once started, with a live loop and no failing dependency."""
        with self._lock:
            started = self._started
            checks_ok = all(check["healthy"] for check in self._checks.values())
        return started and self.alive and checks_ok

    def mark_started(self) -> None:
        with self._lock:
            self._started = True
        self.heartbeat()

    def set_check(self, name: str, healthy: bool, detail: str = "") -> None:
        """Record a dependency probe result. Unknown checks default to healthy."""
        with self._lock:
            self._checks[name] = {"healthy": bool(healthy), "detail": detail}

    def remove_check(self, name: str) -> None:
        with self._lock:
            self._checks.pop(name, None)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            checks = {name: dict(check) for name, check in self._checks.items()}
            started = self._started
        failing = sorted(name for name, check in checks.items() if not check["healthy"])
        return {
            "service": self.service,
            "instance": _instance_id(),
            "status": "ok" if self.ready else "degraded",
            "alive": self.alive,
            "ready": self.ready,
            "started": started,
            "seconds_since_heartbeat": round(self.age_since_heartbeat, 3),
            "checks": checks,
            "failing": failing,
        }


def _handler_for(metrics: ServiceMetrics, health: HealthState) -> type[BaseHTTPRequestHandler]:
    """Build a request handler bound to one service's metrics and health."""

    class Handler(BaseHTTPRequestHandler):
        # Silence the default stderr access log; scrape noise is not a signal.
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            logger.debug("metrics endpoint: " + format, *args)

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_json(self, status: int, payload: dict[str, Any]) -> None:
            self._send(status, json.dumps(payload, indent=2).encode(), "application/json")

        def do_GET(self) -> None:  # noqa: N802 - name fixed by BaseHTTPRequestHandler
            path = self.path.split("?", 1)[0].rstrip("/") or "/"

            if path == "/metrics":
                try:
                    body = generate_latest(metrics.registry)
                except Exception:
                    logger.exception("Failed to render metrics")
                    self._send_json(
                        500, {"error": "metrics rendering failed", "service": metrics.service}
                    )
                    return
                self._send(200, body, CONTENT_TYPE_LATEST)
                return

            if path == "/healthz":
                snapshot = health.snapshot()
                self._send_json(200 if snapshot["alive"] else 503, snapshot)
                return

            if path == "/readyz":
                snapshot = health.snapshot()
                self._send_json(200 if snapshot["ready"] else 503, snapshot)
                return

            self._send_json(404, {"error": "not found", "path": path})

    return Handler


class MetricsServer:
    """Threaded HTTP server exposing /metrics, /healthz, /readyz."""

    def __init__(
        self,
        metrics: ServiceMetrics,
        health: HealthState,
        host: str = "0.0.0.0",
        port: int = DEFAULT_METRICS_PORT,
    ):
        self._metrics = metrics
        self._health = health
        self._host = host
        self._port = port
        self._server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    @property
    def port(self) -> Optional[int]:
        """The bound port, which differs from the request when 0 was passed."""
        if self._server is None:
            return None
        return self._server.server_address[1]

    def start(self) -> bool:
        """Bind and serve in a daemon thread.

        Returns False instead of raising when the port cannot be bound: an
        observability endpoint that collides should degrade to logs, not stop a
        consumer that is doing its job.
        """
        handler = _handler_for(self._metrics, self._health)
        try:
            self._server = ThreadingHTTPServer((self._host, self._port), handler)
            self._server.daemon_threads = True
        except OSError:
            logger.exception(
                "Could not bind metrics server on %s:%s; metrics stay in-process only",
                self._host,
                self._port,
            )
            self._server = None
            return False

        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name=f"{self._metrics.service}-metrics",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "Metrics server listening on %s:%s (/metrics /healthz /readyz)",
            self._host,
            self.port,
        )
        return True

    def stop(self) -> None:
        if self._server is None:
            return
        try:
            self._server.shutdown()
            self._server.server_close()
        except Exception:
            logger.debug("Error shutting down metrics server", exc_info=True)
        finally:
            self._server = None
            self._thread = None


class KafkaLagCollector:
    """Publishes per-partition consumer lag for a consumer group.

    Uses its own short-lived ``Consumer`` rather than the service's, because
    ``confluent_kafka`` clients are not safe to share across threads and the
    service's consumer owns committed offsets. This one never subscribes, never
    commits, and joins no group; it only issues OffsetFetch and ListOffsets
    requests.
    """

    def __init__(
        self,
        metrics: ServiceMetrics,
        *,
        bootstrap_servers: str,
        group_id: str,
        topics: tuple[str, ...] | list[str],
        interval_sec: float = 10.0,
        timeout_sec: float = 5.0,
    ):
        self._metrics = metrics
        self._group_id = group_id
        self._topics = tuple(topics)
        self._interval_sec = max(1.0, interval_sec)
        self._timeout_sec = timeout_sec
        self._bootstrap_servers = bootstrap_servers
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._consumer: Optional[Consumer] = None

    @staticmethod
    def compute_lag(committed: int, low: int, high: int) -> int:
        """Records between the committed offset and the end of the partition.

        The starting point is ``max(committed, low)``, not ``committed`` alone.
        Retention and compaction can move the low watermark past a committed
        offset, and those records are gone: the consumer will never be asked to
        re-read them. Measuring from the stale offset would report lag the group
        can do nothing about. With no committed offset at all the group starts
        at ``low``, so the whole retained backlog counts as lag rather than a
        misleading zero.
        """
        start = low if committed == OFFSET_UNSET or committed < 0 else max(committed, low)
        return max(0, high - start)

    def _ensure_consumer(self) -> Consumer:
        if self._consumer is None:
            self._consumer = Consumer(
                {
                    "bootstrap.servers": self._bootstrap_servers,
                    "group.id": self._group_id,
                    "enable.auto.commit": False,
                    # Never subscribe; this client exists only for metadata.
                    "allow.auto.create.topics": False,
                }
            )
        return self._consumer

    def collect_once(self) -> dict[str, int]:
        """Poll lag for every configured topic and partition.

        Returns the lag keyed by ``topic:partition``. Never raises: a broker
        that is down shows up as stale gauge values and an error log, which is
        the correct outcome for a monitoring thread.
        """
        consumer = self._ensure_consumer()
        metadata = consumer.list_topics(timeout=self._timeout_sec)
        partitions: list[TopicPartition] = []
        for topic in self._topics:
            topic_meta = metadata.topics.get(topic)
            if topic_meta is None or topic_meta.error is not None:
                logger.debug("Lag collector: no metadata for %s", topic)
                continue
            partitions.extend(TopicPartition(topic, pid) for pid in sorted(topic_meta.partitions))

        if not partitions:
            return {}

        committed = consumer.committed(partitions, timeout=self._timeout_sec)
        lags: dict[str, int] = {}
        for tp in committed:
            try:
                low, high = consumer.get_watermark_offsets(
                    tp, timeout=self._timeout_sec, cached=False
                )
            except Exception:
                logger.debug("Lag collector: watermarks unavailable for %s", tp, exc_info=True)
                continue
            lag = self.compute_lag(tp.offset, low, high)
            self._metrics.set_lag(self._group_id, tp.topic, tp.partition, lag)
            lags[f"{tp.topic}:{tp.partition}"] = lag
        return lags

    def start(self) -> None:
        if not self._topics:
            logger.debug("Lag collector: no topics configured, not starting")
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="kafka-lag", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.collect_once()
            except Exception:
                # A monitoring loop that dies is worse than one that is noisy.
                self._metrics.record_error("lag_collect")
                logger.exception("Lag collection failed; retrying")
            self._stop.wait(self._interval_sec)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None
        if self._consumer is not None:
            try:
                self._consumer.close()
            except Exception:
                logger.debug("Error closing lag consumer", exc_info=True)
            self._consumer = None


class Observability:
    """Facade wiring metrics, health, lag, and the HTTP server for one service.

    Daemons construct this once in ``main()`` and call it from their loop. The
    FastAPI backend uses :class:`ServiceMetrics` and :class:`HealthState`
    directly instead, because it already has a web server to mount routes on.
    """

    def __init__(self, service: str, config: Optional[ObservabilityConfig] = None):
        self.config = config or ObservabilityConfig.from_env(service)
        self.metrics = ServiceMetrics(service)
        self.health = HealthState(service, heartbeat_max_age_sec=self.config.heartbeat_max_age_sec)
        self._server: Optional[MetricsServer] = None
        self._lag: Optional[KafkaLagCollector] = None

    @property
    def server(self) -> Optional[MetricsServer]:
        return self._server

    def start(
        self,
        *,
        lag: Optional[KafkaLagCollector] = None,
        ready_checks: bool = True,
    ) -> "Observability":
        """Start the endpoints and lag collector. Safe to call once."""
        self.metrics.set_up(True)
        self.metrics.set_ready(ready_checks)
        self.health.mark_started()

        if self.config.serves_http:
            self._server = MetricsServer(
                self.metrics,
                self.health,
                host=self.config.metrics_host,
                port=self.config.metrics_port,
            )
            if not self._server.start():
                self._server = None

        if lag is not None:
            self._lag = lag
            self._lag.start()

        return self

    def set_ready(self, ready: bool) -> None:
        """Record the overall readiness verdict on the metric gauge."""
        self.metrics.set_ready(ready)

    def stop(self) -> None:
        self.metrics.set_up(False)
        if self._lag is not None:
            self._lag.stop()
            self._lag = None
        if self._server is not None:
            self._server.stop()
            self._server = None
