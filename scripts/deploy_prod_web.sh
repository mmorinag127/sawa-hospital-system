#!/usr/bin/env bash
set -euo pipefail

WEB_URL="${WEB_URL:-}"
WORKER_URL="${WORKER_URL:-}"
OPERATOR_USER="${OPERATOR_USER:-}"
OPERATOR_PASSWORD="${OPERATOR_PASSWORD:-}"
PROJECT_ID="${PROJECT_ID:-sawahospitalsystem}"
REGION="${REGION:-asia-northeast2}"
GOOGLE_CLIENT_ID="${GOOGLE_CLIENT_ID:-167795504375-hu5316gut0ke8vruc857qsb524q4kq50.apps.googleusercontent.com}"
STRICT_OCR_QUALITY="${STRICT_OCR_QUALITY:-0}"

if [ -z "$WORKER_URL" ]; then
  echo "WORKER_URL is required"
  exit 1
fi

TAG="prod-frontend-$(date +%Y%m%d-%H%M%S)"
IMAGE="asia-northeast2-docker.pkg.dev/${PROJECT_ID}/backend/frontend:${TAG}"
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
      - 'NEXT_PUBLIC_GOOGLE_CLIENT_ID=${GOOGLE_CLIENT_ID}'
      - '-t'
      - '${IMAGE}'
      - '.'
images:
  - '${IMAGE}'
YAML

# Predeploy checks
WEB_URL="$WEB_URL" WORKER_URL="$WORKER_URL" OPERATOR_USER="$OPERATOR_USER" OPERATOR_PASSWORD="$OPERATOR_PASSWORD" \
  CHECK_WEB_PROXY="0" STRICT_GMAIL_WATCH="0" STRICT_OCR_QUALITY="$STRICT_OCR_QUALITY" \
  /Users/mmorinag/Sawa/2025.12/workspace/scripts/predeploy_prod_checks.sh

# Build & deploy

gcloud builds submit /Users/mmorinag/Sawa/2025.12/workspace/frontend --project="$PROJECT_ID" --config="$BUILD_CONFIG"

gcloud run deploy web-prod --project="$PROJECT_ID" --region="$REGION" --image="$IMAGE" --quiet

# Postdeploy checks
WEB_URL="$WEB_URL" WORKER_URL="$WORKER_URL" OPERATOR_USER="$OPERATOR_USER" OPERATOR_PASSWORD="$OPERATOR_PASSWORD" \
  CHECK_WEB_PROXY="1" STRICT_GMAIL_WATCH="0" STRICT_OCR_QUALITY="$STRICT_OCR_QUALITY" \
  /Users/mmorinag/Sawa/2025.12/workspace/scripts/predeploy_prod_checks.sh
