# Prod Release Readiness 2026-05-03

## Scope
- Target stg fix: `ORDab6c77ff` / `FAC00008` must resolve the facility-specific template and show facility-specific sheet columns.
- Target prod decision: decide whether current stg can be promoted to prod without corrupting existing prod data.
- This document is a release gate input, not a prod deploy approval.

## Current Stg Evidence
- Backend fix commit: `955b221 Allow workflow v2 facility-resolved templates`.
- Deployed stg worker revision: `worker-stg-00283-7v4`.
- Stg worker image digest: `sha256:b3fd73937944005c33823fd1927485755f6571e873a56e74cbcd2e7b825b20bf`.
- `FAC00008` stg config was reset to `{}` so the facility master, not a generic override, is canonical.
- `ORDab6c77ff` workflow-v2 context confirmed with no blockers.
- Latest selected OCR result: `OEV0f1a2f44de75`.
- `sheet-source` fields are facility-specific:
  - `qty.regular_2f`
  - `qty.regular_3f`
  - `qty.soft_2f`
  - `qty.soft_3f`
  - `qty.mixer_2f`
  - `qty.mixer_3f`
- UI screenshot evidence: `tmp/live_checks/ORDab6c77ff_workflow_v2_sheet_after.png`.

## Mistake Root Cause
- Failure class: deploy/source/data canonical mismatch.
- Code cause: workflow-v2 treated a missing explicit `fax_template_id` as unresolved even when the facility config already resolved a materialized facility template. That made a valid facility-template-only configuration fail.
- Data cause: stg had a manual `facility_configs` override for `FAC00008` pointing to generic `fax_layout_regular_forbidden_v1`. That override outranked the facility master and produced wrong sheet sections.
- Release cause: local/stg validation did not include an invariant that every facility's resolved quantity fields match the intended facility-specific schema before reporting completion.
- Process cause: deploy readiness was judged from current branch cleanliness and one live surface, while sibling jj/worktree changes and environment-specific facility configs were not treated as release inputs.

## Prevention Now Applied
- `AGENTS.md` contains `Git/JJ Release Source Discipline`; deploy readiness now requires worktree, jj, sibling commit, Cloud Run revision/image, and exact live-surface checks.
- For Sawa releases, `git status --short` is not a sufficient readiness signal.
- For facility-template changes, compare stg/prod resolved facility configs before deploy.
- For a facility-specific schema, empty `fax_template_id` is allowed only when `resolved_config.fax_template` materializes quantity fields.
- A generic facility config override must be treated as data drift and reviewed before prod promotion.
- Generated artifacts and production code must not be released as one indistinct unit.
- One-off Python heredoc execution remains forbidden for verification code; reusable verification must be saved as files.

## Stg vs Prod Snapshot
- Worker stg revision: `worker-stg-00283-7v4`, created `2026-05-03T14:01:03Z`.
- Worker prod revision: `worker-prod-00480-2tp`, created `2026-04-17T05:51:22Z`.
- Web stg revision: `web-stg-00109-86b`, created `2026-05-03T13:27:36Z`.
- Web prod revision: `web-prod-00294-2bk`, created `2026-04-14T11:43:30Z`.
- Prod currently returns `404` for workflow-v2; stg returns `200`.
- Backend source diff is large:
  - `src/api/orders.py`: `1045` insertions, `137` deletions.
  - `src/services/order_service.py`: `10293` insertions, `2197` deletions.
  - New stg-only services include Hakodate OCR, workflow-v2, order current state, and template field schema services.
  - New stg-only migration: `0013_order_current_states.py`.
- Web source diff is large:
  - `src/pages/orders/[id].tsx`: `4929` insertions, `811` deletions.
  - New stg-only pages: `orders/[id]/workflow-v2.tsx`, `orders/[id]/inspection-v2.tsx`.

## Data Snapshot
- Stg orders: `14`; prod orders: `124`.
- Stg uploaded PDFs: `14`; prod uploaded PDFs: `94`.
- Stg ingest jobs: `14`; prod ingest jobs: `127`.
- Stg DB quota used rows: `618`; prod DB quota used rows: `68509`.
- Prod has existing order history and must not be cleaned like stg.
- Prod has `13` archived orders in the light order list.

## Facility Config Drift
- Facility IDs exist in both environments: `FAC00001` through `FAC00016`.
- Resolved quantity fields differ for multiple facilities.
- Important examples:
  - `FAC00008`: stg and prod resolve to 2F/3F facility-specific fields after stg correction.
  - `FAC00009`, `FAC00010`, `FAC00011`: stg has explicit generic/regular-soft-mixer overrides while prod resolves differently from facility master.
  - `FAC00016`: stg resolves only `regular/diabetes`; prod resolves additional forbidden/change fields.
- Prod deploy must not assume stg facility config data is the prod source of truth.

## Prod Deploy Position
- Direct prod deploy is not approved yet.
- A smooth prod update is feasible, but only after the following gates:
  - Build from a named release branch that contains all stg fixes and no missing jj sibling changes.
  - Verify facility config drift and decide the canonical source per facility before deploy.
  - Take a prod Cloud SQL backup and raw bucket snapshot/export before deploy.
  - Confirm new table creation for `order_current_states` is safe and additive.
  - Do not run cleanup scripts against prod order data.
  - Deploy backend and web from fresh source copies after the final release commit.
  - Run live smoke checks on at least one existing prod order and one new uploaded order.
  - Verify workflow-v2, order detail, OCR overlay, sheet generation, bagging, output confirmation, and order list status.

## Required Pre-Prod Checklist
- Confirm no production-code changes remain only in `/Users/mmorinag/Sawa/2025.12/workspace/.jj`.
- Confirm no critical-path commits are outside the release branch with `git log --all --not HEAD -- <critical paths>`.
- Compare stg/prod facility configs using `tmp/prod_stg_diff/reports/facility_template_field_summary.tsv`.
- Preserve prod existing orders, PDFs, OCR results, sheets, snapshots, and archive state.
- Backup prod Cloud SQL and prod raw bucket before deploy.
- Deploy to prod only after stg exact-order validation passes on the same release branch.
