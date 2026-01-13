#!/usr/bin/env bash
# Usage: PROJECT_ID=... TOPIC=... RAW_BUCKET=... PDF_PATH=... [OBJECT_PATH=...] [MESSAGE_ID=...] [RECEIVED_AT=...] ./push_ingest.sh

set -euo pipefail

if [[ -z "${PROJECT_ID:-}" || -z "${TOPIC:-}" || -z "${RAW_BUCKET:-}" || -z "${PDF_PATH:-}" ]]; then
  echo "Usage: PROJECT_ID=... TOPIC=... RAW_BUCKET=... PDF_PATH=... [OBJECT_PATH=...] [MESSAGE_ID=...] [RECEIVED_AT=...] $0"
  exit 1
fi

if [[ ! -f "$PDF_PATH" ]]; then
  echo "PDF_PATH not found: $PDF_PATH"
  exit 1
fi

timestamp="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
MESSAGE_ID="${MESSAGE_ID:-pubsub-${timestamp}}"
RECEIVED_AT="${RECEIVED_AT:-$timestamp}"
OBJECT_PATH="${OBJECT_PATH:-test/$(basename "$PDF_PATH")}"
PDF_URI="gs://${RAW_BUCKET}/${OBJECT_PATH}"

export MESSAGE_ID RECEIVED_AT PDF_URI

gsutil cp "$PDF_PATH" "$PDF_URI" >/dev/null

payload="$(python3 - <<'PY'
import json
import os

payload = {
    "message_id": os.environ["MESSAGE_ID"],
    "pdf_uri": os.environ["PDF_URI"],
    "received_at": os.environ["RECEIVED_AT"],
}
print(json.dumps(payload, ensure_ascii=True))
PY
)"

gcloud pubsub topics publish "$TOPIC" --project="$PROJECT_ID" --message="$payload"
