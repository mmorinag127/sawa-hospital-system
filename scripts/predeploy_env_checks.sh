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
USE_CLOUD_RUN_PROXY="${USE_CLOUD_RUN_PROXY:-0}"

fail=0
PROXY_PIDS=()
PROXY_LOGS=()

cleanup_proxies() {
  local pid
  for pid in "${PROXY_PIDS[@]-}"; do
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
      wait "$pid" 2>/dev/null || true
    fi
  done
  local log_path
  for log_path in "${PROXY_LOGS[@]-}"; do
    if [ -n "$log_path" ] && [ -f "$log_path" ]; then
      rm -f "$log_path"
    fi
  done
}

trap cleanup_proxies EXIT

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

pick_free_port() {
  python3 - <<'PY'
import socket

sock = socket.socket()
sock.bind(("127.0.0.1", 0))
print(sock.getsockname()[1])
sock.close()
PY
}

wait_for_local_port() {
  local port="$1"
  python3 - "$port" <<'PY'
import socket
import sys

port = int(sys.argv[1])
sock = socket.socket()
sock.settimeout(0.2)
try:
    ok = sock.connect_ex(("127.0.0.1", port)) == 0
finally:
    sock.close()
raise SystemExit(0 if ok else 1)
PY
}

start_cloud_run_proxy() {
  local label="$1"
  local service_name="$2"
  if [ -z "$service_name" ]; then
    echo "[FAIL] Missing env: ${label^^}_SERVICE"
    fail=1
    return 1
  fi
  local port
  port="$(pick_free_port)"
  local log_path
  log_path="$(mktemp)"
  gcloud run services proxy "$service_name" \
    --project="$PROJECT_ID" \
    --region="$REGION" \
    --port="$port" \
    >"$log_path" 2>&1 &
  local pid=$!
  PROXY_PIDS+=("$pid")
  PROXY_LOGS+=("$log_path")
  local attempt
  for attempt in $(seq 1 30); do
    if wait_for_local_port "$port"; then
      echo "http://127.0.0.1:${port}"
      return 0
    fi
    if ! kill -0 "$pid" 2>/dev/null; then
      break
    fi
    sleep 1
  done
  echo "[FAIL] ${label}_proxy: failed to start proxy for ${service_name}" >&2
  sed -n '1,80p' "$log_path" >&2 || true
  fail=1
  return 1
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

if [ "$USE_CLOUD_RUN_PROXY" = "1" ]; then
  require_env PROJECT_ID "$PROJECT_ID"
  require_env REGION "$REGION"
  require_env WEB_SERVICE "$WEB_SERVICE"
  require_env WORKER_SERVICE "$WORKER_SERVICE"
else
  if [ -z "$WEB_URL" ] && [ -n "$WEB_SERVICE" ]; then
    WEB_URL="$(resolve_service_url "$WEB_SERVICE" || true)"
  fi

  if [ -z "$WORKER_URL" ] && [ -n "$WORKER_SERVICE" ]; then
    WORKER_URL="$(resolve_service_url "$WORKER_SERVICE" || true)"
  fi
fi

if [ "$USE_CLOUD_RUN_PROXY" != "1" ]; then
  require_env WEB_URL "$WEB_URL"
  require_env WORKER_URL "$WORKER_URL"
fi
require_env OPERATOR_USER "$OPERATOR_USER"
require_env OPERATOR_PASSWORD "$OPERATOR_PASSWORD"

if [ "$fail" -ne 0 ]; then
  exit 1
fi

if [ "$USE_CLOUD_RUN_PROXY" = "1" ]; then
  WEB_URL="$(start_cloud_run_proxy "web" "$WEB_SERVICE" || true)"
  WORKER_URL="$(start_cloud_run_proxy "worker" "$WORKER_SERVICE" || true)"
  require_env WEB_URL "$WEB_URL"
  require_env WORKER_URL "$WORKER_URL"
  if [ "$fail" -ne 0 ]; then
    exit 1
  fi
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

worker_status_file="$(mktemp)"
worker_status_code="$(
  curl -sS -L -u "$OPERATOR_USER:$OPERATOR_PASSWORD" \
    -o "$worker_status_file" \
    -w "%{http_code}" \
    "$WORKER_URL/system/status" || true
)"
if [ "$worker_status_code" != "200" ]; then
  echo "[FAIL] worker_system_status_json: expected 200, got ${worker_status_code}"
  cat "$worker_status_file"
  rm -f "$worker_status_file"
  exit 1
fi

if [ "$CHECK_WEB_PROXY" = "1" ]; then
  web_status_file="$(mktemp)"
  web_status_code="$(
    curl -sS -L -u "$OPERATOR_USER:$OPERATOR_PASSWORD" \
      -o "$web_status_file" \
      -w "%{http_code}" \
      "$WEB_URL/api/system/status" || true
  )"
  if [ "$web_status_code" != "200" ]; then
    echo "[FAIL] web_api_system_status_json: expected 200, got ${web_status_code}"
    cat "$web_status_file"
    rm -f "$worker_status_file" "$web_status_file"
    exit 1
  fi
  python3 - "$worker_status_file" "$web_status_file" <<'PY' || fail=1
import json
import sys

worker_path, web_path = sys.argv[1], sys.argv[2]

with open(worker_path, "r", encoding="utf-8") as fh:
    worker = json.load(fh)
with open(web_path, "r", encoding="utf-8") as fh:
    web = json.load(fh)

checks = [
    ("intake.mode", worker.get("intake", {}).get("mode"), web.get("intake", {}).get("mode")),
    (
        "intake.manual_upload_enabled",
        worker.get("intake", {}).get("manual_upload_enabled"),
        web.get("intake", {}).get("manual_upload_enabled"),
    ),
    (
        "intake.manual_upload_storage.mode",
        worker.get("intake", {}).get("manual_upload_storage", {}).get("mode"),
        web.get("intake", {}).get("manual_upload_storage", {}).get("mode"),
    ),
    (
        "intake.manual_upload_storage.bucket",
        worker.get("intake", {}).get("manual_upload_storage", {}).get("bucket"),
        web.get("intake", {}).get("manual_upload_storage", {}).get("bucket"),
    ),
    (
        "intake.manual_upload_storage.persisted",
        worker.get("intake", {}).get("manual_upload_storage", {}).get("persisted"),
        web.get("intake", {}).get("manual_upload_storage", {}).get("persisted"),
    ),
    (
        "oauth_config.configured",
        worker.get("oauth_config", {}).get("configured"),
        web.get("oauth_config", {}).get("configured"),
    ),
]

diffs = [f"{name}: worker={worker_val!r} web={web_val!r}" for name, worker_val, web_val in checks if worker_val != web_val]
if diffs:
    print("[FAIL] web_api_system_status mismatch")
    for diff in diffs:
        print(f"  - {diff}")
    raise SystemExit(1)

print("[OK]   web_api_system_status matches worker")
PY
  rm -f "$web_status_file"
fi

status_json="$(cat "$worker_status_file")"
rm -f "$worker_status_file"
python3 - <<'PY' "$status_json" "$STRICT_OCR_QUALITY" || fail=1
import json,sys
raw=sys.argv[1]
strict_quality=(sys.argv[2] == "1")
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
if not oauth.get("configured"):
    print("[FAIL] oauth_config.configured is false")
    raise SystemExit(1)
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
