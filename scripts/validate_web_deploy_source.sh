#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WORKSPACE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SOURCE_FRONTEND_DIR="${SOURCE_FRONTEND_DIR:-$WORKSPACE_DIR/frontend}"
DEPLOY_FRONTEND_DIR="${1:-${FRONTEND_DIR:-}}"

if [[ -z "${DEPLOY_FRONTEND_DIR}" ]]; then
  echo "usage: $0 <frontend_deploy_dir>" >&2
  exit 1
fi

MANIFEST_PATH="${DEPLOY_FRONTEND_DIR}/.codex-deploy-source.json"
if [[ ! -f "${MANIFEST_PATH}" ]]; then
  echo "web deploy source manifest missing: ${MANIFEST_PATH}" >&2
  echo "create a fresh deploy copy via scripts/prepare_web_deploy_source.sh" >&2
  exit 1
fi

python3 "${SCRIPT_DIR}/validate_web_deploy_source.py" \
  "${SOURCE_FRONTEND_DIR}" \
  "${DEPLOY_FRONTEND_DIR}" \
  "${MANIFEST_PATH}"
