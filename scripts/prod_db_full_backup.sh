#!/usr/bin/env bash
set -euo pipefail

# Exception-only path. This is not part of the normal prod release flow.

PROJECT_ID="${PROJECT_ID:-sawahospitalsystem}"
INSTANCE="${INSTANCE:-orders-prod}"
DATABASE="${DATABASE:-orders}"
EXPORT_BUCKET="${EXPORT_BUCKET:-sawahospitalsystem-prod-exports}"
OUT_DIR="${OUT_DIR:-tmp/prod_release_from_stg/db_full_copy}"
STAMP="${STAMP:-$(date -u +%Y%m%dT%H%M%SZ)}"
CONFIRM="${CONFIRM:-}"

if [[ "$CONFIRM" != "BACKUP_PROD_DB" ]]; then
  echo "Refusing to export prod DB without CONFIRM=BACKUP_PROD_DB" >&2
  exit 2
fi

mkdir -p "$OUT_DIR"
uri="gs://${EXPORT_BUCKET}/cloudsql-backups/${INSTANCE}/${STAMP}-${DATABASE}.sql.gz"

gcloud sql export sql "$INSTANCE" "$uri" \
  --project="$PROJECT_ID" \
  --database="$DATABASE" \
  --offload

cat >"${OUT_DIR}/prod-backup-${STAMP}.json" <<EOF
{
  "project_id": "${PROJECT_ID}",
  "instance": "${INSTANCE}",
  "database": "${DATABASE}",
  "backup_uri": "${uri}",
  "created_at": "${STAMP}"
}
EOF

printf '%s\n' "$uri"
