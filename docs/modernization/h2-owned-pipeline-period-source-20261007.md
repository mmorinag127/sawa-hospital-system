# Owned pipeline results and explicit order periods

Source-only continuation of frozen staged tree c37be59d1f0a157ba0d6efdf0c05fe6a555b298f.
Branch: codex/modernization-h2-workflow-revisions-20261006.
Base: d3d39bf3198f3ba631c82c7c8bd396323dcd2484.
No runtime, tests, builds, installation, network, database, OCR, workbook,
frontend, commit, merge, push, or deployment was performed for this correction.

## Bounded production corrections

1. `ocr_pipeline/app/main.py`: handler captures the pinned input and full original
   configured/PDF/execution receipt before job creation and before recognition.
   Output namespace hashes the complete source (excluding only its derived output
   reference), not a basename or directory-insensitive suffix. The four actual
   intermediate-write calls share that source through the scoped owner context.
   Existing JSON must match the full typed source; creation uses generation zero,
   replacement uses the exact generation read, and Firestore progress updates use
   the original claim plus update-time CAS. Terminal exact-generation writes and
   final full execution-claim reread remain intact. Intermediate writes cannot
   reopen a terminal result. No recognizer replay was added.
2. All main.py asset writers share `_upload_bytes`: corrected PDF, figures,
   markdown, OCR/layout overlays, quantity-subgrid crops and overlays, combined
   overlays. Asset keys are under the original result's owner namespace. Creation
   is conditional on generation zero. Identical same-owner byte/metadata replay
   is accepted; changed bytes or owner metadata stop. Returned asset references
   pin the actual generation. Both figure-name lookups use the URI path without
   discarding the original generation-pinned URI. Figure/parser hunks from the
   separately committed 0ffa source were not copied or overwritten.
3. `_captured_order_document_selection` consumes the original workflow owner or
   explicit nonworkflow execution owner. Its actual callers are
   `_rerun_ocr_evidence_only_with_input` and `_reparse_order_with_attempt`.
   Direct capture uses `_capture_nonworkflow_order_execution` followed by
   `capture_nonworkflow_execution`; selection is fenced and matches the original
   selected version or captured order-document/PDF receipt. A missing or changed
   source stops, with no current/first-document substitution.
4. `_resolve_sheet_week_id`, `_resolve_reparse_own_week`, and
   `_resolve_current_sheet_context_week_id` no longer substitute another order's
   week, a received month, line/OCR-derived dates, or a neighboring menu month.
   Current-sheet read/refresh, weekly overwrite, OCR-sheet and recoverable-sheet entry stop on
   `week_unresolved` before source capture/menu continuation. A blocked refresh
   neither persists nor deletes the saved current-state record.
   Context blockers reach the existing review apply/confirm blocker fields.
   Explicit valid
   periods retain the existing authored month/range schema, including partial
   and cross-month ranges; no placeholder menu schema was introduced.

## Actual caller and reader scope

- Backend `run_ocr_pipeline` predicts the same full-source output namespace as
  main.py and records that reference through `record_own_pipeline_delivery`.
  The original pinned upload metadata, PDF bytes, configured digest, execution
  owner and selected PDF receipt must match before delivery is recorded. Input
  PDF and configured JSON uploads now also reject replacement via generation zero.
- Actual pipeline callers inspected: ingest_worker initial and repeat submission,
  backend polling `_wait_for_output`, pipeline handler duplicate/terminal repair,
  intermediate calls, and every main.py asset-upload call.
- `storage_service.load_bytes_from_uri` already honors generation query/argument;
  `generate_signed_url` accepts the generation; order_service `_signed_url_from_uri`
  extracts it. These readers were inspected, not executed or rewritten. Backend
  output polling now reloads and downloads with its exact observed generation.
- Workflow capture callers inspected: workflow_v2 document selection/enqueue,
  direct rerun capture and selected-document read, reparse selected-document read,
  workflow/nonworkflow original-input and PDF guard capture.
- Week callers inspected: weekly overwrite, uncached/current persisted-context
  read/refresh, OCR-sheet and recoverable-sheet construction, review-summary
  merging, reparse original week. The older
  `_load_sheet_week_hints` and `_list_latest_completed_ocr_outputs` have no
  production references in backend/src in this source search. Existing public
  definitions and legacy cases were not deleted or represented as passing.

## Source definitions, not execution evidence

