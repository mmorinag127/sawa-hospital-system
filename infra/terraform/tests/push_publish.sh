#!/usr/bin/env bash
# Usage: PROJECT_ID=... TOPIC=... gcloud pubsub topics publish "$TOPIC" --project="$PROJECT_ID" --message='{"test":"ping"}'
# Ensure push subscription is set to Cloud Run worker with OIDC auth (roles/run.invoker).

set -euo pipefail

if [[ -z "${PROJECT_ID:-}" || -z "${TOPIC:-}" ]]; then
  echo "Usage: PROJECT_ID=... TOPIC=... $0"
  exit 1
fi

gcloud pubsub topics publish "$TOPIC" --project="$PROJECT_ID" --message='{"test":"ping"}'
