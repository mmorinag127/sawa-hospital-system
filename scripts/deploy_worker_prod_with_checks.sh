#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WORKSPACE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

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
WORKFLOW_V2_DEPLOY_CHECK="${WORKFLOW_V2_DEPLOY_CHECK:-0}"
OCR_SHEET_GATE_MIN_ROW_FILLED_RATIO="${OCR_SHEET_GATE_MIN_ROW_FILLED_RATIO:-0.99}"
OCR_SHEET_GATE_ABS_MAX_QTY="${OCR_SHEET_GATE_ABS_MAX_QTY:-50}"
PREDEPLOY_SCRIPT="${PREDEPLOY_SCRIPT:-$SCRIPT_DIR/predeploy_env_checks.sh}"
ENSURE_GCLOUD_AUTH="${ENSURE_GCLOUD_AUTH:-$SCRIPT_DIR/ensure_prod_gcloud_auth.sh}"

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

normalize_workflow_v2_json() {
  jq -S '
    walk(
      if type == "object" then
        (if has("overlay_url") then .overlay_url = "<signed-url>" else . end)
        | (if has("preview_url") then .preview_url = "<signed-url>" else . end)
        | (if has("pdf_url") then .pdf_url = "<signed-url>" else . end)
      else
        .
      end
    )
  ' "$1"
}

compare_workflow_v2_json() {
  local left="$1"
  local right="$2"
  local diff_file
  diff_file="$(mktemp)"
  if ! diff -u <(normalize_workflow_v2_json "$left") <(normalize_workflow_v2_json "$right") >"$diff_file"; then
    cat "$diff_file"
    rm -f "$diff_file"
    return 1
  fi
  rm -f "$diff_file"
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

"$ENSURE_GCLOUD_AUTH"

if [[ "${CHECK_WEB_PROXY}" == "1" && -z "${WEB_URL}" ]]; then
  echo "WEB_URL or WEB_SERVICE is required when CHECK_WEB_PROXY=1"
  exit 1
fi

if [[ "${RUN_LOCAL_REGRESSION}" == "1" ]]; then
  echo "[0/9] run mandatory local regression tests"
  (
    cd "${WORKSPACE_DIR}/backend"
    uv run --extra dev pytest \
      tests/integration/test_draft_sheet_service.py::test_current_sheet_context_prefers_canonical_order_week_over_stale_draft_week \
      tests/integration/test_workflow_state_service.py::test_refresh_workflow_state_uses_canonical_order_week_over_stale_current_sheet_week \
      tests/integration/test_workflow_state_service.py::test_refresh_workflow_state_does_not_mutate_saved_sheet_blockers_on_read \
      tests/integration/test_ocr_sheet_history.py::test_set_facility_keeps_saved_sheet_until_operator_resolves_context_change \
      tests/integration/test_ocr_sheet_history.py::test_set_facility_does_not_mutate_clean_saved_sheet_header_when_fields_match \
      tests/integration/test_ocr_sheet_history.py::test_force_overwrite_current_sheet_with_weekly_menu_can_blank_quantities \
      tests/integration/test_ocr_sheet_history.py::test_force_overwrite_current_sheet_with_facility_schema_blanks_quantities_and_survives_refresh \
      tests/contract/test_orders_draft_review_api.py::test_order_detail_and_draft_sheet_expose_same_blocker_reason_for_missing_menu \
      tests/contract/test_orders_draft_review_api.py::test_ocr_sheet_api_prefers_current_sheet_context_without_persisted_draft \
      tests/contract/test_orders_draft_review_api.py::test_ocr_sheet_api_prefers_generic_current_sheet_context_before_recoverable_fallback \
      tests/contract/test_orders_draft_review_api.py::test_order_endpoints_expose_draft_ready_state_from_saved_sheet_and_reject_reason \
      tests/contract/test_orders_ocr_sheet_history_api.py::test_orders_force_weekly_menu_api_accepts_blank_quantities \
      -q
  )
fi

echo "[1/9] capture current prod revision/image"
CURRENT_REVISION="$(gcloud run services describe "${SERVICE}" --project="${PROJECT_ID}" --region="${REGION}" --format='value(status.latestReadyRevisionName)' || true)"
CURRENT_IMAGE=""
if [[ -n "${CURRENT_REVISION}" ]]; then
  CURRENT_IMAGE="$(gcloud run revisions describe "${CURRENT_REVISION}" --project="${PROJECT_ID}" --region="${REGION}" --format='value(spec.containers[0].image)' || true)"
fi
echo "current revision=${CURRENT_REVISION:-unknown}"
echo "current image=${CURRENT_IMAGE:-unknown}"

echo "[2/9] deploy ${SERVICE}"
gcloud run deploy "${SERVICE}" \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --image="${IMAGE}" \
  --quiet
DEPLOYED_REVISION="$(gcloud run services describe "${SERVICE}" --project="${PROJECT_ID}" --region="${REGION}" --format='value(status.latestCreatedRevisionName)')"
if [[ -z "${DEPLOYED_REVISION}" ]]; then
  echo "deploy failed: latestCreatedRevisionName is empty"
  exit 1
fi
gcloud run services update-traffic "${SERVICE}" \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --to-revisions="${DEPLOYED_REVISION}=100" \
  --quiet

echo "[3/9] verify latest revision/image"
LATEST_REVISION="$(gcloud run services describe "${SERVICE}" --project="${PROJECT_ID}" --region="${REGION}" --format='value(status.latestReadyRevisionName)')"
TRAFFIC_REVISION="$(
  gcloud run services describe "${SERVICE}" --project="${PROJECT_ID}" --region="${REGION}" --format=json \
    | python3 -c 'import json, sys; data=json.load(sys.stdin); traffic=data.get("status",{}).get("traffic") or []; matches=[item.get("revisionName") for item in traffic if item.get("percent")==100 and item.get("revisionName")]; print(matches[0] if matches else "")'
)"
VERIFY_REVISION="${TRAFFIC_REVISION:-${LATEST_REVISION}}"
LATEST_IMAGE="$(gcloud run revisions describe "${VERIFY_REVISION}" --project="${PROJECT_ID}" --region="${REGION}" --format='value(spec.containers[0].image)')"
EXPECTED_REPO="${IMAGE%:*}"
if [[ "${LATEST_IMAGE}" != "${IMAGE}" && "${LATEST_IMAGE}" != "${EXPECTED_REPO}@sha256:"* ]]; then
  echo "deploy mismatch: traffic image is ${LATEST_IMAGE}"
  exit 1
