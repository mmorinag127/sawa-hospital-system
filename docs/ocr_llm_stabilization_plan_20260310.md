# OCR/LLM Stabilization Plan 2026-03-10

## Goal

Stabilize the OCR + LLM pipeline for production test operation without regressing already-good orders.

This iteration focuses on:

1. Making `ocr_reparse_quality` reflect actual LLM reparse quality instead of mixed OCR job noise.
2. Reducing dependence on dense quantity-only rows by anchoring LLM output to structural sheet rows.
3. Making OCR pipeline trigger/readiness status explicit in system health and runbooks.
4. Strengthening benchmark coverage so trusted orders are protected from regressions.

## Non-Negotiable Constraints

- `yomitoku` remains the default baseline.
- LLM prompts must stay generic; no facility-specific prompt hacks.
- Corrected PDF should be preferred when LLM reparse runs.
- Already-good orders must be protected with targeted regression tests and trusted corpus checks.

## Current Problems

### 1. Quality Gate Is Too Broad

`ocr_reparse_quality` currently samples OCR jobs by provider without cleanly separating:

- explicit user-triggered LLM reparses
- automatic OCR pipeline jobs
- fallback/rescue paths

This can cause gate failures that do not represent actual LLM reparse quality.

### 2. LLM Reparse Still Relies Too Much on Row Count Pressure

The repair pass still strongly instructs:

- exact body row count
- continuous row indexes

That keeps structural alignment possible, but it also encourages dense-fill behavior. The current flow often succeeds because downstream structural mapping and validation rescue the result, not because the raw candidate is structurally right.

### 3. OCR Pipeline Health Is Not Transparent Enough

The pipeline can run in GCS-trigger mode without `OCR_PIPELINE_URL`, but current status surfaces do not make the trigger mode and wait expectations explicit enough.

### 4. Benchmark Protection Is Still Weak

Trusted corpus coverage is small, and corpus enforcement is opt-in. This is enough for development, but weak for production hardening.

### 5. Maintainability Risk Remains High

`order_service.py` and `orders/[id].tsx` are still oversized. Full decomposition is not the goal of this iteration, but any new code should isolate logic into helper functions and keep future extraction possible.

## Implementation Policy

### A. Quality Gate Policy

- Measure only explicit LLM reparse quality in `ocr_reparse_quality`.
- Exclude plain OCR pipeline jobs from that gate.
- Keep provider-level visibility, but include summary fields that explain which jobs were included and skipped.

### B. Structural Reparse Policy

- When a structural baseline sheet exists, LLM should repair quantities against those structural rows.
- LLM candidates that omit structural cells should be projected back onto baseline rows before parse/validation.
- Validation remains fail-closed for drift patterns.

### C. Pipeline Visibility Policy

- Expose whether OCR is running in `gcs_only`, `http_trigger`, or mixed mode.
- Expose whether synchronous waiting is actually supported.
- Keep existing behavior, but remove ambiguity from health/readiness reporting.

### D. Regression Policy

- Add targeted unit/integration tests for each changed behavior.
- Add manifest-backed trusted fixtures for regression where practical.
- Keep existing ORD37 blank-anchor behavior and corrected-PDF path covered.

## Concrete Work Items

### Work Item 1: Reparse Quality Scope Fix

Implementation:

- Update `src/services/ocr_quality_service.py`
  - count only jobs that are true LLM reparses
  - expose `included_jobs`, `skipped_non_reparse_jobs`, and scope metadata
- Update `src/api/system.py`
  - surface the refined payload without changing endpoint contract shape unexpectedly

Tests:

- `backend/tests/integration/test_ocr_quality_service.py`
  - non-reparse OCR jobs are excluded
  - reparse jobs are included
  - gate pass/fail reflects only reparse population

### Work Item 2: Structural Row Projection for LLM Quantity-Only Output

Implementation:

- Update `src/services/order_service.py`
  - add a helper that projects quantity-only candidate rows onto baseline structural rows
  - use it before parse/validation when baseline rows are available
  - strengthen prompts to require preserving structural rows from the current sheet

Tests:

- `backend/tests/integration/test_ocr_pipeline.py`
  - projected rows preserve blank anchors
  - projected rows preserve date/daypart/menu cells from structural baseline
  - corrected PDF path still used
  - already-good repaired cases do not regress

### Work Item 3: Pipeline Trigger/Readiness Transparency

Implementation:

- Update `src/services/ocr_pipeline_service.py`
  - report explicit trigger mode and wait capability
- Update `src/api/system.py`
  - return these fields in `ocr_pipeline`
- Update runbook/docs where needed

Tests:

- add or extend tests for config/runtime serialization

### Work Item 4: Benchmark Guardrail Strengthening

Implementation:

- Update corpus regression helper/tests
  - add trusted cases where possible
  - add a small non-optional trusted subset assertion

Tests:

- `backend/tests/integration/test_ocr_sheet_corpus_regression.py`
  - trusted subset helper executes without env override
  - full corpus benchmark remains opt-in

## Test Execution Plan

Minimum test loop per iteration:

1. `backend/tests/integration/test_ocr_quality_service.py`
2. `backend/tests/integration/test_ocr_pipeline.py`
3. `backend/tests/integration/test_ocr_sheet_history.py`
4. `backend/tests/integration/test_ocr_sheet_corpus_regression.py`
5. `frontend` typecheck when frontend files change

Regression-sensitive cases:

- `ORD37ff2bcf` style blank-anchor / sparse fill structure
- corrected-PDF reparse path
- trusted corpus cases such as `ORD7499f262`

## Success Criteria

- `ocr_reparse_quality` reports on actual reparse jobs only
- structural LLM output is less dependent on downstream rescue
- system status clearly explains OCR pipeline trigger/readiness mode
- trusted regression coverage increases without breaking existing good orders

## 2026-03-10 Follow-up Hardening

Additional hardening after the first stabilization pass:

1. Add explicit `quality_track` metadata to `reparse_order()` job metrics/debug payloads.
2. Include `reparse_origin` and `feedback_retry_depth` so quality summaries stop inferring intent from heuristics alone.
3. Narrow structural row projection so it only fires for truly quantity-only candidates, not merely any shorter candidate.
4. Add regression tests that protect:
   - explicit quality tagging behavior
   - projection refusal when candidate rows already contain structural cells
   - successful reparse metrics carrying the new metadata
