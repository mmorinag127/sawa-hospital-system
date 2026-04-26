# OCR Position Fallback Plan 2026-03-26

## Context

`ORD23e1dc28` shows the current gap clearly:

- raw OCR table structure exists
- `tables[0].rows`, `tables[0].cells`, `row_index`, `col_index`, and `bbox` exist
- quantity-looking cells also exist in the body
- but `template_resolution`, `grid_column_edges`, `grid_row_edges`, and `quantity_subgrid_passes` are missing

Today the system treats this as:

- `semantic_shell_only = true`
- `quantity_column_semantics_ready = false`
- `template_unresolved`
- `sheet_quantity_column_unmapped`

So the system is not failing because position is unavailable. It is failing because the current implementation only accepts quantity semantics from the strict template/grid path.

## Problem Statement

The current Step2 semantics pipeline is too binary:

1. strict template/grid path succeeds -> quantity columns can be trusted
2. strict template/grid path fails -> quantity columns are treated as unresolved

This misses an important middle case:

- the page has usable table coordinates
- the quantity columns are visibly present
- the facility template already defines the expected quantity slots
- only the high-level grid/template metadata is missing or noisy

This middle case should not be forced into the same bucket as "no positional information".

## Design Goal

Add a **position-based quantity/column fallback** that is used only when the current strict path is unavailable or low confidence.

This fallback must:

- coexist with the current template/grid method
- never reduce confidence when strict metadata is already good
- produce candidate mappings from real `row/col/bbox` evidence
- allow operator confirmation when confidence is not strong enough for auto-apply
- avoid silently auto-mapping columns from weak OCR text alone

## Desired Decision Ladder

### Tier 1: strict deterministic path

Use the existing logic when all of these are present:

- resolved non-blocked `template_resolution`
- effective `table_box`
- effective `grid_column_edges`
- quantity column semantics judged ready by current logic

This remains the highest-confidence path.

### Tier 2: position fallback

Use this only when Tier 1 fails but raw table position data exists:

- `tables[].cells` exist
- `row_index`, `col_index`, `bbox` exist for the relevant header/body cells
- a facility quantity schema exists
- the table has a plausible menu column and at least one plausible quantity region

Tier 2 should produce:

- `column_mapping_candidates`
- optional `quantity_candidates`
- `decision_source = position_fallback`
- `evidence_ref` for the header/body cells used to justify the guess

Tier 2 should not immediately force auto-apply unless confidence is clearly high.

### Tier 3: operator-confirmed position mapping

If Tier 2 yields one or more plausible mappings but not enough confidence for full auto trust:

- show the candidate mapping in Step2
- let the operator choose/confirm
- bind that choice to `evidence_run_id`

Once confirmed, the system should treat quantity semantics as ready for the current draft/evidence path.

### Tier 4: LLM ranking only when Tier 2 remains ambiguous

Use Gemini only after position-derived candidates have already been constructed.

Gemini should:

- rank or relabel candidate mappings
- explain ambiguous header OCR (`肉款` -> likely `肉禁`)
- return candidates, not unconditional truth

Gemini should not be the only source of mapping when no positional evidence exists.

## Why Position Is Good Enough Here

For `ORD23e1dc28`, evidence already contains:

- header cells at concrete columns
- body quantity values concentrated in a stable right-side region
- duplicated "変更" style columns appearing in order
- known facility quantity slots on the app side

That means the problem is not "where are the columns?".

The real problem is "how do we turn those columns into canonical quantity slots when template/grid metadata is weak?".

That is a mapping problem, not a visibility problem.

## Proposed Backend Architecture

### 1. Add a dedicated service for position fallback

Create a new service, for example:

- `backend/src/services/position_column_mapping_service.py`

Input:

- evidence payload
- facility template quantity columns
- optional saved facility column order

Output:

- observed quantity-region columns
- candidate mapping list
- confidence band
- evidence references for UI
- reasons and ambiguity notes

### 2. Detect the quantity region from cells, not from template metadata

Use `tables[].cells` and `tables[].rows` to derive:

- header band rows
- menu column candidate
- quantity region start
- quantity candidate columns to the right of the menu column

Signals:

- text density in the menu column
- numeric density in the quantity columns
- x-center ordering of columns
- repeated numeric population in body rows
- sparse or empty note columns at the far right

Existing related code worth reusing:

- `quantity_subgrid_experiment.py`
- `infer_quantity_subgrid()`

The new fallback should reuse the row/col and numeric-density ideas, but output mapping candidates rather than only a subgrid box.

### 3. Score candidate mappings against the facility schema

For each candidate observed quantity column set, score it against expected facility quantity fields:

- left-to-right order consistency
- header OCR similarity to expected labels
- numeric fill density
- stable spacing between quantity columns
- penalties for note-like or text-heavy columns

Example scoring signals:

- `header_similarity_score`
- `left_to_right_alignment_score`
- `numeric_density_score`
- `spacing_consistency_score`
- `non_quantity_penalty`

### 4. Emit candidate resolutions instead of hard-blocking immediately

When strict semantics are missing but position fallback finds plausible mappings:

- fill `column_mapping_candidates`
- optionally fill `quantity_candidates`
- mark `decision_source = position_fallback`

Then candidate resolution can decide:

- high confidence: resolved automatically
- medium confidence: `column_mapping_choice_required`
- low confidence: stay blocked

### 5. Distinguish "template unresolved" from "quantity semantics unresolved"

Today `template_unresolved` effectively kills the path.

After this change:

- `template_unresolved` should not automatically block apply if
  - position fallback produced a stable quantity mapping
  - the operator confirmed it, or confidence is high enough

