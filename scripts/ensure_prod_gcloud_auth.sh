#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-sawahospitalsystem}"
DEPLOY_SA_EMAIL="${DEPLOY_SA_EMAIL:-terraform-admin@${PROJECT_ID}.iam.gserviceaccount.com}"
DEPLOY_SA_KEY_PATH="${DEPLOY_SA_KEY_PATH:-/Users/mmorinag/.config/gcloud/keys/terraform-admin-sawahospitalsystem.json}"

if [[ ! -f "${DEPLOY_SA_KEY_PATH}" ]]; then
  echo "missing deploy service account key: ${DEPLOY_SA_KEY_PATH}" >&2
  exit 1
fi

ACTIVE_ACCOUNT="$(gcloud auth list --filter=status:ACTIVE --format='value(account)' 2>/dev/null | head -n 1)"
if [[ "${ACTIVE_ACCOUNT}" != "${DEPLOY_SA_EMAIL}" ]]; then
  gcloud auth activate-service-account "${DEPLOY_SA_EMAIL}" --key-file="${DEPLOY_SA_KEY_PATH}" >/dev/null
fi

gcloud config set project "${PROJECT_ID}" >/dev/null

ACTIVE_ACCOUNT="$(gcloud auth list --filter=status:ACTIVE --format='value(account)' 2>/dev/null | head -n 1)"
if [[ "${ACTIVE_ACCOUNT}" != "${DEPLOY_SA_EMAIL}" ]]; then
  echo "active gcloud account mismatch: expected=${DEPLOY_SA_EMAIL} actual=${ACTIVE_ACCOUNT:-none}" >&2
  exit 1
fi
