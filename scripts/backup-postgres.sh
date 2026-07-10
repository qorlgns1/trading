#!/usr/bin/env bash
set -Eeuo pipefail

: "${OCI_BUCKET_NAME:?OCI_BUCKET_NAME is required}"
: "${OCI_NAMESPACE:?OCI_NAMESPACE is required}"

APP_DIR="${APP_DIR:-/opt/quant-trend-lab}"
STAMP="$(date -u +%Y-%m-%dT%H%M%SZ)"
TMP_FILE="$(mktemp -t "quant-postgres-${STAMP}.XXXXXX.dump")"
trap 'rm -f "$TMP_FILE"' EXIT

docker compose -f "$APP_DIR/compose.yaml" exec -T postgres \
  pg_dump --username=quant --dbname=quant --format=custom > "$TMP_FILE"

oci os object put \
  --auth instance_principal \
  --namespace "$OCI_NAMESPACE" \
  --bucket-name "$OCI_BUCKET_NAME" \
  --name "backups/postgres/${STAMP}.dump" \
  --file "$TMP_FILE" \
  --force
