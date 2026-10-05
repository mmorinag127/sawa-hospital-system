# C0 LLM Fixture Correction - FAC00010 Final Synthetic Record

Source: `58f41b976c8d52a6836a6c66e6d70bd5c33b8cbb`, with an uncommitted test-only diff. Test-file SHA256: `051fbdbfd19d638dba3701042dd7cda2bb41f3dd5890f1a7a7421b7071ecaa2a` for `backend/tests/integration/test_ocr_pipeline.py`. No product, master, schema, baseline-getter, assertion, sibling expectation, stage, commit, push, or deploy change was made.

## Final FAC00010 Synthetic Component Evidence

Both target scenarios use canonical `FAC00010` / `山城`, materialized and resolved per order with `facility_service.ensure_facility_materialized` and `order_service._resolve_order_fax_template`. The resolved template has id `山城`, one `regular/2F` quantity column at `index=3`, `source_index=4`, and this exact ten-field `main_ocr_row_fields` list: `date_mmdd`, `daypart`, `menu`, `qty.regular_2f`, `qty.regular_3f`, `qty.soft_2f`, `qty.soft_3f`, `qty.mixer_2f`, `qty.mixer_3f`, `remarks`.

The helper derives the Sunday-Saturday context from the fixture date, adopts a workflow-v2 saved sheet, retrieves it, and proves `_resolve_reparse_llm_baseline` has `baseline_source="sheet"` with the same fields and rows. The final exact XML records the isolated generated template version `FTV6f118f0a794d474b` in the accepted structural-test order: [/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c0-llm-fixtures/tmp/c0-direct-mocks/c0-llm-fixtures-exact-final-20261005/results.xml](/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c0-llm-fixtures/tmp/c0-direct-mocks/c0-llm-fixtures-exact-final-20261005/results.xml).

Final candidate exact6: `5 passed, 1 failed`, XML SHA256 `ff3669d8fa988315695f1a80199878a2f164026d1dd835335362d423e01ada4e`. The prompt test passed; the large structural-projection assertion remains failed because the full-table request is accepted rather than returning `sheet_structural_projection_requires_review`. Final candidate sibling2: `1 passed, 1 failed`, XML SHA256 `2e0a9a127dbd1f0fee3a281d7822f9f15462004780fa8aac1c8fbdc0e44fba20`.

These are synthetic offline component runs only, not FAX/upload/live proof and not bounded whole-correction completion. The sibling Gemini 400 text is emitted by that test's fake `extract_fax_data` exception; no external Gemini request occurred.

## Historical FAC00001 / Invalid Raw-Base Proof - Context Probe

`tmp/c0-llm-fixtures/context_probe.py` (SHA256 `29cbda42de836cebe591577008245b543113c982fd45be4fd52378d177a760eb`) clears environment credentials and installs the committed runner's socket/subprocess audit guard before project imports. Its fresh SQLite DB is `tmp/c0-llm-fixtures/context-probe-v4.sqlite`. This is synthetic offline component evidence only, not FAX/upload/live proof.

The probe materialized `FAC00001` from the fixture master, then confirmed the real workflow-v2 context for `2026-02-01` through `2026-02-07`, selected a public evidence run, and saved a public manual sheet. `get_saved_sheet` returned the saved sheet; `_resolve_reparse_llm_baseline` returned the same row with `baseline_source="sheet"`. The real selected-OCR build separately reported synthetic-evidence blockers `hakodate_ocr_evidence_missing` and `hakodate_target_cell_map_missing`; no acceptance was fabricated.

Relevant paths: `backend/src/services/order_workflow_v2_service.py:1658` (`confirm_context`), `:2250` (`select_ocr_result`), `:2412` (`save_sheet`), `:3171` (`get_saved_sheet`), `backend/src/services/order_service.py:31355` (`_resolve_reparse_llm_baseline`), and `:34051` (LLM full-table baseline gate).

## Historical FAC00001 / Invalid Raw-Base Proof - Fixture Blocker