fi

SERVICE_URL="$(gcloud run services describe "${SERVICE}" --project="${PROJECT_ID}" --region="${REGION}" --format='value(status.url)')"

if [[ "${WORKFLOW_V2_DEPLOY_CHECK}" == "1" ]]; then
  echo "[4/9] call worker workflow-v2 APIs"
  WORKER_WORKFLOW_V2_JSON="$(mktemp)"
  HTTP_CODE="$(
    curl -sS -u "${OPERATOR_USER}:${OPERATOR_PASSWORD}" \
      -o "${WORKER_WORKFLOW_V2_JSON}" \
      -w "%{http_code}" \
      "${SERVICE_URL}/orders/${ORDER_ID}/workflow-v2"
  )"
  if [[ "${HTTP_CODE}" != "200" ]]; then
    echo "workflow-v2 check failed: status=${HTTP_CODE}"
    cat "${WORKER_WORKFLOW_V2_JSON}"
    exit 1
  fi

  WORKER_INSPECTION_V2_JSON="$(mktemp)"
  HTTP_CODE="$(
    curl -sS -u "${OPERATOR_USER}:${OPERATOR_PASSWORD}" \
      -o "${WORKER_INSPECTION_V2_JSON}" \
      -w "%{http_code}" \
      "${SERVICE_URL}/orders/${ORDER_ID}/workflow-v2/inspection"
  )"
  if [[ "${HTTP_CODE}" != "200" ]]; then
    echo "workflow-v2 inspection check failed: status=${HTTP_CODE}"
    cat "${WORKER_INSPECTION_V2_JSON}"
    exit 1
  fi

  if [[ "${CHECK_WEB_PROXY}" == "1" ]]; then
    echo "[5/9] verify web proxy workflow-v2 parity"
    WEB_WORKFLOW_V2_JSON="$(mktemp)"
    WEB_CODE="$(
      curl -sS -u "${OPERATOR_USER}:${OPERATOR_PASSWORD}" \
        -o "${WEB_WORKFLOW_V2_JSON}" \
        -w "%{http_code}" \
        "${WEB_URL}/api/orders/${ORDER_ID}/workflow-v2"
    )"
    if [[ "${WEB_CODE}" != "200" ]]; then
      echo "web proxy workflow-v2 check failed: status=${WEB_CODE}"
      cat "${WEB_WORKFLOW_V2_JSON}"
      exit 1
    fi

    WEB_INSPECTION_V2_JSON="$(mktemp)"
    WEB_CODE="$(
      curl -sS -u "${OPERATOR_USER}:${OPERATOR_PASSWORD}" \
        -o "${WEB_INSPECTION_V2_JSON}" \
        -w "%{http_code}" \
        "${WEB_URL}/api/orders/${ORDER_ID}/workflow-v2/inspection"
    )"
    if [[ "${WEB_CODE}" != "200" ]]; then
      echo "web proxy workflow-v2 inspection check failed: status=${WEB_CODE}"
      cat "${WEB_INSPECTION_V2_JSON}"
      exit 1
    fi

    if ! compare_workflow_v2_json "${WORKER_WORKFLOW_V2_JSON}" "${WEB_WORKFLOW_V2_JSON}"; then
      echo "worker/web mismatch: workflow-v2 differs"
      exit 1
    fi
    if ! compare_workflow_v2_json "${WORKER_INSPECTION_V2_JSON}" "${WEB_INSPECTION_V2_JSON}"; then
      echo "worker/web mismatch: workflow-v2 inspection differs"
      exit 1
    fi
    rm -f "${WEB_WORKFLOW_V2_JSON}" "${WEB_INSPECTION_V2_JSON}"
  fi

  echo "[6/9] run system predeploy checks"
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
  "${PREDEPLOY_SCRIPT}"

  rm -f "${WORKER_WORKFLOW_V2_JSON}" "${WORKER_INSPECTION_V2_JSON}"
  echo "deploy verification passed: ${LATEST_REVISION}"
  exit 0
