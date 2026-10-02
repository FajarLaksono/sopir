# Streaming Data Pipeline Plan

Phase 2 planning for **Sopir**: adding a real ingestion pipeline so the platform
can take data from **multiple heterogeneous sources**, including real vehicle
logs, not only synthetic SUMO telemetry.

Everything runs locally on `docker compose`. No AWS account, no paid service,
no external API.

---

## 1. Why This Phase Exists

Phase 1 (see `docs/PLANNING_phase_1_MVP.md`) proves the validation loop end to end,
but its data flow has one producer, one schema and one sink:

```
SUMO worker --> bulk INSERT --> PostgreSQL --> Dashboard
```

That is a batch pipeline wearing a streaming costume. It does not demonstrate
the part of the job that matters for real vehicle data platforms:

| Phase 1 limitation | What production actually requires |
|--------------------|-----------------------------------|
| One producer | Many sources: CAN bus, GNSS, perception, events, plus SUMO |
| One schema | Heterogeneous payloads, each with its own contract |
| Direct DB writes | Producers must not know or care about the sink |
| No raw retention | Raw events replayable for reprocessing, model training, audit |
| No backpressure | Slow consumers must not lose data or kill producers |
| No schema governance | Consumers break when a producer changes a field |

The target companies ingest real vehicle logs. Real logs arrive
**continuously, from many sources, with imperfect timing**, and they must be
**replayable** because a model evaluation needs to be re-run against the same
raw bytes six months later.

So the problem this phase solves is:

> Build a multi-source, replayable, schema-governed ingestion pipeline in which
> producers are decoupled from storage, raw data is retained in an
> object-store-shaped lake, and downstream processing can be rebuilt at any
> time without re-running a single vehicle.

That is the portfolio story. The technology choices below are just the local
expression of it.

---

## 2. Target Architecture

```
                 +-----------------------------------------------+
                 |                PRODUCERS                       |
                 |                                               |
   SUMO worker ----+                                            |
                   +--> vehicle_log_simulator                   |
                        (CAN + GNSS + events, N vehicles)       |
                 +-----------------------------------------------+
                                   |
                                   v  Avro + Schema Registry
                 +-----------------------------------------------+
                 |            sopir.ingest.v1                    |
                 |        Redpanda (Kafka API, KRaft)            |
                 |        one topic per source type              |
                 +-----------------------------------------------+
                         |                       |            |
             at-least-once, offset-committed    |            |
                         |                       |            |
                         v                       v            |
        +---------------------+     +------------------------+  |
        |  RAW LAKE           |     |  PROCESSOR             |  |
        |  LocalStack S3     |     |  consumer group        |  |
        |  Parquet, bronze    |     |  windowed aggregation  |  |
        |  Hive-style layout  |     |  -> evaluation/ reused |  |
        +---------------------+     +------------------------+  |
                  |                            |              |
                  | replay / training set       | query layer  |
                  v                            v              |
        ML notebooks, reprocessing      +--------------------+  |
                                         |     PostgreSQL     |  |
                                         |  metrics, failures |  |
                                         +--------------------+  |
                                                   |
                                                   v
                                       +---------------------+
                                       |      Dashboard       |
                                       |  + streaming health  |
                                       +---------------------+
```

Layered the way a real data platform is layered, so the naming is familiar:
**bronze** (raw, immutable, as-received) in LocalStack S3; **silver** (cleaned,
aggregated) in PostgreSQL. No gold tier is built.

---

## 3. Technology Choices

Constraint: free, open source, single `docker compose up`, no cloud account.

