# Authored Component Migration

These are local component contracts, not recognition accuracy or live FAX proof.
No new test run has been performed during this implementation batch.

`unit/test_daily_override_projection.py` now declares the saved facility in every
pure candidate. Added two foreign-caller cases (foreign override only / owned
and foreign overrides) require the accepted configured facility to win, retain
raw copies and keep DB/candidate immutable. Ten missing/invalid facility-receipt
shapes must fail closed when overrides are enabled; disabled overrides still
return an independent original-line copy. These minimal projection inputs are explicitly
not acceptance or execution proof. Parent exclusively owns the production fix;
no test has been executed here.
Added the actual50g embedded / current60g / restored40g regression with SQL
event observation proving zero queries during the raw projection and immutable
embedded candidate. Six producer-captured facility-ID defects must reject before
SQL. Four incomplete original-field receipts (`present=True` without `value`)
must fail before facility/date early returns and raw projection; eight explicit
None-versus-absent cases check actual field presence, preserved None, removed
override metadata and zero queries. This is restoration metadata validation,
not fabrication of recognition or accepted execution receipts.

Native-selection integration dependency: `selected_bagging_order` now imports
the parent-owned `tests.helpers.native_selection.select_authored_native_result`
after actual registration/context confirmation. It passes the exact authored
raw rows, copied canonical column names/source indexes/roles, explicit
`unlisted_cells=""` and `artifact_root=tmp_path / "native-artifacts"`;
generic `authored_pipeline_provider`/delivery/select is
removed from this helper. The returned actual terminal receipt must be done
and name that evidence. Typed sheet values and downstream saves remain intact.
The shared module is deliberately not created/copied/edited in this worktree;
the parent must integrate it before batch execution. Until that integration
and actual verification, bagging/output/perf and materialization positives are
not claimed to have valid native lineage or to pass.
`materializer_acceptance.accepted_materializer` has the same native-selection
connection and artifact-root argument; its original typed June sheet/save and
bagging remain intact. Artifact/environment isolation belongs to the shared
helper, not to a new remote-output fallback here.
Parent resolved the shared-helper identity review: both raw input and physical
target keys now use the existing `_normalize_daypart_key`, accepting only the
three explicit morning/noon/evening identities and refusing other values.
Raw rows, target plan, required cells and quantities remain unchanged. This is
an integration dependency, not an executed verification result or permission
for this worker to modify the parent-owned module.
One remaining source-reviewed helper identity issue is the menu key: raw rows
currently use `str(...).strip()` while canonical physical targets use
`hakodate_assignment_service._normalize_slot_text` (NFKC/strip). Halfwidth
specimens such as `胡瓜のﾌﾚﾝﾁｻﾗﾀﾞ` therefore differ. Parent-owned helper must
align identity keys with the existing function without changing raw rows,
quantities, required cells or target coverage; the worker does not alter it.

`integration/test_ocr_sheet_history.py` and `integration/test_ocr_pipeline.py` are exclusively parent-owned in
`assistant-business-workflows-20261023`. This worker's copy was unchanged at
handoff and must not be edited here. The additive
`authored_layout_registration(message_id=..., facility_id=..., received_at=...,
week=..., effective_date=..., lines=...)` interface remains unchanged.
The parent also exclusively owns `test_h2_operator_repair_revision.py`,
`test_order_workflow_revision_contract.py` and `test_rerun_atomic_receipt.py`
for removal of the obsolete cache-preservation argument. Their worker diffs
were zero at handoff. This worker does not alter them or the shared native
selection module. Cache writer/production changes remain parent-owned.

Current worker migrations in `test_workflow_state_service.py`:
`test_persist_current_state_upserts_existing_order_row` now publishes two actual
saved drafts with real selected evidence and input receipts, preserving the
second-snapshot/source assertions and checking exactly one SQL row.
`test_get_current_sheet_context_rebuilds_when_persisted_current_state_evidence_changes`
corrupts only JSON `base_evidence_run_id`, not the physical evidence FK or canonical evidence service;
it checks readonly rebuild, retained physical stale record and actual selected
draft/quantities. `test_project_workflow_state_does_not_sync_critical_decisions_on_read`
uses actual registration with explicit March 22–28 period for its existing
projection-only assertions. The remaining registration seeds now require the
real registration helper, explicit facility, dated period and effective date;
no dummy PDF/month-only direct create remains in this module. This is only
registration preparation: old evidence/job/projection preparations are still
pending, not declared valid by that change.

The former facility-choice workflow node now maps to
`test_unselected_facility_candidates_cannot_replace_explicit_owned_selection`:
the original FAC00001/.81 and FAC00002/.76 candidates accompany an actual
source with no explicit facility selection. Real ingest rejects that payload
before writes; full DB/source readonly auditing retains the independently
selected/saved quantity 5 and actual facility. OCR score selection is not a
permitted replacement for explicit intake selection.

The two old new-evidence/apply-readiness nodes now prepare actual native 5,
formal selected save, second owned terminal result 8 and readonly diagnostic
workflow projection (`test_project_workflow_state_returns_new_evidence_available_when_latest_evidence_differs_from_draft`
and `test_project_workflow_state_new_evidence_available_retains_apply_readiness_for_clean_draft`).
They retain the gate/revision/candidate assertions and add actual preview 8 plus
unchanged selected saved 5. The real job now supplies the actual new-candidate
metric, so the diagnostic action is `new_evidence_available` /
`select_candidate_evidence`, not the former fake-job-free `apply_ready` label;
the original saved draft's `can_apply` assertion remains true. Diagnostic projection
must not overwrite the workflow-v2 execution row.

| Former workflow node | Current real-owner node |
| --- | --- |
| `test_refresh_workflow_state_returns_rerun_in_progress_when_ocr_rerun_running` | `test_owned_running_rerun_retains_selected_saved_five`: real producer/selected/save 5, second queue/claim and owned running progress; recognizer not yet called, current workflow-v2 `ocr_running`, readonly diagnostic wait action, unchanged saved 5/job. |
| `test_refresh_workflow_state_returns_rerun_failed_keep_current_when_latest_rerun_failed` | `test_owned_failed_rerun_retains_selected_saved_five`: real current 5 and actual second owned execution; external recognizer raises, actual failure terminal receipt has no new evidence, current `ocr_failed` and prior selected/saved 5 remain. No fabricated job status or partial success. |
| `test_refresh_workflow_state_ignores_stale_rerun_candidate_when_latest_is_already_active` | `test_owned_selected_eight_is_not_replaced_by_stale_candidate_five`: two actual native results 5/8, explicit selection/formal save 8. Only stale diagnostic `metrics.evidence_run_id=older` is injected into SQL; physical owner and actual terminal receipt remain identical. Actual readonly projection keeps active 8/no candidate, saved source/row unchanged. |

| Former read-time reconcile node | Exact case / current positive |
| --- | --- |
| `test_get_order_workflow_state_reconciles_completed_rerun_output_when_current_reference_is_still_pending` | `test_unowned_completed_file_cannot_finish_owned_rerun[awaiting-newer-nine]`: original complete quantity-9 JSON, template/grid/subgrid/pages and URI fields are written as an actual local unowned neighbor. Real queue/claim/awaiting owner points at the original running pending JSON. Actual reconcile refuses, current workflow remains running, actual selected/saved 5/job/files are unchanged, no new terminal receipt. |
| `test_get_order_workflow_state_keeps_rerun_awaiting_output_when_only_older_completed_output_exists` | Same node `[awaiting-older-eight]`: exact quantity-8 JSON and old URI/timestamped basename, newer pending basename and old awaiting timestamp inputs retained. Same real pending-owner and complete readonly no-adoption checks. No mocked output discovery or menu context. |
| `test_get_order_workflow_state_reconciles_completed_rerun_output` | Same node `[running-current-nine]`: original complete quantity-9 JSON is the actual owned job's untrusted output reference. A done file is not a terminal receipt: read-time reconcile stays false, running owner/selected saved 5 remain unchanged. |
| These former nodes' legitimate completion/new evidence 9 responsibility | `test_owned_native_nine_requires_real_completion_selection_and_save`: actual first source 5, actual second native producer 9, done terminal receipt, explicit selection and formal save/reread. The new evidence ID differs, current job is really done; no old `ocr-rerun-reconcile` source or GCS neighbor is forged as accepted evidence. |

