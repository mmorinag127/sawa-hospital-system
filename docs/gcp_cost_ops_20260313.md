# GCP Cost / Ops Notes (2026-03-13)

## Summary
- Gmail ingest/watch has been fully removed from runtime and GCP settings.
- `Cloud SQL` automated backups have been enabled on `orders-prod`.
- `Cloud Run` sizing has been reviewed, but no further scale/minScale change was applied in this pass.
- `Artifact Registry` cleanup has been executed with revision-aware safeguards.

## What Changed
### Gmail removal
- Removed Gmail-specific Cloud Run env vars from:
  - `worker-prod`
  - `web-prod`
  - `ocr-pipeline-prod`
- Deleted Gmail scheduler jobs.
- Deleted Gmail secrets.
- Disabled `gmail.googleapis.com`.
- Verified Gmail API endpoints now return `404`.

### Cloud SQL backups
- Instance: `orders-prod`
- Automated backups: enabled
- Start time: `17:00`
- Retention: `7` backups

### Artifact Registry cleanup
- Safety report generated with keep rules:
  - current Cloud Run referenced digests
  - recent 15 versions per package
- Cleanup result:
  - deleted: `810`
  - errors: `4`
- Candidate counts before cleanup:
  - `backend`: `490` untagged, `35` old tagged
  - `frontend`: `252` untagged, `32` old tagged
  - `ocr-pipeline`: `4` untagged, `1` old tagged
- Caveat:
  - one deleted digest overlapped with very old `ocr-pipeline` revisions with no current traffic
  - current live revision remained healthy
  - rollback to those ancient revisions would now require rebuild/redeploy
- Cleanup policy has been added for future automatic control:
  - keep most recent `15`
  - delete `untagged` older than `7d`
  - delete `tagged` older than `45d`
  - policy file: [artifact_registry_cleanup_policy_backend.json](/Users/mmorinag/Sawa/2025.12/workspace/infra/artifact_registry_cleanup_policy_backend.json)
  - policy is active (`dry run` disabled)

## Current Prod Cost Posture
### Cloud Run
- `web-prod`
  - `1 vCPU / 512Mi`
  - `minScale=0`
  - `maxScale=3`
  - `concurrency=80`
- `worker-prod`
  - `1 vCPU / 1Gi`
  - `minScale=0`
  - `maxScale=3`
  - `concurrency=80`
- `ocr-pipeline-prod`
  - `2 vCPU / 8Gi`
  - `minScale=1`
  - `maxScale=5`
  - `concurrency=1`

### Cloud SQL
- `orders-prod`
  - `POSTGRES_15`
  - tier `db-g1-small`
  - `10 GB` disk
  - zonal

### Artifact Registry
- Repository: `backend`
- Approx size before cleanup: `114.9 GiB`
- Approx size after cleanup started reflecting: `100.7 GiB`
- This is still the most obvious cleanup target if more cost reduction is needed.

## Recommended Next Action
### 1. Artifact Registry cleanup follow-up
- Re-run the cleanup report after repository size metrics catch up.
- Review and retry the `4` failed deletions only if needed.
- Consider pruning very old Cloud Run revisions so rollback expectations and kept digests stay aligned.

Use:
- [report_artifact_registry_cleanup_candidates.py](/Users/mmorinag/Sawa/2025.12/workspace/scripts/report_artifact_registry_cleanup_candidates.py)

Example:

```bash
cd /Users/mmorinag/Sawa/2025.12
python3 workspace/scripts/report_artifact_registry_cleanup_candidates.py \
  --output /tmp/artifact_cleanup_report.json
```

### 2. Cloud SQL
- Backups are now enabled.
- Next recommendation is not cost-cutting, but recovery testing.

### 3. Cloud Run
- Keep current `ocr-pipeline-prod` sizing for now.
- It is expensive, but tied directly to OCR throughput.
- Revisit only after measuring actual OCR latency and backlog under current load.

## Post-change Checks
- `POST /watch-refresh` -> `404`
- `POST /ingest/gmail-scan` -> `404`
- `gcloud services list --enabled` does not include `gmail.googleapis.com`
- `gcloud sql instances describe orders-prod` shows backups enabled
- `worker /health` -> `200`
- `web /` -> `200`
- `ocr-pipeline` authenticated `POST /` with `{}` -> `400 invalid_event` (expected)
