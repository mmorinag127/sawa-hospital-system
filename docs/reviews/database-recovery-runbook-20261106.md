# Hospital production database recovery

Prepared 2026-11-06. This is an unexecuted production procedure, not a recovery guarantee.
Use only the owner-dispatched `Deploy Production` workflow on the approved
`release/prod-*` source and its source-bound local test receipt. Never run the
recovery script locally against production.

## Before deployment

- Choose `rollback_strategy=restore_database` only with the signed local restore
  rehearsal evidence matching the migration source. This does not assert old
  runtime compatibility with the migrated database.
- Retain the pre-migration artifact containing original run, source, revisions,
  revision UIDs, image identities and successful Cloud SQL backup ID.
- Freeze external writers and administrative jobs. The script checks the known
  regional services and attached Cloud Run jobs; it cannot exclude unknown
  clients elsewhere. Confirm the maintenance window before starting.
- The whole `orders-prod` instance is restored. Another database on that instance
  is a hard stop. Do not combine the school database with this instance.

## Restore after a failed release

Dispatch the same workflow with `production_confirmation=DEPLOY_PRODUCTION`,
`rollback_mode=database`, the original `rollback_run`/`rollback_attempt`, and
`database_recovery_confirmation=RESTORE_ORDERS_PROD_DATABASE`. Supply the exact
currently serving revision map in `current_revisions_json` for `web-prod`,
`worker-prod` and `ocr-pipeline-prod`; do not reuse a guessed or stale map.

The script validates source, backup and retained revisions; pauses transports;
clears tagged routes and scales all three services to zero; waits for database
clients to drain; preserves a successful post-update backup; restores the original
backup; restores backup policy and old revision traffic. Services remain disabled
and transports remain paused. Post-update business writes are not automatically
merged into the restored database. Keep that backup for separate reconciliation.

## Resume a partially completed recovery

When the recovery artifact proves `database_restored=true`, use
`FINISH_RESTORED_ORDERS_PROD` with that `recovery_run`/`recovery_attempt`.
This completes policy and traffic restoration without restoring the database twice.
If the runner died before recording success, inspect the Cloud SQL operation and
backups first. Do not forge the receipt or rerun a destructive restore blindly.

After the finished recovery has been reviewed, dispatch
`REOPEN_RESTORED_ORDERS_PROD` against its successful run. It verifies the exact old
traffic, retained revisions, backups and disabled services before restoring
automatic scaling. A failure attempts to disable the services again and records
uncertain outcomes. Queue and scheduler resumption is intentionally excluded.

Verify login, reads, a designated save/reload and output before separately
authorizing transport resumption. `business_flow_verified=false` in the reopening
receipt must not be interpreted as successful business verification.

## Evidence and limitations

- Signed local evidence: `docs/release-evidence/database-restore-rehearsal.json`.
- Local restore 12.188s; migration 58.186s; two restore comparisons 29.848s.
- Original snapshot sequence consistency was not established by simultaneous
  acquisition. Restored sequences and table fingerprints were compared.
- Focused local recovery/deployment checks: 60 passed. Cloud mutations mocked.
- Actual Cloud SQL restoration, retained old runtime startup and production
  business operations have not been executed. Local seconds are not Cloud SQL
  recovery estimates.
- Backup PATCH excludes `backupTier` and `transactionalLogStorageState`, which are
  output-only in the [Cloud SQL API](https://docs.cloud.google.com/sql/docs/postgres/admin-api/rest/v1/instances).
  Readback still checks the full recorded policy.
