#!/usr/bin/env bash
set -Eeuo pipefail

: "${OCI_BUCKET_NAME:?OCI_BUCKET_NAME is required}"
: "${OCI_NAMESPACE:?OCI_NAMESPACE is required}"
APP_DIR="${APP_DIR:-/opt/quant-trend-lab}"
CUTOFF_EPOCH="$(date -u -d '7 days ago' +%s)"

oci os object list \
  --auth instance_principal \
  --namespace "$OCI_NAMESPACE" \
  --bucket-name "$OCI_BUCKET_NAME" \
  --prefix "runs/" \
  --all \
  --output json |
  jq -r '.data[] | [.name, .["time-created"]] | @tsv' |
  while IFS=$'\t' read -r name created; do
    if (( $(date -u -d "$created" +%s) < CUTOFF_EPOCH )); then
      oci os object delete \
        --auth instance_principal \
        --namespace "$OCI_NAMESPACE" \
        --bucket-name "$OCI_BUCKET_NAME" \
        --object-name "$name" \
        --force
    fi
  done

mapfile -t OLD_BACKUPS < <(
  oci os object list \
    --auth instance_principal \
    --namespace "$OCI_NAMESPACE" \
    --bucket-name "$OCI_BUCKET_NAME" \
    --prefix "backups/postgres/" \
    --all \
    --output json |
    jq -r '.data | sort_by(.["time-created"]) | reverse | .[7:][]?.name'
)
for name in "${OLD_BACKUPS[@]}"; do
  oci os object delete \
    --auth instance_principal \
    --namespace "$OCI_NAMESPACE" \
    --bucket-name "$OCI_BUCKET_NAME" \
    --object-name "$name" \
    --force
done

docker compose -f "$APP_DIR/compose.yaml" exec -T postgres psql \
  --username=quant --dbname=quant --set=ON_ERROR_STOP=1 <<'SQL'
DELETE FROM artifacts WHERE created_at < CURRENT_TIMESTAMP - INTERVAL '7 days';
DELETE FROM backtest_runs WHERE created_at < CURRENT_TIMESTAMP - INTERVAL '7 days';
SQL
