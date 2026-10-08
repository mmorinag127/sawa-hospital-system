# Authored Hospital Diet Fixture

Base: `4f216349546d8e26283c457bcad7be1daee4307d`.
Branch: `codex/modernization-hospital-diet-fixture-20261008`.
Files: backend/tests/helpers/ingest_original.py and this document only.

Failure class: authored_sources' physical monthly row omitted explicit diet identity despite the same synthetic facility explicitly declaring qty.regular_2f / diet_type regular / area 2F. Shared source consumer order_service._build_position_menu_entries rejects the missing normalized diet with physical_menu_diet_identity_required before the provider. The fixture invariant is that the authored monthly row records the explicitly authored regular diet, not a production default or inferred mapping.

The only helper change adds diet_type="regular" to the existing MonthlyMenuEntry. Original authored PDF construction/bytes procedure, digest, date/week, Menu A, slot 0, facility_override, JSON version and area 2F, quantity inputs, registration/reservation/delivery/receipt paths remain unchanged. All test files and assertions are untouched. No production fallback or canonical business data is added.

Historical source baseline: hospital-main/tmp/lineage-recovery-parent-20261008/logs/tests.log records 45 passed / 5 failed, no skips/errors. Failed selectors under tests/integration/test_uploaded_pdf_recovery_flow.py:
- test_owned_delivery_marks_completed_and_links_entities
- test_owned_runner_uses_canonical_order_ocr_job_id
- test_owned_delivery_business_failure_is_terminal_after_stale_lease
- test_owned_first_pass_finalizes_without_implicit_latest_evidence_adoption
- test_owned_first_pass_duplicate_keeps_evidence_and_original_receipt

The terminal failure case explicitly reported physical_menu_diet_identity_required and no provider calls. Other four share the fixture/capture path; their exact persisted errors are not separately claimed observed.

Read-only neighbor review: tests/unit/test_h2_geometry_physical_identity.py::test_normal_only_dated_entry_keeps_own_values preserves regular metadata including 0/null, rejects missing soft variant, then removes diet_type and requires physical_menu_diet_identity_required. tests/unit/test_h2_geometry_physical_identity.py::test_same_slot_variants_survive_digest_and_json_roundtrip duplicates its first row and requires physical_menu_row_duplicate; these definitions and guards are unchanged. Adjacent worker and evidence helper consumers remain candidates for parent regression, not new passing claims.

Applicable AGENTS, helper and neighboring source definitions were read. Manual apply_patch/diff/hash/staging only; no tests, runtime imports, generation, DB access, commit, integration, push or deploy. Source packet completion is not full modernization or operator/PDF recognition acceptance. Parent monitor/commit/integrated tests remain pending.

Helper SHA256: `3f2e28f1c0819172afb582b1450596389153981be6f1a2f8e68ce07aad1c8718`.
