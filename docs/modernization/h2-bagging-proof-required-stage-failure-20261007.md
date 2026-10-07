# H2 bagging proof and mandatory OCR stage failure

Source-only worktree: hospital-h2-revisions.
Branch: codex/modernization-h2-workflow-revisions-20261006.
Base: d3d39bf3198f3ba631c82c7c8bd396323dcd2484.
All prior staged changes are retained. This is not overall H2 completion.

## Saved-sheet adoption caller audit

- run_bagging -> _build_materialization_candidate_for_saved_sheet captures synchronous input in the caller's Session before candidate computation. The builder consumes exactly that full config/policy and includes its start snapshot. Saved-sheet read projection uses this same helper; direct apply and confirm retain their own captured snapshots. Output reconstruction remains a read candidate with captured config, not a final-confirm adoption receipt.
- run_bagging removes the ephemeral full snapshot from the immutable candidate before artifact digesting/storage. Workflow metadata holds one explicit bagging_input_proof: artifact ID, candidate digest, original input snapshot, and owned revision. Duplicate evidence/draft payload copies are omitted, while exact input_versions remain for the shared comparison.
- _finalize_workflow_mutation advances this proof only from its exact previous owned revision and after the real synchronous fence checks original order/evidence/draft/cache/lines/PDF/config. Pending workflow values are retained across guard refresh, and no metadata mutation follows the final flush. Different source selection/input is rejected, not blessed with a new snapshot.
- final_confirm requires the explicit artifact ID/digest and its owned revision. It restores only serialization types and the proven own revision; it never captures current inputs on the final click. It copies the immutable artifact candidate and supplies the original proof to the mandatory actual OrderLine adoption fence. Missing proof stops before writes. The proof is retired after successful adoption, while the confirmed immutable snapshot/artifacts remain historical output.
- Existing anomaly/output review commands use the common finalizer and therefore may advance an unchanged artifact's proof, not its original input values. Changed PDF/config/draft/lines/evidence cannot use that advance to launder an old result.

## Mandatory OCR stage failure caller audit

Failure class: mandatory OCR stage failure accepted as quantity success.
Invariant: no canonical result, mapper, evidence selection or adoption after a required stage failed.

- Producer handler: enabled classification must resolve its captured template; enabled ROI requires that context and an actual result; enabled correction must return actual bytes/summary. Exceptions are terminal MandatoryOcrStageFailed outcomes, not warnings followed by done or raw substitution. Authored enabled template warp cannot fall through to another correction when unresolved.
- Explicit disabled stages retain disabled declarations. Successful correction, including proven no-op identity, retains the existing recognition-input declaration and preprocessing algorithms. Quantities are not synthesized or inferred by this change.
- Successful owned output includes canonical_stages and canonical_adoption_allowed. Required failure writes the real generation-bound terminal JSON and Firestore failed job. A persisted mandatory/source-validation failure returns HTTP 200 with failed outcome, not success and not business retry. Failure to persist that outcome is not silently acknowledged.
- run_ocr_pipeline validates the actual output before marking pipeline success. validate_own_pipeline_result preserves existing job/input/output/generation/SHA/config receipt checks, rejects failed outcomes and done outputs without successful/explicit-disabled stage proof.
- extract_fax_data, map_own_ingest_result, direct reparse/rerun and recovery loaders already use that shared result validator before reading structured/table_raw quantities. Recovery propagates failed own output into its existing own failure completion instead of resubmission. Firstpass lineage errors bypass inner OCR retries and return the actual atomic failure result, not another recognizer attempt or old lines. Nonworkflow recovery failure reconstruction carries original captured facility/week.
- evidence_run_is_selectable explicitly rejects diagnostic payloads declaring canonical_adoption_allowed=false. Existing independent ready/rejected manual review semantics are not replaced by a new acceptance policy.
- Pending is not terminal: the shared validator and recovery loader retain genuine running/waiting output behavior. No missing evidence is fabricated and no timeout is guessed.

## Focused source definitions

- test_real_bagging_artifact_confirm_uses_original_inputs: formal migration fixture, real candidate mapper/ORM artifacts/finalizer/shared fences/OrderLine adoption; own revision advance; zero unchanged; replaced PDF/config/draft and missing proof deny writes. Only orthogonal bag allocation/menu resolution is isolated.
- test_handler_required_stage_failures_are_owned_terminal_not_done: actual handler, actual captured-config loader and terminal JSON writer with storage/transport substitutes; classification/ROI/correction computation failures never invoke recognizer or emit done. Assertions retain generation-bound failed output.
- test_actual_owned_pipeline_gate_and_recovery_block_failed_stages: actual ORM job/start capture/delivery receipt/shared validator/recovery loader; failed and warning-done block, genuine pending waits, valid zero remains zero. Provenance and persistence gates are not mocked to always pass.

## Validation and residuals

Source reads, apply_patch, git diff --check and staging only. No runtime/tests/build/typecheck/lint/install/network/DB/solver/commit/merge/push/deploy.
Focused cases are unexecuted source definitions. Full producer, PostgreSQL concurrency, complete operator flow and integration behavior are unverified; no runtime success is claimed.
The 193 legacy unit caller/expectation migrations remain deferred, preserved, and not claimed successful. This audit is limited to the two reviewed common failure classes and their actual callers, not all code or all staged changes.