| Layer | Local choice | Production AWS equivalent | Why this one |
|-------|--------------|--------------------------|--------------|
| Stream buffer | **Redpanda** (KRaft mode) | Amazon MSK / Kinesis Data Streams | Kafka wire protocol and consumer API, single binary, no ZooKeeper, pure local. Same client code works on MSK. |
| Schema registry | **Redpanda Schema Registry** | AWS Glue Schema Registry | Free, bundled with Redpanda, Avro-native with compatibility rules. |
| Serialization | **Apache Avro** | Glue / MSK | Compact binary, real schema evolution (backward/forward/full), registry-native. Protobuf would need a hand-rolled registry. |
| Raw lake store | **LocalStack (S3 API)** | Amazon S3 | S3 API verbatim. Same `boto3` client code. Parquet + partitioning work identically. |
| Table format | **Apache Parquet** (via PyArrow) | S3 + Athena / Iceberg | Columnar, compressed, the actual ML-training input format. |
| Stream processing | **Python + confluent-kafka** (see Q1) | Kinesis Data Analytics / Flink / Kafka Streams | Python keeps one language across the whole repo. |
| Object client | **`boto3` against the S3 API** | boto3 | Already in the `boto3` shape. |

### Why Redpanda and not plain Kafka or NATS or Redis Streams

- **vs Apache Kafka**: Kafka 3.x in KRaft mode works, but ZooKeeper-free
  single-node config plus the external Confluent registry is more moving parts.
  Redpanda is one container and ships the registry with it.
- **vs Redis Streams**: Redis Streams cannot express per-source schemas, has no
  consumer-group rebalancing story worth putting on a CV, and has no schema
  registry. For a data-engineering portfolio that is disqualifying.
- **vs NATS JetStream**: solid, but far less recognisable to interviewers than
  the Kafka API.

The bet: an interviewer who has seen Kinesis/MSK will recognise Redpanda
immediately, and the consumer code is the same `confluent-kafka` client they
would write against MSK.

---

## 4. Data Contracts

### 4.1 Topic design

One topic per source type. Clear contract per topic beats a single topic with
union-typed messages, and it lets each source set its own retention and
partition count.

| Topic | Producer | Payload | Partitions |
|-------|----------|---------|-----------|
| `sopir.sim.telemetry.v1` | SUMO worker | per-vehicle pose, speed, angle, lane | 6 |
| `sopir.veh.can.v1` | vehicle log simulator | engine RPM, wheel speed, brake, steer, SOC | 6 |
| `sopir.veh.gnss.v1` | vehicle log simulator | lat, lon, alt, fix quality, HDOP | 6 |
| `sopir.veh.events.v1` | vehicle log simulator | event code, severity, source ECU | 3 |

Keys: `vehicle_id` (or `run_id` for SUMO). Keying guarantees per-vehicle
ordering, which the metric computation depends on.

Dead-letter: `sopir.dlq.v1`. Any message that fails schema or validation is
routed here with the failure reason, never silently dropped.

### 4.2 Schemas (Avro, under `schemas/`)

Every schema carries the envelope the platform needs to reason about data:

```json
{
  "type": "record",
  "name": "VehicleCanSignal",
  "namespace": "sopir.veh",
  "fields": [
    { "name": "schema_version", "type": "string", "default": "1.0.0" },
    { "name": "event_id",      "type": "string" },
    { "name": "vehicle_id",    "type": "string" },
    { "name": "captured_at",   "type": "long",   "logicalType": "timestamp-millis" },
    { "name": "run_id",        "type": ["null", "string"], "default": null },
    { "name": "rpm",           "type": "double" },
    { "name": "wheel_speed",   "type": "double" },
    { "name": "brake_pressure","type": "double" },
    { "name": "steering_angle","type": "double" },
    { "name": "battery_soc",   "type": ["null", "double"], "default": null }
  ]
}
```

Notes:

- `event_id` exists so **silver can deduplicate bronze**. A replayed batch does
  rewrite rows — bronze is append-only and raw row counts grow — but it never
  loses a record or mutates a payload, so collapsing on `event_id` in silver is
  safe and lossless. See §7.2 for the replay evidence.
- `battery_soc` is a `["null", "double"]` union **with a default**, which is
  what makes adding it a backward-compatible change. This is the schema
  evolution demo (see 7.3).
- `run_id` is nullable so vehicle logs from outside a simulation are valid
  first-class data, not an error case.

### 4.3 Lake layout

Hive-style partitioning, which is what makes the raw lake queryable by
Athena/DuckDB later without an index:

