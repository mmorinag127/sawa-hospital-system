# Workflow contract fixture migration, 2026-10-24

## Scope and status

- Worktree: /Users/mmorinag/Sawa/2025.12/worktrees/local-only-hospital-20261023
- Branch: codex/local-only-actions-20261023
- Base: ee0352d8bfc91d3497435e86bff0048e1b304df9
- Implementation scope: the eleven nodes below in backend/tests/integration/test_workflow_state_service.py and this document only.
- No pytest, test collection/import, typecheck, build, CI, Actions, commit, push, merge, deployment or cloud calls were executed. These are static implementation changes, not passed-test evidence.
- This is one assigned implementation slice, not completion of the full Hospital migration.

## Real component boundary

HistoryCase/authored_sheet_case prepare an explicitly authored local source, facility, dated menu and exact period. These are synthetic component inputs, never live FAX or corpus/prod-parity proof.
Actual context confirmation, queue, delivery claim, original capture, H2 producer, terminal receipt, explicit evidence selection and current reads are used. Only the external recognizer/rendered provider outputs are doubles; no ownership, selected evidence, current-state, quantity result or gate is mocked.
The parent-owned native_selection helper is imported read-only for low-score successors. It validates unchanged physical target identities, including shared daypart/menu normalization and all declared quantity cells.
All formal saves use HistoryCase.save/save_draft: anomaly review, review confirmation and PUT workflow-v2/sheet. HistoryCase.command reads the current revision before each request and explicitly sends If-Match; workflow_command receives that expected revision. Internal save_ocr_sheet_exact/persist_sheet_draft are not used as selected-save substitutes in these replacements.
The original unowned payloads retain their raw values/geometry/failure metadata. _assert_unowned_input_cannot_replace_selected calls the actual persist_ocr_evidence_run without a captured owner and expects original_template_capture_missing_or_mismatched, with DB/files unchanged. Diagnostic cache injection never creates or edits selected evidence/receipt SQL. Independent actual owned positives prove current quantities, not guessed legacy OCR conversion. There is no generic HTTP 410 replacement.

## Per-node responsibility mapping

### 1. test_refresh_workflow_state_keeps_numeric_review_when_position_fallback_has_high_risk_numeric_issues

Replacement: `test_owned_low_score_and_nonnumeric_cells_remain_editable_without_position_guessing`.

Original FAC00001 March 22 table retains the six values 5/4/3/2/1/1, row/column geometry, missing grid and failed_cells merged_numeric_cell at column 3. A real owned authored six-column source independently produces exactly those six values, is selected and formally saved. Real native successor runs at score 0.09 use literal ?, blank, then 5 in the first quantity cell; the other five values stay 4/3/2/1/1. Each is explicitly selected, projected and formally saved without numeric-content adoption blockers. The exact original unowned payload cannot be persisted or replace the selected sheet. Numeric trust is not a source-identity failure.

### 2. test_refresh_workflow_state_keeps_partial_position_fallback_apply_blocked

Replacement: `test_owned_partial_schema_is_blocked_without_guessing_missing_quantity_columns`.

Original FAC00005 noisy header, daypart strings, Menu A 57/2 and Menu B 58/4 plus original cells/bboxes are retained unchanged as unowned diagnostic input. No interpretation of those noisy labels is claimed. Independent authored current-format FAC00005 March 22 declarations explicitly pair soft_x/change_1_x with 57/2 and 58/4, produce/select/save them. A deliberately incomplete formal save omits change_1_x; current read and actual run_bagging reject quantity_template_field_missing with unchanged DB/source after the command. This replaces old position-fallback/semantic-shell guessing, not its compatibility success.

### 3. test_refresh_workflow_state_blocks_malformed_raw_ocr_projection

Replacement: `test_malformed_unowned_rows_cannot_replace_owned_editable_cells_or_saved_five`.

Original FAC00005 April 5 malformed table retains quote, &, 9, < menu strings, 0/0 and 58/2 cells, unknown dayparts and structured cell geometry. That exact unowned input is refused at the actual source-capture boundary, never projected. Independent current authored April 5 FAC00005 Menu A/regular_x quantity 5 is produced/selected/formally saved and remains current. Its actual registered PDF is temporarily made unavailable; load_selected_pdf_bytes with its real receipt rejects selected_pdf_verification_failed, DB/source are unchanged during refusal, and the same bytes/path are restored in finally. This is an original-byte storage boundary component, not a missing-PDF live rerun claim.

### 4. test_refresh_workflow_state_does_not_use_position_fallback_when_facility_conflicts_with_evidence

Replacement: `test_foreign_facility_candidates_and_changed_source_receipt_cannot_replace_owned_cells`.

Original FAC99999 (0.96) / FAC88888 (0.52) candidates and the six 5/4/3/2/1/1 cells are preserved. Actual owned FAC00001 production/selection/save keeps its explicit six-column source; candidate resolution retains both foreign candidates but the configured current facility stays FAC00001. For each foreign value, a negative-only copy of the real H2 source digest is deliberately changed and the actual canonical artifact validator rejects canonical_artifact_source_changed. No changed receipt is persisted or selected.

### 5. test_refresh_workflow_state_requires_choice_for_stale_ambiguous_position_fallback

Replacement: `test_stale_ambiguous_position_candidates_cannot_override_owned_columns_or_selected_five`.

Original two tables retain both physical layouts (including the extra auxiliary column), all six quantity values, candidate mappings starting at source columns 3 versus 4, confidence 0.99 and stale template/grid metadata. Actual resolver explicitly clears resolved_value and requires choice with position_fallback_removed and column_mapping_choice_required, retaining both candidates. Separately owned/formally selected and saved Menu A 5 remains unchanged; no stale candidate becomes the current mapping.

