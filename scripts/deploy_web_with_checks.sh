#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WORKSPACE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

PROJECT_ID="${PROJECT_ID:-sawahospitalsystem}"
REGION="${REGION:-asia-northeast2}"
WEB_SERVICE="${WEB_SERVICE:-}"
WORKER_SERVICE="${WORKER_SERVICE:-}"
ORDER_ID="${ORDER_ID:-}"
WEB_URL="${WEB_URL:-}"
WORKER_URL="${WORKER_URL:-}"
OPERATOR_USER="${OPERATOR_USER:-}"
OPERATOR_PASSWORD="${OPERATOR_PASSWORD:-}"
ALLOW_BASIC_ONLY_AUTH="${ALLOW_BASIC_ONLY_AUTH:-0}"
DEFAULT_GOOGLE_CLIENT_ID="167795504375-hu5316gut0ke8vruc857qsb524q4kq50.apps.googleusercontent.com"
if [[ "${ALLOW_BASIC_ONLY_AUTH}" == "1" ]]; then
  GOOGLE_CLIENT_ID="${GOOGLE_CLIENT_ID:-}"
else
  GOOGLE_CLIENT_ID="${GOOGLE_CLIENT_ID:-$DEFAULT_GOOGLE_CLIENT_ID}"
fi
STRICT_OCR_QUALITY="${STRICT_OCR_QUALITY:-0}"
WORKFLOW_V2_DEPLOY_CHECK="${WORKFLOW_V2_DEPLOY_CHECK:-0}"
IMAGE_REPO="${IMAGE_REPO:-asia-northeast2-docker.pkg.dev/${PROJECT_ID}/backend/frontend}"
TAG_PREFIX="${TAG_PREFIX:-frontend}"
PROVIDED_IMAGE="${IMAGE:-}"
ALLOW_WEB_IMAGE_PROMOTION="${ALLOW_WEB_IMAGE_PROMOTION:-0}"
FRONTEND_DIR="${FRONTEND_DIR:-}"
WEB_DEPLOY_LABEL="${WEB_DEPLOY_LABEL:-web}"
PREDEPLOY_SCRIPT="${PREDEPLOY_SCRIPT:-$SCRIPT_DIR/predeploy_env_checks.sh}"
PREPARE_WEB_DEPLOY_SOURCE="${PREPARE_WEB_DEPLOY_SOURCE:-$SCRIPT_DIR/prepare_web_deploy_source.sh}"
VALIDATE_WEB_DEPLOY_SOURCE="${VALIDATE_WEB_DEPLOY_SOURCE:-$SCRIPT_DIR/validate_web_deploy_source.sh}"
ENSURE_GCLOUD_AUTH="${ENSURE_GCLOUD_AUTH:-$SCRIPT_DIR/ensure_prod_gcloud_auth.sh}"

"$ENSURE_GCLOUD_AUTH"

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

if [ -z "$WEB_SERVICE" ]; then
  echo "WEB_SERVICE is required"
  exit 1
fi

if [ -n "$PROVIDED_IMAGE" ] && [ "$ALLOW_WEB_IMAGE_PROMOTION" != "1" ]; then
  echo "blocked: web image promotion is disabled by default because Next rewrites bake API_PROXY_TARGET at build time."
  echo "blocked: build web-prod with the prod WORKER_URL, or first remove the build-time rewrite dependency."
  exit 1
fi

if [ -n "$PROVIDED_IMAGE" ]; then
  echo "[0/7] promote provided web image"
elif [ -z "$FRONTEND_DIR" ]; then
  echo "[0/7] prepare fresh web deploy source"
  FRONTEND_DIR="$("$PREPARE_WEB_DEPLOY_SOURCE" "$WEB_DEPLOY_LABEL")"
else
  echo "[0/7] validate provided web deploy source"
fi

if [ -z "$PROVIDED_IMAGE" ]; then
  "$VALIDATE_WEB_DEPLOY_SOURCE" "$FRONTEND_DIR"
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

if [ -z "$WEB_URL" ]; then
  echo "WEB_URL could not be resolved"
  exit 1
