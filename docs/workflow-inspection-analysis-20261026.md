# Workflow inspection analysis

## Observed staging behavior

Existing worker-stg-00900-wdl logs on 2026-10-26 show order lookup taking
0.009-0.024 seconds, with response_serialization taking 50.7-55.0 seconds.
Nested metrics are included in that phase and must not be added to it again.
No new benchmark was run.

The inspection read calls workflow projection, saved-sheet materialization
candidate construction and evidence serialization before returning. The
materialization capture previously discarded the request's evidence analysis
object, repeating pure recognition-review and assignment work even when its
inputs were unchanged.

## Change

Pass the existing operation-local analysis through menu resolution,
materialization capture, selectability and evidence serialization. Retain all
live artifact/checkpoint reads, hash validation, source validation, quantities,
error propagation and write fences. No cross-request results or storage bytes
are cached. This change does not establish that the full delay is resolved.

## Local verification

Python: /private/tmp/sawa-hospital-secure-20261024/bin/python

71 passed in the combined workflow read-receipt, menu owner-session, inspection
timing and OCR single-read tests. The added test compares resolution output and
all database tables, and proves actual selectability and serialization calls
receive the same analysis object.

Log: /private/tmp/sawa-revision-analysis-tests-20261026.log

Staging verification and final latency outcome are pending. This document is
not a claim of deployment or performance completion.

## Read/write snapshot separation

A read-only SQL query on the selected OEV9632f7d180be found 20,677,198 bytes
in diagnostic_pipeline_output and 14,275,657 bytes in _ocr_execution (JSON text
sizes, not network measurements). Projection was copying entire evidence/cache
rows into a write CAS snapshot, then constructing a complete locked execution
snapshot just to obtain configured_input.

Menu-resolution reads now retain their canonical template/source validation but
omit the write-only row snapshots and full execution capture. Write operations
retain the complete old capture and locks. A partial read proof is explicitly
marked and rejected by the write fence. Candidate output, receipt errors and
database state are compared with the full-capture path in a regression test.

The final combined run passed 315 tests, including write ownership, selected PDF
receipt failures, full/read candidate equality, and rejection through both
revision and draft persistence entry points. Log:
/private/tmp/sawa-revision-read-projection-final-tests-20261026.log.
The read input supplies no input_versions dictionary, so the shared low-level
write ownership guard rejects it before taking write locks. This also covers
write callers that do not use the order-service wrapper fence.

After applying the same read-only marker and absent write proof to imported
saved inputs, the combined eight-file run passed 355 tests. The imported-source
regression checks unchanged evidence/configuration/lines and revision-write
rejection. Final log:
/private/tmp/sawa-revision-read-projection-boundary-tests-20261026.log.

## Response projection

The history_details=false read also returned the selected result's complete
_ocr_execution body in execution_lineage, despite no frontend consumer reading
it. The initial read now includes ownership identity fields only, marked
execution_details_state=not_loaded. Full history and single-result detail reads
retain the original execution body; storage and selectability are unchanged.
The new regression persists a large execution body, compares workflow and
selectability, verifies the full detail, and checks that stored bytes remain.

Final combined nine-file run: 367 passed. Log:
/private/tmp/sawa-revision-read-projection-compact-tests-20261026.log.
No staging performance result is claimed.
