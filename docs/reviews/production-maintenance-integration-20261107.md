# Production maintenance integration

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
