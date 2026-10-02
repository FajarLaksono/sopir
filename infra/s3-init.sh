#!/bin/sh
# Create and configure the raw lake bucket. Idempotent.
#
# Shared by docker-compose.yml and docker-compose.test.yml so the two stacks
# cannot drift. Versioning matters most here: a replay writes new objects rather
# than destroying history, and the integration test reads the lake back
# expecting exactly that (new keys, old keys intact).
#
# Re-running is safe and is the expected path. Bucket creation is guarded on an
# existing bucket, while versioning and lifecycle are re-applied every time, so
# edits to infra/lifecycle.json take effect without a manual teardown.
set -eu

: "${AWS_ENDPOINT_URL:?AWS_ENDPOINT_URL must be set}"
: "${BUCKET_NAME:?BUCKET_NAME must be set}"

if aws s3api head-bucket --bucket "$BUCKET_NAME" --endpoint-url "$AWS_ENDPOINT_URL" 2>/dev/null; then
  echo "Bucket $BUCKET_NAME already exists"
else
  aws s3 mb "s3://$BUCKET_NAME" --endpoint-url "$AWS_ENDPOINT_URL"
fi

# Enabled before anything writes, so a replay can only ever add object versions.
aws s3api put-bucket-versioning \
  --bucket "$BUCKET_NAME" \
  --versioning-configuration Status=Enabled \
  --endpoint-url "$AWS_ENDPOINT_URL"

aws s3api put-bucket-lifecycle-configuration \
  --bucket "$BUCKET_NAME" \
  --lifecycle-configuration file:///tmp/lifecycle.json \
  --endpoint-url "$AWS_ENDPOINT_URL"

echo "S3 bucket $BUCKET_NAME configured with versioning and lifecycle"
