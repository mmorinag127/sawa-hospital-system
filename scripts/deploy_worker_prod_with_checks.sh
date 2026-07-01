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
WORKFLOW_V2_DEPLOY_CHECK="${WORKFLOW_V2_DEPLOY_CHECK:-1}"
OCR_SHEET_GATE_MIN_ROW_FILLED_RATIO="${OCR_SHEET_GATE_MIN_ROW_FILLED_RATIO:-0.99}"
OCR_SHEET_GATE_ABS_MAX_QTY="${OCR_SHEET_GATE_ABS_MAX_QTY:-50}"
WORKER_MEMORY="${WORKER_MEMORY:-8Gi}"
WORKER_CPU="${WORKER_CPU:-2}"
WORKER_TIMEOUT="${WORKER_TIMEOUT:-1800}"
WORKER_CONCURRENCY="${WORKER_CONCURRENCY:-2}"
SERVICE_ENV_SUFFIX="${SERVICE##*-}"
TEMPLATE_BUCKET="${TEMPLATE_BUCKET:-${PROJECT_ID}-${SERVICE_ENV_SUFFIX}-templates}"
PREDEPLOY_SCRIPT="${PREDEPLOY_SCRIPT:-$SCRIPT_DIR/predeploy_env_checks.sh}"
ENSURE_GCLOUD_AUTH="${ENSURE_GCLOUD_AUTH:-$SCRIPT_DIR/ensure_prod_gcloud_auth.sh}"

"$SCRIPT_DIR/require_ci_cd_deploy.sh" "${SERVICE}"

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
        | (if has("sheet_review_base_url") then .sheet_review_base_url = "<signed-url>" else . end)
        | (if has("context_suggestion") and (.context_suggestion | type) == "object" and (.context_suggestion | has("created_at")) then .context_suggestion.created_at = "<timestamp>" else . end)
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
      tests/unit/test_order_workflow_v2_service.py::test_mark_ocr_run_queued_requires_context_and_clears_downstream \
      tests/unit/test_order_workflow_v2_service.py::test_mark_ocr_run_completed_preserves_context_and_does_not_select_result \
      tests/unit/test_order_workflow_v2_service.py::test_sheet_source_uses_only_selected_ocr_payload \
      tests/unit/test_order_workflow_v2_service.py::test_expanded_cell_copy_mode_override_is_passed_to_sheet_projection \
      tests/unit/test_order_workflow_v2_service.py::test_facility_template_columns_save_is_disabled_and_keeps_downstream \
      tests/unit/test_order_workflow_v2_service.py::test_selecting_ocr_result_clears_downstream_sheet \
      tests/unit/test_order_workflow_v2_service.py::test_deleting_selected_ocr_result_deletes_derived_sheet_and_returns_to_step1_context \
      tests/unit/test_order_workflow_v2_service.py::test_workflow_v2_does_not_use_order_lines_as_sheet_source \
      tests/unit/test_order_workflow_v2_service.py::test_inspection_is_read_only_projection_of_current_lineage \
      tests/unit/test_order_workflow_v2_service.py::test_bagging_requires_saved_sheet_and_uses_saved_sheet_as_source \
      tests/unit/test_order_workflow_v2_service.py::test_step5_confirm_requires_output_review_and_writes_confirmed_snapshot \
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
set +e
DEPLOY_OUTPUT="$(
  gcloud run deploy "${SERVICE}" \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --image="${IMAGE}" \
  --update-env-vars="TEMPLATE_BUCKET=${TEMPLATE_BUCKET}" \
  --memory="${WORKER_MEMORY}" \
  --cpu="${WORKER_CPU}" \
  --timeout="${WORKER_TIMEOUT}" \
  --concurrency="${WORKER_CONCURRENCY}" \
  --quiet 2>&1
)"
DEPLOY_EXIT=$?
set -e
printf '%s\n' "${DEPLOY_OUTPUT}"
DEPLOYED_REVISION="$(gcloud run services describe "${SERVICE}" --project="${PROJECT_ID}" --region="${REGION}" --format='value(status.latestCreatedRevisionName)')"
if [[ -z "${DEPLOYED_REVISION}" ]]; then
  echo "deploy failed: latestCreatedRevisionName is empty"
  exit 1
fi
if [[ "${DEPLOY_EXIT}" != "0" ]]; then
  DEPLOYED_READY="$(
    gcloud run revisions describe "${DEPLOYED_REVISION}" \
      --project="${PROJECT_ID}" \
      --region="${REGION}" \
      --format=json \
      | python3 -c 'import json, sys; data=json.load(sys.stdin); conditions=data.get("status",{}).get("conditions") or []; print(next((item.get("status","") for item in conditions if item.get("type")=="Ready"), ""))' || true
  )"
  DEPLOYED_IMAGE="$(
    gcloud run revisions describe "${DEPLOYED_REVISION}" \
      --project="${PROJECT_ID}" \
      --region="${REGION}" \
      --format='value(spec.containers[0].image)' || true
  )"
  EXPECTED_REPO_FOR_DEPLOY="${IMAGE%:*}"
  if [[ "${DEPLOYED_READY}" != "True" || ( "${DEPLOYED_IMAGE}" != "${IMAGE}" && "${DEPLOYED_IMAGE}" != "${EXPECTED_REPO_FOR_DEPLOY}@sha256:"* ) ]]; then
    echo "deploy failed and latest created revision is not a ready revision for the expected image"
    echo "latestCreatedRevision=${DEPLOYED_REVISION}"
    echo "latestCreatedReady=${DEPLOYED_READY:-unknown}"
    echo "latestCreatedImage=${DEPLOYED_IMAGE:-unknown}"
    exit "${DEPLOY_EXIT}"
  fi
  echo "deploy command returned ${DEPLOY_EXIT}, but latest created revision ${DEPLOYED_REVISION} is ready with expected image; continuing"
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
python3 "${SCRIPT_DIR}/check_ocr_sheet_quality_gate.py" \
  "${WORKER_JSON}" \
  "${STRICT_OCR_SHEET_GATE}" \
  "${OCR_SHEET_GATE_MIN_ROW_FILLED_RATIO}" \
  "${OCR_SHEET_GATE_ABS_MAX_QTY}"

echo "[6/9] verify current-order surface parity"
python3 "${SCRIPT_DIR}/check_worker_surface_parity.py" \
  "${WORKER_DRAFT_JSON}" \
  "${WORKER_JSON}" \
  "${WORKER_WORKFLOW_JSON}"

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
  python3 "${SCRIPT_DIR}/check_worker_web_surface_consistency.py" \
    "${WORKER_JSON}" \
    "${WEB_JSON}" \
    "${WORKER_DRAFT_JSON}" \
    "${WEB_DRAFT_JSON}" \
    "${WORKER_WORKFLOW_JSON}" \
    "${WEB_WORKFLOW_JSON}"
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
