# H2 transitive captured inputs, source-only

Worktree: hospital-h2-revisions. Branch: codex/modernization-h2-workflow-revisions-20261006.
Base: d3d39bf3198f3ba631c82c7c8bd396323dcd2484.
Previous staged freeze supplied by reviewer: c56ed9c62e9c7d185c8f23ddd00e454fa06aea49ca43e84a3f047e8fb05a9fc5.
Existing staged implementation/test changes are retained. No integration worktree edits.

## Production caller inventory

1. API async proposal -> start_sheet_auto_edit_job -> ORM finalizer -> run_sheet_auto_edit_job -> propose_sheet_auto_edit -> owned terminal metadata patch. Reservation stores start_input and predicted own revision before its single finalizer flush; no metadata mutation follows that flush. The runner consumes exactly this accepted revision. Sync proposal retains its required expected revision and start fence.
2. Quad GET -> source receipt -> save_quad_review_decision -> saved workflow quad -> _build_live_hakodate_manifest_item -> _normalize_operator_quad_override. Both adoption and native consumption compare selected PDF, captured full config digest and order facility/week identity. A subsequent own workflow revision does not invalidate otherwise identical quad provenance; a changed PDF/config/input does. All three frontend selected-document setters retire quad review/points. No old receipt is backfilled.
3. apply_submitted_ocr_sheet -> apply_latest_draft -> candidate builder -> _materialize_confirmed_lines_from_candidate. Confirm's _build_confirm_materialization_candidate_from_draft captures its own input and uses captured order values. Saved-sheet workflow read/materialization -> _build_materialization_candidate_for_saved_sheet -> candidate builder; workflow finalizer rechecks full config. Output-builder reconstruction passes one captured config through its builder closure. All four production candidate-builder callers now supply full config/policy. Both direct row mapping and structured parsing consume that policy. Atomic OrderLine adoption checks full source and synchronous input immediately before deletion/insertion. Semantic empty results stop rather than switching to generic interpretation.
4. Normal ingest/recovery -> map_own_ingest_result -> configured extract_fax_data -> own recognizer evidence -> autoLLM/reparse _resolve_reparse_llm_pdf_bytes. Nonpipeline OpenAI/Gemini evidence declares the actual supplied verified original input, while preserving configured provider/raw rows/quantities and auxiliary pipeline separately. This is a producer-owned declaration, not a missing-artifact fallback or auxiliary OCR adoption.
5. run_ocr_pipeline -> generation-pinned full-config sidecar -> PDF metadata receipt -> main handler _load_configured_input -> _run_template_classification -> captured load_template_config / authored_templates matcher -> ROI extraction. Page correction passes the same authored map through its matcher. Metadata template identity is the exact template_id in the captured JSON; explicit requested IDs are validated against that template. Producer output echoes the verified full-config digest; record_own_pipeline_delivery and validate_own_pipeline_result bind canonical callback/recovery output to it. The canonical handler cannot query Firestore for the template. Independent matcher/ROI diagnostic invocations without captured config retain their existing diagnostic source but cannot satisfy the owned result receipt gate.

The real ORM source case exposed an existing nonexistent OrderDocument.order_id access. Selected-PDF ownership now uses the actual Order.current_document_id plus URI/message relationship, or exactly one linked OrderVersion. No fabricated foreign-key attribute, latest row, or first candidate is used.

## Focused source cases

- test_h2_transitive_captured_inputs.py: actual reservation/committed mapper revision and real runner with formal migration fixture; real source fence is not mocked. Same PDF gives own quantity-zero proposal; replaced bytes yield failed/no provider/no result. Quad same source versus URI/digest replacement; captured materialization policy and changed-source adoption rejection; actual full-config sidecar producer preserves zero/null and actual upload generation, rejects absent generation.
- test_ingest_recognizer_input_contract.py: existing actual OpenAI/Gemini mapper and evidence cases retain all quantity assertions, and now feed their own declared input into the real reparse PDF consumer. Auxiliary quantity is not adopted.
- ocr_pipeline/tests/test_main.py: actual captured template loader/classifier/ROI crop and generation-pinned sidecar consumer, wrong digest rejection. Removed the ROI module stub so these assertions execute the actual loader and crop, not a substitute implementation.

## Validation and residuals

Only source reads, apply_patch, git diff --check and staging are performed. No runtime, pytest, database, OCR, network, build, lint, typecheck, install, commit, merge, push or deploy.
The focused cases are source definitions, not passing-test evidence. Real PostgreSQL concurrency, complete pipeline rendering/recognition, UI execution and regression runs remain unverified.
The 193 legacy normal-caller revision migrations and fallback-expectation fixture updates remain explicitly deferred to the technical verification phase. They are neither omitted nor claimed successful.
This document covers these five reviewed transitive source defects, not overall H2 completion or an all-code audit.