```
s3://sopir-raw/
  source=can/dt=2026-10-01/hour=09/vehicle_0a1b/part-00000.parquet
  source=gnss/dt=2026-10-01/hour=09/vehicle_0a1b/part-00000.parquet
  source=sim/dt=2026-10-01/hour=09/run_7f3e/part-00000.parquet
```

Partition by `source` and time only, never by `vehicle_id` as a partition key.
Vehicle-level grouping is a **column**, so the raw lake keeps a sane file
count. Partitioning by a high-cardinality key is the classic small-files
mistake.

Immutable: files are written once, never updated. A correction is a new
object. That is what makes replay honest.

---

## 5. Implementation Phases

Each phase ends with something demonstrable. Nothing is refactored until the
thing it replaces has been shown working.

### Phase 2.1 — Infrastructure (Day 1)

| Task | Files |
|------|-------|
| Redpanda (KRaft) + Schema Registry in compose | `docker-compose.yml` |
| LocalStack in compose, bucket + versioning + lifecycle | `docker-compose.yml`, `infra/lifecycle.json` |
| Topic creation with retention and partition counts | `infra/create-topics.sh` |
| Avro schemas for 4 topics + DLQ | `schemas/*.avsc` |
| Register schemas, record the returned version ids | `scripts/register_schemas.py` |
| New env vars for brokers, bucket, consumer group | `.env.example`, `backend/config.py` |

Exit criteria: `docker compose up -d` yields a healthy Redpanda, a LocalStack bucket
exists, all topics listed by `rpk topic list`, all schemas listed by the
registry REST API.

### Phase 2.2 — Producers (Day 2)

| Task | Files |
|------|-------|
| Producer wrapper: Avro serialise, key by id, delivery callback, bounded retry | `streaming/producer.py` |
| Worker publishes batches instead of `bulk_save_objects` | `simulation/worker.py`, `simulation/telemetry_sink.py` |
| Vehicle log simulator: N vehicles, CAN + GNSS + event streams, seeded RNG | `vehicle_simulator/` |
| Demo config: replay rate, vehicle count, duration | `vehicle_simulator/config.py` |

`simulation/telemetry_sink.py` is an interface with two implementations,
`PostgresSink` and `KafkaSink`. The worker depends on the interface, so Phase 1
behaviour stays selectable via `TELEMETRY_SINK=postgres|kafka`. That keeps the
existing loop working while the new path is proven.

Vehicle simulator output must look like a plausible vehicle: correlated signals
(RPM follows wheel speed, brake ramps before deceleration, SOC falls with
distance travelled), not independent noise. Independent noise makes every
downstream metric meaningless and an interviewer will say so.

Exit criteria: `docker compose logs vehicle_simulator` shows steady production;
Redpanda consumer lag visible; SUMO run telemetry appears in
`sopir.sim.telemetry.v1` with the same row count it used to insert into
Postgres.

### Phase 2.3 — Raw lake landing (Day 3)

| Task | Files |
|------|-------|
| Consumer group: read, batch by size or time, write Parquet to the raw lake, then commit offsets | `streaming/lake_writer.py` |
| Partition path builder from `source` + event time | `streaming/lake_paths.py` |
| DLQ routing on schema/validation failure | `streaming/dlq.py` |

Write Parquet **before** committing offsets. Flip the order and a crash between
the two loses data. This is the single most important correctness detail in the
whole phase and it belongs in the interview story.

The corollary is that a replay *does* re-write those records. Object keys embed
the offset range buffered at flush time, and that range is a function of timing,
so a replay lands a second copy under a different key. Bronze therefore grows;
`event_id` is the dedupe key that makes the duplication harmless downstream.

Exit criteria: object count in the bucket grows with events produced; a
consumer restart does not duplicate rows; a deliberately corrupted payload
lands in the DLQ with a reason.

### Phase 2.4 — Stream processing to Postgres (Day 4)

| Task | Files |
|------|-------|
| Second consumer group on the same topics | `streaming/processor.py` |
| Reuse `evaluation/compute_metrics` and `detect_failures` unchanged | imported, not rewritten |
| Windowed aggregation (per run, per vehicle) then write `metrics` / `failures` | `streaming/processor.py` |
| Postgres stays the query layer; dashboard unchanged | -- |
| New `stream_events` table: source, topic, partition, offset, lag | `backend/models.py` + migration |

