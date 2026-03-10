# Pre-Test Stabilization Tech Debt Plan

Date: 2026-03-10

## Goal

Before test operation starts, reduce the highest-risk maintenance debt without changing existing OCR/order behavior.

This round intentionally avoids broad algorithm rewrites. The focus is:

1. isolate pure helper logic from giant files
2. add guard tests around current behavior
3. keep broader OCR regressions green

## Selected Scope

### 1. Backend OCR sheet revision helpers

Current issue:
- `backend/src/services/order_service.py` mixes OCR runtime logic with sheet revision snapshot / digest / exact-save / export helper logic.
- This makes exact-save/manual-label behavior harder to reason about and harder to test in isolation.

Planned change:
- Extract revision snapshot / digest / revision selection / revision-to-sheet rebase helpers into a dedicated module.
- Keep thin wrappers in `order_service.py` so runtime behavior and private call sites stay unchanged.

Tests:
- snapshot normalization pads fields/header/row_ids correctly
- exact revision selection prefers exact save when requested
- revision payload rebase preserves saved rows by row id
- existing integration tests for `save_ocr_sheet_exact()` and `export_ocr_sheet_label()` stay green

### 2. Backend LLM review prompt helpers

Current issue:
- `backend/src/services/order_service.py` still mixes OCR runtime orchestration with pure LLM review prompt/schema assembly.
- This makes prompt changes harder to test without exercising the full service file.

Planned change:
- Extract LLM review prompt row builders, response schema assembly, and prompt text composition into a dedicated module.
- Keep wrappers in `order_service.py` so call sites and runtime behavior stay unchanged.

Tests:
- prompt row construction preserves baseline row ids and field order
- payload rows omit fully blank rows
- review schema still requires baseline fields
- prompt text still includes baseline revision/source and structured-table context

### 3. Frontend order detail pure utility extraction

Current issue:
- `frontend/src/pages/orders/[id].tsx` is too large and contains UI + data transforms + formatting helpers in one file.
- Low-risk pure transforms should live outside the page.

Planned change:
- Extract week label normalization helpers
- Extract bag-summary grouping / formatting helpers
- Extract OCR preview / markdown parsing / sheet column spec helpers
- Keep page rendering and API behavior unchanged

Tests:
- frontend `tsc --noEmit`
- broader backend OCR regressions to ensure no API-facing drift

## Non-Goals for This Round

- rewriting OCR/LLM algorithms
- changing acceptance policy
- changing order/bag output schemas
- removing all legacy/fallback concepts

## Regression Gates

- targeted helper tests must pass
- broader OCR regression suite must pass
- frontend typecheck must pass
- no intentional changes to trusted order outputs

## Exit Criteria

- helper logic is split out of the largest hot paths
- dedicated guard tests exist for extracted backend behavior
- broader OCR regression remains green
