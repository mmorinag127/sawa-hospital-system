# C1 Hospital Staging Verification: 179f0d3

Snapshot: 2026-10-05 05:53 UTC. **The requested automated actual-STG flow passed, including cleanup. Human usability approval is not performed; C1 overall is not complete.**

## Source and Actions

- Full generating/develop/verification-WT HEAD: `179f0d302f7aded8091c0f261bc21275c4b6b246`; branch `codex/modernization-c1-hospital-verify-20261005`. This worker made no product, test, lockfile or workflow change.
- [Manual opted-in run 37268691648, attempt1](https://github.com/mmorinag127/sawa-hospital-system/actions/runs/37268691648): all7 jobs succeeded; completed `2026-10-05T05:49:59Z`. Source, bootstrap, migration, both builds and both deploy jobs passed.
- [Actual UI verification step, job111632619242](https://github.com/mmorinag127/sawa-hospital-system/actions/runs/37268691648/job/111632619242): `05:48:37Z` to `05:49:53Z`, success/exit0. Command: `uv run --project backend --extra dev --frozen python scripts/verify_stg_menu_master_ui.py`.
- [Duplicate push 37268655587](https://github.com/mmorinag127/sawa-hospital-system/actions/runs/37268655587): parent cancellation completed `05:38:34Z`. Bootstrap/migration/backend build/backend deploy were `skipped`, steps empty; frontend deploy was `cancelled`, steps empty. Neither deploy ran in that push. No dispatch/cancel/restart was issued by this worker.
- Historical [b28833f failure](menu-master-stg-verification-b28833f-20261005.md) and [356df78 failure](menu-master-stg-verification-356df78-20261005.md), their review hashes and every raw artifact file were checked unchanged. Those failures remain historical evidence, not passing results for this SHA.

## Runtime Provenance

The run verified immutable image source labels against the full generating SHA above for both serving revisions:

| Service | Serving revision | Immutable image |
| --- | --- | --- |
| web-stg | `web-stg-00355-f9s` | `asia-northeast2-docker.pkg.dev/sawahospitalsystem/backend/frontend@sha256:5c38523bfbf99d3333dc4d841f82d53ed5e46c77db3f1fc78b8cd757bdbfb410` |
| worker-stg | `worker-stg-00775-869` | `asia-northeast2-docker.pkg.dev/sawahospitalsystem/backend/backend@sha256:cc1316c2ffa656e366a9c0298e17dbe6fe45d133b075501bd9b9b0678ec51450` |

Actual targets: `https://web-stg-avlnzjjrca-dt.a.run.app`, `https://worker-stg-avlnzjjrca-dt.a.run.app`. Database proof: PostgreSQL `15.18`, `orders`, role `orders_app`. Schema/ownership/reference preflight, real auth/me and hospital access, filtered-name absence, and unauthenticated API401 passed. Authentication used the existing service-principal token, **not human Google/GIS login**; no auth bypass or grant change was performed by this worker.

## Real Flow and Cleanup

Owned name: `c1-live-37268691648-1-f0683f89670640399bb5e1c6a01b3178`; ID `MNU45bb2dde`.

1. Real UI POST200 returned revision1. All9 payload fields match the canonical receipt, including cut, quantity0, bag1500/count, cold, Japanese daypart/category and two condiments. All10 list cells were asserted.
2. Real UI PUT200 with expected revision1 returned revision2: g, quantity null, bag0/g, hot and edited Japanese values. A new document reload with increased timeOrigin and all9 input values passed.
3. A real second editor PUT200 with revision2 returned revision3. The first editor's stale revision2 PUT returned actual HTTP409; dirty input remained, save locked, and explicit discard/reload adopted the second editor's value.
4. A permitted revision3 save was deliberately aborted in browser routing before backend submission. The failure alert and dirty draft remained. This is injected network-abort coverage, **not a real server503 test**. There is no fourth successful receipt and ledger `pending` is null.

No success response was mocked: authorized same-origin requests continued to the actual backend. Ledger has three POST/PUT/PUT200 receipts, one ID, revisions1/2/3, exact9-field payload/response equality, and a schema fingerprint matching the database proof. Browser recorded all4 checks, final checkpoint `finished`, last checked HTTP status409, and page/hydration/unexpected-write counts all0.

Workflow cleanup verified ownership, final revision3 snapshot and absence of references, then deleted **only `MNU45bb2dde`**. `apiAbsenceVerified: true` confirms the committed runner's GET-by-ID404 and filtered `items=[]`, `total=0` checks (Python runner lines179-185). Browser and runner record owned processes stopped. No manual DML or additional live write was performed by this evidence worker.

## Numeric Layout

Unrounded values are retained in [browser-result.json](/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c1-verify/tmp/stg-179f0d3/live-menu-master-stg-37268691648-1/browser-result.json):

| Viewport | Document client/scroll | Heading width/scroll | Heading text width | Heading height | Table client/scroll |
| --- | --- | --- | --- | --- | --- |
| 360 | 360 / 360 | 328 / 328 | 321.435546875 | 96 | 328 / 962 |
| 1280 | 1280 / 1280 | 1232 / 1232 | 606.044921875 | 32 | 1232 / 1232 |

Heading text stays within the heading bounds at both widths; document-wide horizontal overflow is absent. Mobile retains internal table horizontal scrolling. Full10-cell text assertions and saved null/0/Japanese labels passed before screenshots. These measurements and images belong to this run, not the earlier364px reproduction.

## Evidence and Commands

Own proof root: `/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c1-verify/tmp/stg-179f0d3/`. Only sanitized artifact `live-menu-master-stg-37268691648-1` was downloaded; GitHub ID `11328051119`. Its archive metadata digest is `sha256:d30d4654e4cb93069298668e78d100b6790727de309a58a5756bfe39fcc8c247` (archive bytes not independently hashed after extraction).

- [Raw manifest](/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c1-verify/tmp/stg-179f0d3/live-menu-master-stg-37268691648-1/manifest.json): SHA256 `766d19ba52d9e5e152b84f3894eaeeb54ece83aa5a6a2c4f1e890c0795398b4f`; all13 files verified, no unexpected extra files except the manifest itself.
- [Result](/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c1-verify/tmp/stg-179f0d3/live-menu-master-stg-37268691648-1/result.json): SHA256 `1ea4c15e1865507b01735e3e1b4b78eda82a66dc5af858def31ecef0d23aee14`; [ledger](/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c1-verify/tmp/stg-179f0d3/live-menu-master-stg-37268691648-1/ledger.json): `6bee86d363467934de568bbd2d7828060ab52214a220e4eb3833bb3a4c8f540c`.
- [Artifact review](/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c1-verify/tmp/stg-179f0d3/artifact-review.json): SHA256 `994c6f52aa2445720da08e5d558266425f70569a7b5e39c41bd2b7ddc5892092`; full source hashes, runtime identity, checks/layout, receipt summary and old-proof comparisons are retained.
- Parent visual review: [created1280](/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c1-verify/tmp/stg-179f0d3/live-menu-master-stg-37268691648-1/owned-created-1280.png), [reloaded row1280](/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c1-verify/tmp/stg-179f0d3/live-menu-master-stg-37268691648-1/owned-row-1280.png), [row360](/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c1-verify/tmp/stg-179f0d3/live-menu-master-stg-37268691648-1/owned-row-360.png), [right-scroll360](/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c1-verify/tmp/stg-179f0d3/live-menu-master-stg-37268691648-1/owned-row-right-360.png), [edit360](/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c1-verify/tmp/stg-179f0d3/live-menu-master-stg-37268691648-1/owned-edit-360.png), [edit1280](/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c1-verify/tmp/stg-179f0d3/live-menu-master-stg-37268691648-1/owned-edit-1280.png). All10 PNGs and hashes are in the manifest. Images capture table/form regions at revision1 or reloaded revision2, not full-page or human approval evidence.

Exact expanded gh commands/timestamps/exits are saved in each observation `<label>.json`, with stdout/stderr logs. Invocations from own WT: `node tmp/stg-179f0d3/observe.mjs duplicate-final status 37268655587` exit0; `manual-watch-1 watch 37268691648` ended at its local10-minute bound (SIGTERM, wrapper exit1, not an Actions failure); `manual-after-wait status 37268691648`, `manual-artifacts artifacts 37268691648`, `manual-download download 37268691648` all exit0. The final status read confirmed workflow success already recorded at05:49:59Z; no second watcher or remote rerun was needed. `node tmp/stg-179f0d3/verify-artifact.mjs` exit0 validated13 files,3 receipts,4 browser checks,2 layout measurements and cleanup.

Watcher stopped; this worker started no local browser/server/DB. Ubuntu runner migration and Node20-Action-to-Node24 warnings remain in saved logs. No stage/commit/push/deploy was performed. Stage candidate is this new report only. Human Google login/usability approval, overall C1, full C0, remaining C2 and full modernization/prod acceptance remain outside this passing automated-STG result.