`evaluation/` stays pure. The processor is an adapter that feeds it telemetry.
This is the payoff of Phase 1's decision to keep those functions free of I/O.

Exit criteria: end-to-end `docker compose up` produces metrics and failures
without anyone calling the evaluate endpoint; `evaluation/` tests still pass
untouched.

### Phase 2.5 — Observability (Day 5) — **Done**

| Task | Files |
|------|-------|
| Shared observability module: registries, health state, lag collector, metrics server | `streaming/observability.py` |
| Instrument lake writer, processor, worker with counters and lag gauges | `streaming/lake_writer.py`, `streaming/processor.py`, `simulation/worker.py` |
| Mount `/metrics`, `/healthz`, `/readyz` on the backend | `backend/observability.py`, `backend/main.py` |
| Compose healthchecks on all four services | `docker-compose.yml` |
| Prometheus + scrape config | `infra/prometheus.yml` |
| Grafana with provisioned datasource and pipeline dashboard | `infra/grafana/` |

**Planned differently, and the change was right.** The original plan put this in
the Streamlit dashboard (`dashboard/components/streaming_health.py`). That was
wrong for two reasons. Streamlit is for domain metrics — runs, failures, TTC
trends — and infra metrics are a different audience with a different tool. And
consumer lag, throughput and DLQ depth are time series; a dashboard redraw on
every page load is not how anyone reads a lag graph. Those belong in Prometheus
and Grafana, which is also what an interviewer expects to see.

So Phase 2.5 became Prometheus + Grafana, and the Streamlit dashboard stays
focused on validation results. The two concerns do not compete.

Three endpoint types, deliberately distinct:

| Endpoint | Question | Failure means |
|----------|----------|---------------|
| `/healthz` | Is the main loop still turning? | Restart the container |
| `/readyz` | Are dependencies reachable? | Take it out of rotation |
| `/metrics` | What is it doing and how fast? | Investigate |

Two design constraints worth stating in an interview:

**Observability never raises into the service.** A metrics port that will not
bind logs and degrades to in-process-only collection. Losing visibility is
annoying; losing the pipeline is worse.

**Each service owns a private registry**, not the `prometheus_client` global,
which raises `Duplicated timeseries` when two services register the same metric
name in one process — exactly what pytest does when it imports both.

Verified state: 6/6 Prometheus targets up, 4/4 services `healthy` via compose
healthchecks, 16-panel Grafana dashboard auto-provisioned.

### Phase 2.6 — Tests and documentation (Day 6)

| Task | Files |
|------|-------|
| Schema compatibility test (v1 producer vs v2 consumer) | `tests/test_schema_evolution.py` |
| Lake writer write-before-commit ordering | `tests/test_lake_writer.py` |
| `compute_metrics` parity: streamed vs batch input | `scripts/parity_test.py` |
| Consumer replay: no loss, no payload drift | `tests/test_pipeline_integration.py` |
| Alert rules for DLQ, lag, stalled progress | `infra/prometheus-rules.yml` |
| Docker Compose integration profile | `docker-compose.test.yml` |
| CI: lint, test, build, smoke parity | `.github/workflows/ci.yml` |
| Architecture update, streaming health doc | `docs/architecture.md`, this doc |

Parity is the important one, and it is **done** — see Â§11. It proves the
streaming path and the Phase 1 batch path compute the same metrics, which is
what makes the migration safe to claim in an interview.

It is a script rather than a pytest case on purpose: parity needs both paths to
have processed real telemetry through Kafka, which means live containers. A
pytest function that shells out to `docker compose` is a test that fails for
reasons unrelated to what it claims to verify.

---

## 6. What This Phase Does Not Do

