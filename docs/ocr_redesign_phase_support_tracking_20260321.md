# OCR Redesign Phase Support Tracking

Updated: 2026-03-21

## Purpose

This note tracks additive support work for OCR redesign Phases 1-3 on `ocr-redesign-march-2026` without duplicating the main backend implementation effort.

## Phase 1

- Focus: draft-first review states, recoverable OCR outcomes, saved draft visibility.
- Current support:
  - saved sheet revisions already surface `draft_ready` / `draft_saved` style metadata through `get_order_review_summary()`
  - confirm/apply blockers and warnings are already derived from saved-draft age plus reject reasons
- Coverage added here:
  - targeted test that a saved newer draft plus reject reasons maps to `draft_ready`, `blocked`, and `needs_human_review`

## Phase 2

- Focus: clearer user-facing state mapping and structured blocker/warning detail.
- Current support:
  - `ocr_review_stage`, `ocr_reparse_status`, blocker-detail arrays, and line-count metadata are already exposed by order review helpers
  - request-path OCR page loading already defers expensive grid recovery when template metadata is incomplete
- Coverage added here:
  - targeted request-path guardrail test that partial template grid metadata still returns a deferred state instead of recomputing overlays

## Phase 3

- Focus: preserved evidence, corrected-PDF aware review context, and safe review fallbacks.
- Current support:
  - LLM review output preparation already carries `pdf_variant_requested`, `pdf_variant_used`, and optional fallback reason
  - changed cells become `applied_overwrites`; unchanged flagged cells remain in `cell_issues`
- Coverage added here:
  - targeted test that evidence survives both paths:
    - applied overwrite evidence stays attached
    - unresolved issue evidence stays attached
    - `needs_more_review` remains true when unresolved evidence-backed issues still exist

## What This Support Work Intentionally Avoids

- no queue/job orchestration rewrite
- no full Step2 rollout by default
- no attempt to finish all redesign phases in one pass
- no destructive DB migration

## Current Branch Alignment Notes

- This support note is narrower than the full branch scope.
- The branch currently also includes implementation-side work for:
  - `template_resolution` artifacts / blockers
  - `evidence_manifest` completeness tracking
  - evidence-only Step2 behavior that prefers saved edited revisions instead of recomputing from confirmed lines
  - a request-path `/ocr-pages` recovery response (`409`) when overlay evidence is missing
- The evidence-only Step2 path is currently feature-flagged and remains **off by default** via `OCR_EVIDENCE_ONLY_STEP2=false`.
- The support test file covers only a thin slice of those behaviors:
  - review-state mapping
  - LLM review evidence propagation
  - request-path no-recompute guardrails
  - evidence-only Step2 preference for saved revisions
- If this doc is used as a rollout tracker, it should be read as `support coverage added in this pass`, not as a complete inventory of branch implementation work.

## Suggested Next Checkpoints For Main Implementation

1. Keep user-facing state derivation centralized on the existing review helpers instead of duplicating state logic in endpoints.
2. Preserve the request-path deferral rule: no overlay/grid recompute unless metadata is already available or an explicit offline job owns that work.
3. Treat `evidence` as required for any review overwrite that mutates sheet values; unresolved issues should remain visible until evidence-backed resolution lands.