fi

TAG="${TAG_PREFIX}-$(date +%Y%m%d-%H%M%S)"
IMAGE="${PROVIDED_IMAGE:-${IMAGE_REPO}:${TAG}}"
BUILD_CONFIG="/tmp/cloudbuild.frontend.${TAG}.yaml"

echo "[1/7] capture current web revision/image"
CURRENT_REVISION="$(gcloud run services describe "${WEB_SERVICE}" --project="${PROJECT_ID}" --region="${REGION}" --format='value(status.latestReadyRevisionName)' || true)"
CURRENT_IMAGE=""
if [[ -n "${CURRENT_REVISION}" ]]; then
  CURRENT_IMAGE="$(gcloud run revisions describe "${CURRENT_REVISION}" --project="${PROJECT_ID}" --region="${REGION}" --format='value(spec.containers[0].image)' || true)"
fi
echo "current revision=${CURRENT_REVISION:-unknown}"
echo "current image=${CURRENT_IMAGE:-unknown}"

if [ -z "$PROVIDED_IMAGE" ]; then
  cat > "$BUILD_CONFIG" <<YAML
steps:
  - name: 'gcr.io/cloud-builders/docker'
    args:
      - 'build'
      - '--build-arg'
      - 'NEXT_PUBLIC_API_BASE_URL=/api'
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
fi

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

if [ -z "$PROVIDED_IMAGE" ]; then
  echo "[2/7] build web image"
  gcloud builds submit "$FRONTEND_DIR" --project="$PROJECT_ID" --config="$BUILD_CONFIG"
else
  echo "[2/7] skip build; promoting provided image ${IMAGE}"
fi

echo "[3/7] deploy ${WEB_SERVICE}"
gcloud run deploy "$WEB_SERVICE" --project="$PROJECT_ID" --region="$REGION" --image="$IMAGE" --quiet

echo "[4/7] run postdeploy env checks"
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

