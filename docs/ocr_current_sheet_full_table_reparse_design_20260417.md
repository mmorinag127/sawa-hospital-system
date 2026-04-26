# OCR Current-Sheet Anchored Full-Table Reparse Design

## Problem

Reported orders such as `ORDf2b6d176` still show row drift and column drift after Gemini/OpenAI reparse.

The current `llm_assist` path tells the model to preserve the current sheet structure, but the actual provider schema still runs in `quantity_only_mode` and returns sparse `row_index + qty.*` output. Backend then re-materializes rows through quantity-only merge, blank-anchor realignment, structural projection, and downstream `parse_order_lines()`.

That means the shared decision point is still:

- model output sparse OCR rows
- backend reconstructs sheet rows from OCR payload and column mapping

This permits the same failure class to re-enter through row drift, column drift, and partial quantity mapping.

## Failure Class

`OCR payload re-materialization drift against canonical current-sheet shell`

## Required Invariant

For `llm_assist` reparses with a resolvable current-sheet baseline:

- the current sheet is the canonical shell
- the model receives the full current sheet as the anchor
- the model returns a full-table candidate aligned to that shell
- backend validates that the candidate preserves row count, column count, row order, and all non-quantity cells
- backend applies only quantity-field diffs
- if the invariant cannot be satisfied, reparse blocks explicitly instead of falling back to sparse OCR row projection

## Non-Goals

- letting the model freely edit date/daypart/menu/remarks/structure
- continuing quantity-only sparse row projection for current-sheet anchored `llm_assist`
- rescuing invalid full-table output by silently falling back to legacy OCR payload materialization

## Root Fix

### 1. Introduce explicit full-table assist mode

`llm_assist` with a current-sheet baseline becomes `llm_full_table_mode`.

This mode:

- disables provider-side `quantity_only_mode`
- serializes the current sheet into a full-table anchor for the prompt
- requires the provider response schema to include every baseline field for every row

### 2. Make the model output a full-sheet candidate

The prompt keeps the existing structural constraints, but the output contract changes:

- rows must match the current sheet row count
- each row must include every baseline field
- quantity cells may change
- non-quantity cells must be copied exactly unless the fax clearly contradicts them

The backend still does not trust that freedom. The output is a candidate, not an accepted sheet.

### 3. Validate against the canonical current sheet

Backend compares candidate rows with baseline rows and enforces:

- same row count
- same field list / column order
- same `row_ids` cardinality
- no change to non-quantity fields
- quantity fields must remain digit-only or empty string

Reject reasons:

- `llm_full_table_baseline_missing`
- `llm_full_table_row_count_mismatch`
- `llm_full_table_structural_drift`
- `llm_full_table_invalid_quantity_text`

### 4. Deterministically merge quantity diffs only

If validation passes:

- start from the canonical current-sheet rows
- copy only validated `qty.*` cell changes from the candidate
- produce merged rows
- parse downstream order lines from the merged rows

### 5. Block dangerous sibling paths

For `llm_assist` with a baseline:

- do not set `llm_quantity_only_mode`
- do not run quantity-only merge / realignment / structural projection
- do not accept payload-row reconstruction fallback when full-table validation fails
- do not let auxiliary LLM audit own the critical rerun path

If baseline is missing, stop with explicit blocker instead of downgrading to sparse-row reconstruction.

### 6. Treat audit as auxiliary, not canonical

- pre-inference prompt hints come from deterministic structural feedback derived from:
  - current-sheet baseline rows
  - first-pass OCR rows
  - blank-anchor / row-count heuristics
- optional cross-model / evaluator audit may still run after candidate generation
- optional audit timeout or provider failure must not hard-fail the current-sheet rerun
- only structural validation / quantity validation / weekly-menu validation may block application

## Shared Path Changes

Primary change points:

- `src/services/order_service.py`
  - `reparse_order()`
  - `_build_llm_assist_prompt()`
  - new full-table validation / merge helper
- `src/services/gemini_ocr_service.py`
  - provider prompt/schema selection for full-table mode
- `src/services/openai_ocr_service.py`
  - provider prompt/schema selection for full-table mode

## Validation Rules

Accepted candidate:

- same structure as baseline
- only quantity cells changed
- quantity changes are digit-only or empty string

Rejected candidate:

- row added or dropped
- column added, dropped, or reordered
- date/daypart/menu/remarks/non-qty changed
- invalid quantity text
- missing current-sheet baseline

## Test Plan

### Reported class

- `llm_assist` full-table candidate with quantity-only changes is accepted
- merged sheet rows remain structurally identical to baseline
- downstream parsed lines reflect new quantities

### Close sibling cases

- full-table candidate changes a date/daypart/menu anchor cell -> reject with `llm_full_table_structural_drift`
- full-table candidate injects only `remarks`/`note` text drift -> ignore the drift and preserve the current-sheet shell
- full-table candidate changes row count -> reject with `llm_full_table_row_count_mismatch`
- optional post-inference audit times out -> keep the rerun result and record audit failure as auxiliary metadata only

### Stop behavior

- `llm_assist` without a resolvable baseline -> explicit `llm_full_table_baseline_missing`
- no fallback to quantity-only sparse re-materialization in this mode
- no hard-fail when optional audit itself times out or its provider is unavailable

### Provider contract

- Gemini full-table mode schema includes all baseline fields and does not require `row_index`-only sparse rows
- OpenAI full-table mode schema includes all baseline fields and does not require `row_index`-only sparse rows

## Completion Criteria

The fix is complete only when:

- the shared `llm_assist` reparse path is current-sheet anchored
- sparse OCR row reconstruction is no longer the active path for that mode
- reported case and close siblings are covered by tests
- invalid outputs block explicitly instead of degrading through fallback reconstruction
