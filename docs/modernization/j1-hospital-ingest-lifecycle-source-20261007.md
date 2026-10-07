# J1 hospital ingest lifecycle source unit

Base: `735b0ccc6831c7d9def4d479712ae9291f485823`.
Branch: `codex/modernization-j1-ingest-lifecycle-20261007`.
This document records source implementation, not executed tests or deployment acceptance.

## Persisted ownership

`IngestJob.input_digest` hashes the exact persisted normalized JSON payload, including unknown keys, null and zero. It is not a hash of current configuration or a replacement PDF. `generation` is a persisted reservation identity. Explicit force/restart changes that identity; `attempts` remains monotonic. Claim creates an independent `claim_id` and returns the original payload and lease as an own-session DTO.

Worker dispatch carries the captured generation/digest. Claim, finish and failure compare the original lease under a conditional SQL UPDATE in the same transaction. A duplicate processing/done/error message cannot replace payload. Business exceptions produce terminal `error` with `failure_kind=business`; scans and duplicate enqueue do not retry that state. Explicit operator restart retains the recorded input and creates new ownership.

`attempts` is cumulative history, not the automatic recovery budget. `generation_attempts` counts claims since the last explicit force/restart and resets only at those authorized boundaries. Interrupted recovery can rotate the reservation identity but retains this budget. Existing automatic recovery limits/window are unchanged; a new explicit manual attempt does not inherit an exhausted historical budget. Migration retains the known pending-row count as its existing budget, while legacy execution budgets remain unavailable.

The actual claim UPDATE includes `generation_attempts < INGEST_JOB_AUTO_RECOVERY_MAX_ATTEMPTS`, not merely the scan query. For eligible pending/stale input, a second conditional UPDATE records exhaustion as terminal `error`, `failure_kind=budget_exhausted`, and `last_error=ingest_recovery_budget_exhausted`; no execution claim or counter increment is created. The committed refusal DTO is distinct from an execution lease. When dispatched through the upload lane, the original upload lease is stopped as `failed` with no retry time in that same transaction. Dispatch reports that own refusal, not guessed success. A currently active/nonstale execution or genuine asynchronous wait is not stopped by a duplicate. Stale redispatch and the reset API cannot reset the budget or implicitly create a new manual attempt; only explicit force/restart resets it to zero.

`recovery_claim_id` records the interrupted predecessor, not a business retry. Recovery finds the exact predecessor OCR reservation and rebinds only its saved nonworkflow execution. It does not rebuild configured inputs, physical menu entries, selected PDF receipts or processing-start snapshots from current data. Missing predecessor capture stops explicitly. A saved terminal predecessor is reconciled as that result, never recognized again. Genuine awaiting-output/recovering OCR is excluded from stale ingest restart.

## Actual caller coverage

| Source | Actual entrances / changes |
| --- | --- |
| `services/ingest_job_service.py` | `create_ingest_receipt` / compatible `create_ingest_job`; `restart_ingest_job`; `claim_ingest_job`; `complete_ingest_job`; `fail_ingest_job`; `fail_ingest_attempt`; original OCR reservation/recovery/terminal reconciliation; pending/stale and OCR recovery candidate scans |
| `workers/ingest_worker.py` | `enqueue_ingest`, `enqueue_ingest_async`, `enqueue_ingest_job_async`, `process_ingest_job`, Celery `process_ingest`; explicit lease dispatch instead of a later payload GET; no Celery business-exception retry |
| `workers/ingest_worker.py` | `_process_ingest_inline`, `_capture_and_process_ingest`, `_process_ingest_captured`; original registration receipt, canonical existing uploaded-order OCR ID convention, capture failure persistence, asynchronous wait and terminal result separation |
| `services/workflow_attempt_guard_service.py` | `capture_nonworkflow_execution`, `lock_order_execution_input`, `lock_nonworkflow_execution_job`, `lock_actual_write_ownership`; original ingest lease before source capture/writes, exact registered document and PDF identity; all existing H2 source checks remain |
| `services/order_service.py` | `create_order_from_ingest` registration and repeated registration; exact own registration receipt; `_finalize_async_first_pass_side_effects` / `finalize_first_pass_side_effects` / asynchronous first-pass reconciliation use recorded execution lease and real terminal OCR receipt |
| `workers/ingest_worker.py` | `process_uploaded_pdf_job`, `run_uploaded_pdf_recovery_once`, `run_ocr_job_recovery_once`, `_mark_ocr_job_final_failed`; no latest-link read, implicit force restart or legacy backfill authority; original hardfail propagates to own upload |
| `services/uploaded_pdf_service.py` | `claim_uploaded_pdf`, `refresh_uploaded_pdf_links`, completion helpers, retry/failure helper, explicit `requeue_uploaded_pdf`; token + worker + monotonic attempt + original source fields checked in the writer transaction |
| `api/ingest.py` | actual upload saves returned upload ID with ingest input and returns recorded own source links, not a latest-order lookup; retry/recover-ready/reset-stale dispatch captured reservations or the recorded uploaded-PDF lane; explicit upload retry retains its existing operator authorization |
| `api/system.py`, `api/health.py` | existing read-only summary callers; no mutation interface change |

