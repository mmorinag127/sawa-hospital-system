# J1 Retry Fixture Source Migration

- Base: 38e096557266cff1abe83902ee980ea2c2b91d26.
- Branch: codex/modernization-j1-retry-fixtures-20261008.
- Changed files: backend/tests/contract/test_ingest.py and this document only.
- Test file total/nonblank: 965/849 -> 981/859.
- Test source SHA256: b5e5771c91adb68663159a79c442f41ea7cc690dd136a1bf176c9cf161ea7453.

## Exact Preserved Expectations

Only test_retry_uploaded_pdf_requeues_row, test_retry_uploaded_pdf_rejects_only_healthy_completed_rows and test_retry_uploaded_pdf_allows_completed_row_with_missing_week changed, plus minimal imports/two local helpers. Other test definitions remain unchanged.

All 17 original assertion statements remain verbatim: requeue 7, healthy/processing 6, missing-week 4. HTTP URLs/headers, 202/409, accepted true, uploaded IDs, pending/current_stage uploaded, completed detail uploaded_pdf_already_completed, one dispatch ID and one restart message ID remain. No output expectation was weakened or replaced.

## Fixture Reconnection

Each target uses existing isolated_formal_schema and configure_delivery; no shared production/test helper changed. authored_sources supplies an explicit synthetic FAC001 component master/menu/date 2026-02-15, physical generated component bytes and matching digest. Existing week 2026-02@2026-02-15~2026-02-21 is retained. No actual source PDF was uploaded or processed during this task.

Healthy completed is defined through register_authored_original, authored_pipeline_provider and process_uploaded_original, with business_done/completed assertions and actual persisted order/document linkage. The processing case uses actual claim_uploaded_pdf and asserts its real lease token; retry must clear the token without resetting attempt_count.

The missing-week fixture no longer attempts normal upload with absent required week or calls mark_uploaded_pdf_completed(id) without a terminal owner. It defines a valid registered completed source first, verifies its document linkage and original week, then clears ONLY persisted order.week_code to represent legacy corruption. The fixture filename changes from misleading completed-missing-week_0405_.pdf to authored-component_20260215.pdf. The fabricated FAC001/date/Menu A/quantity 2 provider rows are explicit component data, not recognized PDF/operator/live proof. Original tests had no quantity assertions or authored quantity baseline; no production quantity or original assertion was changed.

The old process_uploaded_pdf_job mock is moved to actual enqueue_uploaded_pdf_async entry while retaining processed_uploaded_pdf_ids and its original one-dispatch assertions. That variable records scheduling only, NOT processed/runtime proof. restart_ingest_job is wrapped at the actual service module imported by requeue_uploaded_pdf; original job_id/write_session are forwarded, the active transaction is asserted, and the real return value is preserved. There is no fake True/commit/ownership receipt.

Added source payload equality/digest preservation, changed business generation, preserved monotonic total attempts, reset generation_attempts/claim_id and new delivery identity assertions. Initial requeue in the first test remains; observations begin afterward just as original expectations target the HTTP retry.

## Source-Only Commands and Deferred Proof

Executed: read-only cat/sed/rg/git status/rev-parse/diff, shasum and awk; manual apply_patch; exact two-file git add; staged patch hash and unstaged inspection. Source search initially found no isolated_formal_schema in backend/tests; backend/conftest.py:55 provides it. Searches for absent top-level conftest paths reported missing files. No tests, runtime imports, DB/API/network/browser/server/generation/install/build/commit/push/deploy occurred.

Definitions are unexecuted. Formal isolated migration, actual receipt/terminal/lease ownership, rollback, HTTP dispatch/restart behavior, old/new generation invariants, all original assertions and neighboring lifecycle regression require later consolidated execution. No passing-test, processed-PDF, runtime/business/root/whole completion claim. Two-file source freeze is for parent review, not self-approval. Patch/doc hashes are reported externally.
