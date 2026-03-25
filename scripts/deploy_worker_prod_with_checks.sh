#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

PROJECT_ID="${PROJECT_ID:-sawahospitalsystem}"
REGION="${REGION:-asia-northeast2}"
SERVICE="${SERVICE:-worker-prod}"
WEB_SERVICE="${WEB_SERVICE:-web-prod}"
IMAGE="${1:-}"
ORDER_ID="${2:-}"
OPERATOR_USER="${OPERATOR_USER:-}"
OPERATOR_PASSWORD="${OPERATOR_PASSWORD:-}"
WEB_URL="${WEB_URL:-}"
RUN_LOCAL_REGRESSION="${RUN_LOCAL_REGRESSION:-1}"
STRICT_OCR_SHEET_GATE="${STRICT_OCR_SHEET_GATE:-1}"
STRICT_OCR_QUALITY="${STRICT_OCR_QUALITY:-1}"
CHECK_WEB_PROXY="${CHECK_WEB_PROXY:-1}"
USE_CLOUD_RUN_PROXY="${USE_CLOUD_RUN_PROXY:-0}"
OCR_SHEET_GATE_MIN_ROW_FILLED_RATIO="${OCR_SHEET_GATE_MIN_ROW_FILLED_RATIO:-0.99}"
OCR_SHEET_GATE_ABS_MAX_QTY="${OCR_SHEET_GATE_ABS_MAX_QTY:-50}"
PREDEPLOY_SCRIPT="${PREDEPLOY_SCRIPT:-$SCRIPT_DIR/predeploy_env_checks.sh}"
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
  echo "failed to start ${label} proxy for ${service_name}" >&2
  sed -n '1,80p' "$log_path" >&2 || true
  return 1
}

if [[ -z "${IMAGE}" ]]; then
  echo "usage: $0 <image> <order_id>"
  echo "example: $0 asia-northeast2-docker.pkg.dev/sawahospitalsystem/backend/backend:prod-backend-YYYYMMDD-HHMMSS ORDc935f9e2"
  exit 1
fi

if [[ -z "${ORDER_ID}" ]]; then
  echo "order_id is required"
  exit 1
fi

if [[ -z "${OPERATOR_USER}" || -z "${OPERATOR_PASSWORD}" ]]; then
  echo "OPERATOR_USER / OPERATOR_PASSWORD are required"
  exit 1
fi

if [[ -z "${WEB_URL}" ]]; then
  WEB_URL="$(resolve_service_url "$WEB_SERVICE" || true)"
fi

if [[ "${CHECK_WEB_PROXY}" == "1" && "${USE_CLOUD_RUN_PROXY}" != "1" && -z "${WEB_URL}" ]]; then
  echo "WEB_URL or WEB_SERVICE is required when CHECK_WEB_PROXY=1"
  exit 1
fi

if [[ "${RUN_LOCAL_REGRESSION}" == "1" ]]; then
  echo "[0/7] run mandatory local regression tests"
  (
    cd backend
    uv run pytest \
      tests/integration/test_ocr_pipeline.py \
      tests/integration/test_ocr_sheet_history.py \
      tests/integration/test_ocr_sheet_corpus_regression.py \
      tests/contract/test_orders_ocr_sheet_history_api.py \
      tests/contract/test_orders_ocr_status_api.py \
      -q
  )
fi

echo "[1/7] deploy ${SERVICE}"
gcloud run deploy "${SERVICE}" \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --image="${IMAGE}" \
  --quiet

echo "[2/7] verify latest revision/image"
LATEST_REVISION="$(gcloud run services describe "${SERVICE}" --project="${PROJECT_ID}" --region="${REGION}" --format='value(status.latestReadyRevisionName)')"
LATEST_IMAGE="$(gcloud run revisions describe "${LATEST_REVISION}" --project="${PROJECT_ID}" --region="${REGION}" --format='value(spec.containers[0].image)')"
EXPECTED_REPO="${IMAGE%:*}"
if [[ "${LATEST_IMAGE}" != "${IMAGE}" && "${LATEST_IMAGE}" != "${EXPECTED_REPO}@sha256:"* ]]; then
  echo "deploy mismatch: latest image is ${LATEST_IMAGE}"
  exit 1
fi

SERVICE_URL="$(gcloud run services describe "${SERVICE}" --project="${PROJECT_ID}" --region="${REGION}" --format='value(status.url)')"

if [[ "${USE_CLOUD_RUN_PROXY}" == "1" ]]; then
  SERVICE_URL="$(start_cloud_run_proxy "worker" "${SERVICE}")"
  if [[ "${CHECK_WEB_PROXY}" == "1" ]]; then
    WEB_URL="$(start_cloud_run_proxy "web" "${WEB_SERVICE}")"
  fi
fi

echo "[3/7] call worker ocr-sheet API"
WORKER_JSON="$(mktemp)"
HTTP_CODE="$(
  curl -sS -u "${OPERATOR_USER}:${OPERATOR_PASSWORD}" \
    -o "${WORKER_JSON}" \
    -w "%{http_code}" \
    "${SERVICE_URL}/orders/${ORDER_ID}/ocr-sheet"
)"
if [[ "${HTTP_CODE}" != "200" ]]; then
  echo "ocr-sheet check failed: status=${HTTP_CODE}"
  cat "${WORKER_JSON}"
  exit 1
fi

