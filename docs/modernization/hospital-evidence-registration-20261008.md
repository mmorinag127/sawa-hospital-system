# Hospital evidence registration-only fixtures

## Frozen source scope

- Base: `42db2edd7f60377aae657ff0a80e910e9ea2b009`.
- Branch: `codex/modernization-hospital-evidence-registration-20261008`.
- Changes: this document and `backend/tests/integration/test_ocr_evidence_service.py` only; unstaged, no commit.

## Exact six migrations

Each case replaces its own `_seed_order` call with shared `registration_only_order`, retaining its original message ID. Existing cache payloads and assertions are unchanged. The five artificial active-template attachments in these cases are removed; registration materializes the canonical projection instead.

1. `test_save_order_ocr_cache_does_not_create_legacy_mirror_for_hakodate_payload`.
2. `test_get_evidence_endpoint_does_not_backfill_from_cache_when_run_missing`.
3. `test_get_latest_ocr_evidence_run_default_does_not_backfill_from_cache`.
4. `test_legacy_cache_backfill_option_is_disabled`.
5. `test_legacy_ocr_output_fallback_options_are_disabled`.
6. `test_get_ocr_output_endpoint_does_not_promote_cache_only_payload_to_current`.

The fixture uses existing `authored_sources`, `configure_delivery`, `reserve_uploaded_original`, and `register_authored_original`. Facility `FAC00001`, breakfast, date `2026-03-22`, and received timestamp `2026-03-22 09:00:00` retain the old authored fixture. Selected week `2026-03@2026-03-22~2026-03-28` is copied from the existing migrated inline case, not inferred from a live FAX. Real upload creation, reservation receipt, and registration remain in the definition. Added assertions check receipt identity, saved canonical projection existence, and absent evidence runs. The provider stub fails if OCR is invoked; the fixture does not process a delivery or invoke OCR.

No owner, evidence, or quantity row is manually inserted by this fixture. The old `_seed_order` quantity 3 is not a quantity-3 OCR result from the new fixture. Cache quantities remain original authored payload values; neither those payloads nor synthetic source registration prove real FAX or quantity parity. No active DB-template fallback is introduced. Date behavior and fixture clocks are not repaired or adjusted.

## Eight Unresolved Cases Unchanged

- `test_persist_evidence_run_dedupes_identical_payload_digest`.
- `test_persist_evidence_run_does_not_dedupe_different_record_identity_for_same_digest`.
- `test_persist_evidence_run_blocks_without_template_version`.
- `test_get_document_does_not_use_cache_only_ocr_artifact_as_current_source`.
- `test_legacy_draft_sheet_endpoint_is_disabled_after_evidence_persistence`.
- `test_get_draft_sheet_endpoint_prefers_semantic_shell_over_raw_evidence_when_recovery_warning_remains`.
- `test_classify_evidence_payload_accepts_hakodate_live_artifacts`.
- `test_persist_evidence_run_uses_resolved_template_registry_grid_metadata_when_classifier_differs`.

These include seven remaining old `_seed_order` cases and one classification positive. All four classification cases, the already-passing migrated inline case, and the shared `_seed_order` implementation remain unchanged. No successful repeat of the six changed cases is inferred.

## Verification Limits

Source inspection and Git diff/hash commands only. No tests, builds, runtime imports, DB execution, network, browser, staging, commit, push, or deployment occurred. Original 404, disabled-fallback, evidence-absence, and cache assertions are retained, not reported as passing. Parent consolidated technical verification remains pending; source freeze is not runtime, quantity, or operator-flow proof.
