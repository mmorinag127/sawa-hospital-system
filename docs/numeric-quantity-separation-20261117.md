# Numeric Quantities And OCR Originals

## Scope

- Keep quantity fields numeric or blank; retain uncertain recognition in `quantity_ocr_review` with the exact source cell.
- Do not extract digits from prose, infer zero, or rewrite numeric OCR values.
- Generate, edit and save pending sheets. Require explicit operator resolution before materializing quantities or confirming outputs.
- Project existing saved JSON without backfilling stored history. Preserve manual numeric edits and source evidence.
- No production, IAM or budget changes.

## Progress

| Item | Status |
| --- | --- |
| Shared numeric parsing and original-text separation | Implemented |
| Read-only saved-sheet projection and strict aggregate parsing | Implemented |
| Pending-cell editor, explicit blank confirmation, output boundary | Implemented |
| Regression fixtures and mounted save/reload coverage | Passed, including numeric input and explicit blank confirmation without losing originals |
| Serial local verification | Final source: backend 960, agent 482, frontend 319 passed; no test failures/skips. Type check and production frontend build passed. |
| Develop integration and manual staging deployment | Source `fb24db83fef9c8cdc42b388145982abf6ebc4a69`; manual run `43914538923` succeeded |
| Staging save/reload and historical preservation | December: generated from selected OCR, checked, saved new revision, reloaded; all 400 cells matched the expected projection. November: all 160 cells matched. Original stored JSON, selected evidence and numeric values unchanged. |

## Verification History

- Initial batch: backend 910 passed / 4 failed; frontend 317 passed / 2 failed. Fixed native intake raw-review preservation and updated the output blocker/review-before-save fixtures together. Next batch passed backend 914, agent 482, frontend 319, types/build.
- Initial staging run `43907946814` succeeded on attempt 2. Attempt 1 failed downloading the PostgreSQL signing key (TLS EOF), before deployment; only failed jobs were retried.
- Live verification found a sibling stale-anomaly path in the lightweight initial/output display API. Added current-sheet hash binding for initial/sheet/output/inspection reads without rewriting stored reviews.
- Follow-up local batch: backend 957 passed / 2 failed, all other stages passed. Fixed legitimate unsaved-review persistence using an explicit current numeric-policy version; legacy unbound reports remain excluded. Review, confirmation and save use the same numeric projection.
- Final batch and final staging run succeeded. No further code edits followed the signed final verification.

## Live Evidence

- Worker: `worker-stg-00969-s66`, 100% traffic, digest `8a23e2a8926d31cfed35df79ce6bb07ab438836e857f8644e3e83509d6673963`.
- Web: `web-stg-00529-hff`, 100% traffic, digest `bb367f215155d3af89acae8ef66c5e2b53b5fe70d59a9675e9ff3591f1de472f`.
- December `ORD2db6fc20`: selected evidence `OEV382b9b027793`, new saved draft `ODS8a82709c84064554`. Non-numeric originals: 19, kept separate and pending, not guessed as zero. Existing numeric cells: 81 unchanged. Prose input into a quantity cell was rejected in the real browser.
- Historical draft `ODSc2b5aabe989d4cee` remains unchanged: SHA256 `e81354d2021495ea47c2e96ad99f376d79427142c68ee9ef259a311f940252d7`.
- November `ORDef98a31b`: historical/current draft `ODS32c4e37dd4bd4d61` unchanged, 33 numeric cells unchanged, 7 pending originals displayed separately.
- December's old 13 warnings were not reused. A new normal anomaly review returned 5 warnings, with no digit-concatenation quantities 1120/14804. This is not proof that all numeric OCR is correct.
- Pending sheets can be saved; output/confirmation controls are disabled until operator resolution. No bagging/output/confirmation was performed with pending quantities. Original OCR job counts and selected evidence JSON were unchanged.
- Production, IAM and budgets were not modified. No local Docker or local deployment was used. Two manual staging dispatches in total; one failed-job retry on the initial run.

Local evidence in this worktree:

- `tmp/numeric-quantity-display-final-proof-20261117/`: signed receipt, exact commands/log hashes, backend/agent JUnit, frontend/type/build logs.
- `tmp/numeric-quantity-display-staging-proof-20261117/receipt.json`: signed local proof bound to successful staging run.
- `tmp/numeric-quantity-stg-before-20261117.json`, `tmp/numeric-quantity-stg-pre-save-20261117.json`, `tmp/numeric-quantity-stg-after-20261117.json`: read-only historical JSON/evidence/numeric/job-count comparison.
- `tmp/numeric-quantity-ui-before-20261117.json`, `tmp/numeric-quantity-ui-after-reload-20261117.json`, `tmp/numeric-quantity-november-ui-20261117.json`: actual browser cell values.
- `tmp/numeric-quantity-after-20261117.jpg`: staging screenshot of separate OCR originals and pending quantities.

## Known Separate Accuracy Issue

Numeric OCR `909` remains unchanged. The source has a crossed-out 90 and red 92; the red correction intersects a column edge. This change does not invent 92 or claim to fix that recognition geometry defect. See the December anomaly audit for source comparisons.