fi

echo "[4/9] call worker current-order APIs"
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

WORKER_DRAFT_JSON="$(mktemp)"
HTTP_CODE="$(
  curl -sS -u "${OPERATOR_USER}:${OPERATOR_PASSWORD}" \
    -o "${WORKER_DRAFT_JSON}" \
    -w "%{http_code}" \
    "${SERVICE_URL}/orders/${ORDER_ID}/draft-sheet"
)"
if [[ "${HTTP_CODE}" != "200" ]]; then
  echo "draft-sheet check failed: status=${HTTP_CODE}"
  cat "${WORKER_DRAFT_JSON}"
  exit 1
fi

WORKER_WORKFLOW_JSON="$(mktemp)"
HTTP_CODE="$(
  curl -sS -u "${OPERATOR_USER}:${OPERATOR_PASSWORD}" \
    -o "${WORKER_WORKFLOW_JSON}" \
    -w "%{http_code}" \
    "${SERVICE_URL}/orders/${ORDER_ID}/workflow-state"
)"
if [[ "${HTTP_CODE}" != "200" ]]; then
  echo "workflow-state check failed: status=${HTTP_CODE}"
  cat "${WORKER_WORKFLOW_JSON}"
  exit 1
fi

echo "[5/9] verify worker ocr-sheet quality gate"
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
apply_blockers = data.get("apply_blockers") or []
warnings = data.get("warnings") or []
if not fields:
    raise SystemExit("invalid response: fields is empty")
if "menu" not in fields:
    raise SystemExit("invalid response: menu field missing")
qty_indexes = [idx for idx, field in enumerate(fields) if str(field).startswith("qty.")]
if not qty_indexes:
    raise SystemExit("invalid response: qty.* fields missing")
