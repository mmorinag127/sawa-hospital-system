# Output Canonical Source Root Fix

## Scope

- Worktree: `hospital-output-source`
- Branch: `codex/modernization-output-source-20261005`
- Parent source: `919b802c1b341b8361fb45224c90d2e5e83a646a`
- Failure class: `OUTPUT_CANONICAL_SOURCE_BYPASS`

## Current Integrated State

Parent committed this worktree candidate, fast-forward integrated it, and pushed `develop` at `bf00faf79d4b844dcc4e908c9fc66944cb15b0d6`.

- Parent integrated result: 95 tests, 87 passed, 8 failed. Against the 13-failure baseline, 5 failures were resolved; new failures: 0; deleted test nodes: 0.
- XML: `/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-output-source/tmp/parent-output-integrated-bf00faf/results.xml`; SHA256 `3146887b6e5ade8fd58c6a6fb0e7aac2c0060d011e8cf7ca89b93b5954d4e956`.
- Node configuration/origin tests: 4 passed, 0 failed.
- Manual staging run `37326602539` completed with failure for this exact source SHA. `deploy-backend` and `deploy-frontend` both succeeded; the failing step was `Verify opted-in existing output source is read-only and canonical`, which stopped before API reads with `ModuleNotFoundError: scripts`.
- Parent read-only Cloud Run evidence records `worker-stg-00777-4zn` and `web-stg-00357-gp6` at 100% traffic. Immutable image metadata/source verification remains incomplete, and there is no saved-sheet lineage, quantity equality, or full-page PNG evidence.
- The entrypoint import repair is an uncommitted candidate, not a statement about `bf00faf79d4b844dcc4e908c9fc66944cb15b0d6`. It explicitly adds its repository root and backend dependency root, then imports through `scripts.*`; its external-cwd, unset-`PYTHONPATH` regression uses an explicit `--output` temporary directory, requires the invalid-context stop before authentication or cloud access, and validates the sanitized result and manifest without touching existing proof artifacts. The default Actions output remains `tmp/output-source-live`.
- Historical 9/13 label evidence and `/Users/mmorinag/Sawa/2025.12/tmp/verify_daily_output_sections.report.json` support the candidate order/date (`ORD37344b72`, `2026-09-13`) only. They do not establish the required saved-sheet lineage (workflow saved-sheet ID, wrapper order ID/template version, and saved-sheet target date).

This is not a claim of staging success, quantity equality, full-system completion, or production approval.

## Invariant

When a persisted workflow has a saved draft, that draft is the only source for daily bags, totals, and output materialization. A rebuild error, draft/order mismatch, bagging lineage mismatch, empty draft result, lookup failure, missing required draft, missing template version, or template-version mismatch stops the output path. It does not use `OrderLine`, a prior bagging materialization candidate, metadata/cache substitution, or auxiliary bootstrap quantities. Workflow and draft template version IDs must both be present and equal.

Confirmed non-V2 orders with no workflow continue to use their existing `OrderLine` source. A database/session lookup failure is distinct from a confirmed missing workflow and always blocks because canonical-source status cannot be established.

## Caller Parity

`include_expanded_copy` is independent from canonical-source selection:

- `get_daily_bag_summary` default remains `True` for ordinary service callers.
- `/orders/daily-bags`, `/orders/daily-output-context`, and the audit's internally built summary explicitly use `False`, preserving their prior transform selection.
- totals continue to use `False`.
- The removed `allow_stale_draft_lines` bypass is not reintroduced.

The original test node names remain: `test_build_order_lines_for_outputs_can_allow_stale_lines_for_audit` and `test_build_order_lines_for_outputs_accepts_stale_lines_keyword_without_materializing`. Their docstrings record the approved contract change: the former stale-line acceptance now blocks because it violated the canonical-source invariant. The stale flag is not restored.

The historical blank weekly-menu saved draft / auxiliary bootstrap `12` case remains as `test_nonwriting_materialization_rebuilds_blank_weekly_menu_from_canonical_bootstrap`. It now proves the blank persisted draft blocks and that bootstrap is not called or adopted.