if [[ -n "${ORDER_ID}" ]]; then
  if [[ "${WORKFLOW_V2_DEPLOY_CHECK}" == "1" ]]; then
    echo "[5/7] verify exact-order workflow-v2 worker/web parity"
    WORKER_WORKFLOW_V2_JSON="$(mktemp)"
    WEB_WORKFLOW_V2_JSON="$(mktemp)"
    WORKER_INSPECTION_V2_JSON="$(mktemp)"
    WEB_INSPECTION_V2_JSON="$(mktemp)"

    WORKER_CODE="$(
      curl -sS -u "${OPERATOR_USER}:${OPERATOR_PASSWORD}" \
        -o "${WORKER_WORKFLOW_V2_JSON}" \
        -w "%{http_code}" \
        "${WORKER_URL}/orders/${ORDER_ID}/workflow-v2"
    )"
    if [[ "${WORKER_CODE}" != "200" ]]; then
      echo "worker workflow-v2 check failed: status=${WORKER_CODE}"
      cat "${WORKER_WORKFLOW_V2_JSON}"
      exit 1
    fi
    WEB_CODE="$(
      curl -sS -u "${OPERATOR_USER}:${OPERATOR_PASSWORD}" \
        -o "${WEB_WORKFLOW_V2_JSON}" \
        -w "%{http_code}" \
        "${WEB_URL}/api/orders/${ORDER_ID}/workflow-v2"
    )"
    if [[ "${WEB_CODE}" != "200" ]]; then
      echo "web workflow-v2 check failed: status=${WEB_CODE}"
      cat "${WEB_WORKFLOW_V2_JSON}"
      exit 1
    fi

    WORKER_CODE="$(
      curl -sS -u "${OPERATOR_USER}:${OPERATOR_PASSWORD}" \
        -o "${WORKER_INSPECTION_V2_JSON}" \
        -w "%{http_code}" \
        "${WORKER_URL}/orders/${ORDER_ID}/workflow-v2/inspection"
    )"
    if [[ "${WORKER_CODE}" != "200" ]]; then
      echo "worker workflow-v2 inspection check failed: status=${WORKER_CODE}"
      cat "${WORKER_INSPECTION_V2_JSON}"
      exit 1
    fi
    WEB_CODE="$(
      curl -sS -u "${OPERATOR_USER}:${OPERATOR_PASSWORD}" \
        -o "${WEB_INSPECTION_V2_JSON}" \
        -w "%{http_code}" \
        "${WEB_URL}/api/orders/${ORDER_ID}/workflow-v2/inspection"
    )"
    if [[ "${WEB_CODE}" != "200" ]]; then
      echo "web workflow-v2 inspection check failed: status=${WEB_CODE}"
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
    rm -f "${WORKER_WORKFLOW_V2_JSON}" "${WEB_WORKFLOW_V2_JSON}" "${WORKER_INSPECTION_V2_JSON}" "${WEB_INSPECTION_V2_JSON}"
    echo "ok: exact-order workflow-v2 parity"
    echo "[6/7] done"
    echo "deploy verification passed: service=${WEB_SERVICE} image=${IMAGE}"
    exit 0
  fi

  echo "[5/7] verify exact-order worker/web current-state parity"
  OCR_WORKER_JSON="$(mktemp)"
  OCR_WEB_JSON="$(mktemp)"
  DRAFT_WORKER_JSON="$(mktemp)"
  DRAFT_WEB_JSON="$(mktemp)"
  WORKFLOW_WORKER_JSON="$(mktemp)"
  WORKFLOW_WEB_JSON="$(mktemp)"

  curl -sS -u "${OPERATOR_USER}:${OPERATOR_PASSWORD}" \
    -o "${OCR_WORKER_JSON}" \
    "${WORKER_URL}/orders/${ORDER_ID}/ocr-sheet"
  curl -sS -u "${OPERATOR_USER}:${OPERATOR_PASSWORD}" \
    -o "${OCR_WEB_JSON}" \
    "${WEB_URL}/api/orders/${ORDER_ID}/ocr-sheet"
  curl -sS -u "${OPERATOR_USER}:${OPERATOR_PASSWORD}" \
    -o "${DRAFT_WORKER_JSON}" \
    "${WORKER_URL}/orders/${ORDER_ID}/draft-sheet"
  curl -sS -u "${OPERATOR_USER}:${OPERATOR_PASSWORD}" \
    -o "${DRAFT_WEB_JSON}" \
    "${WEB_URL}/api/orders/${ORDER_ID}/draft-sheet"
  curl -sS -u "${OPERATOR_USER}:${OPERATOR_PASSWORD}" \
    -o "${WORKFLOW_WORKER_JSON}" \
    "${WORKER_URL}/orders/${ORDER_ID}/workflow-state"
  curl -sS -u "${OPERATOR_USER}:${OPERATOR_PASSWORD}" \
    -o "${WORKFLOW_WEB_JSON}" \
    "${WEB_URL}/api/orders/${ORDER_ID}/workflow-state"

  python3 - "${OCR_WORKER_JSON}" "${OCR_WEB_JSON}" "${DRAFT_WORKER_JSON}" "${DRAFT_WEB_JSON}" "${WORKFLOW_WORKER_JSON}" "${WORKFLOW_WEB_JSON}" <<'PY'
import json
import re
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

draft_fields = draft_worker.get("fields") or []
if draft_fields and all(re.fullmatch(r"col\d+", str(field or "")) for field in draft_fields):
    raise SystemExit("web deploy parity failed: current draft-sheet is generic raw columns")

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
    "ok: exact-order current-state parity "
    f"ocr_rows={len(ocr_worker.get('rows') or [])} "
    f"draft_rows={len(draft_worker.get('rows') or [])} "
    f"state={workflow_worker.get('state')}"
)
PY

  rm -f "${OCR_WORKER_JSON}" "${OCR_WEB_JSON}" "${DRAFT_WORKER_JSON}" "${DRAFT_WEB_JSON}" "${WORKFLOW_WORKER_JSON}" "${WORKFLOW_WEB_JSON}"
fi

echo "[6/7] done"

echo "deploy verification passed: service=${WEB_SERVICE} image=${IMAGE}"