echo "[4/7] verify worker ocr-sheet quality gate"
python3 - "${WORKER_JSON}" "${STRICT_OCR_SHEET_GATE}" "${OCR_SHEET_GATE_MIN_ROW_FILLED_RATIO}" "${OCR_SHEET_GATE_ABS_MAX_QTY}" <<'PY'
import json
import statistics
import re
import sys
path = sys.argv[1]
strict = sys.argv[2] == "1"
min_ratio = float(sys.argv[3])
abs_max_qty = float(sys.argv[4])
with open(path, "r", encoding="utf-8") as fh:
    data = json.load(fh)
fields = data.get("fields") or []
rows = data.get("rows") or []
if not fields:
    raise SystemExit("invalid response: fields is empty")
if "menu" not in fields:
    raise SystemExit("invalid response: menu field missing")
qty_indexes = [idx for idx, field in enumerate(fields) if str(field).startswith("qty.")]
if not qty_indexes:
    raise SystemExit("invalid response: qty.* fields missing")
if not rows:
    raise SystemExit("invalid response: rows is empty")

def parse_num(value):
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if not re.fullmatch(r"-?\d+(?:\.\d+)?", text):
        return None
    return float(text)

row_numeric_counts = []
values = []
for row in rows:
    if not isinstance(row, list):
        row = []
    count = 0
    for col in qty_indexes:
        if col >= len(row):
            continue
        val = parse_num(row[col])
        if val is None:
            continue
        values.append(val)
        count += 1
    row_numeric_counts.append(count)

filled_rows = sum(1 for count in row_numeric_counts if count > 0)
filled_ratio = (filled_rows / len(rows)) if rows else 0.0
warnings = data.get("warnings") or []
source = str(data.get("source") or "")

if strict:
    if warnings:
        raise SystemExit(f"ocr-sheet gate failed: warnings present: {warnings}")
    if not values:
        raise SystemExit("ocr-sheet gate failed: no numeric quantity cell")
    if source.startswith("weekly_menu") and filled_ratio < min_ratio:
        raise SystemExit(
            f"ocr-sheet gate failed: filled_row_ratio={filled_ratio:.3f} < {min_ratio:.3f}"
        )
    max_qty = max(values) if values else 0
    if max_qty > abs_max_qty:
        raise SystemExit(f"ocr-sheet gate failed: max_qty={max_qty:g} > {abs_max_qty:g}")
    positives = [v for v in values if v > 0]
    if positives:
        median = statistics.median(positives)
        spike_threshold = max(median * 3.5, 15.0)
        if max_qty > spike_threshold:
            raise SystemExit(
                f"ocr-sheet gate failed: spike max_qty={max_qty:g} median={median:g} threshold={spike_threshold:g}"
            )

print(
    f"ok: fields={len(fields)} rows={len(rows)} source={source} "
    f"qty_cells={len(values)} filled_row_ratio={filled_ratio:.3f} "
    f"max_qty={(max(values) if values else 0):g}"
)
PY

WEB_JSON=""
if [[ "${CHECK_WEB_PROXY}" == "1" ]]; then
  echo "[5/7] call web proxy ocr-sheet API"
  WEB_JSON="$(mktemp)"
  WEB_CODE="$(
    curl -sS -u "${OPERATOR_USER}:${OPERATOR_PASSWORD}" \
      -o "${WEB_JSON}" \
      -w "%{http_code}" \
      "${WEB_URL}/api/orders/${ORDER_ID}/ocr-sheet"
  )"
  if [[ "${WEB_CODE}" != "200" ]]; then
    echo "web proxy ocr-sheet check failed: status=${WEB_CODE}"
    cat "${WEB_JSON}"
    exit 1
  fi

  echo "[6/7] verify worker/web ocr-sheet consistency"
  python3 - "${WORKER_JSON}" "${WEB_JSON}" <<'PY'
import json
import sys
worker_path, web_path = sys.argv[1], sys.argv[2]
with open(worker_path, "r", encoding="utf-8") as fh:
    worker = json.load(fh)
with open(web_path, "r", encoding="utf-8") as fh:
    web = json.load(fh)
worker_fields = worker.get("fields") or []
web_fields = web.get("fields") or []
worker_rows = worker.get("rows") or []
web_rows = web.get("rows") or []
if worker_fields != web_fields:
    raise SystemExit("worker/web mismatch: fields differ")
if worker_rows != web_rows:
    raise SystemExit(
        f"worker/web mismatch: rows differ worker={len(worker_rows)} web={len(web_rows)}"
    )
print(f"ok: worker/web match rows={len(worker_rows)} fields={len(worker_fields)}")
PY
fi

echo "[7/7] run mandatory system predeploy checks (strict ocr quality)"
WEB_URL="${WEB_URL}" \
WORKER_URL="${SERVICE_URL}" \
WEB_SERVICE="${WEB_SERVICE}" \
WORKER_SERVICE="${SERVICE}" \
PROJECT_ID="${PROJECT_ID}" \
REGION="${REGION}" \
OPERATOR_USER="${OPERATOR_USER}" \
OPERATOR_PASSWORD="${OPERATOR_PASSWORD}" \
STRICT_OCR_QUALITY="${STRICT_OCR_QUALITY}" \
CHECK_WEB_PROXY="${CHECK_WEB_PROXY}" \
USE_CLOUD_RUN_PROXY="${USE_CLOUD_RUN_PROXY}" \
"${PREDEPLOY_SCRIPT}"

rm -f "${WORKER_JSON}"
if [[ -n "${WEB_JSON}" ]]; then
  rm -f "${WEB_JSON}"
fi
echo "deploy verification passed: ${LATEST_REVISION}"
