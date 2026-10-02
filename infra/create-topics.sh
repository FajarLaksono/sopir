#!/usr/bin/env bash
# Create Kafka topics for Sopir streaming pipeline.
#
# Idempotent: existing topics are left alone, so this is safe to run on every
# `docker compose up` as the topic-init service. Topic settings that already
# exist are NOT reconciled -- rpk has no "upsert topic config" verb -- so a
# change to the retention values below needs a fresh volume to take effect.

set -euo pipefail

BROKER="${KAFKA_BOOTSTRAP_SERVERS:-redpanda:9092}"

echo "Creating topics on $BROKER..."

# Topic configuration:
# sopir.sim.telemetry.v1  - 6 partitions, 7-day retention, cleanup.policy=delete
# sopir.veh.can.v1        - 6 partitions, 7-day retention
# sopir.veh.gnss.v1       - 6 partitions, 7-day retention
# sopir.veh.events.v1     - 3 partitions, 7-day retention
# sopir.dlq.v1            - 1 partition, 30-day retention, cleanup.policy=compact (keep latest per key)

# rpk topic create has no --if-not-exists and a bare `|| true` would also
# swallow a genuine broker failure, so existence is checked explicitly. The
# distinction matters: "already there" is success, "cannot reach the broker"
# should abort the run and take the dependent services down with it.
#
# The comparison is against awk's first field, not `grep -x` on the whole line.
# `rpk topic list` prints aligned columns, so the line for a topic is
# "sopir.veh.can.v1        6           1" and `grep -qx sopir.veh.can.v1` never
# matches it -- which silently turned this into a non-idempotent script that
# failed on the second `docker compose up`.
create_topic() {
  local topic="$1"
  shift
  if rpk topic list --brokers "$BROKER" 2>/dev/null |
    awk '{print $1}' | grep -qx -- "$topic"; then
    echo "  exists:  $topic"
    return 0
  fi
  rpk topic create "$topic" --brokers "$BROKER" "$@"
}

create_topic sopir.sim.telemetry.v1 \
  --partitions 6 \
  --replicas 1 \
  --config retention.ms=604800000 \
  --config cleanup.policy=delete

create_topic sopir.veh.can.v1 \
  --partitions 6 \
  --replicas 1 \
  --config retention.ms=604800000 \
  --config cleanup.policy=delete

create_topic sopir.veh.gnss.v1 \
  --partitions 6 \
  --replicas 1 \
  --config retention.ms=604800000 \
  --config cleanup.policy=delete

create_topic sopir.veh.events.v1 \
  --partitions 3 \
  --replicas 1 \
  --config retention.ms=604800000 \
  --config cleanup.policy=delete

# Compacted on purpose. The DLQ key is `topic:partition:offset`, so every retry
# of the same bad record lands on the same key and compaction collapses the
# repeats into one entry instead of accumulating duplicates.
create_topic sopir.dlq.v1 \
  --partitions 1 \
  --replicas 1 \
  --config retention.ms=2592000000 \
  --config cleanup.policy=compact \
  --config min.cleanable.dirty.ratio=0.5

echo "Topics ready. Listing all topics:"
rpk topic list --brokers "$BROKER"