The required original test quantity is `diet_type=regular`, `area_id=2F`. Canonical `FAC00001` metadata at `backend/src/data/facility_master.template.json:250-260` has only `diet_type=regular`, `area_id=X`, `name=qty.regular_x`; it has no `regular/2F` or `qty.regular_2f` column. The focused helper derives the Sunday-Saturday interval with `sheet_week_service.build_calendar_week_value`, asserts the canonical menu date is inside it, and refuses first-candidate quantity selection.

Therefore a real v2 saved current sheet cannot represent the two original `regular/2F` fixtures under the immutable canonical master. This is a fixture/master contract mismatch, not a reason to use `force_overwrite`, cache/structural baseline, or a mocked baseline getter. The two original business assertions remain unchanged and were not reached in the final guarded run.

## Historical FAC00001 / Invalid Raw-Base Proof - Attempt Results

- 2026-10-05 FAC00001 raw-base setup attempt: exact6 `5 passed, 1 failed`; prompt assertion passed while structural projection reported `llm_full_table_structural_drift`. This is invalid canonical proof because FAC00001 does not represent regular/2F.
- 2026-10-05 FAC00001 raw-base blocker attempt: exact6 `4 passed, 2 failed`; both target setups stopped at the missing `regular/2F` canonical-template assertion. XML: `tmp/c0-direct-mocks/c0-llm-fixtures-exact-master-context-blocker-20261005/results.xml`, SHA256 `51bc6ad00ae8b0774df9ad6c93655c235f78a84484bb2c67d916c10c8b5ffa6e`.
- 2026-10-05 FAC00001 sibling attempt: `1 passed, 1 failed`; unchanged third sibling returned `main_ocr_failed:gemini:Gemini OCR HTTP 400 INVALID_ARGUMENT: Budget 0 is invalid. This model only works in thinking mode.` rather than its unchanged expected `llm_full_table_baseline_missing`. XML: `tmp/c0-direct-mocks/c0-llm-fixtures-siblings-master-context-blocker-20261005/results.xml`, SHA256 `a295e799962b78741968097840860f4f87c04e64b2468a1841055f8884721a8e`.

The bounded correction is frozen as blocked. Overall modernization remains incomplete.

## Detailed Final FAC00010 Resolved-Template Record

The two target setups use only `FAC00010` (canonical facility `山城`, `backend/src/data/facility_master.template.json:1935`) and obtain each order's effective template through `facility_service.ensure_facility_materialized` followed by `order_service._resolve_order_fax_template`. No target setup, first-pass assertion, candidate draft, or fixture baseline reads raw `get_facility_config(...)["fax_template"]` columns.

The helper confirms workflow-v2 context template `山城`, derives the Sunday-Saturday interval containing the fixture date, and requires exactly one `regular/2F` quantity column at `index=3`, `source_index=4`. Its resolved `main_ocr_row_fields` requires `qty.regular_2f` exactly once; the final provider row is the resolved 10-field width with canonical `02/01`, `朝`, `Boundary Feb`, and `qty.regular_2f=2`. It saves and retrieves the current workflow-v2 sheet and verifies `_resolve_reparse_llm_baseline` returns the same fields/rows with `baseline_source="sheet"`. The final accepted structural-test order carried isolated-template version `FTV6f118f0a794d474b`.

Final exact6 command: `PYTHONDONTWRITEBYTECODE=1 /Users/mmorinag/Sawa/2025.12/worktrees/daily-output-label-requests-20260616/backend/.venv/bin/python -B scripts/run_c0_mock_contracts_isolated.py c0-llm-fixtures-exact-final-20261005 exact`.

- Result: `5 passed, 1 failed`.
- `test_reparse_order_llm_prompt_includes_previous_saved_candidate_rows` passed with its original business assertions.
- `test_reparse_order_large_structural_projection_requires_manual_review` retained its original assertion and failed because reparse returned an accepted order, not `sheet_structural_projection_requires_review`.
- XML: [/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c0-llm-fixtures/tmp/c0-direct-mocks/c0-llm-fixtures-exact-final-20261005/results.xml](/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c0-llm-fixtures/tmp/c0-direct-mocks/c0-llm-fixtures-exact-final-20261005/results.xml), SHA256 `ff3669d8fa988315695f1a80199878a2f164026d1dd835335362d423e01ada4e`.