- `ocr_pipeline/tests/test_owned_intermediate_assets.py`: actual source capture,
  conditional intermediate write, partial-to-terminal repair, duplicate delivery,
  foreign replacement and claim stop, full-URI same-basename separation,
  generation/execution namespace neighbors, immutable asset replay/conflict,
  historical pinned bytes after an external replacement, typed full-source stop.
  The transport fake preserves generations and metadata and enforces CAS; it is
  not an always-success upload mock.
- Existing terminal persistence definitions now derive the output path through
  the real namespace producer; original done/failed/replay assertions remain.
- `test_h2_operator_repair_revision.py`: connected workflow/nonworkflow canonical
  production case now selects the original document with the real guard; the
  nonworkflow branch has no workflow ContextVar identity. Existing actual
  preprocessing, logical plan, external recognizer adapter, canonical payload,
  JSON serialization, evidence persistence and selected-adoption checks remain.
  Zero/null, merged regions, changed source and missing original proofs remain.
- New period definitions exercise real sheet-read/refresh/weekly-write callers
  with unresolved periods and require no source/cache/state write; explicit
  partial/cross-month periods cannot be replaced by received-month or draft hints.
  Existing observed-FAX cases explicitly distinguish outside-period calendar
  blanks from missing required days; the connected actual partial-period header,
  row and preprocessing collector case remains unchanged.
- Enqueue transaction definitions now supply order_id and original revision to
  the actual guard and capture a real original file/document/PDF snapshot for
  legitimate retry completion. The replaced-PDF quad case and transitive quad
  consumer case derive the original geometry digest through its real producer.
  The previous text's broader claim did not cover the normal approved/manual
  quad adoption definition: that definition still lacked the original proof.
  The bounded correction below supplies it. No validator was weakened. The grid
  case's local Path import was corrected.

## Bounded fb568f correction

Frozen input stage: fb568f31815290eaaf44f4bd1ad2190ba6f97b6c.
Only main.py, the conditional transport fake, owned intermediate/asset source
cases, the captured-proposal source case file and this document were changed.

- `_safe_job_id` now hashes the complete typed original input source, excluding
  only the derived output reference. Slash/underscore spellings, generation,
  configured/PDF receipt and execution owner remain distinct. The original URI
  job_id and output/asset references are unchanged. The handler is the only
  production caller and the only Firestore document-key selection site found in
  the bounded backend/ocr_pipeline Python search. That single JobRef flows into
  intermediate progress/claim CAS, asset owner checks, terminal intent/write,
  final acknowledgement reread and duplicate repair. No legacy-key lookup exists.
- When the new key is absent, an existing original namespace terminal intent is
  handled through the existing full-source replay validator after conditional
  Firestore claim creation. It is not re-recognized or relabeled from another
  source. Existing output without an intent and original claim stops with
  `output_original_claim_missing`; the old punctuation-key row is not read or
  mutated. Historical unowned data is not backfilled.
- The real handler neighbor definition uses independently keyed Firestore
  transport records and explicit GCS input generations for input/a/b.pdf and
  input/a_b.pdf. It covers a terminal persistence interruption, a complete
  neighbor, alternating duplicate delivery, original intent repair when the new
  key is absent, unchanged historical row, and orphan output without an intent.
  Recognition/classification adapters reject any rerun; capture, key selection,
  intermediate/terminal ownership validators and repair are not mocked.
- The normal approved_estimate/manual_override quad definition now creates a
  declared authored configuration and original physical input through external
  data adapters, then calls actual capture_reparse_read_input,
  capture_selected_pdf_receipt and geometry_source_digest. Its source receipt
  contains the original physical_menu_entries and geometry_source_digest.
  save_quad_review_decision uses real session/order/workflow capture and PDF/
  geometry validation. Existing metadata serialization/finalization adapters
  remain; this is not a full mutation-CAS execution proof. Both original zero
  coordinate and exact stored original receipt assertions remain unchanged.

Source definitions and source search only: these cases were not run. Hygiene is
limited to git diff --check. No whole-repository or runtime success is claimed;
all held items and unverified integration work below remain held/unverified.

## Held and unverified

This is not a whole-repository audit, database inspection, execution proof, or
overall H2 completion. Historical unowned output/assets or missing original
snapshots are not backfilled. Old basename-only pipeline delivery cannot be
silently relabeled as a new owner namespace. Legacy tests/fakes (including the
previously deferred 193 migration/runtime cases) still require migration and
execution; no broad mock contract compatibility was restored. Diagnostic bench
scripts are not production proof. Integration with 0ffa figure/page/parser work
and technical verification remain pending.

Mixed-settings bundle presentation, public multiorder original-version transport,
acceptance timing and the facility-effective-date question remain held only where
dependent. J1 status/retry is separately pending. No new business decision or
quantity interpretation was made here.
