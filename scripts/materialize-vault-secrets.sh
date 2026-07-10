#!/usr/bin/env bash
set -Eeuo pipefail

: "${POSTGRES_SECRET_OCID:?POSTGRES_SECRET_OCID is required}"
: "${RATE_LIMIT_SECRET_OCID:?RATE_LIMIT_SECRET_OCID is required}"
: "${OCIR_TOKEN_SECRET_OCID:?OCIR_TOKEN_SECRET_OCID is required}"
: "${OCIR_USERNAME:?OCIR_USERNAME is required}"
: "${OCIR_REGISTRY:?OCIR_REGISTRY is required}"
: "${OCI_BUCKET_NAME:?OCI_BUCKET_NAME is required}"
: "${OCI_NAMESPACE:?OCI_NAMESPACE is required}"
: "${PUBLIC_DOMAIN:?PUBLIC_DOMAIN is required}"

read_secret() {
  oci secrets secret-bundle get \
    --auth instance_principal \
    --secret-id "$1" \
    --stage CURRENT \
    --query 'data."secret-bundle-content".content' \
    --raw-output | base64 --decode
}

POSTGRES_PASSWORD="$(read_secret "$POSTGRES_SECRET_OCID")"
RATE_LIMIT_SECRET="$(read_secret "$RATE_LIMIT_SECRET_OCID")"
OCIR_TOKEN="$(read_secret "$OCIR_TOKEN_SECRET_OCID")"

for value in "$POSTGRES_PASSWORD" "$RATE_LIMIT_SECRET" "$OCIR_TOKEN"; do
  if [[ ! "$value" =~ ^[A-Za-z0-9_+/@.=-]+$ ]]; then
    echo "Vault secrets must use a single-line shell-safe character set" >&2
    exit 1
  fi
done
if (( ${#RATE_LIMIT_SECRET} < 32 )); then
  echo "rate-limit secret must be at least 32 characters" >&2
  exit 1
fi

umask 077
cat > /opt/quant-trend-lab/.env <<EOF
POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
RATE_LIMIT_SECRET=${RATE_LIMIT_SECRET}
ARTIFACT_BACKEND=oci
OCI_BUCKET_NAME=${OCI_BUCKET_NAME}
OCI_NAMESPACE=${OCI_NAMESPACE}
OCI_REGION=${OCI_REGION:-ap-seoul-1}
PUBLIC_DOMAIN=${PUBLIC_DOMAIN}
OCIR_REGISTRY=${OCIR_REGISTRY}
EOF

printf '%s' "$OCIR_TOKEN" | docker login "$OCIR_REGISTRY" --username "$OCIR_USERNAME" --password-stdin