Final sibling2 command: `PYTHONDONTWRITEBYTECODE=1 /Users/mmorinag/Sawa/2025.12/worktrees/daily-output-label-requests-20260616/backend/.venv/bin/python -B scripts/run_c0_mock_contracts_isolated.py c0-llm-fixtures-siblings-final-20261005 siblings`.

- Result: `1 passed, 1 failed`.
- The unchanged third sibling still returns `main_ocr_failed:gemini:Gemini OCR HTTP 400 INVALID_ARGUMENT: Budget 0 is invalid. This model only works in thinking mode.` rather than its unchanged expected `llm_full_table_baseline_missing`.
- XML: [/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c0-llm-fixtures/tmp/c0-direct-mocks/c0-llm-fixtures-siblings-final-20261005/results.xml](/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c0-llm-fixtures/tmp/c0-direct-mocks/c0-llm-fixtures-siblings-final-20261005/results.xml), SHA256 `2e0a9a127dbd1f0fee3a281d7822f9f15462004780fa8aac1c8fbdc0e44fba20`.

The structural assertion is unresolved, not retired. `reparse_order` computes `llm_full_table_active` from the effective template at `backend/src/services/order_service.py:34167`, then forces `llm_quantity_only_active=False` at `:34265-34266`. Its projection branches require quantity-only mode (`:34305` and `:34821`), and `_validate_structural_projection_requires_manual_review` returns without error unless that mode is active (`:17384-17390`, called at `:35287-35294`). With the required canonical anchored full-table row, the original large-projection manual-review assertion is unreachable through this public full-table request. Mode, preset, projection guard, and expected error were not altered to manufacture a pass.

Runner SHA256: `3962513eedccb056b159fbc56320fc6eb0ba3db789e9e1123dfd0f9e4a094147` for [/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c0-llm-fixtures/scripts/run_c0_mock_contracts_isolated.py](/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c0-llm-fixtures/scripts/run_c0_mock_contracts_isolated.py). All owned runner processes terminated. The bounded correction and overall modernization remain incomplete.

## Postcommit 27c6ffc Source-Bound Evidence

Generating source: `27c6ffc89d89db2cea68fdc6046013a3fdd26457`; the tree was clean before both runs. Committed test SHA256: `051fbdbfd19d638dba3701042dd7cda2bb41f3dd5890f1a7a7421b7071ecaa2a` for `backend/tests/integration/test_ocr_pipeline.py`. Master SHA256: `18b47a19bde8159e22b23b9c9eabeba9ab6be300099bd689d8f6562ebccc0025` for `backend/src/data/facility_master.template.json`. Committed runner SHA256: `3962513eedccb056b159fbc56320fc6eb0ba3db789e9e1123dfd0f9e4a094147`.

Exact6 command: `PYTHONDONTWRITEBYTECODE=1 /Users/mmorinag/Sawa/2025.12/worktrees/daily-output-label-requests-20260616/backend/.venv/bin/python -B scripts/run_c0_mock_contracts_isolated.py postcommit27c6ffc-exact-20261005 exact`.

- Result: `5 passed, 1 failed`, matching the required residual count. The residual is unchanged: `test_reparse_order_large_structural_projection_requires_manual_review` receives an accepted order where its unchanged assertion expects `updated is None`.
- XML: [/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c0-llm-fixtures/tmp/c0-direct-mocks/postcommit27c6ffc-exact-20261005/results.xml](/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c0-llm-fixtures/tmp/c0-direct-mocks/postcommit27c6ffc-exact-20261005/results.xml), SHA256 `e2903348a482ba8599de13eb5f4ba6a1e14da8726b7bc887cea60d95b014746e`.
- Import/isolated DB proof: [/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c0-llm-fixtures/tmp/c0-direct-mocks/postcommit27c6ffc-exact-20261005/import-proof.log](/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c0-llm-fixtures/tmp/c0-direct-mocks/postcommit27c6ffc-exact-20261005/import-proof.log): current worktree root, `dont_write_bytecode: True`, and run-local `test.sqlite`.

