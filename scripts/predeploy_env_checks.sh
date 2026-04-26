#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-sawahospitalsystem}"
REGION="${REGION:-asia-northeast2}"
WEB_SERVICE="${WEB_SERVICE:-}"
WORKER_SERVICE="${WORKER_SERVICE:-}"
WEB_URL="${WEB_URL:-}"
WORKER_URL="${WORKER_URL:-}"
OPERATOR_USER="${OPERATOR_USER:-}"
OPERATOR_PASSWORD="${OPERATOR_PASSWORD:-}"
CHECK_WEB_PROXY="${CHECK_WEB_PROXY:-0}"
STRICT_OCR_QUALITY="${STRICT_OCR_QUALITY:-0}"
ALLOW_BASIC_ONLY_AUTH="${ALLOW_BASIC_ONLY_AUTH:-0}"

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
  local auth="$4"
  local code
  if [ -n "$auth" ]; then
    code=$(curl -sS -u "$auth" -o /dev/null -w "%{http_code}" "$url" || true)
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
require_env OPERATOR_USER "$OPERATOR_USER"
require_env OPERATOR_PASSWORD "$OPERATOR_PASSWORD"

if [ "$fail" -ne 0 ]; then
  exit 1
fi

check_http_code_any "web_root" "$WEB_URL/" "200,308" ""
check_http_code_any "web_login" "$WEB_URL/login" "200,308" ""

check_http_code_any "worker_health" "$WORKER_URL/health" "200" ""
check_http_code_any "worker_backlog" "$WORKER_URL/health/backlog" "200" ""

check_http_code_any "worker_orders" "$WORKER_URL/orders?include_ocr=false" "200" "$OPERATOR_USER:$OPERATOR_PASSWORD"
check_http_code_any "worker_system_status" "$WORKER_URL/system/status" "200" "$OPERATOR_USER:$OPERATOR_PASSWORD"

if [ "$CHECK_WEB_PROXY" = "1" ]; then
  check_http_code_any "web_api_orders" "$WEB_URL/api/orders?include_ocr=false" "200,308" "$OPERATOR_USER:$OPERATOR_PASSWORD"
  check_http_code_any "web_api_system_status" "$WEB_URL/api/system/status" "200,308" "$OPERATOR_USER:$OPERATOR_PASSWORD"
fi

status_json=$(curl -sS -u "$OPERATOR_USER:$OPERATOR_PASSWORD" "$WORKER_URL/system/status" || true)
python3 - <<'PY' "$status_json" "$STRICT_OCR_QUALITY" "$ALLOW_BASIC_ONLY_AUTH" || fail=1
import json,sys
raw=sys.argv[1]
strict_quality=(sys.argv[2] == "1")
allow_basic_only_auth=(sys.argv[3] == "1")
try:
    data=json.loads(raw)
except Exception:
    print("[FAIL] system_status JSON parse failed")
    raise SystemExit(1)

oauth=data.get("oauth_config", {})
intake=data.get("intake", {})
intake_mode = str(intake.get("mode") or "").strip().lower()
if intake_mode != "manual_upload":
    print(f"[FAIL] intake.mode is invalid: {intake_mode or 'missing'}")
    raise SystemExit(1)
if not intake.get("manual_upload_enabled"):
    print("[FAIL] intake.manual_upload_enabled is false")
    raise SystemExit(1)
upload_storage = intake.get("manual_upload_storage") or {}
if not upload_storage.get("configured"):
    print("[FAIL] manual_upload_storage.configured is false")
    raise SystemExit(1)
if not oauth.get("configured") and not allow_basic_only_auth:
    print("[FAIL] oauth_config.configured is false")
    raise SystemExit(1)
if not oauth.get("configured") and allow_basic_only_auth:
    print("[WARN] oauth_config.configured is false (basic-auth staging mode allowed)")
print(f"[OK]   intake mode: {intake_mode or 'manual_upload'}")

quality=data.get("ocr_reparse_quality")
gate_status = ""
scope_mode = ""
included_jobs = 0
if isinstance(quality, dict):
    gate = quality.get("gate")
    if isinstance(gate, dict):
        gate_status = str(gate.get("status") or "").strip().lower()
    scope = quality.get("scope")
    if isinstance(scope, dict):
        scope_mode = str(scope.get("mode") or "").strip().lower()
        try:
            included_jobs = int(scope.get("included_jobs") or 0)
        except Exception:
            included_jobs = 0

if strict_quality:
    allow_warming_up = (
        gate_status == "insufficient_data"
        and scope_mode == "explicit_only"
        and included_jobs == 0
    )
    if gate_status not in {"pass"} and not allow_warming_up:
        fail_detail = ""
        if isinstance(quality, dict):
            gate = quality.get("gate")
            if isinstance(gate, dict):
                fail_detail = (
                    f" fail_providers={gate.get('fail_providers')}"
                    f" warming_up={gate.get('warming_up_providers')}"
                    f" scope_mode={scope_mode or 'missing'} included_jobs={included_jobs}"
                )
        print(f"[FAIL] ocr_reparse_quality.gate.status is {gate_status or 'missing'}{fail_detail}")
        raise SystemExit(1)
    if allow_warming_up:
        print(
            "[WARN] ocr_reparse_quality.gate.status is insufficient_data "
            f"(explicit_only warming up; included_jobs={included_jobs})"
        )
else:
    if gate_status and gate_status != "pass":
        print(f"[WARN] ocr_reparse_quality.gate.status is {gate_status} (non-blocking)")
    if not gate_status:
        print("[WARN] ocr_reparse_quality.gate.status is missing (non-blocking)")
print("[OK]   system_status checks passed")
PY

if [ "$fail" -ne 0 ]; then
  exit 1
fi

echo "All predeploy checks passed."