| Deferred | Why |
|----------|-----|
| Exactly-once end to end | Needs transactional producers and Postgres write-ahead integration. At-least-once bronze plus `event_id` deduplication in silver reaches effectively-once *results* for far less complexity — but the raw layer does genuinely duplicate on replay, and that is worth stating out loud. |
| Spark / Flink | Heavyweight for a portfolio; the windowing concepts are demonstrable in one Python consumer. |
| Kubernetes | ECS/Fargate mapping belongs in the docs, not in the repo. |
| TLS on Redpanda / S3 auth | Local only. Note it as a production requirement. |
| Iceberg / Delta | Overkill now. Mention Parquet plus partition pruning and move on. |
| Active learning loop | Still Phase 3. The raw lake makes it possible later, which is the point. |
| Real vehicle datasets | Comma.ai and similar need an EULA review. The simulator is safer and demonstrates the same pipeline. Revisit only if a licence is clearly permissive. |

---

## 7. Interview Talking Points

### 7.1 "Walk me through the pipeline."

Producers publish Avro to Redpanda and never touch a database. Two independent
consumer groups read the same topics: one lands raw Parquet into the raw lake as
immutable bronze data, one aggregates into Postgres for the dashboard and the
API. Because the two groups are independent, replay is free: a new consumer
group can rebuild the entire silver layer from the raw lake without asking the
producers for anything.

### 7.2 "What happens when a producer changes a field?"

Avro schemas are registered, and the registry enforces backward compatibility.
Adding an optional field with a default is a backward-compatible change and old
consumers keep working. Removing or retyping a field is rejected at registration
time rather than discovered at 3am by a broken consumer.

### 7.3 "Show me schema evolution working."

Ship `battery_soc` as an optional field with a default. Register v1 and v2,
produce with v2, consume with the v1 reader, show it succeeds. Then attempt to
register a version that deletes a required field and show the registry refusing.
That is the demo, and it is about ten minutes long.

### 7.4 "How do you know you did not lose data?"

Three numbers that must reconcile: records produced, records landed in the lake,
records processed into Postgres, plus DLQ count. The dashboard shows all four.
Offsets are committed after a successful write, never before, so at-least-once
delivery plus `event_id` deduplication in silver gives effectively-once results
without distributed transactions.

Be precise about which layer owns the guarantee. Bronze *can* hold duplicates
after a replay, and it will; what it never does is lose a record or alter a
payload. Silver is where a replay collapses. Anyone who claims raw idempotency
here has not run the replay test.

### 7.5 "Why Redpanda instead of Kafka?"

Single binary, no ZooKeeper, bundled registry, identical client API and wire
protocol, so the same consumer code runs against MSK in production. Swapping
needs a broker address, not a rewrite.

### 7.6 "How does this map to AWS?"

Redpanda to MSK or Kinesis Data Streams. Schema Registry to Glue Schema
Registry. LocalStack to S3, same API. The windowed consumer to Kinesis Data Analytics
or Flink. The phase exists to show the patterns, and the mapping is one sentence
per component.

---

## 8. Effort

| Phase | Days | Cumulative | Status |
|-------|------|-----------|--------|
| 2.1 Infrastructure | 1 | 1 | **Done** |
| 2.2 Producers | 2 | 3 | **Done** |
| 2.3 Raw lake landing | 1 | 4 | **Done** |
| 2.4 Stream processing | 1 | 5 | **Done** |
| 2.5 Observability | 0.5 | 5.5 | **Done** |
| 2.6 Tests and docs | 0.5 | 6 | Partial — parity done, alert rules and CI remain |

Six days planned, five and a half done. Sequenced so there is something
demonstrable at the end of every single day. Phase 2.3 alone is worth showing:
producers to stream to Parquet in an S3-shaped store is the most visually
convincing artifact in the project.

Actual elapsed time exceeded the estimate, and the reason is worth recording:
roughly half of it went into defects that only appeared when the code ran
against real infrastructure, not against mocks. Those are listed in Â§11.

---

## 9. Open Decisions

Resolve before starting Phase 2.2.

