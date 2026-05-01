#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WORKSPACE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SOURCE_BACKEND_DIR="${SOURCE_BACKEND_DIR:-$WORKSPACE_DIR/backend}"
SOURCE_OCR_PIPELINE_DIR="${SOURCE_OCR_PIPELINE_DIR:-$WORKSPACE_DIR/ocr_pipeline}"
INTEGRATION_ROOT="${INTEGRATION_ROOT:-$(cd "$WORKSPACE_DIR/.." && pwd)/integration}"
LABEL_INPUT="${1:-stg-backend-hakodate}"
DEST_ROOT_INPUT="${2:-}"

slugify() {
  printf '%s' "$1" | tr '[:upper:]' '[:lower:]' | tr -cs 'a-z0-9' '-'
}

SAFE_LABEL="$(slugify "${LABEL_INPUT}")"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
DEST_ROOT="${DEST_ROOT_INPUT:-${INTEGRATION_ROOT}/backend-deploy-${TIMESTAMP}-${SAFE_LABEL}}"
DEST_BACKEND_DIR="${DEST_ROOT}/backend"

mkdir -p "${DEST_ROOT}"
rsync -a --delete \
  --exclude '.venv' \
  --exclude '__pycache__' \
  --exclude '.pytest_cache' \
  --exclude '.ruff_cache' \
  --exclude 'dev.db' \
  --exclude 'tests' \
  --exclude 'tmp' \
  "${SOURCE_BACKEND_DIR}/" "${DEST_BACKEND_DIR}/"

if [[ ! -d "${SOURCE_OCR_PIPELINE_DIR}/app" ]]; then
  printf 'missing ocr_pipeline app source: %s\n' "${SOURCE_OCR_PIPELINE_DIR}" >&2
  exit 1
fi

rsync -a --delete \
  --exclude '__pycache__' \
  --exclude '.pytest_cache' \
  --exclude '.ruff_cache' \
  --exclude 'tests' \
  "${SOURCE_OCR_PIPELINE_DIR}/" "${DEST_BACKEND_DIR}/ocr_pipeline/"

mkdir -p \
  "${DEST_BACKEND_DIR}/tmp/outer_quad_eval_correct_20260426/pdfs" \
  "${DEST_BACKEND_DIR}/tmp/outer_quad_eval_correct_20260426/preprocess_v10_template_snap_real_orders_20260425_0430/templates" \
  "${DEST_BACKEND_DIR}/tmp/outer_quad_eval_correct_20260426/step123_no_code_change_20260427"

MANIFEST="${WORKSPACE_DIR}/tmp/outer_quad_eval_correct_20260426/step123_no_code_change_20260427/manifest.json"
rsync -a "$MANIFEST" "${DEST_BACKEND_DIR}/tmp/outer_quad_eval_correct_20260426/step123_no_code_change_20260427/manifest.json"

python3 "${SCRIPT_DIR}/materialize_hakodate_deploy_artifacts.py" "$WORKSPACE_DIR" "$DEST_BACKEND_DIR" "$MANIFEST"

cat > "${DEST_BACKEND_DIR}/.gcloudignore" <<'EOF'
__pycache__/
*.pyc
*.pyo
*.pyd
.pytest_cache/
.mypy_cache/
.ruff_cache/
.venv/
dev.db
tests/
ocr_pipeline/tests/
*.log
EOF

printf '%s\n' "$DEST_BACKEND_DIR"