Sibling2 command: `PYTHONDONTWRITEBYTECODE=1 /Users/mmorinag/Sawa/2025.12/worktrees/daily-output-label-requests-20260616/backend/.venv/bin/python -B scripts/run_c0_mock_contracts_isolated.py postcommit27c6ffc-siblings-20261005 siblings`.

- Result: `1 passed, 1 failed`, matching the required residual count. The unchanged sibling still compares expected `llm_full_table_baseline_missing` to its fake-extractor Gemini thinking-mode 400 result.
- XML: [/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c0-llm-fixtures/tmp/c0-direct-mocks/postcommit27c6ffc-siblings-20261005/results.xml](/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c0-llm-fixtures/tmp/c0-direct-mocks/postcommit27c6ffc-siblings-20261005/results.xml), SHA256 `6e47e00930ca13d65f8039b10f307897e873453cb56a773e3a10f90bf05d16f9`.

No product or test code changed during postcommit verification. All owned processes terminated. This is source-bound postcommit evidence for the committed candidate only; it is not whole-migration completion or an all-tests-pass claim.

### Postcommit Import-Proof Hashes

- Exact6 import proof: [/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c0-llm-fixtures/tmp/c0-direct-mocks/postcommit27c6ffc-exact-20261005/import-proof.log](/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c0-llm-fixtures/tmp/c0-direct-mocks/postcommit27c6ffc-exact-20261005/import-proof.log), SHA256 `670d4f466f25bacba2b9f1bbddf10ecf75c47ce0efc4b5e6e3e44e46f8d62db2`.
- Sibling2 import proof: [/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c0-llm-fixtures/tmp/c0-direct-mocks/postcommit27c6ffc-siblings-20261005/import-proof.log](/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c0-llm-fixtures/tmp/c0-direct-mocks/postcommit27c6ffc-siblings-20261005/import-proof.log), SHA256 `b4709ae09c0b7f84968ef125b9c8f92355881d3db38158de38d1594fe992c0b6`.
- Generating runner: [/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c0-llm-fixtures/scripts/run_c0_mock_contracts_isolated.py](/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c0-llm-fixtures/scripts/run_c0_mock_contracts_isolated.py), SHA256 `3962513eedccb056b159fbc56320fc6eb0ba3db789e9e1123dfd0f9e4a094147`.

## Historical FAC00001 / Invalid Raw-Base Proof - Provider-Row Follow-up

The focused follow-up inspected the resolved `fax_template.main_ocr_row_fields` in the isolated exact runner. Its actual list is `date_mmdd`, `daypart`, `menu`, `qty.regular_x`, `qty.placeholder_x`, `qty.no_meat_x`, `qty.no_fish_x`, `qty.change_1_x`, `qty.change_2_x`, `remarks`; `qty.regular_2f` occurs zero times. The provider stub cannot truthfully emit a canonical full-width row containing `qty.regular_2f=2` in this requested FAC00001 mode. The mismatch is introduced by the facility-scoped override being authoritative for `columns` (`config_service._merge_template`, `backend/src/services/config_service.py:113-128`), while the original test fixture still expresses a 2F field.

The full-table mode keeps the real structural guard in `order_service._validate_and_merge_reparse_full_table_candidate` (`backend/src/services/order_service.py:15937`), and rejects structural drift at `:16209`. No guard, alignment rule, baseline getter, or expected `sheet_structural_projection_requires_review` was changed. Historical master changes containing `qty.regular_x` include `0040055 Restore Choseian facility scoped templates`; its facility-master diff changed 325 insertions and 36 deletions.

2026-10-05 FAC00001 raw-base attempt reached `5 passed / 1 failed`: the prompt test passed on public workflow-v2 saved state, while the structural-projection test failed `llm_full_table_structural_drift`. The later FAC00001 provider-schema attempt recorded `4 passed / 2 failed`; XML `tmp/c0-direct-mocks/c0-llm-fixtures-exact-provider-schema-20261005/results.xml`, SHA256 `0fff2f41264fcec566c89ed9bd32e1e7c10bb0cfc9a9cb1251aa38f351bf2177`. Neither is valid canonical FAC00010 proof or evidence that either original business assertion is fixed.