blocked_empty = not rows
if blocked_empty:
    allowed_blockers = {"rows_empty", "draft_rows_empty", "menu_entries_missing", "monthly_menu_object_missing"}
    blocker_codes = {str(code) for code in apply_blockers}
    warning_codes = {str(code) for code in warnings}
    if "rows_empty" not in blocker_codes:
        raise SystemExit("invalid response: rows is empty without rows_empty blocker")
    if not (blocker_codes | warning_codes) & allowed_blockers:
        raise SystemExit(
            "invalid response: rows is empty without an explicit blocked-sheet reason "
            f"blockers={apply_blockers} warnings={warnings}"
        )

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
    if warnings and not blocked_empty:
        raise SystemExit(f"ocr-sheet gate failed: warnings present: {warnings}")
    if not values and not blocked_empty:
        raise SystemExit("ocr-sheet gate failed: no numeric quantity cell")
    if source.startswith("weekly_menu") and not blocked_empty and filled_ratio < min_ratio:
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

echo "[6/9] verify current-order surface parity"
python3 - "${WORKER_DRAFT_JSON}" "${WORKER_JSON}" "${WORKER_WORKFLOW_JSON}" <<'PY'
import json
import re
import sys

draft_path, ocr_path, workflow_path = sys.argv[1:4]
with open(draft_path, "r", encoding="utf-8") as fh:
    draft = json.load(fh)
with open(ocr_path, "r", encoding="utf-8") as fh:
    ocr = json.load(fh)
with open(workflow_path, "r", encoding="utf-8") as fh:
    workflow = json.load(fh)

draft_fields = draft.get("fields") or []
draft_rows = draft.get("rows") or []
ocr_fields = ocr.get("fields") or []
ocr_rows = ocr.get("rows") or []
ocr_can_apply = bool(ocr.get("can_apply"))
ocr_apply_blockers = ocr.get("apply_blockers") or []
apply_gate = workflow.get("apply_gate") or {}
workflow_can_apply = bool(apply_gate.get("can_apply"))
workflow_ocr_can_apply = bool(workflow.get("ocr_can_apply_draft", workflow_can_apply))
workflow_blockers = apply_gate.get("blockers") or []

if not draft_fields or not draft_rows:
    draft_apply_blockers = draft.get("apply_blockers") or []
    if not draft_fields:
        raise SystemExit("surface parity failed: draft-sheet fields are empty")
    if not draft_rows:
        if "rows_empty" not in draft_apply_blockers:
            raise SystemExit("surface parity failed: draft-sheet rows are empty without rows_empty blocker")
        if workflow_can_apply or ocr_can_apply:
            raise SystemExit(
                "surface parity failed: blocked empty draft-sheet disagrees with apply gate "
                f"workflow_can_apply={workflow_can_apply} ocr_can_apply={ocr_can_apply}"
            )

generic_pattern = re.compile(r"col\d+$")
draft_is_generic = all(generic_pattern.fullmatch(str(field or "")) for field in draft_fields)
if draft_is_generic:
    raise SystemExit(
        "surface parity failed: draft-sheet is generic raw columns "
        f"fields={draft_fields}"
    )

draft_has_menu = "menu" in draft_fields
draft_has_qty = any(str(field).startswith("qty.") for field in draft_fields)
ocr_has_menu = "menu" in ocr_fields
ocr_has_qty = any(str(field).startswith("qty.") for field in ocr_fields)

if ocr_has_menu and ocr_has_qty and not (draft_has_menu and draft_has_qty):
    raise SystemExit(
        "surface parity failed: ocr-sheet is semantic but draft-sheet is not "
        f"draft_fields={draft_fields} ocr_fields={ocr_fields}"
    )

if ocr_can_apply and not workflow_ocr_can_apply:
    raise SystemExit(
        "surface parity failed: ocr-sheet can_apply=true but workflow-state blocks apply "
        f"workflow_blockers={workflow_blockers}"
    )

if workflow_can_apply and ocr_apply_blockers:
    raise SystemExit(
        "surface parity failed: workflow-state can_apply=true but ocr-sheet still has blockers "
        f"ocr_apply_blockers={ocr_apply_blockers}"
    )

print(
    "ok: draft/ocr/workflow parity "
    f"draft_rows={len(draft_rows)} ocr_rows={len(ocr_rows)} "
    f"workflow_can_apply={workflow_can_apply} workflow_ocr_can_apply={workflow_ocr_can_apply} "
    f"ocr_can_apply={ocr_can_apply}"
)
PY

