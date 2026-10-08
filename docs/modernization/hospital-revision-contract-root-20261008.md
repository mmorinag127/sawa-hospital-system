# Hospital Revision Contract Source Migration

Base 5f6a3314c668d3224115555685849e00dd02a303.
Branch codex/modernization-hospital-revision-contract-20261008.
Only test_order_workflow_revision_contract.py and this document changed.
No tests, runtime imports, DB, API or build executed; no production edits or
commit. Parent historical two-module log reports 81 pass / 9 fail; it is
unchanged at hospital-main/tmp/receipt-parent-20261008/logs/tests-explicit-export-dir.log.
Five CloudTasks SDK import failures are outside this packet, not skipped or
faked. Parent must run all 90 with its actual read-only SDK/audit environment.

## Four Diagnoses Before Edits And Assertion Mapping

| Original test | Actual failure / canonical contract | Source-only migration |
| --- | --- | --- |
| finalize_turns_a_simultaneous_sql_update_into_a_conflict | secondary_actions_json absent at _workflow_meta; finalizer also reads session.info | Add empty authored metadata and session.info to fixture; keep revision 4, identical StaleDataError and conflict assertion; add rollback observation |
| pre_save_status_for_uninitialized_workflow_is_a_read_projection | obsolete checks key; public projection returns anomaly_review_confirmed, sheet_review_confirmed, ready | Keep empty input and sheet-digest; assert all three public flags False and original input remains {} |
| queue_callers_dispatch_the_queue_captured_revision_and_attempt | removed direct task dispatcher; durable owned intent now carries captured tokens | AST checks both API dispatch callers, exact intent order/job/revision/attempt/PDF/request identity and selected document fields; missing input returns before session opens, missing delivery identity raises before transport |
| cache_actual_write_requires_the_callers_real_job_id_and_keeps_first_pass_explicit | old mandatory job-only assertion excludes approved synchronous snapshot CAS | Keep normalized ID, async job fence and evidence job identity assertions; AST checks mutually exclusive modes, absent ownership refusal, synchronous evidence=False, exact async/sync fences and config mismatch False |

Failure class: outdated fixture/source contracts bypass the current shared
ownership prerequisites. No current output or receipt is repaired. No OCR job,
PDF, evidence or selected source identity is invented. New source assertions
inspect actual function AST and call keyword values, not a rewritten text
baseline or a mocked production body. All adjacent tests and parameter inputs
remain unchanged. Only the explicitly obsolete checks-shape, direct-dispatch
and mandatory-job-only expectations are replaced with their approved contracts.

## Source History And Shared Boundaries

- aa8b131 (Move step3 pre-save status to backend) introduced public boolean
  pre-save projection in order_workflow_v2_service._pre_save_status_for_sheet.
- e4788eb (bind OCR/output adoption to original inputs) introduced
  synchronous_write_input and materialization_configured_input ownership.
  _finalize_workflow_mutation reads metadata and session.info before flush;
  _save_order_ocr_cache forces synchronous persist_evidence=False;
  _synchronous_cache_write_fence rejects changed configured input before the
  snapshot CAS call. This is not permission for jobless evidence writes.
- 7cd6588 (durable workflow OCR delivery ownership) replaced direct task
  dispatch with committed delivery intent. mark_ocr_run_queued captures actual
  final workflow revision, job, attempt, selected PDF and request identity;
  both API callers transport queued.delivery. No job-only transport fallback.
- Base 5f6a331 retains the previously approved callback receipt contract tests;
  those success/error/crash/foreign-receipt assertions are untouched.

Source reads covered all four failures before editing, the matching production
functions and git log -S / git show history. No product gap was established by
these four obsolete contracts. This packet does not prove runtime correctness.

## Frozen Evidence

Test SHA256:
82f8fd3dec4677b8db0e2d8806c35ef351924135417fa0c99005caab38c400e2.
Commands: cat/sed/rg source and historical-log reads; git log/show source history;
apply_patch manual test/doc edits; shasum -a 256; git diff --stat; git add exactly
the two allowed files; git diff --cached --binary | shasum -a 256;
git status --short. Binary patch hash reported externally to avoid self-hashing.
All definitions unexecuted. Parent review/integration and actual SDK-backed
owned regression remain pending. No passing claim or self approval.
