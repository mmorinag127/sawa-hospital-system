# OCR Provider Contract Migration

## Node-Specific Responsibility Decisions

Each row separates a still-required invariant from a retired provider algorithm.
An N/A row is an explicit scope exclusion, not a passed replacement test. The
original source remains available at develop `3dc31bdc`; current raw/identity
coverage must execute in the consolidated suite before release.

| Original node | Failure class / decision | Current exact test or scope reason |
| --- | --- | --- |
| `test_reparse_order_records_explicit_failed_llm_reparse_when_no_lines_survive` | Failure status/terminal ownership | unit/test_modernization_owned_ocr_workflow.py::test_native_producer_real_lineage_terminal_select_projection_and_save; contract/test_orders_ocr_sheet_history_api.py::test_stored_terminal_reparse_error_survives_current_reads |
| `test_reparse_order_blocks_llm_reparse_without_first_pass_context` | No authoritative source | contract/test_saved_identity_publication.py::test_missing_source_keeps_selected_saved_display_but_refuses_output |
| `test_ingest_to_reparse_flow` | Owned intake-to-save lifecycle | unit/test_modernization_owned_ocr_workflow.py::test_native_producer_real_lineage_terminal_select_projection_and_save |
| `test_reparse_order_openai_enforces_strict_quantity_rules` | Old strict numeric rejection deliberately replaced by editable raw content | unit/test_ocr_quantity_content_editable_sheet.py::test_quantity_raw_content_and_score_do_not_require_review |
| `test_reparse_order_sets_job_metrics_changed_even_when_counts_match` | Metrics must belong to the actual owned terminal attempt | unit/test_modernization_owned_ocr_workflow.py::test_native_producer_real_lineage_terminal_select_projection_and_save |
| `test_reparse_order_llm_full_table_truncated_blocks_without_pipeline_rescue` | Truncated/partial output cannot authorize publication | unit/test_h2_operator_repair_revision.py::test_real_template_targets_to_assignment_and_owned_cache_gate (partial/coordinated_shrink) |
| `test_reparse_order_llm_full_table_truncated_empty_rows_blocks_without_pipeline_rescue` | Empty/partial output cannot authorize publication | unit/test_h2_operator_repair_revision.py::test_real_template_targets_to_assignment_and_owned_cache_gate (partial/coordinated_shrink) |
| `test_reparse_order_quantity_only_rows_merge_without_provider_flag` | Quantity-only rows cannot borrow structural identity | unit/test_modernization_saved_materialization_identity.py::test_public_invalid_identity_blocks_all_lines_with_readable_original_cell |
| `test_reparse_order_resolves_week_from_ocr_payload_when_existing_week_has_no_menu` | OCR text cannot choose a different period | unit/test_modernization_saved_materialization_identity.py::test_public_explicit_selected_period_resolves_month_and_year_boundary_not_received_time |
| `test_reparse_order_does_not_apply_legacy_position_mapping_on_single_date_anchor` | No legacy positional mapping | integration/test_ocr_pipeline.py::test_registered_original_cannot_start_unowned_legacy_reparse; retained test_legacy_menu_position_mapping_is_disabled |
| `test_reparse_order_rejects_date_anchor_drift_when_llm_quantity_only` | Changed date/period cannot be silently realigned | unit/test_modernization_saved_materialization_identity.py::test_public_explicit_selected_period_resolves_month_and_year_boundary_not_received_time; unit/test_modernization_saved_materialization_identity.py::test_shared_canonical_mapper_rejects_explicit_weekday_or_year_contradiction_before_copy |
| `test_reparse_order_realigns_rotated_blank_anchor_rows_before_line_parsing` | No heuristic rotation of blank anchors | unit/test_modernization_saved_materialization_identity.py::test_public_authored_continuations_only_resolve_identity_copies |
| `test_reparse_order_auto_falls_back_to_llm_when_pipeline_lines_empty` | Retired full-table-to-LLM provider fallback | integration/test_ocr_pipeline.py::test_registered_original_cannot_start_unowned_legacy_reparse; provider-switch algorithm intentionally not reproduced |
| `test_reparse_order_llm_assist_ignores_cached_order_rows_when_first_pass_rows_missing` | Cached rows are not selected-source authority | contract/test_saved_identity_publication.py::test_missing_source_keeps_selected_saved_display_but_refuses_output |
| `test_reparse_order_llm_second_pass_uses_evaluator_feedback` | Retired evaluator-feedback second-pass sequencing | N/A: old provider algorithm, excluded from current Hakodate scope; not emulated |
| `test_reparse_order_llm_final_audit_failure_retries_with_evaluator_feedback` | Retired final-audit retry/feedback sequencing | N/A: old provider algorithm, excluded from current Hakodate scope; not emulated |
| `test_reparse_order_with_llm_assist_does_not_auto_select_provider` | Retired automatic full-table provider selection | integration/test_ocr_pipeline.py::test_registered_original_cannot_start_unowned_legacy_reparse; provider-choice compatibility intentionally not reproduced |
| `test_reparse_order_llm_assist_rejects_sparse_quantity_only_rows_when_full_table_anchor_required` | Sparse quantity rows need authored identity | unit/test_modernization_saved_materialization_identity.py::test_public_invalid_identity_blocks_all_lines_with_readable_original_cell |
| `test_reparse_order_llm_assist_blocks_provider_fallback_pipeline` | Retired fallback to a different provider | integration/test_ocr_pipeline.py::test_registered_original_cannot_start_unowned_legacy_reparse; old provider fallback compatibility intentionally not reproduced |
| `test_reparse_order_with_yomitoku_rows_runs_evaluator_before_llm_inference` | Retired Yomitoku/evaluator/inference ordering | N/A: old Yomitoku/full-table LLM sequence, not the current Hakodate producer |
| `test_reparse_order_with_yomitoku_rows_runs_second_pass_after_llm_candidate_audit` | Retired Yomitoku/candidate-audit/second-pass ordering | N/A: old Yomitoku/full-table LLM sequence, not the current Hakodate producer |
| `test_reparse_order_without_yomitoku_rows_runs_infer_then_evaluator_then_second_inference` | Retired inference/evaluator retry ordering without Yomitoku | N/A: old full-table LLM sequence, not the current Hakodate producer |
| `test_reparse_order_row_coverage_uses_payload_scoped_expected_rows` | Expected target count comes from original physical source | unit/test_h2_operator_repair_revision.py::test_real_template_targets_to_assignment_and_owned_cache_gate |
| `test_reparse_order_row_coverage_prefers_pipeline_rows_when_anchor_dates_are_weak` | Weak anchors cannot authorize positional identity | unit/test_modernization_saved_materialization_identity.py::test_public_invalid_identity_blocks_all_lines_with_readable_original_cell |
| `test_reparse_order_row_coverage_prefers_existing_line_anchor_scope` | Existing-line dates cannot replace explicitly selected scope | unit/test_modernization_saved_materialization_identity.py::test_public_explicit_selected_period_resolves_month_and_year_boundary_not_received_time |
| `test_reparse_order_applies_repair_pass_on_row_coverage_shortfall` | Missing rows/cells cannot be rescued by guessed repair | unit/test_h2_operator_repair_revision.py::test_real_template_targets_to_assignment_and_owned_cache_gate (partial/coordinated_shrink) |
| `test_reparse_order_fails_when_row_coverage_low_even_after_repair` | Incomplete cells remain rejected after an attempted repair | unit/test_h2_operator_repair_revision.py::test_real_template_targets_to_assignment_and_owned_cache_gate (partial/coordinated_shrink) |
| `test_reparse_order_rejects_mirrored_quantity_columns_without_reference_rows` | Mirrored numeric content alone is no longer an error; identity remains required | unit/test_ocr_quantity_content_editable_sheet.py::test_quantity_raw_content_and_score_do_not_require_review; unit/test_ocr_quantity_content_editable_sheet.py::test_quantity_source_identity_still_blocks_substitution |
| `test_reparse_order_rejects_canonical_mismatch_before_save` | Canonical/physical source mismatch before save | unit/test_h2_operator_repair_revision.py::test_current_repair_actual_cache_write_gate (wrong_page/wrong_evidence/wrong_config) |
| `test_reparse_order_keeps_existing_lines_when_validation_rejected` | Rejection preserves saved operator state | contract/test_saved_identity_publication.py::test_missing_source_keeps_selected_saved_display_but_refuses_output; unit/test_modernization_owned_ocr_workflow.py::test_old_failed_workflow_attempt_cannot_overwrite_force_queued_successor |
| `test_reparse_order_rejects_when_llm_cost_hard_limit_exceeded` | Retired full-table reparse-only LLM cost enforcement | N/A: OCR_REPARSE_COST_ENFORCE_HARD_LIMIT is evaluated only in the old full-table reparse branch; no current Hakodate-cost equivalence is claimed. The new unowned guard refuses entry; independent agent-budget tests are not substitutes. |
| `test_reparse_order_rejects_missing_numeric_source_row` | A missing numeric row is editable content, not guessed data | unit/test_ocr_quantity_content_editable_sheet.py::test_quantity_raw_content_and_score_do_not_require_review; unit/test_modernization_saved_materialization_identity.py::test_public_invalid_identity_blocks_all_lines_with_readable_original_cell |
| `test_reparse_order_merges_quantity_only_rows_with_pipeline` | No borrowing quantity-only rows from another source | unit/test_modernization_saved_materialization_identity.py::test_public_authored_continuations_only_resolve_identity_copies; contract/test_saved_identity_publication.py::test_missing_source_keeps_selected_saved_display_but_refuses_output |
| `test_reparse_order_does_not_merge_pipeline_rows_by_default` | No automatic pipeline-row merge from cached evidence | contract/test_saved_identity_publication.py::test_missing_source_keeps_selected_saved_display_but_refuses_output |
| `test_reparse_order_quantity_only_blank_structure_rows_no_longer_rejected` | Blank padding is not a zero or a new source row | unit/test_modernization_saved_materialization_identity.py::test_public_fully_blank_padding_does_not_create_lines_or_renumber_sources; test_public_zero_is_not_blank_padding_and_requires_its_own_identity |
| `test_reparse_order_rejects_when_llm_audit_fails` | Retired LLM-audit content rejection is not a current quantity gate | unit/test_ocr_quantity_content_editable_sheet.py::test_quantity_raw_content_and_score_do_not_require_review; unit/test_hakodate_review_resolution_root.py::test_real_producer_persists_editable_raw_and_validates_artifact_identity |
| `test_reparse_order_llm_path_uses_rescued_first_pass_payload_and_corrected_pdf` | Selected PDF identity, not fallback to a different provider PDF | unit/test_h2_operator_repair_revision.py::test_actual_grid_uses_only_original_pdf_page |
| `test_reparse_order_llm_path_marks_awaiting_output_when_fresh_first_pass_output_is_still_pending` | Current owned output wait remains active without false completion | integration/test_draft_sheet_service.py::test_rerun_ocr_evidence_only_marks_output_wait_as_running; test_rerun_ocr_evidence_only_async_trigger_marks_job_awaiting_output |
| `test_reparse_order_llm_prompt_includes_previous_saved_candidate_rows` | Retired full-table LLM prompt incorporating prior candidate rows | N/A: old prompt construction is excluded; current saved-value preservation remains covered by contract/test_saved_identity_publication.py::test_missing_source_keeps_selected_saved_display_but_refuses_output |
| `test_reparse_order_large_structural_projection_requires_manual_review` | Structural projection mismatch is not accepted on quantity confidence | unit/test_h2_operator_repair_revision.py::test_real_template_targets_to_assignment_and_owned_cache_gate; unit/test_modernization_saved_materialization_identity.py::test_public_invalid_identity_blocks_all_lines_with_readable_original_cell |
| `test_reparse_order_llm_assist_uses_current_sheet_full_table_anchor` | Retired full-table LLM prompt anchor selection | N/A: old prompt algorithm excluded; current physical target authority is covered by unit/test_h2_operator_repair_revision.py::test_real_template_targets_to_assignment_and_owned_cache_gate |
| `test_reparse_order_llm_assist_continues_when_optional_second_pass_audit_errors` | Retired optional second-pass auditor failure policy | N/A: old provider/auditor sequence excluded, not a current Hakodate completion contract |
| `test_reparse_order_llm_assist_blocks_when_current_sheet_baseline_missing` | Missing authoritative baseline blocks publication | contract/test_saved_identity_publication.py::test_missing_source_keeps_selected_saved_display_but_refuses_output |