The two remaining former failed-healing nodes map to the explicit
`failed-pending` and `failed-timeout-nine` cases in
`test_unowned_failed_jobs_cannot_heal_or_replace_owned_saved_five`.
Original failure strings, request/result metrics, pending JSON and complete
quantity-9 timeout JSON (including template/grid/subgrid/URI fields) remain
real diagnostic job/file inputs. Those jobs deliberately have no attempt or
terminal receipt; they use distinct diagnostic IDs rather than overwriting
the actual native owner. Each readonly reconcile remains false, each
diagnostic job stays failed, every input file stays identical, and the actual
owner/selected saved 5 stays unchanged. No external storage is contacted.
The current native failure responsibility is independently exercised by
`test_owned_failed_rerun_retains_selected_saved_five`; actual process-crash
behavior is in `test_owned_rerun_callback_propagates_system_exit_without_claiming_success`.
The old `SystemExit(1)` diagnostic string alone is not presented as proof of an
actual process crash. Actual successful completion 9 is covered by the native
completion/selection/save node above, not by read-time healing of these files.

## Draft Responsibility Mapping

These are source-reviewed implementation mappings, not executed results.

| Former bootstrap node | Exact case in `test_unselected_legacy_bootstrap_inputs_never_create_successful_sheet` |
| --- | --- |
| `test_build_initial_sheet_draft_from_latest_evidence_run` | `refuse_unselected_legacy_evidence_payload` |
| `test_build_initial_sheet_draft_refuses_legacy_cache_revision_as_current_source` | `refuse_unselected_edited_cache_revision` |
| `test_order_service_build_initial_sheet_draft_prefers_semantic_sheet` | `refuse_semantic_bootstrap_without_selected_source` |
| `test_order_service_build_initial_sheet_draft_keeps_semantic_sheet_when_only_evidence_recovery_warning_remains` | `refuse_warning_only_bootstrap_without_selected_source` |
| `test_build_initial_sheet_draft_uses_recoverable_semantic_payload` | `refuse_recoverable_bootstrap_without_selected_source` |
| `test_build_initial_sheet_draft_keeps_semantic_shell_when_weekly_menu_warning_remains` | `refuse_weekly_warning_bootstrap_without_selected_source` |
| `test_build_initial_sheet_draft_keeps_blocked_empty_semantic_shell` | `refuse_empty_shell_bootstrap_without_selected_source` |
| `test_build_initial_sheet_draft_keeps_blocked_shell_when_menu_missing_even_if_structured_rows_exist` | `refuse_structured_payload_bootstrap_without_selected_source` |
| `test_get_current_sheet_context_keeps_blocked_shell_when_menu_missing_even_if_structured_rows_exist` | `refuse_current_structured_payload_without_selected_source` |
| `test_get_current_sheet_context_keeps_blocked_shell_for_wrapped_raw_output_when_menu_missing` | `refuse_current_wrapped_payload_without_selected_source` |

Each table case uses its original cache/structured/wrapped input (or the
original absent payload), calls the actual draft/order/current builder and
checks no selected evidence, no saved draft, no writes and unchanged source.
It specifically tests missing selected authority, not arbitrary HTTP 410.
Missing monthly-object diagnostics remain checked for each case.
Valid producer quantity 5 is separately exercised by
`test_initial_draft_uses_actual_selected_native_evidence_five`.
Actual transient quantity 6 and physical menu loss are exercised by
`test_owned_transient_source_six_and_actual_menu_loss_are_not_fallback_success`:
exact source/schema/period before loss, no payload/rows after loss, actual
artifact-validation refusal and missing-entry diagnostics, unchanged evidence
and no fabricated persisted snapshot.

The two old legacy table-overlay nodes map to
`test_unowned_legacy_table_cannot_overlay_owned_saved_quantities`, case IDs
`payload-position-metadata` and `unowned-candidate-table`. Original 9/8 raw
tables and position-decision metadata remain actual untrusted cache input;
the actual selected blank cells and saved ID stay unchanged. Their current
positive is `test_owned_projection_and_formal_save_keep_nine_and_eight_in_exact_menu_cells`:
actual native 9/8, exact menu/cell neighbors, formal save and reread.
The extra OCR-menu candidate maps to
`test_current_owned_projection_does_not_append_unowned_ocr_menu_candidates`:
original extra dated OCR candidate is in the raw input, not a mocked mapper;
actual selected Menu A/4 remains the only row and cache/source stay unchanged.
The canonical-week node retains its original exact saved quantity 5, stale
artifact week, schema and row IDs using actual source/selection/save instead
of a private helper from the parent-owned history module.

`selected_bagging_order` now rejects duplicate quantity-cell identities and
mixed dates before preparing sources or DB records; the direct helper refusal
cases in `test_owned_output_scope.py` retain distinct 9/8 inputs and compare DB
and files unchanged. No dictionary collapse may silently choose one quantity.

Further status responsibility mappings (static, not executed):

| Former node / responsibility | Current concrete node / retained input and assertions |
| --- | --- |
| `test_get_order_does_not_mark_reparse_job_done_while_running` | Same node, actual queue/claim and owner-fenced running progress/output-reference update. Original done JSON with `\|a\|b\|` remains the referenced file; historical `ocr_reparse` is metadata only. Authenticated readonly detail stays running, exact job/file/source unchanged, no terminal receipt or evidence is fabricated. |
| `test_get_order_surfaces_awaiting_output_llm_reparse_as_active` | Same node, actual queue/claim and owned progress; same pending output reference, request-mode/provider metadata and quantity 5. Detail plus `workflow-v2.ocr_job` stay awaiting; GET leaves the real job unchanged. Historical `llm_reparse` is display metadata only; no old recognizer is run or reimplemented. |
| `test_get_order_surfaces_awaiting_output_ocr_rerun_as_active` | Same node; same pending reference and quantity 5, real owner and current `ocr_running` workflow state instead of retired `rerun_in_progress`; exact request mode retained in actual job metrics. |
| `test_get_order_prefers_live_metrics_error_for_active_ocr_rerun` | Same timeout text and `ocr_output_pending` metrics input, actual owner; detail keeps pending status and authoritative pending error, job unchanged. |
| `test_get_order_reconciles_ocr_rerun_when_pending_reference_has_newer_completed_output` / unowned neighbor discovery | `test_get_order_does_not_promote_unowned_completed_neighbor_output[newer]` retains the original complete quantity-8 neighbor payload (template resolution, grid, subgrid and URI), original quantity-5 cache/table and running pending file. Real awaiting owner, local neighbor file, readonly detail/current workflow, identical job/cache/files, no evidence/terminal receipt and retained order quantity 5. No mocked discovery/projection or arbitrary HTTP refusal. |
| `test_get_order_keeps_ocr_rerun_active_when_only_older_completed_output_exists` | Same refusal node `[older]` retains its separate original old-PDF URI, timestamped older output reference/payload, newer pending basename and original `awaiting_output_since`. Both detail and current workflow remain awaiting/active with exact job/cache/source equality; the old quantity-8 table never replaces quantity 5. |
| Same former node / valid completion, quantity 8 and saved reread | `test_owned_completed_rerun_publishes_eight_without_neighbor_discovery`: same February 15/Menu A/quantity-5 initial order; real native producer returns 8, actual owned terminal receipt and explicit selection; detail/current workflow done, no error, actual save/reread equality. The unrelated remote neighbor is never treated as the accepted source. |

