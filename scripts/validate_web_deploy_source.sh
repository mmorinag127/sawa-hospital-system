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

python3 - "${SOURCE_FRONTEND_DIR}" "${DEPLOY_FRONTEND_DIR}" "${MANIFEST_PATH}" <<'PY'
import hashlib
import json
import os
import sys

source_dir, deploy_dir, manifest_path = sys.argv[1:4]

def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()

with open(manifest_path, "r", encoding="utf-8") as fh:
    payload = json.load(fh)

sentinels = payload.get("sentinels") or []
if not sentinels:
    raise SystemExit("web deploy source manifest has no sentinel files")

stale = []
mutated = []
for entry in sentinels:
    rel_path = entry["path"]
    expected_source = entry["source_sha256"]
    expected_deploy = entry["deploy_sha256"]
    src_path = os.path.join(source_dir, rel_path)
    dst_path = os.path.join(deploy_dir, rel_path)
    if not os.path.exists(src_path):
        stale.append(f"{rel_path}: missing in current source")
        continue
    if not os.path.exists(dst_path):
        mutated.append(f"{rel_path}: missing in deploy source")
        continue
    current_source = sha256(src_path)
    current_deploy = sha256(dst_path)
    if current_deploy != expected_deploy:
        mutated.append(f"{rel_path}: deploy copy changed after preparation")
    if current_source != expected_source or current_source != current_deploy:
        stale.append(rel_path)

if mutated:
    raise SystemExit(
        "web deploy source invalid; recreate clean deploy copy\n- "
        + "\n- ".join(mutated)
    )

if stale:
    raise SystemExit(
        "web deploy source is stale relative to current frontend sentinel files; "
        "recreate clean deploy copy before deploying\n- "
        + "\n- ".join(stale)
    )

print(
    "ok: web deploy source matches current frontend sentinels "
    f"count={len(sentinels)}"
)
PY
