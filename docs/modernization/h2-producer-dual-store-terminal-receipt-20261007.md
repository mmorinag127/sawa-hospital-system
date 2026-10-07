# H2 producer dual-store terminal receipt

Source-only WT: hospital-h2-revisions.
Named branch: codex/modernization-h2-workflow-revisions-20261006.
Base: d3d39bf3198f3ba631c82c7c8bd396323dcd2484.
All prior staged changes are retained; no other worktree is modified.

## Failure class and common invariant

Dual-store terminal receipt partial persistence acknowledged as complete.
FAILED and DONE use the same terminal persistence/repair gate. HTTP completed acknowledgement requires the exact own source, immutable result digest, Firestore terminal receipt and GCS terminal result to agree. Existence of a job or a default status is not completion evidence.

## Actual caller/write inventory

- backend run_ocr_pipeline transport producer emits execution_owner from its already-captured workflow or nonworkflow execution, with explicit nullable attempt/revision/execution fields. It uses the same owner selection as the existing delivery receipt; no current job token lookup.
- main handler creates the generation-specific Firestore claim with atomic create, not overwrite/set. Normal DONE and exception FAILED call _complete_terminal. Terminal persistence errors are not recast as recognition failures or written over a stored DONE intent.
- _terminal_source verifies actual generation-pinned input bytes, original selected-PDF receipt, exact generation-pinned full configured JSON receipt/content, backend execution ownership and the output identity. It is captured before recognition and compared before terminal persistence and before completed acknowledgement.
- _persist_terminal_intent first saves the entire immutable own result/source/digest to the deterministic terminal-intent object with if_generation_match=0. A pre-existing intent must match exactly. No quantities, nulls, unknown keys or raw recognizer fields are reconstructed from another result.
- Firestore updates require the existing exact execution claim and terminal receipt plus last_update_time write preconditions. The intent receipt records the requested terminal status while transport persistence is unfinished. Firestore terminal status/receipt is committed before publishing the final GCS result. A failed Firestore write cannot publish a newly successful JSON result.
- The GCS output, if present, must belong to the same job/input/generation/output. A conflicting terminal digest is rejected, not overwritten. Both stores are reread/compared and original source is reverified before completion ack. A complete duplicate does not rewrite result/status.
- Existing-job redelivery and atomic-create races use _duplicate_outcome -> _replay_terminal -> the same persistence gate. Repair uses only immutable own intent, never recognizer/provider/another business attempt. GCS-success/Firestore-incomplete and Firestore-success/GCS-incomplete states can be completed from that intent. Missing intent cannot be reconstructed from ordinary output JSON or Firestore status.
- A locally proven still-active owner can return running HTTP 202. An inactive/unknown existing job without terminal intent returns needs_inspection HTTP 409. Receipt/input/generation/config/owner conflicts likewise return needs_inspection without completed acknowledgement or OCR. Transport store failures propagate without successful ack and can redeliver for transport repair only.
- HTTP terminal ack contains source/status/result_digest receipt. Backend validate_own_pipeline_result also verifies the returned original workflow attempt/revision or nonworkflow execution identity against the existing own delivery receipt. Existing mandatory-stage failure, pending, source SHA/config/generation and actual-write fences remain in place.

No mandatory-stage failure is changed to success. No new provider, quantities, retries, date policy, recognition algorithm or business attempt is introduced. Independent legacy terminal records without immutable own intent remain explicit inspection stops, not fabricated repair.

## Focused source cases (not executed)

- test_main.test_handler_required_stage_failures_are_owned_terminal_not_done now uses actual handler, actual source/config verification and actual common persistence functions with versioned storage/Firestore transport fixtures. Original failed/no-recognizer/no-done/generation assertions are retained and terminal digest equality is added.
- test_terminal_persistence covers both FAILED and DONE: Firestore preparation failure, GCS output failure, durable GCS-only terminal fixture with incomplete Firestore, post-GCS Firestore verification failure, successful replay, duplicate complete without new writes, immutable-intent first-store failure, wrong generation/PDF/config/attempt/result/Firestore receipt/output owner, genuine active duplicate, unknown inactive duplicate, atomic-create race and owner switch between read/write precondition.
- The transport fixture stores actual JSON, preserves immutable source snapshots, simulates generation/precondition failures and injects outages. Receipt validation, digesting, ownership comparison, persistence decisions and handler redelivery are not mocked to always succeed. Redelivery recognizer/classifier calls explicitly fail the source cases.
- Existing backend owned pipeline gate source fixture now carries the actual captured execution_owner rather than omitting the new producer contract.

## Changed files

ocr_pipeline/app/main.py; backend/src/services/ocr_pipeline_service.py; backend/src/services/ocr_job_service.py; ocr_pipeline/tests/test_main.py; ocr_pipeline/tests/terminal_store_fakes.py; ocr_pipeline/tests/test_terminal_persistence.py; backend/tests/unit/test_h2_transitive_captured_inputs.py; this audit document.

## Verification and residuals

Only source reading, apply_patch, git diff --check and staging are performed. No runtime/tests/build/typecheck/lint/install/network/DB/commit/merge/push/deploy.
Source cases are unexecuted definitions, not runtime proof. Real Firestore/GCS precondition behavior, concurrent redelivery across instances, SDK integration, callback/operator regression and fault injection remain unverified technical checks. A remote/inactive job without sufficient own intent deliberately needs inspection; it is not treated as a completed or waiting OCR by inference.
The 193 legacy fixture migrations remain deferred and preserved. This freeze covers the reviewed producer failure class and its necessary producer/consumer receipt contract, not all staged files, all code or full H2 completion.
