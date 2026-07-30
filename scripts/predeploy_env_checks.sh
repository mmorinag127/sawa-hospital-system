#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-sawahospitalsystem}"
REGION="${REGION:-asia-northeast2}"
WEB_SERVICE="${WEB_SERVICE:-}"
WORKER_SERVICE="${WORKER_SERVICE:-}"
WEB_URL="${WEB_URL:-}"
WORKER_URL="${WORKER_URL:-}"
AUTHORIZATION_HEADER="${AUTHORIZATION_HEADER:-}"
CHECK_WEB_PROXY="${CHECK_WEB_PROXY:-0}"
STRICT_OCR_QUALITY="${STRICT_OCR_QUALITY:-0}"


fail=0

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

require_env() {
  local name="$1"
  local value="$2"
  if [ -z "$value" ]; then
    echo "[FAIL] Missing env: $name"
    fail=1
  fi
}

check_http_code_any() {
  local name="$1"
  local url="$2"
  local expected_csv="$3"
  local authorization="${4:-}"
  local code
  if [ -n "$authorization" ]; then
    code=$(curl -sS -H "Authorization: ${authorization}" -o /dev/null -w "%{http_code}" "$url" || true)
  else
    code=$(curl -sS -o /dev/null -w "%{http_code}" "$url" || true)
  fi
  IFS=',' read -r -a expected_arr <<< "$expected_csv"
  for e in "${expected_arr[@]}"; do
    if [ "$code" == "$e" ]; then
      echo "[OK]   $name: ${code}"
      return
    fi
  done
  echo "[FAIL] $name: expected one of [${expected_csv}], got ${code} (${url})"
  fail=1
}

if [ -z "$WEB_URL" ] && [ -n "$WEB_SERVICE" ]; then
  WEB_URL="$(resolve_service_url "$WEB_SERVICE" || true)"
fi

if [ -z "$WORKER_URL" ] && [ -n "$WORKER_SERVICE" ]; then
  WORKER_URL="$(resolve_service_url "$WORKER_SERVICE" || true)"
fi

require_env WEB_URL "$WEB_URL"
require_env WORKER_URL "$WORKER_URL"
require_env AUTHORIZATION_HEADER "$AUTHORIZATION_HEADER"

if [ "$fail" -ne 0 ]; then
  exit 1
fi

check_http_code_any "web_root" "$WEB_URL/" "200,308" ""
check_http_code_any "web_login" "$WEB_URL/login" "200,308" ""

check_http_code_any "worker_health" "$WORKER_URL/health" "200" ""
check_http_code_any "worker_backlog" "$WORKER_URL/health/backlog" "200" "$AUTHORIZATION_HEADER"

check_http_code_any "worker_orders" "$WORKER_URL/orders?include_ocr=false" "200" "$AUTHORIZATION_HEADER"
check_http_code_any "worker_system_status" "$WORKER_URL/system/status" "200" "$AUTHORIZATION_HEADER"

if [ "$CHECK_WEB_PROXY" = "1" ]; then
  check_http_code_any "web_api_orders" "$WEB_URL/api/orders?include_ocr=false" "200,308" "$AUTHORIZATION_HEADER"
  check_http_code_any "web_api_system_status" "$WEB_URL/api/system/status" "200,308" "$AUTHORIZATION_HEADER"
fi


check_http_code_any "worker_orders_unauthenticated" "$WORKER_URL/orders?include_ocr=false" "401" ""
if [ "$CHECK_WEB_PROXY" = "1" ]; then
  check_http_code_any "web_api_orders_unauthenticated" "$WEB_URL/api/orders?include_ocr=false" "401,308" ""
fi

status_json=$(curl -sS -u "$AUTHORIZATION_HEADER" "$WORKER_URL/system/status" || true)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
python3 "$SCRIPT_DIR/check_predeploy_system_status.py" \
  "$status_json" \
  "$STRICT_OCR_QUALITY" \
  "0" \
  || fail=1

if [ "$fail" -ne 0 ]; then
  exit 1
fi

echo "All predeploy checks passed."