## Scope

The current release target is the production Hakodate method. The user explicitly
excluded migration/compatibility work for retired OCR methods on 2026-10-13 and
authorized rewriting old implementation constraints and tests on 2026-10-19.
This is not permission to discard current quantity, source, ownership or saved
operator-state guarantees.

The 43 nodes below called the old full-table `reparse_order` provider path without
a workflow owner. Their OpenAI/Gemini/Yomitoku prompt, evaluator, second-pass,
repair and provider-fallback expectations are not current Hakodate contracts.
They are inventoried against develop `3dc31bdc`, not marked as passed or skipped.
The remaining parser/component tests are retained. Six old automatic-applied
revision tests are migrated to explicit selected-evidence/exact-save contracts
below; they do not restore automatic adoption of a pipeline archive.
No inert source archive or arbitrary HTTP-410 payload substitutes for them.

## Current Responsibilities

| Responsibility | Current executable coverage retained |
| --- | --- |
| Actual producer, original input lineage, terminal receipt, selection, projection and save | `unit/test_modernization_owned_ocr_workflow.py::test_native_producer_real_lineage_terminal_select_projection_and_save` |
| Literal quantities, zero/blank and operator editing rather than strict old LLM rejection | `unit/test_ocr_quantity_content_editable_sheet.py`; `unit/test_hakodate_review_resolution_root.py` |
| Explicit period, row/cell identity and no received-time or positional rescue | `unit/test_modernization_saved_materialization_identity.py::test_public_explicit_selected_period_resolves_month_and_year_boundary_not_received_time`; same module's invalid-identity and authored-continuation cases |
| Failed/foreign/superseded results cannot replace current evidence | `unit/test_modernization_owned_ocr_workflow.py::test_old_failed_workflow_attempt_cannot_overwrite_force_queued_successor`; foreign-order, document-retirement and tampered-snapshot cases in that module |
| Saved operator values survive missing or unselected sources and output refuses substitution | `contract/test_saved_identity_publication.py::test_missing_source_keeps_selected_saved_display_but_refuses_output`; `test_saved_identity_does_not_authorize_unselected_or_modified_records` |
| Full geometry, expected cells, owned artifact proof and refusal before cache publication | `unit/test_h2_operator_repair_revision.py::test_current_repair_actual_cache_write_gate`; `test_real_template_targets_to_assignment_and_owned_cache_gate`; history repair-source refusal regressions |
| Auxiliary/display values, canonical revision immutability and quantity parser components | The retained parser cases and six selected/exact-revision cases in `integration/test_ocr_pipeline.py` |
| An unowned legacy reparse cannot execute or mutate an explicitly registered source | New `test_registered_original_cannot_start_unowned_legacy_reparse`, using actual registration and full SQL/source snapshot comparisons |

