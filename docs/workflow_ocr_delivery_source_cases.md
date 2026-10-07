# J1 Source Case Definitions

Status: authored, not executed. These definitions target the real caller and transaction paths.
Technical verification is required before technical acceptance / stg cutover, not before source integration.
`backend/tests/unit/test_workflow_ocr_delivery_runner_contract.py` contains executable unit case
definitions (not executed). The PostgreSQL / file-backed SQLite matrix below is a prose plan for
real-transaction verification, not an authored executable integration suite or completed proof.
No Cloud resource, uploaded live order, or runtime state was changed by authoring these cases.

## Shared Setup

Use an isolated database and the fixture conventions in
`backend/tests/integration/test_workflow_ocr_enqueue_transaction.py`.
Create an original Order, OrderDocument, UploadedPdf with the exact content SHA256,
active FacilityTemplateVersion, confirmed workflow context and revision.
Use one explicit authored config and physical menu source. Preserve their captured JSON.
Provide explicit hospital delivery config through test environment values and a fake Cloud Tasks client.
Call the real `order_workflow_v2_service.mark_ocr_run_queued`; do not replace the transaction,
reservation service, delivery creation, generation state, or write guards with fake helpers.
Mock only external recognizer/transport/storage boundaries and facility-source reads.
Inspect committed OrderWorkflowState, OcrJob and WorkflowOcrDelivery in separate sessions.

## Queue And Real Callers

1. Call `orders.run_order_workflow_v2_ocr` with mode llm, llm_assist false and nonempty
   prompt/provider/model/preset, explicit version selection and If-Match.
   Assert one intent, original parameters byte-for-byte, selected PDF digest, captured source,
   final workflow revision and original job/attempt identity. Assert one Cloud Tasks dispatch
   and zero BackgroundTasks callbacks. Repeat evidence-only mode and current-document selection
   without an OrderVersion; the original None selected-document argument must be preserved.
2. Inject an exception from `create_intent_in_session` after reservation and final revision flush.
   Assert rollback of job reservation, workflow revision/downstream deletion and intent together.
   Repeat with missing config, invalid boolean revision, empty source/request, missing llm_assist,
   mismatched uploaded-PDF digest and selected-version mutation. No runner or task may be created.
3. Save each actual quad/header/row review decision through its PUT endpoint with original
   source receipt and If-Match. Assert no immediate OCR launch. Then call the same OCR-runs
   endpoint with the new revision and document choice; assert exactly one durable intent
   and that the captured source retains the saved geometry. A separate correction runner is forbidden.
4. Repeat ordinary `ocr_reparse`, `llm_reparse`, `ocr_rerun` request modes at the
   real queue boundary. Assert exact mode dispatch and no flag coercion or mode inference.
   Unknown modes stop before enqueue; no inline runner is used when config is absent.

## Transport And Fair Drain

5. Make create_task invoke the authenticated worker before returning. Assert PENDING can
   be claimed and the later DB dispatch ACK cannot overwrite CLAIMED/ACKNOWLEDGED.
6. Exact get_task NotFound before first send: send persisted deterministic task contract.
   AlreadyExists plus exact executable task: accept only identical URL, method, OIDC audience,
   service identity, deadline and task name. AlreadyExists plus NotFound tombstone: commit
   generation N+1 for the same original business attempt under execution exclusion.
   Any permission/network/unknown lookup or different task contract: retain generation and record error.
7. For DISPATCHED exact missing task, renew only while original job/workflow/source remain eligible.
   Inspect claim-expired entries too. Terminal, failed, cancelled and superseded jobs never create
   replacement tasks. A changed snapshot between lookup and renewal must reject the renewal.
8. Drain with limit 1 over three intents while the oldest repeatedly fails exact lookup.
   Assert visit reservations commit before transport and all three receive visits; persistent
   transport failure cannot starve the others or manufacture a new business attempt.

## Claim, Lease And Actual Adoption

9. Two independent processes deliver the same generation simultaneously through
   `worker.process_workflow_ocr_delivery`. Synchronize at claim and recognizer entry.
   Assert exactly one runner invocation and one token; active duplicate receives transport ACK.
   Repeat using PostgreSQL FOR UPDATE and SQLite BEGIN IMMEDIATE.
10. Hold worker A inside its runner beyond process lease. Worker B and independent drain
    must not enter OCR or renew generation while A retains execution_mutex.
    A's actual `_complete_ocr_rerun_in_session` must refuse writes after expiry.
    Terminate A, confirm lock release, renew exact missing transport and claim generation N+1.
    Reintroduce A's original token into actual workflow write guard, cache/evidence/terminal paths:
    all writes and old ACK must refuse. B retains original business attempt/source/parameters.
11. Deliver old generation N after N+1: ACK without runner. Unknown future generation,
    nonexistent intent, missing/boolean/string generation: stop without OCR, renewal or latest-token adoption.
12. Call actual `orders._run_reparse_background` with returned business error and inject
    an exception/refusal from actual `order_service._complete_ocr_rerun_in_session`.
    Assert worker returns error and leaves unacknowledged delivery for recovery.
    Empty normal return or a runner-local receipt not present in the DB also cannot ACK.
13. Persist normal done and normal hard-failed terminal receipts through the actual terminal
    transaction. Assert exact owned receipt permits transport ACK; hard-failed job remains failed,
    is excluded from drain, and duplicate delivery invokes no OCR.

## Durable Async Handoff

14. Exercise real `order_service._mark_reparse_job_awaiting_output` during the actual runner.
    Inspect the committed pending_receipt, pipeline_delivery_receipt, selected PDF/input/output
    and delivery handoff_receipt. Owner generation/token must be bound by the before_flush hook
    within that same original transaction. Launcher ACK is transport handoff, not business done.
15. After launcher ACK, invoke existing `ingest_worker.run_ocr_job_recovery_once`,
    which reads the original pending_receipt and calls `reconcile_pending_workflow_reparse`.
    Assert actual later terminal persistence succeeds under saved handoff ownership without
    injecting latest delivery claim context. Compare page, order endpoint, ocr-sheet, draft-sheet
    and workflow-state after technical verification permits runtime checks.
16. Fail the handoff flush/commit: assert pending receipt and handoff owner both roll back and
    launcher delivery is unacknowledged. Tamper pending input/output/PDF/request or old owner token:
    async write guard rejects. A superseding business attempt rejects the old handoff.
17. Simulate process exit immediately after committed handoff and before launcher ACK.
    Drain recognizes only the exact durable saved handoff and marks transport acknowledged;
    it must not relaunch OCR. Reconciliation continues from that receipt.

## Authentication And Formal Installation

18. Worker and drain independently reject absent bearer, invalid signature, wrong audience,
    wrong email/subject and unverified email. AUTH_DISABLED cannot bypass either endpoint.
19. Parent integrates 0032 before 0033, observes both dialect catalogs, and generates actual
    catalog contracts. require_formal_head must reject absent/unverified 0033 catalogs.
    Do not fabricate fingerprints or bypass the formal gate.