## Isolated Evidence

Runner: `scripts/run_c0_runtime_schema_isolated.py` with the existing local C0 Python at `/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c0/backend/.venv/bin/python`. The runner creates DB, HOME, TMP, cache, artifacts, JUnit XML, and import proof under this worktree's `tmp/c0-runtime-schema/`.

- `output-source-canonical-focused-final-20261005`: 21 passed; SHA256 `5c5ae470705411d4bbb66325bb53838f4ef4a2871ff27420f764d5a8baf98ec7`.
- `output-source-whole-final-20261005`: 69 tests, 61 passed, 8 failed; SHA256 `e64d03a827847fd74b52c5e56ef6124908b88b194a8868d2b12160e34a56cc7d`.
- `output-source-focused-20261005-b`: 59 collected, 51 passed, 8 failed. The eight failures are delivery-template/weekly-weight nodes outside this canonical-source change; they remain unresolved in this worker because no baseline comparison was run. The focused runs above are the current root-fix evidence.

Final local candidate evidence supersedes the older 59-test focused run:

- `output-source-root-verifier-final-20261005`: 34 passed, 0 failed; `results.xml` SHA256 `834d1d15c7792385792c2d7efe51cb4ed4c9cd60781edb920f3dea1dc1a0a975`. It covers persisted-workflow missing draft in `confirmed` and `apply_ready`, database lookup failure, missing order ID, missing required workflow, draft/order mismatch, stale bagging lineage, materializer error/exception, blank saved draft with auxiliary bootstrap `12`, template version mismatch, and either missing template version ID. It also covers explicit verifier ID/date context, sanitized output, exact GET whitelist, and rejection of retired/noncanonical paths.
- `output-source-verifier-whitelist-final-20261005`: 19 passed, 0 failed; `results.xml` SHA256 `26314039d1f420251c1d58323d8dde088b41275b61869552b3e1a5653542e414`. It is the latest verifier-specific evidence and additionally rejects duplicate query values.
- `output-source-whole-final-2-20261005`: 75 tests, 67 passed, 8 failed; `results.xml` SHA256 `9df8c186eb87e0d37254c9ad5a803baf4d419a516dc7f6efde276f5d7bef5c05`. The 8 failed nodes are the same delivery-template/weekly-weight nodes listed by the previous candidate whole-file evidence; the newly added root/verifier tests pass. XML comparison found 0 parent-candidate passes that became failures and 0 current existing-candidate passes that became failures. The parent two-file comparison remains the baseline evidence: 58 tests, 45 passed, 13 failed at `919b802`; candidate 69 tests, 61 passed, 8 failed; no test node was deleted. This worker has not changed those 8 failures.
- `NODE_PATH=/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-main/frontend/node_modules node --test frontend/tests/config/menu-master-live.test.js`: 3 passed, 0 failed. This read-only dependency path is used because this isolated worktree has no local `js-yaml` installation.

Current worker commands:

```sh
/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c0/backend/.venv/bin/python scripts/run_c0_runtime_schema_isolated.py output-source-canonical-focused-final-20261005 --pytest-target backend/tests/integration/test_daily_output_bundle.py::test_daily_bag_summary_keeps_expanded_copy_choice_separate_from_canonical_lines --pytest-target backend/tests/contract/test_daily_output_context_api.py --pytest-target backend/tests/integration/test_daily_output_bundle.py::test_build_order_lines_for_outputs_uses_newer_draft_materialization --pytest-target backend/tests/integration/test_daily_output_bundle.py::test_nonwriting_materialization_rebuilds_blank_weekly_menu_from_canonical_bootstrap --pytest-target backend/tests/integration/test_daily_output_bundle.py::test_workflow_v2_lines_blocks_materializer_exception_with_persisted_draft --pytest-target backend/tests/integration/test_daily_output_bundle.py::test_workflow_v2_lines_blocks_apply_ready_materializer_error_with_persisted_draft --pytest-target backend/tests/integration/test_daily_output_bundle.py::test_workflow_v2_lines_blocks_mismatched_bagging_lineage_with_persisted_draft --pytest-target backend/tests/integration/test_daily_output_bundle.py::test_workflow_v2_lines_blocks_draft_order_mismatch_with_persisted_workflow --pytest-target backend/tests/integration/test_daily_output_bundle.py::test_workflow_v2_lines_keeps_confirmed_non_v2_raw_lines_when_workflow_is_absent --pytest-target backend/tests/integration/test_daily_output_bundle.py::test_workflow_v2_lines_blocks_missing_workflow_when_saved_sheet_is_required --pytest-target backend/tests/integration/test_daily_output_bundle.py::test_build_order_lines_for_outputs_requires_saved_sheet_for_output_review_state --pytest-target backend/tests/integration/test_daily_output_bundle.py::test_workflow_v2_lines_blocks_lookup_failure_instead_of_assuming_raw_lines_are_canonical --pytest-target backend/tests/integration/test_daily_output_bundle.py::test_workflow_v2_lines_blocks_missing_order_id_when_saved_sheet_is_required
/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c0/backend/.venv/bin/python scripts/run_c0_runtime_schema_isolated.py output-source-whole-final-20261005 --pytest-target backend/tests/integration/test_daily_output_bundle.py --pytest-target backend/tests/contract/test_daily_output_context_api.py
```

## Parent Whole-File Comparison

Parent artifacts compare the same two target files:

- Baseline source root: `hospital-main` at `919b802c1b341b8361fb45224c90d2e5e83a646a`; 58 tests, 45 passed, 13 failed; `results.xml` SHA256 `a21a9abd7a9021e753320096530531ab07c29e22e46c1f0dd6b247579e0aac6d`.
- Candidate source root: this worktree; 69 tests, 61 passed, 8 failed; `results.xml` SHA256 `53a6ac0ac9a7239383de2f16564b3a3522d278afef61a37cc16c85f72eba9ce9`.
- Parent import proof records the same `src.main` SHA256 for both: `407b80709d3a0d1a7945edd8ec69bd2d758c83e090602b58b696930ca07bc62e`. Baseline imports `hospital-main`; candidate imports `hospital-output-source`.
- Parent result: original passes that became failures: 0. The candidate's 8 failed nodes are a subset of the baseline's 13 failed nodes; worker whole-file failure nodes equal the parent candidate's 8 nodes.
- The artifact directories contain only `results.xml`, `import-proof.log`, and isolated `test.sqlite`; the exact parent shell invocation was not persisted, so no command is inferred or fabricated here.

The first attempted isolated run, `output-source-focused-20261005-a`, exited before collection because the system Python lacked `celery`. No source behavior result is inferred from that dependency failure.

## Historical Candidate Status Before Parent Integration

The following status was accurate before the parent commit, fast-forward integration, and `develop` push recorded above. It is retained as historical evidence, not the current integration state: this was a worker candidate only and had not been merged, staged, pushed, deployed, or verified in staging.

## Integrated Residual Failures

The parent integrated XML retains eight failed nodes. None is deleted or ignored.

1. `test_write_delivery_note_blocks_when_template_uri_missing` -- implementation required. `backend/src/services/output_builder.py` `_write_delivery_note` falls back to a generated `.xlsx` when the delivery template URI is absent or cannot be read. The invariant requires an explicit blocker instead of surrogate output.
2. `test_build_daily_output_bundle_empty_orders_are_not_errors` -- implementation required. `build_daily_output_bundle` records an item with `status="empty"`, but its manifest does not expose an `empty_orders` aggregate. The aggregate contract is incomplete.
3. `test_weekly_weight_collect_rows_counts_diabetes_as_regular_and_excludes_forbidden` -- implementation required. `_normalize_diet_key` produces `diabetes`, while `_WEEKLY_WEIGHT_REGULAR_DIETS` omits it, so the regular weekly-weight total drops that quantity.
4. `test_build_outputs_download_path_does_not_write_canonical_rows` -- fixture/contract mismatch. The test calls `build_outputs`, whose active contract writes `Bag`, `LabelRow`, and `DeliveryNote` materializations. The intended non-writing public download path must be identified; this is not evidence to restore a fallback or weaken the test.
5. `test_reference_daily_delivery_materializes_static_formula_labels` -- fixture/source mismatch. It asserts static labels from the retired reference daily-delivery workbook route; active bundle assembly has `use_reference_daily_delivery = False`.
6. `test_reference_daily_delivery_rewrites_static_menu_cells_for_target_date` -- fixture/source mismatch for the same inactive reference-workbook route.
7. `test_reference_daily_delivery_writes_excel_readable_workbook` -- fixture/source mismatch for the same inactive reference-workbook route.
8. `test_reference_daily_delivery_removes_static_artifacts` -- fixture/source mismatch for the same inactive reference-workbook route.