These references identify coverage, not execution success. Consolidated local
execution is still pending. Real recognition accuracy, live order proof and
same-input production parity are separate requirements.

## Revision Contract Migration

The former `_publish_applied_reparse_revision` tests assumed that a provider
result automatically became an adopted/cache-selected sheet. The current
function publishes an owned `pipeline_result`/`awaiting_selection` archive;
operator selection and saving remain separate. The six replacement cases use
`HistoryCase` with an explicit February 1–7 source and the authored FAC00004
layout, real queue/claim/producer/terminal receipt/HTTP selection, then the real
`save_ocr_sheet_exact` synchronous ownership fence. Only external recognition and
rendering are doubles. This is not live FAX or recognition-accuracy proof.

| Former responsibility | Current test |
| --- | --- |
| Stale rejected candidate cannot replace quantity 70 / auxiliary display | `test_selected_exact_revision_supersedes_stale_reject_candidate_and_preserves_aux_display` |
| Repeating the same content preserves values and source | `test_selected_exact_revision_same_sheet_resave_preserves_values_and_evidence` (explicit operator saves create distinct revisions, not fictitious provider idempotence) |
| Latest save wins without replacing original provider output | `test_selected_exact_revision_refreshes_latest_without_replacing_raw_source` |
| Unselected cached latest cannot become canonical | `test_selected_exact_revision_overrides_unselected_cached_latest_without_writing` |
| Already matching latest requires no refresh | `test_selected_exact_revision_matching_payload_needs_no_refresh` |
| Expanded rows and auxiliary fields remain immutable | `test_selected_exact_revision_keeps_expanded_rows_and_auxiliary_values_immutable` (70/3/6, three row IDs, auxiliary totals) |

