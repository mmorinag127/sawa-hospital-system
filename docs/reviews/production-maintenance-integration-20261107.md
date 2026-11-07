# Production maintenance integration

## Migrated Checkpoint Continuation

Run 42394152276 completed database migration/bootstrap, then stopped while
comparing a tagged backend image with the digest stored in its Ready revision.
Writer resumption now resolves tags to immutable registry digests before comparison.
The migrated continuation input preserves the original application identities,
successful backup lineage and completed bootstrap flags. It requires an owner
failed run, no earlier frontend attempt, unchanged business/migration sources,
and exact live writer revision UIDs/digests with read-only gates. Only these
verified current writers are stopped; original rollback identities are not replaced.
The database gate checks the formal schema read-only instead of replaying
migration/bootstrap writes. Repeated continuation retains the first backup origin.

Local related tests passed; full source-bound release validation and live
continuation are not yet established. Production remains in maintenance.

## Current state: interrupted production entry

This section supersedes historical status statements below. Source `6feaac5d`
passed the complete 30-entry local batch (4,163.499 seconds). Owner-dispatched
production run `42349517751` entered maintenance, paused transports and stopped
the hospital worker, but failed to stop `ocr-pipeline-prod`: its deploy identity
lacks `iam.serviceAccounts.actAs` on `ocr-pipeline-exec-prod`. No database migration
or new business application deployment occurred. The portal remains HTTP 503.
The narrowly scoped service-account permission is awaiting user approval.

Recovery implementation checks all three runtime identities before mutations,
preserves original application revision UIDs/digests across retries, rejects
resumption after database/deploy advancement, and accepts an explicit failed
owner-dispatched source run only with its original artifacts and build digest.
It does not replace original application identities with the maintenance image.
Same-run retries and partial-artifact failures fail closed on changed state.

The recovery test batch passed 65 cases using isolated in-memory SQLite:
`tmp/production-maintenance-recovery-tests.log`. The first invocation stopped
before collection because no explicit test DB was configured. No production DB
was used. These tests do not replace the source-bound full release receipt or
prove actual Cloud Run resumption. Full verification and production completion
remain outstanding. Do not rerun the old workflow version as a recovery shortcut.

Approved scope: maintenance display through the existing production Actions route,
preserve original revisions, stop writers, back up, deploy, verify, then reopen.
No local deployment, no OIDC expansion, no automatic unverified rollback.

## Implementation inventory (implemented does not mean verified)

- [x] Connect maintenance entry to owner-dispatched deploy-prod.yml with release and environment gates.
- [x] Capture original revisions before replacing web-prod; keep them distinct from the maintenance revision.
- [x] Pause hospital transports and stop hospital database writers; verify drain before hospital migration.
- [x] Retain database/config access without treating a maintenance HTTP 200 as authentication proof.
- [x] Keep the maintenance display until candidate application verification; explicitly reopen stopped services.
- [x] Preserve failure state without automatically restoring old application traffic or a database.
- [ ] Update tests for the review fixes together, then execute the local serial batch.
- [ ] Review and execute the formal source-bound release verification.
- [ ] Integrate verified source and perform approved production deployment.

Live authentication now requires bounded JSON, a recognized role, and
auth_disabled=false. Targeted checks passed; full release verification and live
maintenance verification remain outstanding. Existing production
services and traffic remain unchanged. Do not dispatch the previously prepared
release as though this integration were already complete.

## Operator sequence

1. Dispatch the verified hospital release once. Approve `production-approval` to
   preserve originals and enter maintenance. Retain the start artifact.
2. Do not approve `prod-db-bootstrap-gate` yet. Perform the school and shift
   release operations through the existing platform workflow. This maintenance
   module does not stop those separate databases or their direct service URLs.
3. Once feature prerequisites are ready, approve the hospital database gate.
   It rechecks those prerequisites and the held hospital services, drains client
   connections, backs up, and migrates. Original revision identities are reused,
   never replaced with the maintenance revision.
4. Approve backend/frontend release jobs. Their exact new images are checked;
   the frontend stays on a zero-percent candidate tag until authentication,
   exact-order proxy checks and labels succeed. Promotion precedes transport
   activation. Any candidate/activation failure retains the maintenance display.

## Failure boundary

Do not start another release to overwrite an unfinished maintenance marker.
Retain start, database and final artifacts and inspect the failing cloud
operation. Failure does not authorize database restoration or old-runtime
reopening. The existing database recovery workflow remains the explicit recovery
path; old-image restart remains unproven. A rerun with a different attempt number
must not silently substitute missing original artifacts. All resumed operations
must be tied to the original source and run.

## Local evidence

- `tmp/maintenance-batch-20261107.xml`: 63 passed, 0.25 seconds pytest time.
  Includes an actual local Node HTTP server and mocked cloud operations. This
  does not establish Cloud Run scaling, candidate routing, or production behavior.
- `tmp/maintenance-workflow-batch-20261107.xml`: 18 passed and 7 subtests, 3.25 seconds.
- Shell syntax checks passed for the four changed shell entry points. Workflow
  YAML parsed with nine jobs and production environment gates on approval,
  bootstrap, backend, frontend and recovery.
- Initial targeted invocation lacked an explicit DB target and stopped before
  collection. Corrected invocation used isolated in-memory SQLite; no live DB.
- No Actions, production changes, Docker startup, commit or push during these checks.
- User approved `gpt-6.1-terra / low`; technical and compliance reviews ran with
  exact canonical wrappers. Prompt-lock monitor returned OK and was closed and archived.
- The 81 passing targeted checks above precede the review fixes below. They are
  not evidence for the current dirty tree. No deployment or release approval is claimed.

## Review fixes in progress

- Preserve service labels and support the existing agent sidecar on future maintenance runs.
- Display maintenance before pausing writers; persist mutation intent before each operation.
- Publish original rollback identities even on early failure; bind retry artifacts to the same run/source.
- Reject candidate writes and hold new backend/producer revisions read-only until application checks pass.
- Reopen only the checked image digests; on failure retain maintenance and stop writers.
- Resolve the operational uncertainty around revision readiness while service scaling is manual zero.
- Update activation failure evidence: pausing prevents further delivery, but never undoes completed work.
- Live inventory found existing `worker-prod` source-export tags. Capture their exact
  revision UID/image identities, retire only captured tags through Actions before
  stopping writers, and reject any new or changed tag. Do not recreate legacy URLs
  on the migrated database. This also closes the manual-scaling tagged-route exception.
- Local batch for `f4ef01ee` was interrupted after this live-inventory discovery;
  it is incomplete and must not be used as a successful release receipt.

Traffic tag behavior was checked against the official
[gcloud update-traffic reference](https://docs.cloud.google.com/sdk/gcloud/reference/run/services/update-traffic).

## Final batch findings

The full batch for `29d7b659` completed all 30 entries in 4,133.704 seconds,
with nine failed entries representing five distinct commands. No successful
receipt was issued. Backend: 10,304 passed, 183 skipped, 3,182.04 seconds.
The failures were collected before changing the source:

- Shipping E2E checked an asynchronously populated request array immediately
  after clicking. Await the same expected request payload; retain all UI assertions.
- The producer read-only test expected a mutated response object, while Flask's
  supported return format is `(body, status, headers)`. Check 503, no-store and
  Retry-After on that tuple without changing the production handler.
- Three PostgreSQL commands used the Linux default binary path on macOS. The
  local launch script now explicitly sets PG_BIN, REFRESH_TEST_PG_BIN and
  OWNED_POSTGRES_PG_BIN to the installed PG16 directory and checks its executables.

These corrections are not yet a successful release verification or deployment.
