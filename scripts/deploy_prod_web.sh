#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-sawahospitalsystem}"
REGION="${REGION:-asia-northeast2}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

WEB_SERVICE="${WEB_SERVICE:-web-prod}"
WORKER_SERVICE="${WORKER_SERVICE:-worker-prod}"
TAG_PREFIX="${TAG_PREFIX:-prod-frontend}"
STRICT_OCR_QUALITY="${STRICT_OCR_QUALITY:-1}"

exec "$SCRIPT_DIR/deploy_web_with_checks.sh" "$@"
