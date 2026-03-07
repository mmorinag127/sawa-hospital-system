#!/usr/bin/env bash
set -euo pipefail

WEB_URL="${WEB_URL:-}"
WORKER_URL="${WORKER_URL:-}"
OPERATOR_USER="${OPERATOR_USER:-}"
OPERATOR_PASSWORD="${OPERATOR_PASSWORD:-}"
CHECK_WEB_PROXY="${CHECK_WEB_PROXY:-0}"
STRICT_GMAIL_WATCH="${STRICT_GMAIL_WATCH:-0}"
STRICT_OCR_QUALITY="${STRICT_OCR_QUALITY:-0}"

fail=0

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

require_env WEB_URL "$WEB_URL"
require_env WORKER_URL "$WORKER_URL"
require_env OPERATOR_USER "$OPERATOR_USER"
require_env OPERATOR_PASSWORD "$OPERATOR_PASSWORD"

if [ "$fail" -ne 0 ]; then
  exit 1
fi

# Web UI basic reachability
check_http_code_any "web_root" "$WEB_URL/" "200,308" ""
check_http_code_any "web_login" "$WEB_URL/login" "200,308" ""

# Worker health
check_http_code_any "worker_health" "$WORKER_URL/health" "200" ""
check_http_code_any "worker_backlog" "$WORKER_URL/health/backlog" "200" ""

# Worker auth-protected endpoints
check_http_code_any "worker_orders" "$WORKER_URL/orders?include_ocr=false" "200" "$OPERATOR_USER:$OPERATOR_PASSWORD"
check_http_code_any "worker_system_status" "$WORKER_URL/system/status" "200" "$OPERATOR_USER:$OPERATOR_PASSWORD"

# Optional web -> worker proxy checks (enable for postdeploy verification).
if [ "$CHECK_WEB_PROXY" = "1" ]; then
  check_http_code_any "web_api_orders" "$WEB_URL/api/orders?include_ocr=false" "200,308" "$OPERATOR_USER:$OPERATOR_PASSWORD"
  check_http_code_any "web_api_system_status" "$WEB_URL/api/system/status" "200,308" "$OPERATOR_USER:$OPERATOR_PASSWORD"
fi

# Validate system/status JSON for Gmail + OAuth
status_json=$(curl -sS -u "$OPERATOR_USER:$OPERATOR_PASSWORD" "$WORKER_URL/system/status" || true)
python3 - <<'PY' "$status_json" "$STRICT_GMAIL_WATCH" "$STRICT_OCR_QUALITY" || fail=1
import json,sys
raw=sys.argv[1]
strict_watch=(sys.argv[2] == "1")
strict_quality=(sys.argv[3] == "1")
try:
    data=json.loads(raw)
except Exception:
    print("[FAIL] system_status JSON parse failed")
    raise SystemExit(1)

gmail=data.get("gmail_config", {})
oauth=data.get("oauth_config", {})
watch=data.get("gmail_watch", {})

if not gmail.get("configured"):
    print("[FAIL] gmail_config.configured is false")
    raise SystemExit(1)
if not oauth.get("configured"):
    print("[FAIL] oauth_config.configured is false")
    raise SystemExit(1)
watch_status = watch.get("status")
if strict_watch and watch_status not in {"ok"}:
    print(f"[FAIL] gmail_watch.status is {watch_status}")
    raise SystemExit(1)
if not strict_watch and watch_status not in {"ok"}:
    print(f"[WARN] gmail_watch.status is {watch_status} (non-blocking)")

quality = data.get("ocr_reparse_quality")
gate_status = ""
if isinstance(quality, dict):
    gate = quality.get("gate")
    if isinstance(gate, dict):
        gate_status = str(gate.get("status") or "").strip().lower()

if strict_quality:
    if gate_status not in {"pass"}:
        fail_detail = ""
        if isinstance(quality, dict):
            gate = quality.get("gate")
            if isinstance(gate, dict):
                fail_detail = f" fail_providers={gate.get('fail_providers')} warming_up={gate.get('warming_up_providers')}"
        print(f"[FAIL] ocr_reparse_quality.gate.status is {gate_status or 'missing'}{fail_detail}")
        raise SystemExit(1)
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