`HistoryCase.queue` reuses the same original PDF/document/upload on subsequent
owned executions and asserts the persisted URI/digest. It does not re-INSERT
fixed document IDs or regenerate the source; the actual queue and lease are
still acquired each time. The owned 3→9 rerun case additionally retains exact
PDF bytes and document/upload/version tables across the rerun.
Context is confirmed only before the first queue. Re-confirming it before a
rerun would invoke the real context reset/delete path even for the same
selection. Subsequent queues instead assert facility/period/template-version
equality against the existing workflow metadata and original document/upload;
they must not erase selected evidence or saved operator work as preparation.

The perf 100-facility preparation now retains each actual generated PDF before
order execution. `prepare_only` returns an authored-original preparation
descriptor, not an execution/selection receipt. The explicit `prepared_original`
input checks facility/period/full line values/quantity columns/canonical facility
definition, fixture-owned path and actual file digest before registering that
same original. It never regenerates the PDF in the measured order phase.
Existing daily variants do not opt into reuse: their distinct quantity/period
originals remain explicitly generated. Perf stays opt-in/skipped normally; no
performance success is claimed. `test_prepared_original_cannot_be_rebound_or_relabelled`
adds seven explicit refusal cases (facility, period, quantity, columns,
configuration, digest and outside-owned-root), comparing DB and every local
source byte after rejection. Neither the helper nor these tests have been executed here.

The three existing draft candidate nodes now share
`_saved_three_and_owned_candidate`: actual producer/selection/save at 3, a second
owned queue/claim/terminal result at 8, and unchanged original saved record.
`test_switch_draft_to_latest_evidence_explicitly_adopts_new_candidate` uses the
current explicit OCR-result selection and formal sheet save, then actual service
reread. The preview and meaningful-diff nodes retain all original 8/3, evidence-ID
and saved-revision assertions, with full readonly DB/source checks instead of
mocked projection or workflow state. No direct synthetic evidence persistence is
used for these positives. `prepare_rerun` checks that queue preparation leaves
the prior evidence record identical (or absent if it was absent); it must not
incorrectly require no evidence after an actual previous run.

`test_done_ocr_rerun_heals_stale_pending_error_across_detail_surfaces` maps to
`test_done_owned_rerun_keeps_stale_pending_diagnostics_readonly`: real completed
8 and its unmodified actual terminal receipt, original stale pending/timeout/
recovery metadata and quantity-8 cache/output input, identical job/cache/files
after current detail/workflow/source reads. The stale evidence-ID metric is
deliberate diagnostic corruption, not a generated evidence record or adopted
source. GET self-healing assertions are retired; successful clean completion and
saved reread remain in `test_owned_completed_rerun_publishes_eight_without_neighbor_discovery`.

## Saved Positive Preparation Audit

Redesign materialization preparation (static only):

| Former node | Current node / exact retained responsibility |
| --- | --- |
| `test_apply_latest_draft_does_not_run_legacy_position_mapping_for_semantic_sheet` | `test_owned_confirm_does_not_run_legacy_position_mapping_for_semantic_sheet`: actual selected FAC00014 source 102/2/2/1, original fields/blank neighbors/remark and row ID formally saved, actual bagging and final-confirm, exact date/meal/menu/quantity 102 reread. No protective legacy-mapper mock. |
| `test_confirm_materialization_rejects_semantic_draft_fallback` | `test_owned_bagging_rejects_empty_semantic_materialization_without_fallback`: actual FAC00004 source 65/45/2 and original saved schema/remark. Original empty materializer fault remains a fault injection, never OCR evidence; actual current bagging returns `draft_lines_empty`, not the removed semantic-fallback code. Every DB table and source file stays unchanged. |
| `test_apply_latest_draft_blocks_when_sheet_quantities_diverge_for_fac00004` | `test_owned_bagging_blocks_when_sheet_quantities_diverge_for_fac00004`: same source 65/45/2, original saved cells and original materializer fault changing regular to 2. Actual current command refuses `draft_materialization_mismatch`; complete DB/source equality. |
| `test_confirm_order_blocks_when_sheet_quantities_diverge_for_fac00014` | `test_owned_bagging_blocks_when_sheet_quantities_diverge_for_fac00014`: same source 102/2/2/1, original saved fields/blank cells and original regular-to-2 materializer fault. Same actual mismatch rejection and DB/source equality. Final-confirm positive quantities remain in the same-named multi-quantity positive node. |

The faulting builder is only the original negative component injection. Real
producer, terminal ownership, selection, saved-sheet receipt and rejection
command are not mocked. These nodes do not claim provider accuracy or execution.

Implementation/source review only; none of these migrations has been executed.

| Former draft node | Actual source/save coverage |
| --- | --- |
| `test_get_latest_sheet_draft_upgrades_generic_cols_from_semantic_sheet` | `test_selected_generic_saved_values_are_not_replaced_by_semantic_refresh[retain_generic_over_semantic_reprojection]`: actual semantic source 3 and fields; exact generic operator 5/header/row ID formally saved and retained. Automatic replacement/emptying of selected operator work is retired. |
| `test_get_latest_sheet_draft_upgrades_generic_cols_from_recoverable_semantic_sheet` | Same node `[retain_generic_over_recoverable_reprojection]` preserves its distinct 常食 header, generic columns and 5; semantic assertions are on the actual producer. |
| `test_get_current_sheet_context_projects_blocked_empty_semantic_shell_over_generic_saved_draft` | Same node `[retain_generic_when_physical_menu_missing]`: remove physical menu entries after actual source/selection/save. Missing-menu refusal remains; saved 5/header/row IDs remain visible, not erased. |
| `test_get_current_sheet_context_rebases_stale_saved_draft_to_authoritative_aux_schema` | `test_owned_aux_source_does_not_overwrite_selected_operator_schema_or_values[retain_saved_schema_without_aux]`: actual current FAC00004 source with category/66, original saved 11-field schema and 9/4; actual receipt publication and readonly/rebase preservation. Legacy total target 67 is rejected by the real role boundary, not treated as recognized current data. |
| `test_get_current_sheet_context_rebases_stale_aux_values_even_when_schema_already_matches` | Same node `[retain_saved_aux_blanks_with_matching_schema]`: original saved 13-field schema, total-header/blank aux and 9/4 retained. Current source 66 and retired total-target 67 refusal are separate. Automatic rebase/overwrite is retired. |
| Two `...applied_reparse_revision...without_erasing_aux_display` nodes | `test_get_ocr_sheet_preserves_formally_saved_quantity_and_aux_display` and `test_get_current_sheet_context_preserves_formally_saved_quantity_and_aux_display`: actual current source with same one/two 菜名, 主/副① and blank regular; original 13-field revision rows/flags are diagnostic history under captured actual input. Formal operator saves explicitly author both total-display 70 and quantity 70, preserving all original final aux/quantity/row assertions. Total 70 is not presented as native recognized output; its legacy target role is refused. Source reread remains original. |
| `test_get_ocr_sheet_ignores_non_authoritative_reparse_candidate_revision` | Same candidate 99/13-field legacy revision retained as actual diagnostic history; current native source keeps blank regular and never adopts 99. The unsupported old total-70 target is actually refused; current total-display 70 positive is explicitly operator-authored in the two saved-sheet nodes, not forged OCR evidence. |
| `test_get_current_sheet_context_does_not_rebase_auto_apply_blocked_draft_on_read` | Same 5/23, Ghost Menu/date/quote, row IDs and warnings. Actual producer; scoped historical blocked record and formal selected save. Original rows remain on read. Quote→朝 inference is retired; original blocker/warning/state assertions remain on the historical record. |
| Non-authoritative auto-blocked / active-evidence nodes | Actual producer plus scoped unselected draft write. Empty diagnostic remains unselected; selected source 3 remains current. Actual candidate 9 becomes active only after explicit selection. |

