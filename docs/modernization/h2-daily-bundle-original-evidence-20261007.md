# Daily bundle and original nonworkflow evidence input

Source-only correction. No tests, runtime, build, OCR, database, network,
deployment, commit, merge or push was executed. Earlier staged contracts remain.

## Daily Bundle

Actual production chain: `api.outputs.download_daily_bundle` ->
`output_builder.build_daily_output_bundle` -> `_prepare_output_context_for_bundle`
-> `_prepare_output_context` -> `_accepted_output_candidate`. Each context uses
its own accepted candidate/configuration. Facility groups compare typed snapshots
of the captured facility configuration, invoice mapping, label profile and
quantity rules before appending the next order's quantities/bags. Incompatibility
escapes the per-order error manifest and reaches the existing API 400 response.
No exporter runs on that mismatch. Equal configurations retain existing grouping.

The request owns a UUID directory; the previous timestamp basenames are retained.
XLSX, ZIP and delivery intermediate files use that directory. Label workbooks and
CSV archive members use in-memory buffers. API download names, headers and shape
are unchanged. No shared accepted artifact is overwritten by a bundle request.

Both actual context-wrapper callers were read: `_weekly_weight_collect_rows` and
`build_daily_output_bundle`. All explicit options now reach the common context;
TypeError cannot trigger a second call with defaults. The daily caller propagates
TypeError rather than creating a successful partial bundle for that failure.

Unexecuted definitions use independently produced accepted bagging receipts and
the actual bundle/API chain, with external order-index/file-export adapters.
They cover mapping/profile mismatch across four formats, compatible quantities
including zero/null, identical-clock requests with different filters, overlapping
XLSX/ZIP generation, unchanged API download names, and explicit option failure.
They do not prove runtime behavior.

## Original Evidence Input

Actual capture entries read: `rerun_ocr_evidence_only` and `reparse_order` ->
`_capture_nonworkflow_order_execution` -> `capture_nonworkflow_execution`;
`ingest_worker` also invokes the same shared capture. `_order_execution_input`
already captures typed original configuration, physical menu entries, order,
document, lines, workflow, draft, cache, evidence and decisions before recognition.
`start_orders` now retains that complete snapshot beside its existing hash/summary,
not just IDs/revision/hash. The original hash binds the complete snapshot.
The selected PDF receipt and original expected cells/semantic mappings are also
captured there before recognition using the existing shared canonical target plan
and exactly those original configuration/physical entries. This requires an
explicit resolvable authored target plan; missing menu/role/period identity stops
the shared capture instead of substituting a legacy target list.

`orders` remains the explicitly advanced write-ownership boundary, not a source
substitute. `processing_start_read_input`, `processing_start_configured_input`
and `_captured_reparse_read_input` use the immutable start snapshot. Advancement
cannot substitute changed configuration or physical entries. The job ownership
guards compare the stored original start snapshots and selected PDF receipt with
the execution boundary. Missing historical snapshots fail explicitly; no backfill.

Canonical provider chain read: `_hakodate_canonical_payload_from_manifest_item`
computes `_hakodate_owned_source` from these original inputs before the batch
recognizer. That shared source derives independent cells/semantic mappings from
`capture_original_target_plan`, not returned results. Runtime receives those same
expectations. `persist_evidence_run` saves the full original input through the
same accessor for workflow and nonworkflow owners. Its existing fenced adoption
records `original_hakodate_source`; `evidence_run_is_selectable`,
`_validate_hakodate_original_execution` and `_validate_selected_hakodate_artifacts`
retain complete provenance/semantic checks. No validator relaxation was added.

The connected preprocessing/plan/evaluation/snap/runtime/payload/JSON/evidence/
selected-adoption definition now covers actual nonworkflow capture as well as
workflow capture, merged regions, recognized zero and blank/null diagnostic
outcomes. Original physical/config mutation or missing original input is rejected.
Only external observations, recognizer/storage and manifest data are adapters;
canonical payload construction and adoption checks are not replaced.
For workflow owners, the existing `reparse_read_input` expression remains intact;
nonworkflow owners use the same complete input expression with additionally
frozen target expectation/PDF fields. The common source helper checks these fields
against the original shared plan when present, without changing workflow receipts.

## Held Scope

- Human mixed-setting bundle presentation remains pending: split by authoritative
  settings versus mismatch stop as the final business policy. This change only
  blocks unsafe combination; it does not choose a split presentation/schema.
- Public multi-order override original-version transport/API binding is pending.
- Worker J1 status/retry ownership remains pending; the worker is not edited here.
- The previously identified 193 legacy case migration and all runtime validation
  remain deferred. Diagnostic bench scripts and unrelated fallback paths are not
  certified by this correction. No whole H2/function completion claim is made.
- Historical nonworkflow owners containing only start summaries need explicit
  operator re-execution; this code does not manufacture their original snapshots.
