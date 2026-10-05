# C1 Hospital Staging Verification: 356df78

Snapshot: 2026-10-05 05:18 UTC. **Live verification failed during the post-reload mobile checks. Owned-record cleanup passed. C1 remains incomplete.**

## Source and Runs

- Generating/develop/verification-WT HEAD: `356df78e7538633f294d73467a369ab8633bbec8`; branch `codex/modernization-c1-hospital-verify-20261005`. Product source/lockfiles unchanged by this worker; `git diff 91f7291ad66019485da448e6a077f2935867b43f HEAD -- frontend/src backend/src` is empty.
- [Duplicate push 37266058465](https://github.com/mmorinag127/sawa-hospital-system/actions/runs/37266058465): parent cancellation completed `05:03:37Z`. Both deploy jobs have empty steps and conclusion `cancelled`; no deploy ran in this duplicate. This worker issued no cancellation.
- [Opted-in run 37266102226, attempt 1](https://github.com/mmorinag127/sawa-hospital-system/actions/runs/37266102226): source/bootstrap/migration/builds and both deploy steps succeeded; final conclusion failure at `05:13:23Z`.
- [Job 111624594447](https://github.com/mmorinag127/sawa-hospital-system/actions/runs/37266102226/job/111624594447), `Verify opted-in actual menu master UI and clean up own record`: `05:12:12Z` to `05:13:18Z`, exit 1. Artifact upload succeeded.
- Executed workflow command: `uv run --project backend --extra dev --frozen python scripts/verify_stg_menu_master_ui.py`. The prior [b28833f failure report](menu-master-stg-verification-b28833f-20261005.md) and raw evidence remain unchanged; no old proof was relabelled as this SHA.

## Runtime and Partial Live Proof

Immutable image checks report the full source SHA above for both actual serving revisions:

| Service | Revision | Image |
| --- | --- | --- |
| web-stg | `web-stg-00354-vfj` | `asia-northeast2-docker.pkg.dev/sawahospitalsystem/backend/frontend@sha256:98519cf2c2638b035ed8e9e4555ac2e10f006e703e2a9a72ad92e2aa941b7669` |
| worker-stg | `worker-stg-00774-rch` | `asia-northeast2-docker.pkg.dev/sawahospitalsystem/backend/backend@sha256:6e0cff6711c527fcb58045504a3d167f30edc693596373ed6d33398d379f6cb5` |

Targets: `https://web-stg-avlnzjjrca-dt.a.run.app`, `https://worker-stg-avlnzjjrca-dt.a.run.app`. Actual database proof: PostgreSQL `15.18`, database `orders`, role `orders_app`. The previous index-metadata gate now passes; schema and real auth/API preflight passed, including authenticated identity/hospital access, unique-name absence and unauthenticated API401. This used the existing service-principal token, not human Google/GIS login.

- Owned name: `c1-live-37266102226-1-aabd0ce687864d539b5b6f9ea984dcdd`; ID `MNU8ec113eb`.
- Receipt 1: real UI POST200, revision1; nine fields match canonical server response, including `cut`, quantity0, bag1500/count, cold, Japanese text and two condiments. Created-row ten-cell assertion passed.
- Receipt 2: real UI PUT200 with expected revision1, canonical revision2; nine fields match, including quantity null, bag0/g, hot, Japanese text and one condiment. Fresh-document reload/timeOrigin and all nine displayed input values passed before the recorded second check was appended.
- No success response mock: the browser runner continues authorized requests to the real same-origin backend. Ledger has two successful receipts, `pending: null`, and a schema fingerprint matching the recorded database proof.
- Cleanup deleted exactly `MNU8ec113eb` after ownership/receipt/current-snapshot/reference checks. `apiAbsenceVerified: true` means the committed runner confirmed GET by that ID returned404 and the owned-name filtered list had `items=[]`, `total=0` (runner lines 179-185). No manual DML or extra deletion was performed by this worker.

## Failure Boundary

Result phase is `browser-failed-cleanup-verified`; browser phase `nine-field-edit`, code `browser-check-failed-at-nine-field-edit`. Browser checks contain only `nine-field-POST` and `nine-field-PUT-fresh-document-null-zero-Japanese`. Page/hydration/unexpected-write counts are all0. These counters do not make the failed browser run a pass.

The [browser runner](../../frontend/scripts/verify-menu-master-live.mjs) appends the successful reload check at line147. The next block (lines148-157) starts at360px: row count, all ten cell values, `document.documentElement.scrollWidth <= 360`, then row/right-scroll/editor screenshots. None of the `owned-row-*` or `owned-edit-*` images exists, while the catch generated `failed-owned-row.png` at360px. This bounds failure to the first mobile block before its first successful screenshot; stale409 starts only at line160 and was not reached.

The sanitized catch intentionally discards exception text and records only the broad phase (lines185-187). It does not preserve the specific failing assertion, actual cell array or width measurement. The table-region failure image alone cannot establish document overflow or distinguish product layout from a harness assertion/capture failure. No HTTP timeout, auth failure or save failure is shown by the evidence. **Fix-worker blocker:** retain the assertions and add safe subphase/width/cell diagnostics for this block, then reproduce to identify the exact failure before changing product behavior. No source repair, threshold relaxation or rerun was performed here.

Real second-editor stale409, explicit conflict reload and injected network-abort preservation remain unexecuted on this run. The planned network check aborts in the browser before backend submission; it is not a real503 test. Human usability approval, full C0 and remaining C2 are separate unfinished gates; no C1/prod acceptance is claimed.

## Evidence and Reproduction

Fresh root: `/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c1-verify/tmp/stg-356df78/`. Only sanitized `live-menu-master-stg-37266102226-1` was downloaded. Its [raw manifest](/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c1-verify/tmp/stg-356df78/live-menu-master-stg-37266102226-1/manifest.json) verifies all8 listed files, with no unexpected files other than the manifest itself.

| Evidence | SHA256 |
| --- | --- |
| Raw manifest | `e96eb67067ec0981828c2ac03aace48ad2b3eb60a947793eaac4a806855b112f` |
| Raw result | `37c0a74cfc74665bbdf9a9fd41800f0c737c16e4fa481aee207b3ed51ee6d983` |
| Raw ledger | `2e307b6ad51971892046341d871f2db2f669c96c2992cc59961b4e0138a65bfd` |
| [Artifact review](/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c1-verify/tmp/stg-356df78/artifact-review.json) | `01d2968618c780d50eaa3dbfb4c9b261bdd54ad868ea4452d2cfc553fc283263` |

GitHub artifact ID `11326547720`; archive digest metadata `sha256:7ee3bc74866663a16f0ba614fa0271c35ef0096ee844ee7e2df9a4ed015add5e` (archive bytes not independently hashed after extraction). Review includes full source/runtime identities, file hashes, cancelled duplicate, receipt summary and prior b288 evidence hash comparisons.

Parent visual review targets: [created1280](/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c1-verify/tmp/stg-356df78/live-menu-master-stg-37266102226-1/owned-created-1280.png), [created360](/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c1-verify/tmp/stg-356df78/live-menu-master-stg-37266102226-1/owned-created-360.png), [created-right360](/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c1-verify/tmp/stg-356df78/live-menu-master-stg-37266102226-1/owned-created-right-360.png), [created-right1280](/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c1-verify/tmp/stg-356df78/live-menu-master-stg-37266102226-1/owned-created-right-1280.png), [failed post-reload row360](/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c1-verify/tmp/stg-356df78/live-menu-master-stg-37266102226-1/failed-owned-row.png). These are table-region images, not completed whole-page mobile proof or human approval.

Commands from own WT: `node tmp/stg-356df78/observe.mjs <label> <mode> <run>`; exact expanded gh arguments/timestamps/exits are in each `<label>.json`, with matching stdout/stderr logs. Executions: `duplicate-final status 37266058465` exit0; `manual-watch-1 watch 37266102226` exit1 (workflow failure, no timeout); `manual-final status 37266102226`, `manual-artifacts artifacts 37266102226`, `manual-download download 37266102226` all exit0. `node tmp/stg-356df78/verify-artifact.mjs` exit0 verifies failed-run evidence integrity, not live acceptance.

The watcher used `gh run watch --exit-status --interval 60` with a10-minute local bound and ended naturally. Browser/runner both record owned-process cleanup; this worker started no local server/browser/DB. Existing Ubuntu migration and Node20-Action-to-Node24 warnings remain in logs. Stage candidate is this new report only; no stage/commit/push/deploy/auth changes were performed.
