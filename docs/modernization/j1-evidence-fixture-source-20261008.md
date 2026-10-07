# J1 Evidence Fixture Source Migration

Source definitions only; no test, runtime, generated artifact or live acceptance result.
Base: `38e096557266cff1abe83902ee980ea2c2b91d26`.
Branch: `codex/modernization-j1-evidence-fixture-20261008`.
Worktree: `/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-j1-evidence-fixture`.

## Failure Class And Boundary

The old direct-inline fixture lacked the original execution lease, captured canonical source and registration receipt. A later active DB template cannot establish original evidence ownership. The intended evidence producer is reached through actual upload reservation, canonical registration and owned delivery/nonworkflow execution, not a mocked whole worker or job-ID completion.

Only `test_process_ingest_inline_persists_evidence_run_from_pipeline_output` and its local imports/signature are changed in the integration file. All unrelated tests, `_sample_payload`, `_attach_active_template_version`, module imports and helpers there are untouched. Production files are unchanged.

The test definition now follows the migrated uploaded-worker positive sequence:
configure_delivery -> authored_sources -> actual create_uploaded_pdf_from_upload -> reserve_uploaded_original -> register_authored_original -> authored_pipeline_provider -> process_uploaded_original -> original order/evidence assertions.
The actual upload service already reserves an original receipt; the explicit reserve helper uses the same production receipt API rather than inventing a token. Delivery resolves original ID/generation/input_digest and invokes the actual owned runner. Registration materializes the canonical JSON projection before execution. The external OCR provider double calls the actual own-pipeline delivery receipt producer; no owned guard is patched out.

## Explicit Synthetic Inputs

| Input | Value |
|---|---|
| Message | msg-evidence-inline-ingest |
| Facility | FAC00001 |
| Menu date | 2026-03-22 |
| Received timestamp | 2026-03-22 10:00:00 |
| Authored component week | 2026-03@2026-03-22~2026-03-28 |
| Monthly source daypart | breakfast |
| OCR double raw row | ["03/22", "朝", "Menu A", "7"] |
| Physical menu slot | 0 |
| Quantity column | regular / 2F |
| skip_ocr | false |

The old month-only week is replaced by this explicitly authored synthetic component week, not a claim that a customer document proved that week. Existing date, facility, message, timestamp, menu name, raw morning spelling and quantity7 are retained. No effective-date business policy is changed: 2026-03-22 is explicit fixture configuration input.

`authored_sources` previously authored only lunch. A backwards-compatible keyword `daypart="lunch"` now supplies MonthlyMenuEntry.daypart; the target test explicitly authors breakfast rather than changing the original morning row. Existing callers retain their original lunch behavior. These two helper lines are the entire helper change.

The helper's PDF is an empty authored component page under tmp_path, with SHA256 calculated from its actual bytes when the test runs. It is NOT a verified FAX, does not contain facility/week/quantity evidence and is not a manual test target. JSON/monthly entries and external-provider raw rows are the explicit synthetic sources. The original dummy URI and "%PDF-1.4 test" bytes cannot establish a real customer identity; that identity remains unproved and is not reconstructed or fabricated. No real OCR/provider/operator flow is claimed.

## Exact Retained Original Assertions

```python
assert isinstance(order, dict)
assert isinstance(evidence, dict)
assert evidence["producer_version"] == "ocr_pipeline_ingest"
assert evidence["capabilities_json"]["step2_view_ready"] is True
```

All four remain verbatim. Raw quantity remains "7" in the provider row; an additional actual persisted OrderLine quantity_original == 7 assertion is defined. Reservation ID is also asserted against the original message. No assertions are removed, weakened or replaced by mocks. No direct inline call, late active-template override, byte-reader/extraction/source-config/skip guard mock or auto-reparse bypass remains in this test.

## Counts And Source Hashes

| File | Base total/nonblank | Current total/nonblank |
|---|---:|---:|
| backend/tests/integration/test_ocr_evidence_service.py | 615/533 | 609/529 |
| backend/tests/helpers/ingest_original.py | 145/125 | 145/125 |
| docs/modernization/j1-evidence-fixture-source-20261008.md | absent | this document |

```text
ad348431a86c898ab9ceb5c79eb458359124944927a21aacfbe321773a0566fc  backend/tests/integration/test_ocr_evidence_service.py
f7cec4ce9d67b22fb1dbbc3912bc8100ffffdda5fcc315f5b9ef8e1f1d71eada  backend/tests/helpers/ingest_original.py
```

Only source reads, git status/rev-parse/diff/show, awk line census, shasum and scoped git add/patch hashing were executed. Edits used apply_patch. No tests/build/tsc/lint/runtime imports/DB/API/network/server/browser/generation/install/commit/push/deploy/other-chat action; no other worktree writes. No generated source PDF/JSON or execution receipt exists from this task: those are deferred test runtime products, not observed evidence.

Parent source monitoring/review/integration, formal isolated-DB test execution, own-lease/receipt/evidence observations and any required real-PDF/operator/live acceptance remain pending. Existing unrelated legacy tests and business holds are not resolved by this packet. Document and cached binary patch SHA256 are reported externally, not self-referentially embedded.
