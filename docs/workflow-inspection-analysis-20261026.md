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