FAC00004 values are authored component inputs, not real FAX recognition proof.
Each physical menu category/name/date/meal is checked against its declared row.
Physical row/field chooses the external recognizer test value. The production
normalizer only asserts captured menu identity; it never rewrites source,
output or expected values. No fake evidence or terminal receipt is injected.

Concrete retirement evidence: `hakodate_ocr_evidence_service.authored_cell_role`
only permits quantity/note targets. The actual FAC00004 repository template has
no post-menu total column. Therefore an `aux.col_4` total is not added to a
current recognizer fixture. `_legacy_aux_target_refused` invokes that actual
boundary with original field/role/column/67-or-70 value and full readonly
DB/source audit. Pre-menu category is declared physical menu data, not a guessed
OCR target. This distinction must remain explicit in review.

## Review Correction And Responsibility Ledger

The rejected whole-file `retired_specimen/input_literals` conversion was removed.
Its helper, manifests and source archives were deleted after restoring the test
bodies. An arbitrary request returning 410 is not a replacement for quantity,
cell identity, source, period, persistence, reload, output or failure assertions.
The following ledger is implementation status, not a passing-test report.

| Original module | Current Hakodate / owned responsibility retained | Exact node and implementation status | Retired responsibility / concrete boundary |
| --- | --- | --- | --- |
| `test_ocr_pipeline.py` | Numeric values/zero/blank, identity/date anchors, immutable remarks/aux, rejected write preservation, revision history | Parent exclusively owns migration in separate MCP WT; worker diff zero at handoff. No writes here. Pure quantity/source assertions must not become arbitrary HTTP refusal. | Old provider/prompt/repair compatibility is outside current OCR scope; only actual obsolete entrypoints may become refusal tests. No whole-file retirement. |
| `test_ocr_sheet_history.py` | Saved fields/header/rows/row_ids, manual values, reload/cache loss, exact source-cell provenance and Hakodate target truth | Parent-owned migration in separate MCP WT; no edits here. Parent reports registration plus producer/terminal/selection/save preparation static OK and original 869 assertions retained. Not yet integrated or executed by this worker. | Position/default/other-order borrowing must be rejected with the original table/cell input; no unrelated payload substitution. |
| `test_ocr_redesign_phase_support.py` | Latest saved 7→8, confirmed 9, multi-column 65/45/2 and 102/2/2/1, cell/aux identity, failed materialization preservation | Same-named latest-draft, confirmed-snapshot and two multi-quantity nodes now use actual selected source/save/bagging/confirm. Original assertions remain. Three registration helpers use explicit periods and actual registration. Remaining projection/failure nodes still require migration. | `test_legacy_position_mapping_wrapper_is_not_executable` directly exercises the removed mapper with its original dated quantity-102 input. Other fallback cases are not declared migrated. |
| `test_workflow_state_service.py` | Readonly state/reload, selected draft/evidence lineage, owner transitions, terminal failures and no mutation on GET | Registration seeds now explicit source/facility/period/owner. Actual 5→8 candidates, active 8 versus stale 5, running/failed keep-5 and real 9 completion/save are mapped above. The five old read-time reconcile inputs now retain their actual diagnostic files/failed jobs with owner-safe no-adoption checks. Candidate acknowledgement, legacy layout/numeric projection and remaining direct evidence/save preparations still need migration. | Read-time healing is retired, not restored. Unowned failed diagnostic jobs are intentional negative SQL specimens, never accepted evidence or successful worker receipts. |
| `test_draft_sheet_service.py` | Saved user quantities/remarks/row IDs, actual rerun, no automatic adoption, rejected diagnostic write boundaries | Original assertions retained except specifically replaced old unowned rerun paths. `test_owned_rerun_keeps_saved_quantities_and_requires_explicit_candidate_adoption` checks actual receipt, retained 3, candidate 9, old saved record and line equality; failed/empty rerun nodes retain real owned execution. Other old projection preparation remains pending. | Missing-job rerun is directly refused in `test_unowned_rerun_cannot_publish_partial_pipeline_output`; this does not replace the owned rerun positive. |
| `test_ocr_sheet_corpus_regression.py` | Actual stored PDF/JSON identity and labeled quantities must remain available; no synthetic PDF may stand in as corpus accuracy proof | Original corpus bodies restored. Need separate authored component responsibilities from old Yomitoku/LLM compatibility and verify any actual corpus facility/period/source before ingestion. Not marked migrated or successful. | Old row-index copy/provider compatibility is out of scope, but corpus quantity/identity assertions cannot simply be dropped. |
| `test_orders_ocr_status_api.py` | Actual job status, list/detail parity, real callback/slot/terminal receipt, accepted output quantities | Original bodies restored. `test_owned_running_job_is_not_completed_by_unowned_cache_success` preserves original cache success/dummy/old-provider input and quantity-5 order, uses a real queued/claimed owner and progress write, checks detail/list running plus unchanged job/cache. Remaining unowned job/debug preparation pending. | Only the 14 exact retired HTTP requests below use 410; current status/output positives are not part of that retirement. |

Existing modern nodes are supporting coverage, not substitutes for unported
assertions: `test_modernization_owned_ocr_workflow.test_native_producer_real_lineage_terminal_select_projection_and_save`
checks persisted source/execution/template lineage, every source cell, terminal
receipt, explicit selection and saved equality; `test_saved_sheet_quantity_schema.test_complete_schema_preserves_typed_operator_values_and_real_candidate`
checks typed user values, neighboring columns, remarks, candidate amounts and
unchanged source; `test_saved_identity_publication.test_selected_saved_identity_survives_source_label_and_empty_rows`
checks identical fields/header/rows/row_ids through initial/current/recovered
reads and publication. Missing responsibilities above remain implementation work.

## Retired API Coverage

Each former draft-review scenario remains as a named table case in
`contract/test_orders_draft_review_api.py` (16 former tests, not 17).
`contract/test_orders_ocr_status_api.py` likewise retains 14 retired requests.
Each table prepares one owned source and saved sheet, then checks every request separately. A real registered operator, selected
source and saved sheet exercise the retired endpoint. `HistoryCase.readonly`
compares every SQL table and the canonical source and observes SQL writes;
HTTP 410 must leave all state unchanged. Missing-menu refusal uses its explicitly
absent menu instead of manufacturing a positive source.