| # | Question | Recommendation | Affects |
|---|----------|----------------|---------|
| 1 | Stream processing style: plain `confluent-kafka` consumer, Faust, or RisingWave? | **Plain `confluent-kafka` consumer.** One language, no framework to learn, and every offset and commit decision stays visible, which is exactly what an interviewer probes. Faust adds a DSL to hide them; RisingWave is a second database to operate. | 2.4 |
| 2 | Retain `TELEMETRY_SINK=postgres` after Phase 2.4, or delete it? | **Keep it behind the interface through Phase 2.** Removing it before parity tests pass is how you lose the ability to prove equivalence. | 2.2, 2.6 |
| 3 | Object store versioning and lifecycle? | **Versioning on, 30-day expiry on non-gold prefixes.** Cheap demo of a real raw-retention policy. | 2.1 |
| 4 | Does the vehicle simulator feed `evaluation/` directly, or only through the stream? | **Only through the stream.** Otherwise there is no pipeline to demonstrate. | 2.4 |
| 5 | Replay a real dataset instead of the simulator? | **No.** Licence risk plus a large download. Simulator now, dataset only if a licence is clearly permissive. | 2.2 |
| 6 | Is the SUMO worker also refactored onto the sink interface, or left alone? | **Refactor it.** Otherwise "multi-source" is only true for synthetic data and the claim is weak. | 2.2 |

All six resolved as recommended.

## 10. Where the Build Diverged From This Plan

Recorded because the divergence is the interesting part, not the plan being
wrong everywhere.

| Planned | Built | Why |
|---------|-------|-----|
| Streamlit `streaming_health.py` for lag/throughput/DLQ | Prometheus + Grafana | Streamlit is the domain-metrics tool; lag graphs belong in a time-series stack. Splitting the audiences was better than the plan assumed. |
| Parity as `tests/test_stream_parity.py` | `scripts/parity_test.py` | Parity needs both paths to have processed live telemetry through Kafka. A pytest case shelling out to `docker compose` fails for reasons unrelated to what it verifies. |
| Health endpoints on one port | Backend on 8000, daemons on 9101 | The backend already has a web server; binding a second listener for three routes is waste. The daemons have no server, so they need one. |
| Liveness from a consume-loop heartbeat | Heartbeat on every loop iteration, plus request handling for the backend | Tying liveness to traffic means an idle-but-healthy service reads as dead. Found in live operation, twice. |

## 11. Next Steps

Phase 2.6, in the order worth doing them:

1. **Alert rules** (`infra/prometheus-rules.yml`) — the metrics exist; make them
   page someone. DLQ rate above zero for 5m, `time() — sopir_last_progress_timestamp_seconds`
   above 120s, consumer lag above a threshold, `sopir_service_up == 0`.
2. **Automate initialization** — the last unchecked item in Â§10. Fold schema
   registration and topic creation into an init container so `docker compose up
   --build -d` needs no manual steps.
3. **Reconciliation view in Streamlit** — produced vs landed vs processed vs
   DLQ, as a dashboard panel. Queried by hand today; surfacing it is what makes
   the accounting demonstrable.
4. **Integration test** for replay safety, turning the manual 2.3
   verification into something CI can run. Scope it as "no loss, no payload
   drift" rather than "no duplicates" — the replay test is what disproved the
   latter.
5. **Schema evolution demo** — v1 producer against v2 consumer, plus a breaking
   change the registry refuses.
6. **CI** — lint, test, build, smoke parity.

Phase 3 (active learning, failure-driven scenario prioritization) is unblocked
by the raw lake: the failure priority queue can now select scenarios against a
retained, replayable dataset.

---

## 12. Definition of Done

Phase 2 is complete when all of the following hold:

- [x] At least two genuinely different sources produce to the stream: SUMO telemetry and vehicle logs
- [x] Raw Parquet is present in the lake, Hive-partitioned, immutable
- [x] Metrics and failures are produced by the stream processor, not by an API call
- [x] `evaluation/` is imported unchanged and its tests still pass
- [x] Streamed metrics equal batch metrics for the same telemetry (parity test green)
- [x] A bad payload lands in the DLQ with a reason
- [x] `docs/architecture.md` updated with the streaming flow and the AWS mapping table
- [x] Lag, throughput and DLQ depth are observable per service (Prometheus + Grafana)
- [x] Per-service health and readiness endpoints, wired into compose healthchecks
- [x] `docker compose up --build -d` starts everything with no manual steps — `schema-init`, `topic-init` and `s3-init` run as gated one-shot services
- [x] Consumer replay loses nothing and rewrites no payload as an automated integration test
- [x] Reconciliation view (produced vs landed vs processed vs DLQ) in the Streamlit dashboard
- [x] Schema evolution demo: backward-compatible change passes, breaking change is refused at registration