All production references to create/claim/complete/fail/restart/dispatch and upload completion/retry/link helpers were searched under `backend/src` and `scripts`. No output worker, transport implementation, UI or other worktree is modified.

## Uploaded PDF boundary

`lease_token` distinguishes claims even when worker identity is reused. Force/requeue invalidates the old token without rewinding attempt history. Source/target links are adopted only from the own registration receipt and verified source document, not a later current-document lookup. Ordinary reads no longer mutate links or create legacy backfill recovery work.

An asynchronous OCR wait remains processing. ValueError/business failures become explicit `failed`, with no retry time. Unknown infrastructure exceptions are not converted to guessed business success or business retry: the existing uncompleted lease can expire under the existing policy. A crash after ingest completion but before upload completion can reconcile only the exact saved successful OCR receipt, original input and actual document under upload/ingest/order/OCR locks; it cannot execute the terminal ingest again or borrow a newer result.

Completed upload retains its historical claim owner/token, with no active lease expiry. This permits the existing same-owned-execution post-result projection; a successor claim/force/requeue still retires it. Capture/new ingest claim only accepts processing/pending, not terminal input.

## Formal migration

`0032_ingest_attempt_ownership.py` follows `0031`; older migration files are unchanged. New ownership columns are explicit, nullable for unavailable legacy metadata. Migration hashes only actual recorded dictionary payloads, preserves statuses/errors/counts/raw values, creates reservations only for known pending rows, and does not invent legacy execution claims. Legacy processing/completed/error ownership remains unavailable; automatic scans do not establish it. Explicit restart is necessary for unowned processing input. Unknown preexisting ownership columns stop migration; downgrade cannot erase fences.

Existing model imports already register IngestJob and UploadedPdf; no duplicate registry is added. The runtime catalog gate in this base still requires observed `0028` catalog contracts. No new observed catalog hash or migrated DB history is fabricated in this source-only unit. Formal-head/catalog integration and native SQLite/PostgreSQL observations are pending the parent's technical phase.

## Source case definitions

`backend/tests/unit/test_j1_ingest_lifecycle.py` defines duplicate state/input preservation; force/restart old finish/fail/capture rejection; explicit hardfail retry; pending versus completed; actual worker capture failure and newer-owner refusal; same-worker upload claim retirement across complete/fail/retry/link; actual uploaded wrapper invalid source; interrupted original capture reuse; exact asynchronous waiting; closed-session terminal DTOs; concurrent duplicate claims; different OCR execution rejection; terminal exclusion before OCR recovery limits; and actual upload-wrapper terminal ACK-gap reconciliation without another recognition attempt.

The interrupted-capture and terminal fixtures intentionally isolate stored lease/receipt behavior. Their small boundary dictionaries are not claimed as full H2 configured/PDF/physical-menu producer fixtures. The stale capture tests invoke the real common capture rejection path; recognizer/network work is not executed.

Additional source cases cover force/restart after cumulative attempts exceed the automatic limit, budget retention across interrupted recovery, actual upload business failure stopping both recovery lanes, and incomplete upload lease rejection without source supplementation.

Budget-boundary cases call the real direct claim for pending and stale processing, actual worker stale redispatch, and the actual reset API function with only its asynchronous scheduling replaced by direct dispatch. They assert no execution, no attempt/input changes, retained exhausted budget, and no implicit manual generation; reset alone may rotate the existing transport reservation as before.

The actual uploaded-wrapper exhaustion case expires an original claim, dispatches its replacement, and asserts persisted ingest/upload stops. Even after another expiry, the ready scan, recovery-loop dispatch and direct duplicate wrapper cannot increase attempts. Original complete/failure callers cannot overwrite the stop. The isolated OCR-recovery scheduler is replaced only to keep this source definition focused on the upload ready/dispatch path; this is not transport or full OCR recovery proof.

`backend/tests/unit/test_j1_ingest_migration.py` defines 0032 data preservation, unavailable legacy claims, duplicate-column refusal and fence-preserving downgrade refusal using the original 0004/0011 table producers. This is not a full Alembic lineage or observed catalog proof.

## Deferred technical proof

- Execute the new source cases after source integration, including PostgreSQL competing connections and actual SQLite driver locking/rollback.
- Exercise real H2 capture/selected-PDF/physical-menu fixtures, terminal evidence and OCR output-wait completion; preserve all original quantity/recognizer parity assertions.
- Existing legacy fixtures in `test_uploaded_pdf_recovery_flow.py`, `test_uploaded_pdf_worker.py`, `test_ingest.py` and `test_ocr_evidence_service.py` still contain job-ID-only completion or whole-worker mocks / simplified H2 boundaries. They are not runtime evidence for this contract. Their normal fixtures need original lease/source receipt construction, and the old business-error `retry_wait` expectation must become the specified terminal stop, before consolidated regression execution. No old assertion is silently weakened here.
- Verify completed-ingest/upload ACK interruption, exact predecessor recovery and manual force/retry races through actual API entrances.
- Durable enqueue intent/outbox/provider delivery identity, independent transport drain and external deployment configuration remain separate approved J1 work. Existing threads/Celery are not claimed as a durable delivery guarantee.

Only source reads, apply_patch, scoped staging and `git diff --check` hygiene are performed. No runtime, tests, build, lint, install, DB/network access, commit, merge, push or deploy is performed.