| Former failure class | Retired request now checked | Current positive contract |
| --- | --- | --- |
| Missing menu / saved draft detail parity | GET draft-sheet / workflow-state | `test_orders_ocr_sheet_history_api.test_operator_saved_values_and_identity_survive_history_reads`; `test_saved_identity_publication.test_missing_source_keeps_selected_saved_display_but_refuses_output` |
| Latest operator quantity / cached revision | POST confirm | `test_orders_draft_review_api.test_current_sheet_save_keeps_latest_operator_values_and_rejects_stale_revision`; `test_h2_operator_repair_revision.test_actual_final_confirm_receipt_to_output_writer` |
| Stale sheet save / concurrent line change | POST ocr-sheet-save / ocr-apply; PUT lines | Current sheet CAS case above; `test_order_workflow_revision_contract.test_revision_guard_accepts_current_and_rejects_old_or_double_command` |
| Apply cleanup and confirm publication | POST ocr-apply / confirm | `test_modernization_owned_ocr_workflow.test_native_producer_real_lineage_terminal_select_projection_and_save`; `test_h2_operator_repair_revision.test_real_final_confirm_fixture_preserves_blank_sheet_and_zero_candidate` |
| Explicit facility/period selection | POST facility / week | `test_orders_ocr_sheet_history_api.test_explicit_operator_ranges_survive_context_and_readonly_options`; `test_workflow_context_lineage.test_context_materializes_exact_dated_selection_once_and_reuses_it` |
| Reparse failure and retained operator values | GET ocr-sheet; POST reparse | `test_orders_ocr_sheet_history_api.test_stored_terminal_reparse_error_survives_current_reads`; `test_h2_operator_repair_revision.test_current_repair_actual_cache_write_gate` |
| Historical cache loss / persisted saved identity | GET draft-sheet | `test_orders_ocr_sheet_history_api.test_ocr_history_keeps_evidence_as_diagnostics_not_revision`; `test_saved_identity_publication.test_selected_saved_identity_survives_source_label_and_empty_rows` |
| Reparse/rerun/recover provider options | POST reparse / ocr-rerun / ocr-recover | `test_orders_workflow_v2_api.test_workflow_v2_ocr_run_forwards_selected_document_id`; workflow delivery/native producer contracts retain owned execution and terminal receipt checks |

Raw OCR cells in candidate-resolution cases no longer authorize a positional
template or quantity mapping. The same synthetic tables remain; names and
expectations now check refusal. Explicit removed positional metadata remains
diagnostic but cannot become a resolved mapping.

## Source Audit

The interrupted JUnit is `/private/tmp/hospital-full-20261023.xml` (232 failures).
SQLite FK enforcement and authentication restoration are parent-owned repairs,
not reasons to disable constraints or bypass authentication in individual tests.

The direct-ingest audit also includes later suite modules, not only the cases
reached before SIGINT. Existing owned helpers/native SQL components and explicit
line-injection refusal tests must retain their direct calls unchanged.

| Module group | Classification |
| --- | --- |
| `helpers/ingest_original.py`, native SQL components, `unit/test_saved_sheet_setting_invalidation.py` | Real claim/lease/registration paths; preserve |
| `integration/test_menu_readonly_boundary.py`, `test_facility_unresolved.py`, direct negative cases in `test_uploaded_pdf_worker.py` | Deliberate unowned-input refusal; preserve |
| `contract/test_orders_ocr_status_api.py`, `test_orders_draft_review_api.py` | Migrate descriptive month/dummy registration; retired endpoints must not expect success |
| `contract/test_totals_api.py`, `integration/test_bagging_delivery_slots.py`, `test_daily_output_bundle.py` | Require real selected/saved/bagged/confirmed sources, explicit recipe quantities and units |
| `integration/test_cross_month_week_support.py`, `test_change_override_priority.py` | Explicit period, template schema, physical menu diet/slot identity; no OCR-date or menu fallback |
| `integration/test_critical_decision_service.py` | Explicit existing Order parent for synchronous decision component; no invented execution receipts |
| `perf/test_performance.py` | `test_output_performance`: prepare 100 explicit synthetic facilities and originals before timing; one formally accepted order per facility avoids same-facility/week supersede. Ten cells per order and original 1/2/3 quantities, 100-order and timing assertions retained. Optional opt-in remains; not executed or reported successful. |
| `integration/test_outputs_golden.py` | `test_outputs_match_authored_golden_values`: actual selected/saved/bagged/confirmed source, explicit 100g portions with 10/5 people, physical invoice workbook and exact CSV/Excel totals 1000g/500g/1500g. Current output shape declared inline; old archived golden files untouched. Static migration only, not recognition/prod parity proof. |
| `integration/test_ocr_sheet_corpus_regression.py` | Audit pending: retain actual corpus source identity; never substitute authored PDF as corpus proof |
| `integration/test_expanded_cell_copy.py` | Removed positional copy is exercised directly with original 44/48 inputs; explicit template merge-policy classification retained. `test_owned_saved_quantity_never_copies_into_empty_menu_rows` checks real accepted saved quantities and adjacent blanks. Actual merged-source positive responsibility still requires source-level review; not claimed fully covered by rejection. |
| `integration/test_ocr_revision_store_contract.py` | `test_append_edited_revision_is_reflected_in_edit_history` and `test_revision_history_keeps_latest_twenty_revisions_and_overwrites_edited_cache` use real selected producer source and fresh synchronous input for each write. Exact row/menu/quantity/history-limit assertions retained; no-input write refusal also compares full DB/source state. |
| `integration/test_ocr_sheet_history.py` | Parent exclusively owns source/period/registration migration in separate WT; this worker's copy remains unchanged. |
| `integration/test_ocr_pipeline.py`, `test_ocr_redesign_phase_support.py`, `test_workflow_state_service.py`, `test_draft_sheet_service.py`, legacy cases in `test_ocr_evidence_service.py` | Audit pending: explicit source/period and actual execution ownership |

## Exact Retired Case Identities

### Native page output (parent production fix; worker cases not executed)

Source review only: `order_service.get_ocr_pages` selects parsed input solely
when `_payload_has_page_artifacts` sees a nonempty `pages` list. The actual
`_hakodate_canonical_payload_from_manifest_item` returns H2 overlay/artifact and
recognition records, not a `pages` list. Later in `get_ocr_pages` an H2-only
synthetic page branch exists but cannot be reached from that native payload at
the initial selection gate in this worker's frozen production copy. Parent has
fixed that gate to admit actual owned H2 content; integration will apply it.
All three page nodes now use actual native producer/selected source, not fake
`pages` injection. The partial-grid case authors its original box/column grid
before capture, keeps row grid absent, checks exact deferred metadata and
forbids expensive grid detection. The stale-cache/job cases keep their exact
old stale URI inputs but verify the actual selected artifact URI and nonempty
current URL; legacy overlay URLs remain absent. Every native page read audits
no SQL/source writes. No production edit or test execution was performed here.

### Active native output source (source review; not executed)

The FAC00006 repeated-round pair shares
`test_get_ocr_sheet_projects_fac00006_repeated_regular_round_columns_from_source_indexes`:
all original source indexes, skipped CHE column, blank second round and exact
11/blank/13/2/3/4/5/6 remain real native inputs and assertions. Its second phase
retains the complete original partial-position cache/table payload from former
`test_get_ocr_sheet_projects_fac00006_repeated_regular_round_columns_even_when_position_fallback_is_partial`.
The real resolver refuses that inferred mapping; current native cells stay
unchanged and real readonly workflow projection supplies actual gate keys,
rather than vacuous `.get` checks on the cache-only workflow serializer.

