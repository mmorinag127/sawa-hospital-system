# Hakodate Production Refactor And Verification Plan

Date: 2026-04-28

This plan starts from the fixed baseline in `docs/hakodate_best_method_fixed_20260428.md`.

## Non-Negotiable Baseline

The next implementation must preserve the accepted observable behavior before changing architecture.

Fixed baseline behavior:

- Use the accepted four points to rectify the FAX page.
- Generate target cells from the facility template structure.
- Snap target-cell X boundaries to actual vertical FAX lines.
- Keep target Y boundaries from the accepted template-derived regions unless a later approved change explicitly replaces that rule.
- Produce review output with four-point markers, green target grid lines, red target-cell centers, and OCR labels when available.
- Produce machine-readable cell coordinates for downstream OCR and sheet assignment.

Not fixed:

- The final OCR engine.
- Whether an OCR engine's structure output is collected as auxiliary evidence.
- The final model/provider used for digit recognition.

The production pipeline must not let OCR-derived structure override the facility-template cell map unless that change is explicitly approved after a separate validation.

## Production Module Boundary

Create one production-oriented service boundary instead of depending on ad-hoc `tmp/` scripts.

Proposed module:

- `backend/src/services/hakodate_preprocessing_pipeline_service.py`

Primary entry point:

```python
run_hakodate_preprocessing(
    fax_pdf_path: Path,
    facility_code: str,
    order_id: str,
    template: FacilityTemplate,
    *,
    render_dpi: int = 200,
    review_overlay: bool = True,
) -> HakodatePreprocessingResult
```

Required inputs:

- Original FAX PDF.
- Facility identity.
- Facility template geometry.
- Facility quantity-column definition.
- Template merged-cell structure.

Required outputs:

- Rectified FAX image.
- Review overlay PDF/PNG.
- Cell coordinate JSON.
- OCR target-cell records.
- Alignment evidence and quality gates.
- Optional OCR result overlay after downstream OCR runs.

No production code should depend on files in `tmp/`.

## Data Contracts

`HakodatePreprocessingResult` must include:

- `facility_code`
- `order_id`
- `page_index`
- `four_points`
- `rectified_image_size`
- `target_cells`
- `alignment_evidence`
- `quality_gate`
- `review_artifacts`

Each `target_cell` must include:

- `sheet_cell`
- `worksheet_row`
- `worksheet_col`
- `semantic_field`
- `date`
- `daypart`
- `menu_row_index`
- `bbox`
- `center`
- `merged_cell_id` when applicable
- `source_template_signature`
- `x_snap_evidence`

Each OCR result must include:

- `target_cell_id`
- `sheet_cell`
- `semantic_field`
- `crop_bbox`
- `raw_text`
- `normalized_value`
- `confidence`
- `engine`
- `engine_metadata`

## Implementation Phases

### Phase 1: Freeze Tmp Baseline As Golden Reference

Move no behavior yet. Add a tracked regression fixture manifest that points to the accepted local artifacts and expected metrics.

Deliverables:

- Golden manifest for `FAC00003 / ORD9d8f9c2b`.
- Golden manifest for all 14 facilities.
- A comparison script that checks newly generated overlays and coordinate JSON against the frozen baseline.

Pass condition:

- The new service has not been introduced yet.
- The fixed artifacts are discoverable from tracked docs.
- The accepted 14-facility PDF can still be regenerated from the tmp scripts.

### Phase 2: Extract Preprocessing Core Without Behavior Change

Move the core accepted logic from the tmp scripts into the production service.

Deliverables:

- Four-point rectification wrapper.
- Template target-cell extraction wrapper.
- FAX vertical-line X snap.
- Green/red/Q overlay renderer.
- Coordinate JSON writer.

Pass condition:

- For `FAC00003 / ORD9d8f9c2b`, the production service output visually matches the fixed single-facility artifact.
- For all 14 facilities, the production service output visually matches the fixed all-facility artifact.
- No OCR engine decision is introduced in this phase.

### Phase 3: Template And Merged-Cell Contract

Make template structure explicit and production-safe.

Deliverables:

- Facility template schema.
- Merged-cell representation.
- Template signature.
- Stale-template blocker when facility category or quantity-column definition changes.
- Template-source resolver that blocks instead of guessing when candidates are ambiguous.

Pass condition:

- Normal facilities and merged-cell facilities both generate target cells without adding or deleting template cells.
- Quantity target cells are derived from template semantics, not OCR interpretation.
- Stale or ambiguous templates produce an explicit blocker.

### Phase 4: OCR Crop And Engine Interface

Separate OCR crop generation from OCR engine execution.

Deliverables:

- Batch crop generator using the fixed crop behavior: fixed padding, known frame erasure, small-noise removal.
- OCR engine interface.
- Engine runner for at least one local/offline-capable path.
- Optional adapters for additional candidates.

OCR engine policy:

- Do not fix the final OCR engine at this planning stage.
- Do not assume one provider must be digit-only.
- If an engine returns structure, store it as auxiliary evidence unless a later approved validation promotes it.
- Sheet assignment must remain keyed by template-derived `target_cell_id`.

Pass condition:

- Crops are generated from `target_cells` only.
- OCR can run in batch or with equivalent throughput.
- OCR result records retain `target_cell_id` and cannot be assigned without it.

### Phase 5: Sheet Assignment

Convert OCR results into draft-sheet/ocr-sheet-compatible values.

Deliverables:

