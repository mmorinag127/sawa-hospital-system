# Workflow Revision Contract

`GET /orders/{order_id}/workflow-v2` returns `revision`.

Every mutating `workflow-v2` endpoint requires `If-Match: "<revision>"`.

- missing or malformed header: `428 workflow_revision_required`
- stale header or SQL optimistic update race: `409 workflow_revision_conflict`
- successful command: the response contains the next revision

The workflow commands are context confirmation, OCR queue/completion, OCR selection/deletion,
quad/header/row corrections, expanded-cell mode, facility template columns, sheet save/review,
auto-edit job start/completion, bagging, anomaly review/dismissal, and final confirmation.

| Command group | Caller supplies | Success payload |
| --- | --- | --- |
| context, corrections, OCR selection, sheet, bagging, review, confirmation | `If-Match` / `expected_revision` | workflow snapshot flushed by that command, including its next `revision` |
| OCR queue | queue caller supplies the prerequisite command's returned revision | workflow snapshot plus `ocr_job.workflow_attempt_id` and final `workflow_completion_revision`; dispatch occurs after queue success |
| OCR completion/progress | background caller supplies the queue-captured revision and attempt | only the matching SQL job attempt may update progress, completion, or failure |
| auto-edit completion | auto-edit worker supplies its start snapshot revision | terminal `done`/`failed` records its own resulting revision; a later workflow revision does not relabel a completed job as `stopped` |

Internal callers:

- ingest first-pass context suggestion uses revision `0` only when no workflow row exists.
- OCR queue records its returned revision as `ocr_jobs.metrics.workflow_completion_revision`.
- OCR completion uses only that recorded value. A mismatch stops the job with
  `workflow_revision_conflict`; it does not apply evidence to a newer workflow.
- auto-edit start records `workflow_completion_revision` in its job metadata. Completion uses
  only that value; a mismatch is exposed as `stopped` without updating the workflow.

GET paths do not initialize or mutate workflow state. The migration initializes persisted rows to
revision `1`; new workflow rows are constructed with revision `1`. Missing persisted revisions are
an error, never a fallback to `1`.

## Follow-up Test Migration

The production API, ingest worker, and workflow background callers pass explicit revisions from
the operator read, bootstrap `0`, or the immediately preceding command result. The remaining
unit-only caller migration in `backend/tests/unit/test_order_workflow_v2_service.py` is a test
verification task: each normal sequence must pass its captured/own-result revision, while
intentional missing, invalid, and stale cases must remain negative cases without a replacement
read. Tests that formerly expected context recovery must be split into an explicit canonical
context fixture success case and a missing-context blocker case.

## Synchronous Pre-Calculation Ownership Pass (2026-10-07)

Source baseline: `d3d39bf3198f3ba631c82c7c8bd396323dcd2484`, branch
`codex/modernization-h2-workflow-revisions-20261006`, worktree
`/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-h2-revisions`.

Failure class: capturing a replacement input at persistence time can authorize an older
quantity calculation as if it used that replacement. The synchronous contract captures
the selected workflow evidence and fixed draft base before calculation, together with
workflow revision, order/line contents, evidence contents, draft contents, and cache contents.
The common write guard compares those exact inputs while holding the workflow lock and
the dependent row locks (SQLite first claims the workflow revision with a conditional write).
There is no latest-evidence selection or missing-job authorization in this contract.

| Entry / caller | Capture before | Actual write boundary |
| --- | --- | --- |
| `apply_submitted_ocr_sheet` -> `save_ocr_sheet_exact` | config reload, markdown/row conversion | explicit captured input to revision + cache + draft; subsequent `apply_latest_draft` receives only the own-write advanced input |
| direct `save_ocr_sheet_exact` | snapshot normalization and digest calculation | `_append_edited_ocr_revision` + `persist_sheet_draft` share one Session; draft rejection rolls back revision/cache too |
| `_maybe_refresh_semantic_sheet_draft`, `_maybe_upgrade_generic_sheet_draft` | semantic build / quantity merge | supplied draft must equal captured draft; `persist_sheet_draft` receives the captured input |
| `_build_current_sheet_context_uncached(allow_state_mutation=True)` -> `_rebase_draft_record_to_facility_schema` | order/draft reads and rebase calculation | capture propagated into `_persist_sheet_draft_with_base_evidence`; no replacement capture in the saver |
| `force_overwrite_current_sheet_with_facility_schema` -> `_rebuild_current_sheet_for_facility_schema` | template resolution and rebuild | captured input forwarded to the common draft guard |
| `force_overwrite_current_sheet_with_weekly_menu` | sheet/menu build and quantity conversion | uses the captured draft/evidence, then the common draft guard |
| `set_facility` -> `_rebuild_current_sheet_for_facility_schema` | rebuild begins after the caller's facility update | helper captures before any rebuild computation; async context instead retains its captured real job |
| `get_ocr_sheet` -> `ensure_hakodate_evidence_draft_current` -> `switch_draft_to_latest_evidence` / projection | sheet reads and projection | exact captured evidence is passed to projection and draft persistence; no late recapture; reads without a valid write snapshot do not invoke this saver |
| direct `switch_draft_to_latest_evidence` | evidence/draft reads and projection | synchronous path uses workflow-selected evidence; async rerun passes its own returned evidence and real job |
| `build_order_hakodate_assignment`, `build_order_quantity_assignment_comparison` | assignment/preview calculations | repair cache receives the pre-calculation input; rejection is an explicit error |
| `build_order_hakodate_projected_sheet` with explicit evidence | assignment and projection | repair still computes, but does not independently commit a cache write before its caller's guarded draft write |
| `ocr_patch_candidate_service.apply_patch_candidate_to_draft` | candidate and draft reads | candidate base draft/evidence must match the captured inputs; common draft guard receives that input and its exact evidence template |

`_append_edited_ocr_revision`, `persist_sheet_draft`, and
`_persist_sheet_draft_with_base_evidence` no longer capture synchronous inputs at save time.
Missing input returns failure. Exact save advances only its own cache timestamp/content
inside the existing Session, not workflow/evidence/order/draft tokens. It returns the
revision it wrote rather than looking up a later revision. In the save-and-apply sequence,
candidate acknowledgement remains after materialization, so it cannot replace the captured
workflow revision between the two writes. Async draft callers retain captured real job,
attempt, and revision; Hakodate rerun explicitly carries its own persisted evidence.
The async cache mirror's obsolete wrapper keyword arguments were removed: the real job
is explicit and the existing wrapper propagates the same captured attempt/revision from
ContextVars to evidence persistence, without a database token lookup.

This is source-only work. No runtime, tests, build, lint, typecheck, or database operations
were run. Existing staged test edits are retained. The 193 normal unit revision callers and
old context-fallback expectations remain deferred, not passed or omitted. Later behavioral
verification must cover pre-calculation supersession, in-place draft/cache/line changes,
wrong evidence/template lineage, missing synchronous input, exact-save rollback, and the
own-write save/apply chain; older synchronous guard fixtures also need `input_versions`.

Separate remaining production task: rejected reparse candidate own-evidence lineage
(`_save_reparse_candidate_as_draft` and the rejected revision branches). This pass does not
claim that task or all H2 implementation is complete.
