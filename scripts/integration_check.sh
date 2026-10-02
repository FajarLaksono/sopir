#!/usr/bin/env bash
# Run tests/test_pipeline_integration.py locally, the same way CI does.
#
#   ./scripts/integration_check.sh              # up, test, tear down
#   ./scripts/integration_check.sh --keep       # leave the stack running
#
# Uses docker-compose.test.yml rather than the main stack on purpose: the
# replay test deliberately duplicates the lake, and that should not happen to a
# developer's running volume. The test itself measures deltas, so --keep works
# and a second run against the same stack still passes.
#
# The teardown is unconditional unless --keep is passed, because a broker and an
# object store left behind quietly consume disk and confuse the next run.

set -euo pipefail

COMPOSE=(docker compose -f docker-compose.test.yml)
KEEP=0
FAILED=0

for arg in "$@"; do
  case "$arg" in
    --keep) KEEP=1 ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "Unknown argument: $arg" >&2; exit 2 ;;
  esac
done

teardown() {
  if [ "$KEEP" = "1" ]; then
    echo "==> Leaving the stack up (--keep). Tear down with:"
    echo "    docker compose -f docker-compose.test.yml down -v"
    return
  fi
  echo "==> Tearing down"
  "${COMPOSE[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
}

# Diagnostics before teardown: a failure here is nearly always visible in the
# init or writer logs, and those are gone once the containers are removed.
report() {
  echo
  echo "==> compose ps"
  "${COMPOSE[@]}" ps -a || true
  for svc in schema-init topic-init s3-init lake_writer; do
    echo
    echo "==> $svc logs"
    "${COMPOSE[@]}" logs --no-color --tail 60 "$svc" || true
  done
}

trap 'if [ "$FAILED" = "1" ]; then report; fi; teardown' EXIT

echo "==> Validating the test stack"
"${COMPOSE[@]}" config --quiet

echo "==> Building the test images"
"${COMPOSE[@]}" build schema-init pipeline_test lake_writer

# Blocks on the init services: topic-init waits for schema-init, and
# lake_writer waits for all three, so this returns only once the topics exist
# and the bucket is versioned.
echo "==> Starting the stack"
"${COMPOSE[@]}" up -d --wait db redpanda localstack schema-init topic-init s3-init lake_writer

echo "==> Running the integration tests"
if ! "${COMPOSE[@]}" run --rm pipeline_test; then
  FAILED=1
  exit 1
fi

echo "==> Integration checks passed"