Unused helpers that fabricated an unowned evidence record were removed after
confirming they had no callers. No production ownership check is weakened.
These replacements are implemented but not yet executed.

## Retired Provider Nodes

| Node | Original call |
| --- | --- |
| `test_reparse_order_records_explicit_failed_llm_reparse_when_no_lines_survive` | `order_service.reparse_order(order['id'], ocr_provider='gemini', llm_assist=False)` |
| `test_reparse_order_blocks_llm_reparse_without_first_pass_context` | `order_service.reparse_order(order['id'], ocr_provider='gemini', llm_assist=True)` |
| `test_ingest_to_reparse_flow` | `order_service.reparse_order(order['id'])` |
| `test_reparse_order_openai_enforces_strict_quantity_rules` | `order_service.reparse_order(order['id'], ocr_provider='openai')` |
| `test_reparse_order_sets_job_metrics_changed_even_when_counts_match` | `order_service.reparse_order(order['id'], ocr_provider='openai')` |
| `test_reparse_order_llm_full_table_truncated_blocks_without_pipeline_rescue` | `order_service.reparse_order(order['id'], ocr_provider='gemini', llm_assist=True)` |
| `test_reparse_order_llm_full_table_truncated_empty_rows_blocks_without_pipeline_rescue` | `order_service.reparse_order(order['id'], ocr_provider='gemini', llm_assist=True)` |
| `test_reparse_order_quantity_only_rows_merge_without_provider_flag` | `order_service.reparse_order(order['id'], ocr_provider='gemini', llm_assist=True)` |
| `test_reparse_order_resolves_week_from_ocr_payload_when_existing_week_has_no_menu` | `order_service.reparse_order(order['id'], ocr_provider='gemini', llm_assist=True)` |
| `test_reparse_order_does_not_apply_legacy_position_mapping_on_single_date_anchor` | `order_service.reparse_order(order['id'], ocr_provider='pipeline')` |
| `test_reparse_order_rejects_date_anchor_drift_when_llm_quantity_only` | `order_service.reparse_order(order['id'], ocr_provider='gemini', llm_assist=True)` |
| `test_reparse_order_realigns_rotated_blank_anchor_rows_before_line_parsing` | `order_service.reparse_order(order['id'], ocr_provider='gemini', llm_assist=True)` |
| `test_reparse_order_auto_falls_back_to_llm_when_pipeline_lines_empty` | `order_service.reparse_order(order['id'])` |
| `test_reparse_order_llm_assist_ignores_cached_order_rows_when_first_pass_rows_missing` | `order_service.reparse_order(order['id'], ocr_provider='gemini', llm_assist=True)` |
| `test_reparse_order_llm_second_pass_uses_evaluator_feedback` | `order_service.reparse_order(order['id'], ocr_provider='gemini', llm_assist=True)` |
| `test_reparse_order_llm_final_audit_failure_retries_with_evaluator_feedback` | `order_service.reparse_order(order['id'], ocr_provider='gemini', llm_assist=True)` |
| `test_reparse_order_with_llm_assist_does_not_auto_select_provider` | `order_service.reparse_order(order['id'], llm_assist=True)` |
| `test_reparse_order_llm_assist_rejects_sparse_quantity_only_rows_when_full_table_anchor_required` | `order_service.reparse_order(order['id'], ocr_provider='gemini', llm_assist=True)` |
| `test_reparse_order_llm_assist_blocks_provider_fallback_pipeline` | `order_service.reparse_order(order['id'], ocr_provider='gemini', llm_assist=True)` |
| `test_reparse_order_with_yomitoku_rows_runs_evaluator_before_llm_inference` | `order_service.reparse_order(order['id'], ocr_provider='gemini', llm_assist=True)` |
| `test_reparse_order_with_yomitoku_rows_runs_second_pass_after_llm_candidate_audit` | `order_service.reparse_order(order['id'], ocr_provider='gemini', llm_assist=True)` |
| `test_reparse_order_without_yomitoku_rows_runs_infer_then_evaluator_then_second_inference` | `order_service.reparse_order(order['id'], ocr_provider='gemini', llm_assist=True)` |
| `test_reparse_order_row_coverage_uses_payload_scoped_expected_rows` | `order_service.reparse_order(order['id'], ocr_provider='gemini', llm_assist=True)` |
| `test_reparse_order_row_coverage_prefers_pipeline_rows_when_anchor_dates_are_weak` | `order_service.reparse_order(order['id'], ocr_provider='gemini', llm_assist=True)` |
| `test_reparse_order_row_coverage_prefers_existing_line_anchor_scope` | `order_service.reparse_order(order['id'], ocr_provider='gemini', llm_assist=True)` |
| `test_reparse_order_applies_repair_pass_on_row_coverage_shortfall` | `order_service.reparse_order(order['id'], ocr_provider='gemini', llm_assist=True)` |
| `test_reparse_order_fails_when_row_coverage_low_even_after_repair` | `order_service.reparse_order(order['id'], ocr_provider='gemini', llm_assist=True)` |
| `test_reparse_order_rejects_mirrored_quantity_columns_without_reference_rows` | `order_service.reparse_order(order['id'], ocr_provider='gemini', llm_assist=True)` |
| `test_reparse_order_rejects_canonical_mismatch_before_save` | `order_service.reparse_order(order['id'], ocr_provider='gemini', ocr_prompt='strict quantity only')` |
| `test_reparse_order_keeps_existing_lines_when_validation_rejected` | `order_service.reparse_order(order['id'], ocr_provider='gemini')` |
| `test_reparse_order_rejects_when_llm_cost_hard_limit_exceeded` | `order_service.reparse_order(order['id'], ocr_provider='gemini')` |
| `test_reparse_order_rejects_missing_numeric_source_row` | `order_service.reparse_order(order['id'], ocr_provider='gemini')` |
| `test_reparse_order_merges_quantity_only_rows_with_pipeline` | `order_service.reparse_order(order['id'], ocr_provider='gemini', llm_assist=True)` |
| `test_reparse_order_does_not_merge_pipeline_rows_by_default` | `order_service.reparse_order(order['id'], ocr_provider='gemini', llm_assist=True)` |
| `test_reparse_order_quantity_only_blank_structure_rows_no_longer_rejected` | `order_service.reparse_order(order['id'], ocr_provider='gemini', llm_assist=True)` |
| `test_reparse_order_rejects_when_llm_audit_fails` | `order_service.reparse_order(order['id'], ocr_provider='gemini', llm_assist=True)` |
| `test_reparse_order_llm_path_uses_rescued_first_pass_payload_and_corrected_pdf` | `order_service.reparse_order(order['id'], ocr_provider='gemini', llm_assist=True)` |
| `test_reparse_order_llm_path_marks_awaiting_output_when_fresh_first_pass_output_is_still_pending` | `order_service.reparse_order(order['id'], ocr_provider='gemini', llm_assist=True)` |
| `test_reparse_order_llm_prompt_includes_previous_saved_candidate_rows` | `order_service.reparse_order(order['id'], ocr_provider='gemini', llm_assist=True)` |
| `test_reparse_order_large_structural_projection_requires_manual_review` | `order_service.reparse_order(order['id'], ocr_provider='gemini', llm_assist=True)` |
| `test_reparse_order_llm_assist_uses_current_sheet_full_table_anchor` | `order_service.reparse_order(order['id'], ocr_provider='gemini', llm_assist=True)` |
| `test_reparse_order_llm_assist_continues_when_optional_second_pass_audit_errors` | `order_service.reparse_order(order['id'], ocr_provider='gemini', llm_assist=True)` |
| `test_reparse_order_llm_assist_blocks_when_current_sheet_baseline_missing` | `order_service.reparse_order(order['id'], ocr_provider='gemini', llm_assist=True)` |
