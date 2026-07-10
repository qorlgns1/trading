#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: $0 <backup-object-name>" >&2
  exit 2
fi
: "${OCI_BUCKET_NAME:?OCI_BUCKET_NAME is required}"
: "${OCI_NAMESPACE:?OCI_NAMESPACE is required}"

APP_DIR="${APP_DIR:-/opt/quant-trend-lab}"
TMP_FILE="$(mktemp -t quant-postgres-restore.XXXXXX.dump)"
trap 'rm -f "$TMP_FILE"' EXIT

oci os object get \
  --auth instance_principal \
  --namespace "$OCI_NAMESPACE" \
  --bucket-name "$OCI_BUCKET_NAME" \
  --name "$1" \
  --file "$TMP_FILE"

cat "$TMP_FILE" | docker compose -f "$APP_DIR/compose.yaml" exec -T postgres \
  pg_restore --username=quant --dbname=quant --clean --if-exists