It can remain a warning, but it should no longer force the order into the same bucket as "no mapping available".

### 6. Preserve strict-path priority

If strict template/grid metadata is present and healthy:

- do not use position fallback for truth
- at most log it for diagnostics

This avoids creating a second competing owner for already-good orders.

## Proposed UI Behavior

### Step2 when strict path fails but position fallback exists

Instead of only saying "数量列が未解決", show:

- observed OCR columns
- proposed mapping
- confidence
- evidence snippet / header labels / column numbers

Example:

- `列4 -> 常食`
- `列6 -> 肉禁`
- `列7 -> 魚禁`
- `列8 -> 変更1`
- `列9 -> 変更2`

If confidence is medium:

- show 2-3 candidate mappings
- operator chooses one

If confidence is high:

- preselect one
- let the operator confirm or edit

### Keep decisions bound to evidence

The mapping choice must be stored as:

- a critical decision
- tied to `evidence_run_id`

Do not let it bleed into a later rerun automatically.

## Implementation Plan

### Phase 1: data extraction and candidate generation

1. Add `position_column_mapping_service.py`
2. Parse `tables[].cells` and `tables[].rows`
3. Detect header band and candidate quantity columns
4. Score candidate mappings against facility quantity schema
5. Return structured candidates

Deliverable:

- backend-only helper with deterministic outputs

### Phase 2: integrate into candidate resolution

1. If strict path fails, call the position fallback service
2. Populate `column_mapping_resolution` and/or `quantity_resolution`
3. Add `decision_source = position_fallback`
4. Add `evidence_ref` payload for UI

Deliverable:

- current `workflow-state` can distinguish:
  - no mapping
  - candidate mapping available
  - confirmed mapping

### Phase 3: gate relaxation for confirmed position mappings

1. Suppress `template_unresolved` as a hard blocker when:
   - position mapping is resolved or operator-confirmed
2. Treat quantity semantics as ready when:
   - a stable position mapping exists
   - and required quantity fields are covered

Deliverable:

- orders like `ORD23e1dc28` move from hard block to review/apply path

### Phase 4: optional Gemini ranking

1. Feed compact positional/header evidence to Gemini only when ambiguity remains
2. Gemini returns ranked candidate mappings
3. Keep deterministic checks as guardrails

Deliverable:

- better handling of noisy headers like `肉款`, `魚炊`, duplicated `変更の`

### Phase 5: UI confirmation flow

1. Render candidate mappings in Step2
2. Let the operator confirm/change them
3. Bind the decision to `evidence_run_id`
4. Recompute `workflow-state` and `ocr-sheet`

Deliverable:

- a visible and explainable path from "position candidate exists" to "apply ready"

## Test Plan

### Unit tests

Add new unit tests for the fallback service:

1. `detects_quantity_region_from_cells_when_template_is_missing`
- input: `tables[].cells` with menu column + 5 quantity columns
- expect: quantity region detected

2. `scores_left_to_right_mapping_against_facility_schema`
- input: expected facility schema + observed columns
- expect: candidate ordering score favors the correct mapping

3. `penalizes_text_heavy_note_column`
- input: far-right column with note text
- expect: it is not selected as quantity

4. `normalizes_noisy_header_variants`
- input: `肉款`, `魚炊`, duplicated `変更の`
- expect: candidate labels still converge to the expected slot family

### Integration tests

1. `position_fallback_populates_column_mapping_candidates_when_template_resolution_is_missing`
- payload has `tables[].cells`
- no `template_resolution`
- expect: `column_mapping_resolution.candidates` present

2. `workflow_state_moves_to_layout_choice_required_instead_of_template_unresolved_when_position_candidates_exist`
- current strict path missing
- position candidates available
- expect: not dead-end blocked only by template absence

3. `operator_selected_position_mapping_unblocks_apply`
- choose mapping candidate
- expect: `apply_gate.can_apply = true`

4. `clean_saved_draft_is_not_overwritten_by_position_fallback_refresh`
- ensure the new logic does not reintroduce draft refresh bugs

5. `strict_template_path_wins_over_position_fallback_when_both_exist`
- ensure no ownership conflict

### Contract/API tests

1. `/workflow-state` returns `column_mapping` decision source and candidates
2. `/ocr-sheet` reflects resolved quantity fields after operator choice
3. `/draft-sheet` uses the confirmed position mapping without stale blockers

### Corpus regression

Add explicit fixtures for:

1. `ORD23e1dc28`-like noisy header with usable positions
2. a normal strict-template order where fallback must not change behavior
3. a false-positive note-column case

### Live verification checklist

For the first production rollout, verify exact orders with:

1. `workflow-state`
2. `draft-sheet`
3. `ocr-sheet`
4. UI confirmation flow in Step2

Success condition for `ORD23e1dc28`:

- no junk rows
- no stale blockers
- quantity column candidates visible
- after operator selection, `can_apply = true`

## Rollout / Risk Control

Introduce the fallback behind a feature flag, for example:

- `OCR_POSITION_FALLBACK_ENABLED`
- `OCR_POSITION_FALLBACK_USE_LLM`

Recommended rollout:

1. log-only candidate generation
2. candidate-resolution only
3. operator-confirmed apply path
4. optional high-confidence auto-resolution

This avoids destabilizing already-working strict-template orders.

## Key Decision

The correct architecture is:

- strict template/grid path remains the primary truth
- position fallback is the secondary path
- Gemini ranks ambiguous mappings only after position candidates exist
- operator confirmation promotes a position mapping into current truth

This lets the system use `row/col/bbox` information that already exists, without turning every noisy OCR table into an unconditional auto-apply.