- Assignment mapper from `target_cell_id` to semantic sheet field.
- Normalizer for numeric values, blanks, and remarks.
- Conflict handling when multiple OCR candidates map to one cell.
- Explicit blocker when a value cannot be mapped to a known target cell.

Pass condition:

- No OCR result can create a new row or column.
- Unknown OCR candidates do not silently enter the sheet.
- `draft-sheet`, `ocr-sheet`, and `workflow-state` use the same mapped result.

### Phase 6: Review UI And Manual Adjustment

Expose the alignment result before sheet application when confidence is insufficient.

Deliverables:

- Review overlay with four points, green grid, red target centers, and OCR labels.
- Operator adjustment UI for green grid/four-point correction.
- Save path for adjusted alignment evidence.
- Re-run path using adjusted geometry.

Pass condition:

- A user can see why an order is blocked.
- A user can correct alignment without editing raw JSON.
- Corrected alignment produces new coordinate JSON and review overlay.

### Phase 7: Staging Rollout

Integrate behind a strategy flag.

Strategy modes:

- `legacy`
- `hakodate_preprocess_only`
- `hakodate_ocr_preview`
- `hakodate_apply`
- `both_compare`

Pass condition:

- `both_compare` can run without overwriting existing draft results.
- The same order can show legacy vs Hakodate outputs side by side.
- `hakodate_apply` is blocked until visual and API parity checks pass.

## Verification Test Plan

### Unit Tests

- Four-point input produces a stable rectified image size and coordinate system.
- X snap moves template boundaries only to monotonic detected FAX vertical lines.
- X snap refuses non-monotonic or zero-width cells.
- Target-cell generation preserves worksheet row/column identity.
- Merged-cell templates preserve merged cell geometry.
- Crop generation uses fixed padding and erases only the known cell frame.
- OCR result assignment requires `target_cell_id`.
- Unknown target cells produce blocker output.
- Stale template signatures produce blocker output.

### Golden Fixture Tests

Use the accepted fixed artifacts as visual/coordinate golden references.

Required cases:

- `FAC00003 / ORD9d8f9c2b` single-facility accepted case.
- All 14 facilities from the 2026-04-26 to 2026-04-30 order set.

Assertions:

- Four points are present.
- Green grid lines are present.
- Red target centers are present.
- Target-cell count matches the fixed baseline per facility.
- X snapped boundaries match the baseline within tolerance.
- Coordinate JSON is stable within tolerance.

### Visual Review Tests

Generate review PDFs and PNGs from the production service.

Required outputs:

- Single-facility PDF/PNG.
- All-facility PDF/PNG.
- Per-facility page artifacts.
- OCR result overlay after OCR runner is connected.

Pass condition:

- Human review can verify cell centers are inside the intended cells.
- Failed or low-confidence alignment is visibly marked and not treated as success.

### OCR Engine Comparison Tests

Compare candidate OCR engines without changing the template-derived assignment logic.

Candidate classes:

- Existing local OCR path.
- Yomitoku-based OCR or structure output as auxiliary evidence.
- Tesseract/PaddleOCR/other OSS local candidates if available.

Evaluation dimensions:

- Cell crop throughput.
- Raw recognition accuracy.
- Blank-cell false positives.
- Non-empty false negatives.
- Ability to batch.
- Determinism across repeated runs.
- Local deployment cost.

Pass condition:

- Engine choice is based on measured result records keyed by target cells.
- OCR structure output does not become the sheet structure owner by accident.

### Integration Tests

- Order preprocessing resolves the facility template from facility/order context.
- Missing template blocks downstream OCR assignment.
- Ambiguous template candidates block instead of selecting the first candidate.
- Saved draft is not overwritten by a preprocessing re-run.
- `draft-sheet`, `ocr-sheet`, and `workflow-state` agree on the same assignment result.
- `both_compare` keeps legacy output intact while storing Hakodate candidate output separately.

### Staging Tests

Required staging set:

- The accepted 14-facility order set.
- At least one merged-cell facility.
- At least one low-quality/distorted FAX.
- At least one stale-template simulation.
- At least one manual-adjustment run.

Pass condition:

- Every generated overlay has four points, green lines, red target centers, and clear blocker/review state.
- No sheet application occurs when the quality gate is blocked.
- Legacy results are not destroyed during comparison.

## Quality Gates

Automatic apply is allowed only when:

- Four-point rectification is accepted.
- Template signature matches.
- Target-cell count matches expected template count.
- X snap is monotonic.
- All required quantity cells have valid bbox and center.
- Cell centers are inside their bboxes.
- Alignment evidence is recorded.
- OCR results are keyed to known target cells.

Manual review is required when:

- Any target-cell count differs from template expectation.
- X snap falls back for a major boundary.
- Grid/line residual exceeds threshold.
- Merged-cell geometry is ambiguous.
- OCR returns values outside known target cells.

Blocker is required when:

- Facility template is missing.
- Template selection is ambiguous.
- Template signature is stale.
- Four-point rectification fails.
- Coordinate JSON cannot be produced.
- Sheet assignment would require creating a new row/column.

## Deliverable Order

1. Golden fixture manifest and regression script.
2. Production preprocessing service with visual parity to the accepted tmp output.
3. Template schema and stale-template blocker.
4. OCR crop/engine interface.
5. Sheet assignment mapper.
6. Review overlay and manual adjustment UI.
7. Strategy flag integration.
8. Staging comparison run.

Do not start `hakodate_apply` until phases 1 through 6 pass locally and `both_compare` passes on staging.
