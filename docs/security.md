# Security Notes

## Retention
- Data retention is controlled via `RETENTION_DAYS` (default 90).
- Purge task: `backend/src/workers/retention_worker.py`.
- Run manually:
  - `python -c "from src.workers.retention_worker import purge_old_records; purge_old_records()"`.

## Encryption In Transit
- Cloud Run endpoints use HTTPS.
- Cloud SQL connections use encrypted transport when using the Cloud SQL connector/Unix socket.

## Encryption At Rest
- Cloud SQL and GCS provide encryption at rest by default.
- Artifacts are stored in Artifact Registry (encrypted at rest by default).

## Secrets
- Secrets are stored in Secret Manager and injected into Cloud Run.
- Avoid storing secrets in repo; use `.env` locally and Secret Manager for cloud.
