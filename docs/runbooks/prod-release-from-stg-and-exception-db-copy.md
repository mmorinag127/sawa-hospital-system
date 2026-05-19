# Prod Release From Stg And Exception DB Copy

## Scope

This runbook documents the Sawa prod release flow where stg is treated as the authoritative code source.

The normal release path does not copy stg DB data into prod. Full prod DB replacement from stg is an explicit exception path only, used for the current large synchronization event or another future event with the same explicit approval.

## Normal Policy

- Code source: stg is authoritative for the prod release branch.
- Deploy source: prod deploys must come from a named `release/prod-*` branch.
- Deploy copies: web/backend deploy sources must be freshly prepared from that release branch.
- Orders: prod is authoritative. Do not sync orders from stg.
- Menus: prod is authoritative. Do not sync menus from stg.
- Facility data: compare stg/prod and resolve each diff explicitly.

Normal planning command:

```bash
task prod_release_plan
```

This records git/JJ/Cloud Run preflight facts and produces a read-only DB diff under `tmp/prod_release_from_stg/`.

## Normal Release Tasks

```bash
task prod_release_from_stg_create_branch
task prod_release_from_stg_preflight
task prod_release_db_diff
task prod_release_prepare_web_source
task prod_release_prepare_backend_source
```

These tasks do not deploy by themselves.

## Exception Policy: Full Prod DB Replacement From Stg

This is not the normal release path.

Use the exception path only when explicitly approved. It replaces the prod DB contents with stg DB contents after a prod backup. This means prod-only orders, OCR history, drafts, workflow states, outputs, menus, and audit data are removed from the active prod database and survive only in the backup export.

Because this is destructive, every exception task has an `exception_` prefix and requires an exact `CONFIRM=...` value.

## Exception Sequence

1. Put prod web into maintenance mode.
2. Export a full prod DB backup.
3. Export stg DB as the restore source.
4. Restore prod DB from the stg export.
5. Deploy prod code from the release branch.
6. Verify prod live surfaces.

## Maintenance Page

Deploy a temporary static page to `web-prod` while the DB copy/release is in progress:

```bash
CONFIRM=DEPLOY_PROD_MAINTENANCE task exception_prod_maintenance_deploy
```

The page says the system is currently being updated. This replaces the current `web-prod` image, so it is intentionally separate from normal release tasks.

## Prod DB Backup

Export `orders-prod/orders` before any restore:

```bash
CONFIRM=BACKUP_PROD_DB task exception_prod_db_backup
```

Do not continue unless the backup URI exists and is recorded.

## Stg DB Export

Export `orders-stg/orders` as the source for prod restore:

```bash
CONFIRM=EXPORT_STG_FOR_PROD_RESTORE task exception_stg_db_export_for_prod_restore
```

Record the printed `STG_EXPORT_URI`.

## Prod Restore From Stg

Destructively replace prod DB from the stg export:

```bash
CONFIRM=RESTORE_PROD_FROM_STG \
PROD_BACKUP_URI="gs://..." \
STG_EXPORT_URI="gs://..." \
task exception_prod_db_restore_from_stg
```

The restore task refuses to run unless:

- `CONFIRM=RESTORE_PROD_FROM_STG`
- `PROD_BACKUP_URI` is set and exists
- `STG_EXPORT_URI` is set and exists

By default it drops and recreates the prod `public` schema before importing the stg SQL export.

## GCS And Environment References

After DB restore, inspect DB values that may reference stg resources:

- `gs://sawahospitalsystem-stg-raw/...`
- `gs://sawahospitalsystem-stg-templates/...`
- `https://worker-stg-...`
- `https://web-stg-...`
- stg service account emails

If DB rows reference stg buckets, choose one explicit policy before reopening prod:

- Copy referenced stg objects into prod buckets and rewrite DB URIs to prod buckets.
- Temporarily allow prod to read stg buckets, then schedule a cleanup migration.

The first option is preferred for long-term prod correctness.

## Verification After Restore And Deploy

Verify:

- `task prod_release_db_diff`
- `task predeploy_prod_checks`
- Cloud Run `web-prod` and `worker-prod` revision/image
- `/system/status`
- order list and order detail
- `ocr-sheet`, `draft-sheet`, and `workflow-state`
- menu screens and facility settings
- output generation
- PDF/artifact links

Do not call the exception copy complete until prod code and prod DB are both on the intended stg-derived state and the user-visible surfaces work.

## Rollback

Rollback requires the recorded prod backup URI.

1. Put `web-prod` back into maintenance mode.
2. Restore prod DB from the recorded prod backup export.
3. Redeploy the previously active prod web/worker images or release branch.
4. Run prod live verification again.

Do not delete the backup export until rollback is no longer needed.