| Former workflow template node | Current owned-source node |
| --- | --- |
| `test_refresh_workflow_state_returns_semantic_shell_only_for_partial_semantic_evidence` | `test_unowned_partial_template_metadata_cannot_degrade_owned_saved_sheet`: full original unknown-template/confidence0.91/missing-grid payload stays unowned; real selected/saved5 and actual state cannot be degraded by that cache. |
| `test_refresh_workflow_state_does_not_get_stuck_semantic_shell_only_when_resolved_template_can_supply_grid` | `test_owned_template_grid_does_not_depend_on_legacy_classification_guess`: exact original classifier/template-warp candidate payload retained as unowned input, but real FAC00006 authored canonical regular-X source/producer/save5 supplies the grid and actual apply-ready state. No menu/sheet-builder mocks. |
| `test_refresh_workflow_state_returns_draft_blocked_when_template_is_unresolved` | `test_unowned_template_resolution_failure_cannot_override_owned_saved_source`: full original empty-template/confidence0.2/blocked-reason payload stays unowned; actual selected/saved5 remains canonical and is reread without inheriting foreign template blockers. This does not authorize unresolved canonical templates. |
| `test_refresh_workflow_state_returns_recovery_required_for_degraded_evidence` | `test_missing_actual_native_artifact_requires_recovery_without_unowned_cache_substitution`: original degraded raw5 payload lacking input/pages remains unowned cache. Independent real native5 first reads correctly; removal of its fixture-owned overlay must fail actual artifact byte validation and return `ocr_evidence_recovery_required`, leaving DB/evidence/cache untouched. No fake evidence or successful degraded source is persisted. |

Actual canonical missing/incomplete/ambiguous template inputs are separately
exercised by `test_actual_authored_template_defects_are_refused_without_guessing_or_state_writes`:
the actual fixture-owned canonical source is authored before execution, and the
real shared `get_authored_facility_config` boundary must raise each exact domain
code without SQL/source writes or creating draft/evidence. No mock supplies a
valid grid, first candidate or substitute facility/template.

`test_get_order_review_summary_maps_blocked_saved_draft_to_user_facing_states`
now prepares actual March21 native2 and formal operator7/`draft` save. The
original structural-review diagnostic input and all original summary assertions
remain; only display diagnostics in the existing cache are deliberately changed,
not evidence/terminal ownership. Summary/current-sheet reads are audited readonly.

The former missing-grid position fallback positive maps to
`test_explicit_owned_columns_work_without_legacy_grid_or_position_fallback`:
same March22/献立A six quantities5/4/3/2/1/1 and full old raw structured rows;
actual authored canonical columns, native producer/selection/formal save replace
guessed header inference. It checks every cell, actual apply-ready state,
canonical (not position-fallback) mapping and immutable legacy cache input.

`test_get_ocr_output_and_sheet_prefer_active_evidence_over_stale_cache` retains
the entire old stale3/cache/template-mismatch input but prepares real native9
for the original March21 explicit period. It compares actual H2 pipeline and
recognition records with selected evidence, then checks exact March21/MenuA/9
sheet cells and unchanged cache. `test_get_ocr_output_prefers_active_evidence_over_stale_job_output`
uses the same actual native source against the original stale output URI; only
that diagnostic pointer is corrupted while its actual terminal receipt remains.
No fake evidence persistence, position-menu mock, or raw-table-as-native proof.

`test_get_ocr_sheet_prefers_saved_revision_over_confirmed_lines_by_default`
now formally saves exact March21/MenuA/9/`draft`, against actual native2,
the original complete raw2/cache input and retained stored line2. The source
remains actual native2 while the operator-facing reread remains9/`draft`.
`test_build_recoverable_ocr_sheet_payload_never_uses_confirmed_lines_fallback`
uses actual selected4 plus the complete old raw4/cache input against stored2;
the real recovery-compatible read must return4 without using confirmed lines.

| Former redesign node | Current actual negative boundary |
| --- | --- |
| `test_confirm_order_fails_safely_when_latest_draft_is_unparseable` | `test_owned_bagging_rejects_unparseable_saved_row_without_changing_existing_two`: same empty identity/quantity and `broken` remark, actual native2 and formal saved broken sheet; real workflow-v2 bagging rejects `draft_lines_empty`, exact saved row and original line2 retained, full DB/source refusal audit. |
| `test_confirm_order_fails_when_no_draft_or_evidence_exists` | `test_owned_bagging_and_confirm_require_formally_saved_source`: explicit March21 source context and original line2; real bagging rejects `saved_sheet_required`, final confirm rejects `output_review_required`, no draft/evidence fabricated and full DB/source refusal audit. Current positive snapshot/output responsibility remains `test_confirmed_snapshot_includes_materialization_candidate_from_latest_draft`. |

### Candidate acknowledgement components (source review; not executed)

`test_refresh_workflow_state_uses_candidate_sheet_state_to_block_unpreviewable_candidate`
and `test_refresh_workflow_state_does_not_prompt_when_candidate_preview_contract_is_unavailable`
map to the missing-overlay / changed-records cases of
`test_unpreviewable_owned_candidate_cannot_replace_saved_five`. Independent real
candidate8 is previewed first; actual artifact loss/hash corruption then fails
the real byte validator and actual preview generation with exact domain codes,
retaining selected/saved5, current revision and immutable stored evidence/job.
The old template-unresolved diagnostic is also covered at the actual canonical
template boundary above. No mock returns preview failure/success or clears gates.
`test_refresh_workflow_state_hides_candidate_prompt_when_candidate_preview_revision_mismatches_current`
maps to `test_real_candidate_selection_rejects_stale_workflow_revision_and_keeps_saved_five`:
real save5 creates a real older revision, another formal save retains5 but
advances revision, actual preview8 reports the new current revision, and stale
explicit selection returns409 under full readonly audit. The current guard is
workflow CAS, not a mocked legacy prompt ID. Fresh explicit8 selection positive
remains `test_owned_selected_eight_is_not_replaced_by_stale_candidate_five`.

The two FAC00002 saved-layout nodes retain their exact old noisy table and
stored blocker/warning metadata, now attached only after real native23/27 and
formal source-linked save. `test_refresh_workflow_state_suppresses_stale_draft_layout_blockers_when_position_fallback_is_ready`
maps to `test_owned_saved_twenty_three_twenty_seven_retain_explicit_layout_blockers`:
the original assertions actually required draft_blocked, template/unmapped and
draft-newer-than-lines blockers, and those expectations remain. A guessed
position fallback must not clear explicitly persisted blockers.
`test_refresh_workflow_state_keeps_active_evidence_layout_blockers_for_authoritative_draft`
maps to `test_current_schema_failure_and_saved_layout_blockers_preserve_authoritative_quantities`:
same23/27, saved/evidence lineage and stored blockers, plus an actual omitted
blank quantity column; the derived schema failure and real bagging refusal are
checked without losing either nonblank quantity or any DB/source state.

### Quantity diagnostics versus authoritative operator values

`test_refresh_workflow_state_returns_layout_choice_required_for_column_mapping_candidates`
and `test_refresh_workflow_state_unblocks_after_column_mapping_choice` share
`test_legacy_column_choice_cannot_override_authored_canonical_columns`.
Both exact cols-a/cols-b labels/scores and complete original raw5 input remain.
Actual resolver ambiguity is checked without binding foreign candidates to
evidence; exact retired decision read/choice requests each receive410 under a
full readonly audit. One actual canonical source/selection/formal save5 supplies
the current positive: fixed regular2F field, apply-ready, no inferred mapping,
no draft-empty/column-choice blockers and consistent typed top-level/gate flags.
This does not claim the retired choice workflow still works.