### Verified state

| Property | Evidence |
|----------|----------|
| Raw lake landing | `7,150 produced = 7,149 landed + 1 DLQ`, exact |
| Replay safety | Full replay under a fresh group: `520 rows → 1040 rows`, **0 lost `event_id`s, 0 divergent payloads**. Bronze duplicates by design; see below |
| Batch/stream parity | 1,535 records both sides; all metrics and all 3 failure rules match |
| Event dedupe | 901 duplicate `event_id`, **0 divergent payloads** — dedupe is correct, not lossy |
| Test suite | 194 passing |
| Scrape targets | 6/6 up (backend, worker, lake_writer, stream_processor, redpanda, prometheus) |
| Compose healthchecks | 4/4 services healthy |

## 13. Defects Found in Live Operation

None of these were visible from reading the code. Each was found by running the
pipeline against real Redpanda, LocalStack and Postgres, which is the argument
for having the integration harness at all.

Worth recording because none of these were visible from reading the code, and
each one is a better interview story than the feature it nearly broke.

### 2.4 — offset conflation skipped records

Simulation offsets and vehicle offsets shared one pending-offset map. Flushing a
vehicle-event batch could commit past sim records still sitting in an open
window, and the next rebalance would skip them silently. Fixed by tracking the
two paths separately and committing per window.

### 2.4 — `event_id` was an unsafe idempotency key

`stream_events` held 2,437 rows against 7,147 source records. The question was
whether that was correct deduplication or data loss. Reading every record in all
three vehicle topics and comparing payloads: **901 duplicated `event_id`, zero
with divergent payloads**. The simulator is seeded, so re-running it re-emits
byte-identical events and `ON CONFLICT DO NOTHING` collapses genuine replays.
`tests/test_processor.py::test_event_id_is_first_write_wins` now pins the
semantics so it is deliberate rather than lucky.

### 2.4 — the worker image was stale and silently ignored config

`Dockerfile.worker` copies `simulation/`, but the image had not been rebuilt
since `telemetry_sink.py` was added. The running worker therefore ignored
`TELEMETRY_SINK=kafka` and wrote to Postgres twice while appearing to succeed.
Nothing errored. This invalidated an earlier claim that the worker published to
Kafka — the producer was verified, the worker path was not.

Lesson worth keeping: containers here have no source bind-mount, so a stale
image is indistinguishable from a code bug until you `docker exec` and look.

### 2.5 — lag over-reported after retention

`compute_lag(committed=90, low=95, high=100)` returned 10. Records 90–94 are
gone; the consumer will never be asked to re-read them. Lag is now measured from
`max(committed, low)`, so the number is always actionable.

### 2.5 — healthy idle consumers reported dead

Liveness was driven by arriving records, so a processor with no traffic aged out
and returned 503 while `sopir_service_up` read 1.0 — two endpoints disagreeing
about the same service. The heartbeat now fires every loop iteration. An idle
consumer is healthy and waiting.

### 2.5 — the backend aged out and was killed

Same root cause, worse. The heartbeat was only set in the FastAPI lifespan, so
after 120s of no traffic `/readyz` went 503 and the compose healthcheck killed
the container. `TestClient` never caught it, because it re-runs the lifespan on
every instantiation. Serving a request is the proof of life for an API server,
so request handling now refreshes the heartbeat. Four regression tests pin this.

### Two measurement traps, not bugs but worth remembering

- **`docker compose exec <svc> ruff check` reads the baked-in image copy**, not
  the working tree. It reported four phantom errors in a file that was clean on
  disk. Linting requires a mounted one-off container.
- **`docker cp <dir> <container>:<existing-dir>` nests instead of
  overwriting**, silently leaving stale test files in place.
