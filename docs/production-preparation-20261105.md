# Production preparation, 2026-11-05

These changes prepare a future owner-approved production release. They do not
authorize production deployment, database writes, IAM grants, or resource creation.
The current worktree is unverified until the final serial batch is recorded.

## Release order

1. Resolve the school canonical data and explicit production settings. Provisioning
   approval remains separate; do not substitute staging resources or data.
2. Preserve retrievable rollback image digests. The previously observed hospital
   worker and OCR producer images could not be described in Artifact Registry.
3. Complete local production-copy migration rehearsal and the serial local tests.
4. Integrate reviewed sources and verify the staging release through Actions.
5. At the separately approved production window, stop writes and drain jobs;
   build all release artifacts, approve the release, validate dependencies and
   create a successful database recovery point before formal migration.
6. Deploy the producer, worker and portal with agent sidecar from the same source.
   Validate business operations before resuming writes and scheduled jobs.

Do not downgrade a changed database merely by selecting an older application
revision. Compatibility and database restoration must be rehearsed first.

## Hospital local migration rehearsal

Use the named preparation worktree. Neither script deploys resources. Capture
requires existing read access; it does not grant access or create a cloud backup.
Local PostgreSQL binaries must be compatible with the production dump. The
dedicated PostgreSQL lifecycle helper binds a Unix socket and stops its server
on exit. Do not run a shared Docker/database daemon for this rehearsal.

```sh
python scripts/capture_hospital_rehearsal_dump.py tmp/prod-rehearsal/source \
  --proxy /Users/mmorinag/Sawa/2025.12/cloud-sql-proxy \
  --pg-dump /opt/homebrew/opt/postgresql@16/bin/pg_dump
OWNED_POSTGRES_PG_BIN=/opt/homebrew/opt/postgresql@16/bin \
OWNED_POSTGRES_TMPDIR="$PWD/tmp/prod-rehearsal/local" \
python scripts/rehearse_hospital_schema.py tmp/prod-rehearsal/source/production.dump \
  --source-receipt tmp/prod-rehearsal/source/source.json
```

Run with the repository's dependency-equipped Python only after implementation
is complete. The capture and rehearsal outputs contain business-data backups;
keep them in ignored, owner-readable temporary storage, never in Git or public
artifacts. A failed capture or restoration is not a valid migration receipt.

The capture uses a read-only repeatable-read transaction and one exported snapshot
for the dump and existing-column hashes. Rehearsal checks the dump digest and
restored values, runs formal adoption/upgrade, then compares every original column.
`rehearsal.json` records restore/migration durations, source SHA, dirty status and
preservation results. A dirty-worktree result is not a release-source attestation.
Existing restored sequences are also compared before and after migration.
PostgreSQL sequences are not part of a repeatable-read row snapshot: a capture
while production writes continue does not prove an identical-time sequence
snapshot or production cutover readiness. The release-window write stop and
successful recovery point remain separate prerequisites.

## Unresolved external prerequisites

- `PROD_FACILITY_MASTER_URI` and `PROD_AGENT_RUNTIME_CONFIG` are absent from both
  repository and production environment variables as observed on 2026-11-05.
- Production school automation audience/email/subject have not been registered.
- Production ingest/OCR queues and recovery schedulers are absent. Production
  delivery validation deliberately does not create IAM bindings or resources.
- School production database, API, worker and Web are not provisioned. The user
  selected current staging saved business data, excluding verification data;
  the exact exclusion inventory is being reconciled with creation evidence.
  The portal agent reads the school API's
  explicit database secret and model configuration, so its preparation cannot
  be substituted with staging settings.
- Existing rollback digests must become retrievable through an approved source
  preservation process before production release. Do not mark this resolved
  merely because the old Cloud Run revision is still serving.

Full progress and estimates are maintained in the workspace release plan:
`/Users/mmorinag/Sawa/2025.12/docs/reviews/PRODUCTION_RELEASE_PLAN_20261105.md`.
