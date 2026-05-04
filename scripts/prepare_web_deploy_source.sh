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

python3 - "${SOURCE_FRONTEND_DIR}" "${DEST_FRONTEND_DIR}" "${MANIFEST_PATH}" "${LABEL_INPUT}" <<'PY'
import datetime as dt
import hashlib
import json
import os
import subprocess
import sys

source_dir, deploy_dir, manifest_path, label = sys.argv[1:5]

sentinels = [
    "src/pages/orders/index.tsx",
    "src/pages/orders/[id].tsx",
    "src/pages/orders/[id]/workflow-v2.tsx",
    "src/pages/orders/[id]/inspection-v2.tsx",
    "src/pages/index.tsx",
]

def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()

entries = []
for rel_path in sentinels:
    src_path = os.path.join(source_dir, rel_path)
    dst_path = os.path.join(deploy_dir, rel_path)
    if not os.path.exists(src_path):
        continue
    if not os.path.exists(dst_path):
        raise SystemExit(f"deploy source missing sentinel file: {rel_path}")
    entries.append(
        {
            "path": rel_path,
            "source_sha256": sha256(src_path),
            "deploy_sha256": sha256(dst_path),
        }
    )

git_head = ""
try:
    git_head = subprocess.check_output(
        ["git", "-C", os.path.dirname(source_dir), "rev-parse", "HEAD"],
        text=True,
    ).strip()
except Exception:
    git_head = ""

payload = {
    "version": 1,
    "label": label,
    "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    "source_frontend_dir": source_dir,
    "deploy_frontend_dir": deploy_dir,
    "source_git_head": git_head,
    "sentinels": entries,
}

with open(manifest_path, "w", encoding="utf-8") as fh:
    json.dump(payload, fh, ensure_ascii=False, indent=2)
    fh.write("\n")
PY

printf '%s\n' "${DEST_FRONTEND_DIR}"
