#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: $0 <image-tag>" >&2
  exit 2
fi

TAG="$1"
APP_DIR="${APP_DIR:-/opt/quant-trend-lab}"
COMPOSE_FILE="$APP_DIR/compose.yaml"
RELEASE_DIR="$APP_DIR/releases"
CURRENT_ENV="$RELEASE_DIR/current.env"
PREVIOUS_ENV="$RELEASE_DIR/previous.env"

mkdir -p "$RELEASE_DIR"
if [[ -f "$CURRENT_ENV" ]]; then
  cp "$CURRENT_ENV" "$PREVIOUS_ENV"
fi

cat > "$CURRENT_ENV" <<EOF
API_IMAGE=${OCIR_REGISTRY}/${OCI_NAMESPACE}/quant-trend-lab/api:${TAG}
WEB_IMAGE=${OCIR_REGISTRY}/${OCI_NAMESPACE}/quant-trend-lab/web:${TAG}
EOF

set -a
# shellcheck disable=SC1090,SC1091
source "$APP_DIR/.env"
# shellcheck disable=SC1090
source "$CURRENT_ENV"
set +a

: "${PUBLIC_DOMAIN:?PUBLIC_DOMAIN is required}"
if [[ "$PUBLIC_DOMAIN" == "localhost" || "$PUBLIC_DOMAIN" =~ ^[0-9.]+$ ]]; then
  echo "production requires a DNS hostname, not localhost or an IP address" >&2
  exit 1
fi

# shellcheck disable=SC2329
rollback() {
  if [[ -f "$PREVIOUS_ENV" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$PREVIOUS_ENV"
    set +a
    docker compose -f "$COMPOSE_FILE" --profile production up -d --remove-orphans
  fi
}
trap rollback ERR

docker compose -f "$COMPOSE_FILE" pull api worker web caddy postgres valkey
docker compose -f "$COMPOSE_FILE" --profile tools run --rm migrate
docker compose -f "$COMPOSE_FILE" --profile production up -d --remove-orphans

for _ in $(seq 1 30); do
  if curl --fail --silent --show-error "https://${PUBLIC_DOMAIN}/health/ready" >/dev/null; then
    trap - ERR
    docker image prune -f --filter "until=168h"
    exit 0
  fi
  sleep 5
done

echo "deployment health check timed out" >&2
exit 1