The four reference-workbook nodes require an authoritative fixture/contract decision. They do not justify re-enabling the retired route or modifying the locked master workbook.

## Staging Read-Only Verifier

`deploy-stg.yml` adds empty-default `workflow_dispatch` inputs `verify_output_source_order_id` and `verify_output_source_date`. Both are required to enable the post-web-deploy verifier; otherwise its steps do not run and no staging verification is claimed. The verifier uses the existing staging WIF service principal and OAuth audience; it records that this is service-principal authentication, not human GIS login.

The verifier checks deployed web/worker revisions and immutable image source SHA, then uses only these current GET routes: order, `workflow-v2`, `workflow-v2/sheet`, `daily-output-context`, `daily-bags`, and `/totals?date=...&include_order_refs=true`. Retired `draft-sheet` and `workflow-state` routes are excluded. Its URL parser whitelists those exact paths and query keys. The explicit target date must be inside the workflow week and present in the saved sheet. The workflow `saved_sheet_id`, `order_id`, and `template_version_id` must exactly equal the saved-sheet wrapper values; no fallback ID chain is used.

The verifier records only selected business fields, HTTP status/body hashes, revision/image/source data, PNG hashes, and a sanitized manifest. It blocks browser writes other than GET/HEAD, uses a private WebKit context, verifies the daily-delivery-notes date input and `daily-output-context` 200 response, writes a full-page PNG, and closes owned browser/processes. A successful job status is `captured`, never `passed`: it is evidence capture only. Parent review must compare actual saved-sheet quantities, daily-bag quantities, totals `order_refs`, and the PNG before declaring a valid case or an explicit blocker. A 200 response, row count, or rejected section alone is not classified as correct.

The browser verifier accepts only the exact configured staging origin `https://web-stg-avlnzjjrca-dt.a.run.app`; production, localhost, an origin with a trailing slash, and an empty value stop before browser startup or session-token injection. Its init script separately checks `location.origin` and follows the browser-session logout/cache-generation convention. The workflow validates the order-ID/date pair on every manual dispatch: both empty is the default skip, either one alone fails, and both are required for the opt-in verifier steps. Inputs reach the validation command through environment variables, not command interpolation.

Monitor-correction evidence: `output-source-origin-pair-final-2-20261005` ran 28 Python tests with 28 passed and 0 failed; `results.xml` SHA256 `5491a011faaa87e69f38e33d787c5d4c2dbc8a2ef0b1c1d5773a30164c40e5f4`. `frontend/tests/config/menu-master-live.test.js` plus `frontend/tests/config/output-source-live-origin.test.mjs` ran 4 Node tests with 4 passed and 0 failed. The latter accepts only the exact staging origin and rejects empty, trailing-slash, evil, production, and localhost origins.

No local cloud call was made: existing user credentials cannot mint the required verification service-principal token (`parent token_mint_exit1`). The opt-in verifier workflow at `bf00faf79d4b844dcc4e908c9fc66944cb15b0d6` has not produced staging evidence: run `37326602539` failed at its pre-API import boundary. The entrypoint import repair is not committed. An opted-in GitHub Actions verification with an explicit order ID and ISO date, immutable metadata/source confirmation, saved-sheet lineage, quantity comparison, and PNG review remain required. Production is not approved.