WEB_JSON=""
WEB_DRAFT_JSON=""
WEB_WORKFLOW_JSON=""
if [[ "${CHECK_WEB_PROXY}" == "1" ]]; then
  echo "[7/9] call web proxy current-order APIs"
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

  WEB_DRAFT_JSON="$(mktemp)"
  WEB_CODE="$(
    curl -sS -u "${OPERATOR_USER}:${OPERATOR_PASSWORD}" \
      -o "${WEB_DRAFT_JSON}" \
      -w "%{http_code}" \
      "${WEB_URL}/api/orders/${ORDER_ID}/draft-sheet"
  )"
  if [[ "${WEB_CODE}" != "200" ]]; then
    echo "web proxy draft-sheet check failed: status=${WEB_CODE}"
    cat "${WEB_DRAFT_JSON}"
    exit 1
  fi

  WEB_WORKFLOW_JSON="$(mktemp)"
  WEB_CODE="$(
    curl -sS -u "${OPERATOR_USER}:${OPERATOR_PASSWORD}" \
      -o "${WEB_WORKFLOW_JSON}" \
      -w "%{http_code}" \
      "${WEB_URL}/api/orders/${ORDER_ID}/workflow-state"
  )"
  if [[ "${WEB_CODE}" != "200" ]]; then
    echo "web proxy workflow-state check failed: status=${WEB_CODE}"
    cat "${WEB_WORKFLOW_JSON}"
    exit 1
  fi

  echo "[8/9] verify worker/web current-order consistency"
  python3 - "${WORKER_JSON}" "${WEB_JSON}" "${WORKER_DRAFT_JSON}" "${WEB_DRAFT_JSON}" "${WORKER_WORKFLOW_JSON}" "${WEB_WORKFLOW_JSON}" <<'PY'
import json
import sys
ocr_worker_path, ocr_web_path, draft_worker_path, draft_web_path, workflow_worker_path, workflow_web_path = sys.argv[1:7]

def load(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)

ocr_worker = load(ocr_worker_path)
ocr_web = load(ocr_web_path)
draft_worker = load(draft_worker_path)
draft_web = load(draft_web_path)
workflow_worker = load(workflow_worker_path)
workflow_web = load(workflow_web_path)

for label, worker, web in (
    ("ocr-sheet", ocr_worker, ocr_web),
    ("draft-sheet", draft_worker, draft_web),
):
    if (worker.get("fields") or []) != (web.get("fields") or []):
        raise SystemExit(f"worker/web mismatch: {label} fields differ")
    if (worker.get("rows") or []) != (web.get("rows") or []):
        raise SystemExit(
            f"worker/web mismatch: {label} rows differ "
            f"worker={len(worker.get('rows') or [])} web={len(web.get('rows') or [])}"
        )

def normalize_workflow(data):
    apply_gate = data.get("apply_gate") or {}
    return {
        "state": data.get("state"),
        "warnings": data.get("warnings") or [],
        "candidate_evidence_run_id": data.get("candidate_evidence_run_id"),
        "active_evidence_run_id": data.get("active_evidence_run_id"),
        "can_apply": bool(apply_gate.get("can_apply")),
        "blockers": apply_gate.get("blockers") or [],
    }

if normalize_workflow(workflow_worker) != normalize_workflow(workflow_web):
    raise SystemExit("worker/web mismatch: workflow-state differs")

print(
    "ok: worker/web current-order match "
    f"ocr_rows={len(ocr_worker.get('rows') or [])} "
    f"draft_rows={len(draft_worker.get('rows') or [])}"
)
PY
fi

echo "[9/9] run mandatory system predeploy checks (strict ocr quality)"
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
"${PREDEPLOY_SCRIPT}"

rm -f "${WORKER_JSON}"
rm -f "${WORKER_DRAFT_JSON}"
rm -f "${WORKER_WORKFLOW_JSON}"
if [[ -n "${WEB_JSON}" ]]; then
  rm -f "${WEB_JSON}"
fi
if [[ -n "${WEB_DRAFT_JSON}" ]]; then
  rm -f "${WEB_DRAFT_JSON}"
fi
if [[ -n "${WEB_WORKFLOW_JSON}" ]]; then
  rm -f "${WEB_WORKFLOW_JSON}"
fi
echo "deploy verification passed: ${LATEST_REVISION}"
