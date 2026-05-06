#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WORKSPACE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SOURCE_FRONTEND_DIR="${SOURCE_FRONTEND_DIR:-$WORKSPACE_DIR/frontend}"
INTEGRATION_ROOT="${INTEGRATION_ROOT:-$(cd "$WORKSPACE_DIR/.." && pwd)/integration}"
LABEL_INPUT="${1:-${WEB_DEPLOY_LABEL:-web}}"
DEST_ROOT_INPUT="${2:-}"

slugify() {
  printf '%s' "$1" | tr '[:upper:]' '[:lower:]' | tr -cs 'a-z0-9' '-'
}

if [[ ! -d "${SOURCE_FRONTEND_DIR}" ]]; then
  echo "source frontend dir not found: ${SOURCE_FRONTEND_DIR}" >&2
  exit 1
fi

SAFE_LABEL="$(slugify "${LABEL_INPUT}")"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
DEST_ROOT="${DEST_ROOT_INPUT:-${INTEGRATION_ROOT}/web-deploy-${TIMESTAMP}-${SAFE_LABEL}}"
DEST_FRONTEND_DIR="${DEST_ROOT}/frontend"

mkdir -p "${DEST_ROOT}"
rsync -a --delete \
  --exclude '.next' \
  --exclude 'node_modules' \
  --exclude '.turbo' \
  --exclude 'coverage' \
  "${SOURCE_FRONTEND_DIR}/" "${DEST_FRONTEND_DIR}/"

MANIFEST_PATH="${DEST_FRONTEND_DIR}/.codex-deploy-source.json"

python3 "${SCRIPT_DIR}/write_web_deploy_source_manifest.py" \
  "${SOURCE_FRONTEND_DIR}" \
  "${DEST_FRONTEND_DIR}" \
  "${MANIFEST_PATH}" \
  "${LABEL_INPUT}"

printf '%s\n' "${DEST_FRONTEND_DIR}"
