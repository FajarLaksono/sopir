#!/usr/bin/env bash
# Drive one SUMO run down both telemetry paths and diff the results.
#
#   Path A (batch)     TELEMETRY_SINK=postgres -> telemetry table -> evaluate
#   Path B (streaming) TELEMETRY_SINK=kafka    -> Kafka -> processor -> stream_metrics
#
# The comparison itself lives in scripts/parity_test.py. This script only sets
# up the run and keeps both halves keyed to the same run_id, which is what makes
# the two results comparable at all.
#
# Usage:
#   ./scripts/run_parity.sh                 # generate + run both paths
#   ./scripts/run_parity.sh <scenario_id>   # reuse an existing scenario

set -euo pipefail

SCENARIO_ID="${1:-}"
WAIT_TIMEOUT="${PARITY_TIMEOUT:-180}"

# `docker compose run -d` leaves its one-shot container alive and polling after
# the run finishes. A leftover worker with TELEMETRY_SINK=kafka will claim the
# next queued run and publish it to Kafka, starving the Postgres-sink worker you
# are about to start -- which shows up as an empty telemetry table and looks
# like a bug in the sink rather than a leftover process. The EXIT trap covers
# the failure paths too, so a mid-script error does not leave one running.
cleanup_workers() {
  local ids
  ids=$(docker ps --filter "name=sopir-worker" -q || true)
  if [ -n "$ids" ]; then
    echo "==> Stopping leftover worker containers: $ids"
    # shellcheck disable=SC2086
    docker stop $ids >/dev/null 2>&1 || true
  fi
}
trap cleanup_workers EXIT

echo "==> Ensuring topics and schemas exist"
docker compose cp infra/create-topics.sh redpanda:/tmp/create-topics.sh
docker compose exec -T redpanda bash /tmp/create-topics.sh >/dev/null 2>&1 || true
python scripts/register_schemas.py >/dev/null

cleanup_workers

echo "==> Ensuring a scenario exists"
if [ -z "$SCENARIO_ID" ]; then
  docker compose exec -T backend python -m scenario_gen.cli generate >/dev/null
  SCENARIO_ID=$(docker compose exec -T db psql -U postgres -d opendrivelab -tAc \
    "SELECT id FROM scenarios ORDER BY created_at LIMIT 1;")
fi
echo "    scenario: $SCENARIO_ID"

# One run row, driven through the batch sink first so the telemetry table is
# the source of truth for the left-hand side.
echo "==> Creating run (batch path: TELEMETRY_SINK=postgres)"
RUN_ID=$(docker compose exec -T db psql -U postgres -d opendrivelab -tAc \
  "INSERT INTO simulation_runs (scenario_id, status)
   VALUES ('$SCENARIO_ID', 'queued')
   RETURNING id;" | tr -d '[:space:]')
echo "    run_id: $RUN_ID"

echo "==> Running the batch path"
docker compose up -d worker >/dev/null
for _ in $(seq 1 60); do
  STATUS=$(docker compose exec -T db psql -U postgres -d opendrivelab -tAc \
    "SELECT status FROM simulation_runs WHERE id = '$RUN_ID';" | tr -d '[:space:]')
  [ "$STATUS" = "completed" ] && break
  [ "$STATUS" = "failed" ] && { echo "    run failed"; docker compose logs --tail 40 worker; exit 1; }
  sleep 2
done
docker compose stop worker >/dev/null

ROWS=$(docker compose exec -T db psql -U postgres -d opendrivelab -tAc \
  "SELECT count(*) FROM telemetry WHERE run_id = '$RUN_ID';" | tr -d '[:space:]')
echo "    telemetry rows: $ROWS"
[ "$ROWS" = "0" ] && { echo "FAIL: batch path wrote no telemetry"; exit 1; }

# Replay the identical scenario through the Kafka sink. SUMO is deterministic
# for a fixed seed, so the replayed run should match row for row.
echo "==> Rebuilding the worker image before the Kafka-sink run"
# Containers have no source bind-mount, so a worker image built before
# simulation/telemetry_sink.py existed ignores TELEMETRY_SINK entirely and
# writes to Postgres. Both halves of the comparison then read the same table and
# the parity check passes while proving nothing.
docker compose build worker >/dev/null
cleanup_workers

echo "==> Replaying the same scenario through Kafka (TELEMETRY_SINK=kafka)"
docker compose run -d --no-deps \
  -e TELEMETRY_SINK=kafka \
  -e POLL_INTERVAL=1 \
  -e PYTHONUNBUFFERED=1 \
  worker >/dev/null

echo "==> Waiting for the processor to close the window (max ${WAIT_TIMEOUT}s)"
# The window closes on an idle timeout after the last record, not when the run
# ends, so allow headroom beyond the worker finishing.
SLEEP_FOR=$((WAIT_TIMEOUT / 2))
sleep "$SLEEP_FOR"

docker compose exec -T backend python scripts/parity_test.py \
  --run-id "$RUN_ID" --timeout "$WAIT_TIMEOUT" --telemetry "$ROWS"
