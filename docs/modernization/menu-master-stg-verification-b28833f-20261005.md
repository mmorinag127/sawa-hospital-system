# C1 Hospital Staging Verification: b28833f

Snapshot: 2026-10-05 04:51 UTC. **Live menu verification failed before browser startup. C1 is not complete.**

## Source and Actions

- Generating/develop/verification-WT HEAD: `b28833f2b7e104f787f6489a5b92f5ab62e6dce6`; branch `codex/modernization-c1-hospital-verify-20261005`. Product source and lockfiles were not changed.
- [Push run 37263819987](https://github.com/mmorinag127/sawa-hospital-system/actions/runs/37263819987): success, completed `04:39:12Z`. Menu live verification was skipped as configured for push, not a passing browser test.
- [Opted-in run 37263836807, attempt 1](https://github.com/mmorinag127/sawa-hospital-system/actions/runs/37263836807): failure, completed `04:48:28Z`. Source, bootstrap, migration, builds, worker deploy and web deploy succeeded.
- [Failing job 111619585840](https://github.com/mmorinag127/sawa-hospital-system/actions/runs/37263836807/job/111619585840): `Verify opted-in actual menu master UI and clean up own record`, `04:47:55Z` to `04:48:22Z`, exit 1. Artifact upload succeeded.
- Existing workflow command: `uv run --project backend --extra dev --frozen python scripts/verify_stg_menu_master_ui.py`. No workflow was dispatched, cancelled or restarted by this verification worker.

## Runtime Provenance

The sanitized result records immutable-image source checks passing for both services with the full source SHA above:

| Service | Serving revision | Image |
| --- | --- | --- |
| web-stg | `web-stg-00353-5rz` | `asia-northeast2-docker.pkg.dev/sawahospitalsystem/backend/frontend@sha256:07cfc20176fa08328e07a569a7dc8ca8f50133e20d8884e23d4dc3a1a7d97110` |
| worker-stg | `worker-stg-00773-j78` | `asia-northeast2-docker.pkg.dev/sawahospitalsystem/backend/backend@sha256:ff68c703accfe874cb857519d496c1069aeb712b379d5a980a3b848d2961f9da` |

Targets were `https://web-stg-avlnzjjrca-dt.a.run.app` and `https://worker-stg-avlnzjjrca-dt.a.run.app`. No prod action or observation was performed. The failed result does not record a completed database proof or PostgreSQL server version.

## Failure and Handoff

- Observed phase: `read-only-database-preflight`; code: `unknown-non-FK-reference-column`. This is a deliberate schema safety rejection, not a missing HTTP response, browser timeout or observed menu application failure.
- Shared check: [`schema_gate`](../../scripts/stg_menu_master_safety.py), lines 95-102, queries `pg_attribute` joined to `pg_class` without a relation-kind distinction, then accepts only the two table names in `ID_TABLES`.
- Concrete code-level cause candidate: PostgreSQL also has index attributes in [`pg_attribute`](https://www.postgresql.org/docs/15/catalog-pg-attribute.html), with index relation kinds documented in [`pg_class`](https://www.postgresql.org/docs/15/catalog-pg-class.html). [`0015_runtime_schema_repairs.py`](../../backend/migrations/0015_runtime_schema_repairs.py), line 80, and [`models/menu.py`](../../backend/src/models/menu.py), line 61, define `uq_menu_facility_override_scope(menu_master_id, facility_id)`. Its index attributes would fail the table-name-only check. The [PG fixture](../../backend/tests/integration/test_stg_menu_master_live.py), lines 120-126, reconstructs reference tables without that unique constraint.
- The artifact does **not** expose the offending live relation name/kind. Therefore the exact live offending object remains unconfirmed; the index misclassification is source-supported, not a newly observed catalog row. No credential lookup, direct DB session or schema alteration was used to fill this gap.
- Fix-worker handoff: distinguish index metadata from independent reference-bearing relations at the shared catalog check; retain rejection of unknown references/views/partitions and other unsafe schema. Add faithful unique/index schema coverage and sanitized offending relation-kind diagnostics. This worker made no source fix or gate relaxation.

## Unreached Proof and Cleanup

The source calls DB preflight before auth/API preflight, ledger creation and browser startup (`verify_stg_menu_master_ui.py`, lines 152-174). The artifact contains only `result.json` and `manifest.json`: no ledger, receipt, browser result or screenshot. Thus this invocation did not reach menu POST/PUT or owned-record cleanup. It has no created-record ID, API404 or filtered-absence proof; these are **not** claimed as passing cleanup tests. `ownedProcessesStopped: true` is recorded.

All nine-field save/reload, real stale-409, draft retention, mobile/desktop screenshots and injected network-abort checks remain unexecuted on this live run. The planned abort is a browser-side injected failure, not proof of a real server 503. Service-principal authentication is not human Google/GIS login, and even its API preflight was not reached here. Human usability approval, full C0 and remaining C2 work remain separate unfinished gates.

## Evidence and Commands

Own root: `/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c1-verify`. Fresh evidence: [`tmp/stg-b28833f`](/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c1-verify/tmp/stg-b28833f/). Existing evidence was preserved. Only the requested sanitized artifact was downloaded.

| Command (from own WT; `observe.mjs` records expanded gh arguments) | Exit |
| --- | --- |
| `node tmp/stg-b28833f/observe.mjs push-watch-1 watch 37263819987` | 0 |
| `node tmp/stg-b28833f/observe.mjs manual-watch-1 watch 37263836807` | 1, workflow failure; no timeout |
| `node tmp/stg-b28833f/observe.mjs push-final status 37263819987` | 0 |
| `node tmp/stg-b28833f/observe.mjs manual-initial status 37263836807` | 0 |
| `node tmp/stg-b28833f/observe.mjs manual-final status 37263836807` | 0 |
| `node tmp/stg-b28833f/observe.mjs manual-artifacts artifacts 37263836807` | 0 |
| `node tmp/stg-b28833f/observe.mjs manual-download download 37263836807` | 0 |
| `node tmp/stg-b28833f/verify-artifact.mjs` | 0: evidence integrity verified, live result remains failed |

Both watchers used `gh run watch --exit-status --interval 60`, with a 10-minute local deadline, and ended naturally. No local server/browser/DB was started. Warnings about Ubuntu runner migration and forced Node 24 execution of Node 20 Actions remain in the saved watch logs; no suppression was added.

- Raw [artifact directory](/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c1-verify/tmp/stg-b28833f/live-menu-master-stg-37263836807-1/): GitHub artifact ID `11325867800`; GitHub archive metadata digest `sha256:e7b031969f268f732bdde3100cfcd8feb9c31e3dfcb64d6eb11c357005eaedde` (archive bytes not independently hashed after extraction).
- Raw [`manifest.json`](/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c1-verify/tmp/stg-b28833f/live-menu-master-stg-37263836807-1/manifest.json) SHA256: `706e544b71974071f2dfb8022b740b2266179974b44eed33c65855123d56e4e1`.
- Raw [`result.json`](/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c1-verify/tmp/stg-b28833f/live-menu-master-stg-37263836807-1/result.json) SHA256: `800b4cc4b9ace2c5aada762307fa070bddd6336b045bb603a7185962e89dd539`; manifest matches 1/1 listed files, no unlisted files except the manifest itself.
- [`artifact-review.json`](/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c1-verify/tmp/stg-b28833f/artifact-review.json) SHA256: `4a57b1da77973659b6a83c91bf1d4d57cc332dd8017d10bc57f9ee98cabbe52d`; includes source-file hashes, full runtime identities, failure boundary and `completedLiveProof: false`.
- Stage candidate is this report only. Ignored observation scripts/logs/raw failed artifact remain evidence, not product changes. No stage/commit/push/deploy performed. Screenshot paths cannot be supplied for this run because none were generated.
