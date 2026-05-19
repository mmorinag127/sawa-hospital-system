#!/usr/bin/env bash
set -euo pipefail

# Exception-only path. This is not part of the normal prod release flow.

PROJECT_ID="${PROJECT_ID:-sawahospitalsystem}"
INSTANCE="${INSTANCE:-orders-stg}"
DATABASE="${DATABASE:-orders}"
EXPORT_BUCKET="${EXPORT_BUCKET:-sawahospitalsystem-prod-exports}"
OUT_DIR="${OUT_DIR:-tmp/prod_release_from_stg/db_full_copy}"
STAMP="${STAMP:-$(date -u +%Y%m%dT%H%M%SZ)}"
CONFIRM="${CONFIRM:-}"

if [[ "$CONFIRM" != "EXPORT_STG_FOR_PROD_RESTORE" ]]; then
  echo "Refusing to export stg DB for prod restore without CONFIRM=EXPORT_STG_FOR_PROD_RESTORE" >&2
  exit 2
fi

mkdir -p "$OUT_DIR"
uri="gs://${EXPORT_BUCKET}/cloudsql-stg-source/${INSTANCE}/${STAMP}-${DATABASE}.sql.gz"

gcloud sql export sql "$INSTANCE" "$uri" \
  --project="$PROJECT_ID" \
  --database="$DATABASE" \
  --offload

cat >"${OUT_DIR}/stg-source-${STAMP}.json" <<EOF
{
  "project_id": "${PROJECT_ID}",
  "instance": "${INSTANCE}",
  "database": "${DATABASE}",
  "stg_export_uri": "${uri}",
  "created_at": "${STAMP}"
}
EOF

printf '%s\n' "$uri"