| Former workflow node | Current source-backed responsibility |
| --- | --- |
| `test_refresh_workflow_state_returns_review_required_for_high_risk_quantity_signals` | `test_unowned_numeric_diagnostics_cannot_block_owned_saved_five`: exact original column_swap/merged_numeric_cell/failed-cell input and full original raw5 payload stay untrusted cache. Real native selected/saved5 and actual state/gate are checked without numeric-review blocking or source/draft mutation. |
| `test_refresh_workflow_state_returns_review_required_for_low_trust_populated_draft` | `test_formal_operator_twenty_one_is_not_blocked_by_unowned_low_trust_flags`: actual native5 and formally saved exact original regular-X/21/blank-remark row. Original merged-cell/low-confidence diagnostics stay explicit unowned input; real current read retains21 and does not invent numeric-trust blocking. |
| `test_refresh_workflow_state_returns_layout_choice_required_for_critical_quantity_candidates` | `test_legacy_quantity_choices_are_not_authority_for_current_operator_values[refuse_legacy_high_impact_quantity_adoption]`: full original raw5, exact critical qty-a/qty-b labels/scores/physical evidence references. The actual quantity resolver retains ambiguity/scope diagnostics, but authenticated retired quantity-choice POST must refuse410 without DB/source or saved5 changes. Actual formal manual3 replacement remains separate from candidate adoption. |
| `test_refresh_workflow_state_clears_review_required_after_quantity_choice` / former `_assert_...` helper | `test_legacy_quantity_choices_are_not_authority_for_current_operator_values[formal_manual_three_not_legacy_quantity_choice]`: exact original merged-numeric issue and qty-a3/qty-b8 candidates, actual resolver/retired choice refusal with saved5 held; formal operator3 save and real apply-ready/readback replace successful obsolete private-choice flow. No candidate is bound to selected evidence by fabricated provenance. |
| `test_refresh_workflow_state_uses_semantic_initial_draft_for_low_trust_review` | `test_formal_regular_twenty_one_and_mixer_three_are_not_inferred_from_unowned_numeric_review`: FAC00006 explicit columns/menu/period and actual native regular5/blank mixer. The old mocked21/3 row is now explicitly operator-authored and formally saved; exact21/3 reread and unchanged native5/blank source are both checked. Original merged-cell/low-trust diagnostics remain unowned cache, not a reason to manufacture recognition values or stop editing. |

The old requirement to stop sheet review because of recognized number contents
is not reinstated. Structural schema refusals and real operator typed-value
preservation remain `test_saved_schema_failure_is_readable_and_blocks_every_materialization_path`
and `test_complete_schema_preserves_typed_operator_values_and_real_candidate`
in `contract/test_saved_sheet_quantity_schema.py`; these source nodes exist.

### Status diagnostic metadata (source review; not executed)

`test_get_order_does_not_reconcile_done_first_pass_output_when_persisted_evidence_exists`
now uses real native selected/saved five rather than a mocked `OEVexisting`.
The original first-pass file (same February15/MenuA table/page representation)
is attached to a genuinely unowned diagnostic job with no attempt/terminal
receipt. The reconcile observer calls the actual implementation if invoked,
never returns fabricated success; authenticated GET must not invoke it and must
leave all DB/source state, selected evidence, saved five and diagnostic job intact.

`test_get_order_preserves_terminal_reparse_failure_status` and
`test_get_order_preserves_terminal_llm_reparse_failure_status_against_cached_done`
map to the two explicit cases of
`test_owned_terminal_failure_cannot_be_completed_by_stale_done_output`.
Each actual native rerun throws the original diagnostic string at the external
recognizer boundary, persists an actual failed owner receipt with no new
evidence, then receives the original done `|a|b|` file as a stale diagnostic
output reference without changing that receipt. Authenticated detail reads must
remain failed, preserve the exact exception in diagnostics and retain saved5.
No removed provider execution or hand-authored failed execution is claimed.

The five former cache/job-only OCR-output positives now prepare real native
selection and formal saved five. Historical `gemini` strings remain diagnostic
input literals only; no removed recognizer is executed and no historical job is
declared a successful native result. `_owned_debug_case` changes display metrics
and untrusted cache debug only, preserving the actual done terminal receipt.
Every HTTP output read uses the actual operator headers and a full readonly
DB/source audit, checks selected evidence and saved five, and checks the job is unchanged.

| Former status node | Current node / retained assertion |
| --- | --- |
| `test_get_ocr_output_includes_reparse_debug_from_cache` | `test_unselected_cache_debug_cannot_replace_owned_ocr_output`: original provider/55 formatting checked by the real pure diagnostic helper; unselected cache row55 cannot supply current OCR output. |
| `test_get_ocr_output_refreshes_stale_reparse_debug_with_latest_job_metrics` | `test_owned_output_debug_uses_explicit_job_diagnostics_without_changing_source`: original 34/78 stale versus 54/216 explicit display metrics, requested provider and changed flag retained. |
| `test_get_ocr_output_surfaces_failed_llm_reparse_debug_from_job_metrics` | `test_owned_output_displays_historical_failure_diagnostics_without_forging_failed_execution`: original error/stage/hard_failed text retained as diagnostics; the real successful producer receipt remains done, never relabelled a failed execution. Actual failure ownership is separately covered by `test_owned_reparse_callback_propagates_crash_to_delivery_terminal_owner`. |
| `test_get_ocr_output_clears_stale_reparse_error_on_success_metrics` | `test_owned_output_debug_clears_stale_errors_without_overwriting_saved_five`: original stale error/reject/validation inputs and exact clearing assertions retained against the real pure helper, plus authenticated actual selected output. |
| `test_get_ocr_output_keeps_reparse_warning_fields_on_success_metrics` | `test_owned_output_preserves_explicit_warning_diagnostics_and_saved_five`: original 62/248 and column anomaly warning payload retained with actual selected/save source protection. |
| `test_get_ocr_output_api_recanonicalizes_stale_aux_position_fallback` | `test_owned_output_refuses_stale_aux_position_mapping_without_copying_totals`: full original April26 raw rows, bbox cells and stale mapping remain actual untrusted cache input. Actual resolver rejects `position_fallback_removed`; original totals67/70 fail the actual native role boundary, while real selected/saved regular66 is reread unchanged. No guessed column shift. |
| `test_get_ocr_output_prefers_applied_reparse_revision_over_stale_reject_candidate` | `test_formally_saved_seventy_is_not_replaced_by_stale_reject_five`: explicit February15 source/period, real native five and formal operator save70, original stale reject revision/digest/row/cache shapes. Current reread keeps exactly one row with aux70/quantity70; selected OCR source remains5. Old applied-cache-mirror flags are not recreated or treated as evidence. |

The three existing `hides_new_candidate_after_acknowledging_current_sheet`,
`reprompts_when_new_candidate_arrives_after_acknowledgement`, and
`suppresses_older_pending_candidate_ids_after_acknowledgement` nodes now use real
selected/saved five, actual terminal eight/nine and actual persisted decisions.
All original prompt/active/acknowledged-ID responsibilities remain. A genuine
fresh terminal candidate is `new_evidence_available`, not the old mocked
`apply_ready`. Only the stale-pointer negative changes job diagnostic metadata;
the real terminal receipt and every source/saved value remain unchanged.
The reprompt node also checks the actual preview nine while the saved five is retained.