### 6. test_refresh_workflow_state_does_not_treat_llm_reparse_as_rerun_state

Replacement: `test_unowned_llm_reparse_job_cannot_become_current_rerun_or_replace_formally_saved_five`.

Original unowned raw Menu A 5/template/grid/page payload is retained and actual persist is refused. A real owned source/terminal/selection/formal save supplies current 5. A separate real OcrJob row carries the original running/llm_reparse diagnostic metadata but has no workflow attempt or terminal receipt. Actual project_workflow_state does not call it a current rerun, keeps apply_draft and saved 5, and preserves both job rows and selected evidence. This does not run or restore a retired LLM OCR provider.

### 7. test_refresh_workflow_state_clears_stale_low_trust_review_when_current_sheet_is_semantic_ready

Replacement: `test_owned_semantic_twenty_six_and_manual_five_are_not_blocked_by_stale_low_trust_diagnostics`.

Original FAC00015 regular_forbidden template metadata, confidence 0.96, merged_numeric_cell and failed column 3 remain exact unowned diagnostics. Current-format explicitly authored April 26 FAC00015 regular_x 26/blank note is truly produced/selected and read as semantic evidence_ready/open_draft. A formal operator edit to 5/operator is then selected as the saved draft and stays apply_ready without quantity_review_required/numeric_trust_low. No current-sheet or capabilities mock supplies 26 or 5.

### 8. test_refresh_workflow_state_keeps_low_trust_review_when_current_sheet_still_requests_ocr_review

Replacement: `test_real_saved_review_warning_is_visible_without_blocking_quantity_edit_or_ocr_adoption`.

Same original FAC00015 low-trust diagnostics are retained, not attached as forged capabilities. Real native April 26 regular_x 26 is selected and formally saved with an explicit sheet_ocr_review_required sheet warning. Current read retains 26 and that warning; confirm_warnings retains ocr_review_required, while numeric content is not an adoption blocker. A subsequent real formal save of operator 5 with warnings=[] clears the explicit review warning. The old mocked numeric-trust stop is not reinstated.

### 9. test_refresh_workflow_state_uses_saved_draft_sheet_blockers

Replacement: `test_formal_saved_five_preserves_weekly_warning_but_missing_physical_menu_blocks_output`.

Original March 22 Menu A 5 fields and sheet_weekly_menu_missing warning, including draft-row-1, now pass through formal selected save. The actual MonthlyMenu and entries are then removed as explicit negative-fixture damage. Current read preserves the saved 5/warning/selected draft, reports monthly_menu_object_missing plus current_sheet_source_unavailable, and refuses run_bagging without mutation. The old can_apply=True despite absent authoritative physical source is not a current positive contract.

### 10. test_refresh_workflow_state_keeps_semantic_initial_draft_when_weekly_menu_warning_remains

Replacement: `test_actual_initial_blank_semantic_sheet_is_editable_but_missing_menu_has_no_guessed_shell`.

Original semantic initial March 22 Menu A/blank quantity responsibility uses an actual owned blank recognizer result with typed fields and no saved draft; it stays editable and reads blank, never derives 5 from stored order lines/unowned cache. Actual menu removal then yields no guessed transient rows/draft and blocks apply/confirm with monthly_menu_object_missing. Actual run_bagging refuses saved_sheet_required. The old fake build_initial_sheet_draft carrying OEVtest plus invented rows/warnings after source loss is removed rather than treated as current success.

### 11. test_refresh_workflow_state_prefers_clean_saved_draft_over_stale_layout_blockers

Replacement: `test_formal_clean_saved_five_outranks_unowned_layout_diagnostics_and_stale_save_is_refused`.

Original unresolved template id, candidate id, confidence 0.42, blocked reason template_resolution_missing and absent grid remain exact unowned input. Actual native source/terminal/selection and formal saved draft_sheet retain operator 5/blank note/row-1. Actual current workflow stays apply_ready without semantic_shell_only/template_unresolved/numeric_trust_low and retains draft_newer_than_lines. A second formal save advances the real revision; PUT sheet with the earlier If-Match and stale 9 is rejected 409 with DB/source unchanged and latest saved 5 retained.

## Retained sibling boundaries (source existence checked)

All are in the same integration test module and remain unchanged by this slice:

- test_project_workflow_state_returns_new_evidence_available_when_latest_evidence_differs_from_draft: real terminal candidate 8 remains unselected; formal saved/current 5 stays selected.
- test_owned_running_rerun_retains_selected_saved_five and test_owned_failed_rerun_retains_selected_saved_five: actual running/failure ownership cannot replace saved 5.
- test_unpreviewable_owned_candidate_cannot_replace_saved_five: missing owned overlay / changed records produce exact artifact failures and preserve current 5.
- test_real_candidate_selection_rejects_stale_workflow_revision_and_keeps_saved_five: actual candidate-selection CAS 409.
- test_current_schema_failure_and_saved_layout_blockers_preserve_authoritative_quantities: actual saved schema failure plus retained layout blockers with 23/27 preserved.

The new clean-save node independently adds stale sheet-save CAS refusal. The malformed-input node adds actual original-byte unavailability, distinct from artifact unavailability. Facility-source digest refusal uses the real artifact validator, not a fabricated success receipt.

## Handoff

All eleven old _persist_evidence preparations have been replaced and the obsolete helper removed. Parent review and exact integrated-SHA serial local verification remain outstanding. No runtime result is asserted by this document. Other suites, helpers, production sources, conftest and manifest are outside this slice.
