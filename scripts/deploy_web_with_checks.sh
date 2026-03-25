#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WORKSPACE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

PROJECT_ID="${PROJECT_ID:-sawahospitalsystem}"
REGION="${REGION:-asia-northeast2}"
WEB_SERVICE="${WEB_SERVICE:-}"
WORKER_SERVICE="${WORKER_SERVICE:-}"
WEB_URL="${WEB_URL:-}"
WORKER_URL="${WORKER_URL:-}"
OPERATOR_USER="${OPERATOR_USER:-}"
OPERATOR_PASSWORD="${OPERATOR_PASSWORD:-}"
GOOGLE_CLIENT_ID="${GOOGLE_CLIENT_ID:-167795504375-hu5316gut0ke8vruc857qsb524q4kq50.apps.googleusercontent.com}"
STRICT_OCR_QUALITY="${STRICT_OCR_QUALITY:-0}"
IMAGE_REPO="${IMAGE_REPO:-asia-northeast2-docker.pkg.dev/${PROJECT_ID}/backend/frontend}"
TAG_PREFIX="${TAG_PREFIX:-frontend}"
FRONTEND_DIR="${FRONTEND_DIR:-$WORKSPACE_DIR/frontend}"
PREDEPLOY_SCRIPT="${PREDEPLOY_SCRIPT:-$SCRIPT_DIR/predeploy_env_checks.sh}"
API_PROXY_TARGET_REQUIRES_IDENTITY_TOKEN="${API_PROXY_TARGET_REQUIRES_IDENTITY_TOKEN:-0}"
API_PROXY_TARGET_AUDIENCE="${API_PROXY_TARGET_AUDIENCE:-}"

resolve_service_url() {
  local service_name="$1"
  if [ -z "$service_name" ]; then
    return 1
  fi
  gcloud run services describe "$service_name" \
    --project="$PROJECT_ID" \
    --region="$REGION" \
    --format='value(status.url)'
}

if [ -z "$WEB_SERVICE" ]; then
  echo "WEB_SERVICE is required"
  exit 1
fi

if [ -z "$WORKER_URL" ] && [ -n "$WORKER_SERVICE" ]; then
  WORKER_URL="$(resolve_service_url "$WORKER_SERVICE" || true)"
fi

if [ -z "$WEB_URL" ]; then
  WEB_URL="$(resolve_service_url "$WEB_SERVICE" || true)"
fi

if [ -z "$WORKER_URL" ]; then
  echo "WORKER_URL or WORKER_SERVICE is required"
  exit 1
fi

if [ -z "$API_PROXY_TARGET_AUDIENCE" ]; then
  API_PROXY_TARGET_AUDIENCE="$WORKER_URL"
fi

if [ -z "$WEB_URL" ]; then
  echo "WEB_URL could not be resolved"
  exit 1
fi

TAG="${TAG_PREFIX}-$(date +%Y%m%d-%H%M%S)"
IMAGE="${IMAGE_REPO}:${TAG}"
BUILD_CONFIG="/tmp/cloudbuild.frontend.${TAG}.yaml"

cat > "$BUILD_CONFIG" <<YAML
steps:
  - name: 'gcr.io/cloud-builders/docker'
    args:
      - 'build'
      - '--build-arg'
      - 'NEXT_PUBLIC_API_BASE_URL=${WORKER_URL}'
      - '--build-arg'
      - 'API_PROXY_TARGET=${WORKER_URL}'
      - '--build-arg'
      - 'API_PROXY_TARGET_AUDIENCE=${API_PROXY_TARGET_AUDIENCE}'
      - '--build-arg'
      - 'API_PROXY_TARGET_REQUIRES_IDENTITY_TOKEN=${API_PROXY_TARGET_REQUIRES_IDENTITY_TOKEN}'
      - '--build-arg'
      - 'NEXT_PUBLIC_GOOGLE_CLIENT_ID=${GOOGLE_CLIENT_ID}'
      - '-t'
      - '${IMAGE}'
      - '.'
images:
  - '${IMAGE}'
YAML

WEB_URL="$WEB_URL" \
WORKER_URL="$WORKER_URL" \
WEB_SERVICE="$WEB_SERVICE" \
WORKER_SERVICE="$WORKER_SERVICE" \
PROJECT_ID="$PROJECT_ID" \
REGION="$REGION" \
OPERATOR_USER="$OPERATOR_USER" \
OPERATOR_PASSWORD="$OPERATOR_PASSWORD" \
CHECK_WEB_PROXY="0" \
STRICT_OCR_QUALITY="$STRICT_OCR_QUALITY" \
  "$PREDEPLOY_SCRIPT"

gcloud builds submit "$FRONTEND_DIR" --project="$PROJECT_ID" --config="$BUILD_CONFIG"

gcloud run deploy "$WEB_SERVICE" \
  --project="$PROJECT_ID" \
  --region="$REGION" \
  --image="$IMAGE" \
  --update-env-vars="API_PROXY_TARGET=${WORKER_URL},API_PROXY_TARGET_AUDIENCE=${API_PROXY_TARGET_AUDIENCE},API_PROXY_TARGET_REQUIRES_IDENTITY_TOKEN=${API_PROXY_TARGET_REQUIRES_IDENTITY_TOKEN}" \
  --quiet

WEB_URL="$WEB_URL" \
WORKER_URL="$WORKER_URL" \
WEB_SERVICE="$WEB_SERVICE" \
WORKER_SERVICE="$WORKER_SERVICE" \
PROJECT_ID="$PROJECT_ID" \
REGION="$REGION" \
OPERATOR_USER="$OPERATOR_USER" \
OPERATOR_PASSWORD="$OPERATOR_PASSWORD" \
CHECK_WEB_PROXY="1" \
STRICT_OCR_QUALITY="$STRICT_OCR_QUALITY" \
  "$PREDEPLOY_SCRIPT"

echo "deploy verification passed: service=${WEB_SERVICE} image=${IMAGE}"