| Former workflow-state node | Current node and retained responsibility |
| --- | --- |
| `test_save_ocr_sheet_exact_invokes_authoritative_candidate_acknowledgement` | `test_exact_internal_save_acknowledges_actual_selected_evidence`: actual selected native source, exact row `03/22/朝/Menu A/5`, actual persisted acknowledgement actor and evidence. Internal cache/draft write does not advance the workflow-selected draft; formal save coverage remains independent. No acknowledgement mock. |
| `test_acknowledge_current_candidate_evidence_uses_cached_candidate_and_lightweight_refresh` | `test_cached_workflow_without_candidate_identity_cannot_acknowledge_latest_evidence`: actual selected/saved five and owned terminal candidate eight. The real cached serializer omits candidate identity, so the service must return `candidate_not_found` without any DB/source/decision mutation. The former fake-cache success/lightweight call-count is not a current operator contract. Actual candidate/preview positive remains `test_project_workflow_state_returns_new_evidence_available_when_latest_evidence_differs_from_draft`. |
| `test_apply_latest_draft_invokes_authoritative_candidate_acknowledgement` | `test_internal_apply_materializes_owned_saved_five_and_records_real_acknowledgement`: formal save of real selected five, actual internal materialization, exact date/menu/quantity and actual acknowledgement actor/evidence; no protected materializer or acknowledgement mock. This is not proof of a retired public apply endpoint. |
| `test_authoritative_candidate_acknowledgement_falls_back_to_active_evidence_when_candidate_already_collapsed` | `test_internal_acknowledgement_uses_explicit_selected_source_without_a_candidate`: real selected native evidence and formal saved sheet; actual decision must use that exact identity while source and saved sheet remain unchanged. No guessed evidence ID or fake refreshed state. |
| `test_refresh_workflow_state_uses_rerun_job_metrics_to_surface_new_candidate` | `test_refresh_workflow_state_uses_owned_terminal_metrics_to_surface_new_candidate`: real second producer/terminal receipt eight while selected/saved five remains authoritative. Original state/active/candidate assertions retained, fake job/menu/state mocks removed. |

The independent current save/CAS positive is not folded into the refusal table.
Every row below performs its own full readonly DB/source audit and saved-sheet equality.
The grouped positive contracts above describe distinct coverage, not proof that the old API still works.
The mocked `test_workflow_v2_ocr_run_forwards_selected_document_id` checks HTTP parameter forwarding only;
real queue/claim/producer/selection coverage is
`test_modernization_owned_ocr_workflow.test_native_producer_real_lineage_terminal_select_projection_and_save`.

| Source module | Former test | Refusal table case |
| --- | --- | --- |
| `test_orders_draft_review_api.py` | `test_order_detail_and_draft_sheet_expose_same_blocker_reason_for_missing_menu` | `refuse_draft_read_without_legacy_menu_resolution` |
| `test_orders_draft_review_api.py` | `test_order_endpoints_expose_draft_ready_state_from_saved_sheet_and_reject_reason` | `refuse_legacy_workflow_state_read` |
| `test_orders_draft_review_api.py` | `test_confirm_endpoint_uses_latest_draft_without_draft_newer_block` | `refuse_legacy_confirm_latest_sheet` |
| `test_orders_draft_review_api.py` | `test_ocr_sheet_save_returns_stale_revision_conflict_for_outdated_revision` | `refuse_legacy_save_stale_revision` |
| `test_orders_draft_review_api.py` | `test_update_lines_returns_stale_lines_conflict_when_lines_changed_elsewhere` | `refuse_legacy_line_write_stale_digest` |
| `test_orders_draft_review_api.py` | `test_ocr_apply_returns_stale_lines_conflict_after_remote_line_update` | `refuse_legacy_apply_stale_digest` |
| `test_orders_draft_review_api.py` | `test_ocr_apply_clears_saved_draft_state_after_successful_apply` | `refuse_legacy_apply_saved_sheet` |
| `test_orders_draft_review_api.py` | `test_ocr_sheet_save_returns_stale_lines_conflict_after_apply_changes_lines` | `refuse_legacy_save_after_apply` |
| `test_orders_draft_review_api.py` | `test_confirm_returns_stale_revision_conflict_when_draft_changed_elsewhere` | `refuse_legacy_confirm_stale_revision` |
| `test_orders_draft_review_api.py` | `test_confirm_route_defers_post_confirm_side_effects_to_background` | `refuse_legacy_confirm_background_dispatch` |
| `test_orders_draft_review_api.py` | `test_confirm_uses_authoritative_current_sheet_revision_not_stale_cached_payload` | `refuse_legacy_confirm_cached_revision` |
| `test_orders_draft_review_api.py` | `test_facility_and_week_updates_return_stale_conflicts` | `refuse_legacy_facility_write_stale_digest` |
| `test_orders_draft_review_api.py` | `test_week_save_conflict_guard_accepts_current_promoted_week_value` | `refuse_legacy_week_write` |
| `test_orders_draft_review_api.py` | `test_order_endpoints_expose_reparse_stage_and_retained_lines_for_recoverable_result` | `refuse_legacy_ocr_sheet_read` |
| `test_orders_draft_review_api.py` | `test_stale_reparse_job_is_marked_failed_and_allows_retry` | `refuse_legacy_reparse_retry` |
| `test_orders_draft_review_api.py` | `test_persisted_ocr_revision_history_survives_missing_cache_payload` | `refuse_legacy_draft_history_read` |
| `test_orders_ocr_status_api.py` | `test_draft_sheet_surfaces_running_llm_reparse_health` | `refuse_draft_sheet_legacy_running_llm_reparse_health` |
| `test_orders_ocr_status_api.py` | `test_draft_sheet_surfaces_awaiting_output_ocr_rerun_state` | `refuse_draft_sheet_legacy_awaiting_output_ocr_rerun_state` |
| `test_orders_ocr_status_api.py` | `test_confirm_endpoint_blocks_when_weekly_menu_missing` | `refuse_confirm_endpoint_blocks_when_weekly_menu_missing` |
| `test_orders_ocr_status_api.py` | `test_reparse_endpoint_marks_job_running_before_background` | `refuse_reparse_endpoint_legacy_job_running_before_background` |
| `test_orders_ocr_status_api.py` | `test_reparse_endpoint_rejects_removed_tesseract_provider` | `refuse_reparse_endpoint_rejects_removed_tesseract_provider` |
| `test_orders_ocr_status_api.py` | `test_ocr_rerun_endpoint_rejects_removed_tesseract_provider` | `refuse_ocr_rerun_endpoint_rejects_removed_tesseract_provider` |
| `test_orders_ocr_status_api.py` | `test_reparse_endpoint_defaults_to_llm_assist_for_user_requested_reparse` | `refuse_reparse_endpoint_legacy_llm_assist_for_user_requested_reparse` |
| `test_orders_ocr_status_api.py` | `test_reparse_endpoint_passes_explicit_ocr_model` | `refuse_reparse_endpoint_legacy_explicit_ocr_model` |
| `test_orders_ocr_status_api.py` | `test_reparse_endpoint_accepts_llm_model_alias` | `refuse_reparse_endpoint_legacy_llm_model_alias` |
| `test_orders_ocr_status_api.py` | `test_reparse_endpoint_accepts_prompt_preset` | `refuse_reparse_endpoint_legacy_prompt_preset` |
| `test_orders_ocr_status_api.py` | `test_reparse_endpoint_accepts_merged_cell_prompt_preset` | `refuse_reparse_endpoint_legacy_merged_cell_prompt_preset` |
| `test_orders_ocr_status_api.py` | `test_ocr_recover_endpoint_requests_pipeline_first_pass` | `refuse_ocr_recover_endpoint_legacy_pipeline_first_pass` |
| `test_orders_ocr_status_api.py` | `test_ocr_recover_endpoint_retries_stale_job_with_pipeline_first_pass` | `refuse_ocr_recover_endpoint_legacy_stale_job_with_pipeline_first_pass` |
| `test_orders_ocr_status_api.py` | `test_ocr_rerun_endpoint_force_replaces_active_job` | `refuse_ocr_rerun_endpoint_legacy_active_job` |
