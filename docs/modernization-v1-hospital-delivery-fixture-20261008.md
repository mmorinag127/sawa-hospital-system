# H2 delivery fixture migration

Branch `codex/modernization-v1-hospital-delivery-fixture-20261008`, inspected/generating base `b09e2af8b6b13c4bbc9d3e8d84b9929178ffb70b` plus the recorded uncommitted test sources. Production unchanged. Scope: existing helper, third integration test, this document only. First two test bodies/assertions and fourth/fifth source unchanged.

The helper now supports explicit multiple menu/daypart rows with unique quantity columns; authored physical menu items/entries, selected canonical JSON projection, synthetic matching PDF, real registration/delivery receipt, external OCR component double, real evidence selection/save/bagging, and optional real final_confirm. No ownership token fabrication, active stamping or quantity fallback. Invoice configuration is authored into the selected JSON version before registration/capture. Existing component dates and unresolved business policies are not reinterpreted. Not recognizer/operator/live proof.

## Executed results and production blocker

- Root + first four: first run and corrected final-confirm run each **42 passed, 1 failed, 1 deselected**, exit 1. The final-confirm run took 3.71 seconds. Root 39 + first two + fourth passed. Fifth deliberately not executed.
- Third-only diagnostics: **1 failed**, exit 1, 1.99 seconds then 1.87 seconds. All six original XLSX cell assertions remain exactly present. Added original accepted quantities `[5, 7]` and actual delivery row count 2 assertions passed.
- Third fails selecting worksheet `2025-02-20`; actual XLSX has `Sheet1`. This remains a failure, not fixture completion.

Actual shared production cause: `output_builder._prepare_output_context:6385` unconditionally calls `_build_invoice_template_from_fax_columns` instead of consuming the original configured `invoice_template`. That helper returns only derived columns/include_menu_name at 6027, dropping authored template URI, sheet name and slot layout. `_write_delivery_note:3920` consequently takes the no-template DataFrame branch, emitting Sheet1 rather than the dated slot template and its unassigned-row clearing behavior. Real final_confirm did not change this. Do not substitute a direct writer for the actual build_outputs proof or change worksheet/assertions to accept the alternate output. Production correction is outside this assignment; no patch or mock applied.

## Fifth test: read-only classification

`test_ikebukuro_label_expiry_is_plus_three_months_and_content_amount_fallbacks` remains unchanged, unexecuted and unaccepted.

1. Registration/output ownership is legacy: nonexistent PDF, unowned direct lines, direct build_outputs. Future migration needs the real original/draft/acceptance chain, not a receipt shim.
2. Expiry: `_resolve_label_expiry_date:299` uses explicit captured label_profile `expiry_offset_months` and `expiry_offset_days`. Preserve the existing exact `2025年4月6日` assertion through explicitly authored +3 months, not facility-name guessing. It does not justify a global +3 month default.
3. Current label headers at `DEFAULT_LABEL_FIELDS:140` include `内容量`, `内容詳細`, and an empty-name servings field; **no `実量` header**. `_label_payload_jp:2637` computes content amount from accepted per-serving quantity/unit multiplied by servings. Therefore old `内容量 == 2人前` and absent `実量` are not evidence of the current amount contract.
4. Amount resolution: `order_serialization_service.candidate_menu_amount_resolution` requires explicit dated/daypart/diet monthly item identity and resolved amount/unit before bagging. Missing master/amount must stop (`menu_amount_unresolved`), not become a two-servings fallback. Existing `accepted_label_unit_required` rejects a quantity with no unit. Proposed later reviewed cases must retain servings 2, configured amount/unit and exact computed content amount plus separate missing-master/amount no-output stop; no new numeric expectation is invented here. Main review must decide the old assertion alignment before any fifth edits.

## Reproduction

All pytest cwd: own `backend`; trusted readonly Python and readonly integrated SDK only, no installs, DB/network/browser/provider jobs. Existing isolated_formal_schema gives fresh real Alembic SQLite per case and disposes engines. Own HOME/TMP/cache/output; no running process remains.

Root/first-four command (exit 1):
```sh
env -i PATH=/usr/bin:/bin HOME="$PWD/../tmp/delivery-proof/home" TMPDIR="$PWD/../tmp/delivery-proof/temp" XDG_CACHE_HOME="$PWD/../tmp/delivery-proof/cache" PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-main/tmp/v1-hospital-task-sdk-20261007:. DB_URI="sqlite:///$PWD/../tmp/delivery-proof/bootstrap-r2.sqlite3" NO_GCE_CHECK=true GOOGLE_CLOUD_DISABLE_GRPC=true /Users/mmorinag/Sawa/2025.12/workspace/backend/.venv/bin/python -m pytest tests/unit/test_h2_canonical_workflow_template.py tests/integration/test_bagging_delivery_slots.py -k 'not ikebukuro' -q -p no:cacheprovider --basetemp=../tmp/delivery-proof/cases-r2 --junitxml=../tmp/delivery-proof/results-r2.xml > ../tmp/delivery-proof/pytest-r2.log 2>&1
```

Final third-only command (exit 1):
```sh
env -i PATH=/usr/bin:/bin HOME="$PWD/../tmp/delivery-proof/home" TMPDIR="$PWD/../tmp/delivery-proof/temp" PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-main/tmp/v1-hospital-task-sdk-20261007:. DB_URI="sqlite:///$PWD/../tmp/delivery-proof/bootstrap-diagnostic-r2.sqlite3" NO_GCE_CHECK=true GOOGLE_CLOUD_DISABLE_GRPC=true /Users/mmorinag/Sawa/2025.12/workspace/backend/.venv/bin/python -m pytest tests/integration/test_bagging_delivery_slots.py::test_delivery_slot_template_writes_menu_display_and_clears_unassigned -q -p no:cacheprovider --basetemp=../tmp/delivery-proof/diagnostic-cases-r2 --junitxml=../tmp/delivery-proof/diagnostic-r2.xml > ../tmp/delivery-proof/diagnostic-r2.log 2>&1
```

SHA256 relative to this WT (absolute root given in assignment):
- `tmp/delivery-proof/results-r2.xml`: `2e35815b9d25d55e850fb3b7431467cbdcbc31f068bef1315530a19192b22d42`
- `tmp/delivery-proof/diagnostic-r2.xml`: `9eeb0c88e7318041a16a900b80b8e6e74c616cfee984925ffb2f32eb53cfc2cd`
- `backend/tests/helpers/newnarrowbaggingfixture.py`: `17f8949dfa708512929ba7c50d5c3da621fdf302f5a0c14df7d16acfcc480b78`
- `backend/tests/integration/test_bagging_delivery_slots.py`: `6c65682ffe457d581d6520ec96005c25a2991d1b1e29c9bcbc336f3f4f1e907a`

`git diff --check` exit 0. Source frozen for parent review, not overall acceptance. No commit/main write/merge/push/deploy. Date/override/capture business holds unchanged.

## Authorized shared invoice repair checkpoint

Base remains `b09e2af8b6b13c4bbc9d3e8d84b9929178ffb70b`. The preceding failures and source hashes are historical checkpoints, not current completion evidence.

`_prepare_output_context` now deep-copies the accepted invoice config instead of deriving it from FAX columns. The existing schema column validation and authored quantity signature validation stop invalid input. URI, sheet, columns, aggregate area omission, unknown slot metadata and explicit booleans remain captured values. The component producer now authors an explicit tabular invoice when no workbook is assigned; production does not synthesize one. A configured missing worksheet stops; an unnamed multi-sheet workbook also stops rather than choosing active. An unnamed single-sheet workbook remains uniquely addressable. Labels/aggregate preview explicitly do not require invoice; delivery and bundle consumers do. No production category/slot algorithm was changed.

Final combined command, cwd this WT `backend` (exit 1; 95 passed, 2 failed, 1 deselected, 4.37s):
```sh
env -i PATH=/usr/bin:/bin HOME="$PWD/../tmp/delivery-proof/home" TMPDIR="$PWD/../tmp/delivery-proof/temp" XDG_CACHE_HOME="$PWD/../tmp/delivery-proof/cache" PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-main/tmp/v1-hospital-task-sdk-20261007:. DB_URI="sqlite:///$PWD/../tmp/delivery-proof/bootstrap-freeze.sqlite3" NO_GCE_CHECK=true GOOGLE_CLOUD_DISABLE_GRPC=true /Users/mmorinag/Sawa/2025.12/workspace/backend/.venv/bin/python -m pytest tests/unit/test_h2_canonical_workflow_template.py tests/unit/test_h2_output_acceptance_receipt.py tests/integration/test_bagging_delivery_slots.py -k 'not ikebukuro' -q -p no:cacheprovider --basetemp=../tmp/delivery-proof/cases-freeze --junitxml=../tmp/delivery-proof/results-freeze.xml > ../tmp/delivery-proof/pytest-freeze.log 2>&1
```

All six original third XLSX assertions remain unchanged. The selected worksheet now exists, but existing `_menu_entry_category_label` / output category processing maps authored morning 主A to 副① and evening to 主. Actual generated XML places `副① メニュー朝` / 5 in C3/D3, empties C2/D2, and writes `主 メニュー夕` / 7 in C4/D4. This is a separate source failure, not missing invoice config; no quantity or slot assertion was weakened. First two and fourth integration cases pass. The other failure is the pre-existing `outputs` mapping refusal fixture: the real ownership gate stops with `output original ownership input missing` before its expected `mapping_unresolved`. Bundle/preview/delivery_preview still satisfy the original mapping refusal assertions with explicit authored nonquantity invoice columns. No ownership shim was added.

After the combined run, the existing duplicate/empty context case gained explicit invoice-free label intent and delivery missing-input rejection assertions. Focused rerun exit 0, 1 passed:
```sh
env -i PATH=/usr/bin:/bin HOME="$PWD/../tmp/delivery-proof/home" TMPDIR="$PWD/../tmp/delivery-proof/temp" PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-main/tmp/v1-hospital-task-sdk-20261007:. DB_URI="sqlite:///$PWD/../tmp/delivery-proof/bootstrap-intent.sqlite3" NO_GCE_CHECK=true GOOGLE_CLOUD_DISABLE_GRPC=true /Users/mmorinag/Sawa/2025.12/workspace/backend/.venv/bin/python -m pytest tests/unit/test_h2_output_acceptance_receipt.py::test_actual_delivery_context_duplicate_preserves_producer_first -q -p no:cacheprovider --basetemp=../tmp/delivery-proof/cases-intent --junitxml=../tmp/delivery-proof/results-intent.xml > ../tmp/delivery-proof/pytest-intent.log 2>&1
```

SHA256 (paths relative to the assigned absolute WT):
- `tmp/delivery-proof/results-freeze.xml`: `dbd9d6c8d729088a1e3440def0142136c948c12b7b4848445d35cdecd2d062eb`
- `tmp/delivery-proof/pytest-freeze.log`: `a83bb1662f9d6a7bcbb4af87f8075a4bf4a2945ed2adcb62432ee51a214be02c`
- `tmp/delivery-proof/results-intent.xml`: `cec3fdf4c441cc3bf1f4381a0d9586e4aab1808cc7f1863bbdf7a0efc3f54a47`
- `backend/src/services/output_builder.py`: `83d325d3227c2d255fa70a729ee7c2cbf772474d612127097cef12146c7d9d41`
- `backend/tests/unit/test_h2_output_acceptance_receipt.py`: `9068936f060aad2d8e3a8f8ba770adf1decef140f34c5c895a66efcbe2f2837f`
- `backend/tests/helpers/newnarrowbaggingfixture.py`: `68c2bc698edaf84d003aa785e740d15df27111c26d4fe8b9f8cd87fab3a16ba1`

Fifth test remains unchanged, unexecuted and pending review. Synthetic PDF/provider-double component coverage only, not recognizer/operator/live proof. Root repair is not accepted as complete while the third case fails. No master workbook, dependency, date policy, main tree, commit, merge, push or deploy writes.

## Consumer intent and explicit category correction

Subsequent parent review rejected the prior default-invoice intent for labels workbook/CSV/weekly/bundle. Every production context call now passes its actual intent: workbook/CSV/weekly and labels-only daily bundles require no invoice, delivery/both/full outputs require invoice. Context retains the `invoice_template` key (None for noninvoice intent) and `_daily_bundle_settings` still compares captured facility/profile/policy/settings without omissions. Actual registration/evidence/save/bagging/confirm fixture with deliberately absent authored invoice successfully produces label XLSX and CSV, labels and labels_csv daily bundles and weekly weight rows; actual delivery preview rejects missing invoice. The old outputs mapping refusal now uses an actual accepted order and captured ownership in the same session, retaining `mapping_unresolved` and the no-write assertion. It passes, without ownership mocking.

`menu_service._is_menu_category_value` lines 964-970 supports explicit 主A/主Ａ, but not 主B/主Ｂ. `_menu_entry_category_label` now preserves those supported raw aliases and normalized specific 主菜/副菜1/2/3; the specific-category predicate recognizes the aliases too. Missing, unknown and unresolved generic 副菜 now raise `delivery_menu_category_unresolved` instead of inferring a meal slot. The initially added missing/generic slot-inference-positive tests were rejected and replaced with explicit refusal tests, not kept as accepted semantics. `menu_service` lines 882-905 produces explicit category using its category_map and physical slot; no captured category_map/count accompanies a generic 副菜 entry here. Its numbering therefore remains unresolved and is blocked, not guessed. B variants remain unaccepted pending actual authoring support.

Changing fixture slot indices to share an index across diets caused first-two actual delivery business failures; that experimental fixture change was reverted and its failed log `tmp/delivery-proof/pytest-consumers.log` retained. Physical slot indices in this fixture remain distinct per diet, preserving actual producer acceptance and original quantity assertions. This is not permission to derive a missing category from those indices.

Final focused command, cwd assigned WT/backend, exit 1 (108 passed, 1 failed, 1 deselected, 6.78s):
```sh
env -i PATH=/usr/bin:/bin HOME="$PWD/../tmp/delivery-proof/home" TMPDIR="$PWD/../tmp/delivery-proof/temp" PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-main/tmp/v1-hospital-task-sdk-20261007:. DB_URI="sqlite:///$PWD/../tmp/delivery-proof/bootstrap-category-stop.sqlite3" NO_GCE_CHECK=true GOOGLE_CLOUD_DISABLE_GRPC=true /Users/mmorinag/Sawa/2025.12/workspace/backend/.venv/bin/python -m pytest tests/unit/test_h2_canonical_workflow_template.py tests/unit/test_h2_output_acceptance_receipt.py tests/integration/test_bagging_delivery_slots.py -k 'not ikebukuro' -q -p no:cacheprovider --basetemp=../tmp/delivery-proof/cases-category-stop --junitxml=../tmp/delivery-proof/results-category-stop.xml > ../tmp/delivery-proof/pytest-category-stop.log 2>&1
```

The remaining failed third test retains all six original assertions. Even after explicit-category resolution, `build_order_lines_for_outputs` calls `_apply_label_meal_slot_categories` after monthly/master/category enrichment; this label-oriented transformation overwrites the category consumed by delivery rows. The user expressly prohibited changing that breakfast label helper. A remaining source-bound proposition is to separate delivery's accepted authored category from label-only display assignment at the shared producer/consumer boundary, while keeping existing label behavior and all six expected cells unchanged. This is a proposed additional boundary repair, not an implemented fix or accepted reconciliation. No expectations were replaced.

Final source/artifact SHA256 relative to the assigned absolute WT:
- `tmp/delivery-proof/results-category-stop.xml`: `73b485c49d7a5c09b0bc854148c12a876299cd774d6484e95026f254b3dc08de`
- `tmp/delivery-proof/pytest-category-stop.log`: `415164fd16cc5234dc925816ecc6b0c4b248f906d4743932990076b6bedbfcc6`
- `backend/src/services/output_builder.py`: `5b580b42ecb8ed9df4a8f5f4d2787a5c83ce7f42c49fa38ec66a71b450b5f0e0`
- `backend/tests/helpers/newnarrowbaggingfixture.py`: `9863b95ad3a068612ebcf033ea3671419245a42bc5f2e8bbb68696b413a28073`
- `backend/tests/unit/test_h2_output_acceptance_receipt.py`: `afb4d08d64a1c05c90e2e4f6f5bbedd357aabab63be471e6be96c0b43c26f39b`

Source frozen for review only. Fifth unchanged/unexecuted, technical component proof only, main integrated proof pending, no commit/deploy. Full completion remains unmet.

## Single canonical category resolver checkpoint

The preceding 108-pass checkpoint and unresolved third case are historical. Shared `_apply_label_meal_slot_categories` now preserves all supported explicit captured categories and explicit operator categories; old `has_explicit_main`, meal sequence reassignment, source/name fallback and first-position inference were removed from that decision. Generic 副菜 uses distinct captured `_monthly_entry_slot_index` values grouped by date/daypart, sorted numerically, with diet variants sharing the same number. Missing/invalid slot provenance and missing/unknown category stop. `_label_display_category` is unchanged. `menu_service.py:853-856` explicitly authors generic 副菜 via its monthly category map; generic is therefore supported rather than categorically rejected. No category or quantity assertion was removed. The standalone first-producer zero fixture now supplies explicit 主菜 instead of relying on the removed missing-category inference; its original receipt/zero assertions remain.

The fixture now authors one regular physical MonthlyMenuEntry per actual menu/daypart slot, separate diet-specific MonthlyMenuItems and quantity columns. It no longer enumerates diets as menu positions. Own fixture facility IDs are unique across isolated databases because production output lookup caches are keyed by facility/week/time bucket, not SQLite engine. This prevents test-database cache leakage without clearing or replacing production caches/guards. Actual generic monthly cases (2 breakfast, 3 lunch, 3 supper, 4 event rows) reach actual registration/evidence/save/bagging/confirm and retain exact quantities. Distinct-slot tests also cover multiple diets without counting diets as slots. Explicit aliases/normal categories and invalid slot/missing/unknown stops pass. Third test passes all six unchanged original XLSX assertions, including 5/7 and cleared unassigned cells; fourth passes. Invoice-free label consumers and delivery missing-input refusal pass.

Final command, cwd this WT/backend; exit 1, 119 passed / 2 failed / 1 deselected, 9.65s:
```sh
env -i PATH=/usr/bin:/bin HOME="$PWD/../tmp/delivery-proof/home" TMPDIR="$PWD/../tmp/delivery-proof/temp" PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-main/tmp/v1-hospital-task-sdk-20261007:. DB_URI="sqlite:///$PWD/../tmp/delivery-proof/bootstrap-isolated-final.sqlite3" NO_GCE_CHECK=true GOOGLE_CLOUD_DISABLE_GRPC=true /Users/mmorinag/Sawa/2025.12/workspace/backend/.venv/bin/python -m pytest tests/unit/test_h2_canonical_workflow_template.py tests/unit/test_h2_output_acceptance_receipt.py tests/integration/test_bagging_delivery_slots.py -k 'not ikebukuro' -q -p no:cacheprovider --basetemp=../tmp/delivery-proof/cases-isolated-final --junitxml=../tmp/delivery-proof/results-isolated-final.xml > ../tmp/delivery-proof/pytest-isolated-final.log 2>&1
```

Remaining first-two failures are actual `menu_amount_unresolved`: soft sauce and mixer 筑前煮 have explicit diet-specific MonthlyMenuItems, but no matching diet-specific dated MonthlyMenuEntry. `order_serialization_service._matches_explicit_line_identity` requires matching diet plus `menu_dates`; its item snapshot obtains menu dates from corresponding entry identities. Conversely `order_service.py:1304-1320` rejects duplicate `(date,daypart,slot)` entries regardless of diet. Adding same-slot diet entries produces `physical_menu_row_duplicate`; enumerating diets as different physical menu slots is now expressly forbidden. Neither guard nor producer was loosened. A shared capture/item-date ownership reconciliation is required outside currently allowed output_builder production scope; no receipt fabrication, copied dates or guessed diet adoption was used. This is a concrete source-contract conflict, not permission to restore the old fixture. First-two original assertions and quantities remain unchanged; complete acceptance is unmet.

Diagnostic failures are retained (`pytest-one-resolver.log`, `pytest-generic-real.log`, `pytest-exception.log`, `pytest-rowtrace.log`). Read-only Python exception tracing revealed empty physical rows in repeated same-facility isolated DB tests; unique fixture identity resolved that cache contamination. An initial diagnostic command had an IndentationError before execution; it was not product proof. A temporary direct unowned producer diagnostic call was removed because it was not equivalent to the actual guarded run. Only the final actual guarded pipeline results above are current evidence.

Current hashes, relative to assigned absolute WT:
- `tmp/delivery-proof/results-isolated-final.xml`: `776e05f00f0f6938658d90f298a2c0a4b6585956968a04796398e565e5cbb2e1`
- `tmp/delivery-proof/pytest-isolated-final.log`: `3c09cbc2ca298158c6cb0b0e9e072f4b56824c7f6a8590cc1711bfc987ef38fe`
- `backend/src/services/output_builder.py`: `138bebbb5dfa94dd07d2422a30401aaee2731627cee998cf3d2c76591e537c13`
- `backend/tests/unit/test_h2_output_acceptance_receipt.py`: `60a175b64f486f137927fa1b9945c056f4e8b29b1abd464d1eefd941565c453a`
- `backend/tests/helpers/newnarrowbaggingfixture.py`: `73b50079066271dd3a1811ee53dbf9cf4286672248d4de46b36cda958ac2f018`

Fifth remains unchanged/unexecuted. Source frozen for parent review; component-only evidence, no integrated/operator/live acceptance, no commit/main/deploy writes.

## Complete captured monthly source numbering

Parent rejected output-subset side numbering at the preceding checkpoint. `_captured_monthly_categories` now receives the complete captured `menu_entries` through `_apply_menu_entry_overrides`, validates date/daypart/typed nonnegative slot and category, merges same-category diet variants of one physical slot, and rejects contradictory definitions. Both explicit numbered side slots and generic side slots participate in ordering. Contradictory explicit numbering is rejected, not preferred. Each matched output line is annotated with its concrete resolved category; `_apply_label_meal_slot_categories` preserves it without recomputing from output lines. Exclusion of first output or outputting only second side cannot renumber side2 to side1. No current-source recapture was added. The unused `_label_meal_slot_sequence` was removed; `_label_display_category` remains unchanged.

Required focused execution (cwd this WT/backend):
```sh
env -i PATH=/usr/bin:/bin HOME="$PWD/../tmp/delivery-proof/home" TMPDIR="$PWD/../tmp/delivery-proof/temp" PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-main/tmp/v1-hospital-task-sdk-20261007:. DB_URI="sqlite:///$PWD/../tmp/delivery-proof/bootstrap-complete-source.sqlite3" NO_GCE_CHECK=true GOOGLE_CLOUD_DISABLE_GRPC=true /Users/mmorinag/Sawa/2025.12/workspace/backend/.venv/bin/python -m pytest tests/unit/test_h2_canonical_workflow_template.py tests/unit/test_h2_output_acceptance_receipt.py tests/integration/test_bagging_delivery_slots.py -k 'not ikebukuro' -q -p no:cacheprovider --basetemp=../tmp/delivery-proof/cases-complete-source --junitxml=../tmp/delivery-proof/results-complete-source.xml > ../tmp/delivery-proof/pytest-complete-source.log 2>&1
```
Exit 1, 122 passed, 2 failed, 1 deselected, 9.68s. Actual generic monthly 2/3/3/4 producer pipeline, original third six assertions/5/7/blank cells, fourth direct-writer assertions, partial/excluded-first numbering, diet-variant numbering, contradictory slot and missing provenance stops, invoice-free consumers and delivery/mapping refusal pass. First-two original quantity assertions are retained but still cannot reach output due to the previously documented `menu_amount_unresolved` identity contract conflict. This checkpoint is not complete acceptance.

SHA256 relative to the assigned absolute WT:
- `backend/src/services/output_builder.py`: `6e69d66319afc08574496a792f85c77c0af02383433bde7c16a97452699d89f9`
- `backend/tests/unit/test_h2_output_acceptance_receipt.py`: `b497a333aa2bc439ae399fb32e22829540b6ed8f9c0c68f1386d73068a4feee2`
- `tmp/delivery-proof/results-complete-source.xml`: `cfce49e0d0a225a23bcd6414adc379cdc9fa8248e3cdbf34de96206764be5e8f`
- `tmp/delivery-proof/pytest-complete-source.log`: `73ab015abcd3f95b06d4bae8da590894da3401ff3896f641a025817cc16bfe04`

Source frozen. Fifth unchanged/unexecuted, no commit/main write/push/deploy; earlier failed logs and quantities preserved.

## Dead FAX invoice generator removal

Read-only caller inventory found zero production callers and exactly three test calls to `_build_invoice_template_from_fax_columns`; `_delivery_invoice_header` had only the generator caller. Both definitions were removed. Unit quantity-signature and aggregate/exact-X tests now use explicit authored invoice columns through `_resolve_captured_invoice_template`, retaining the 10/3 exported quantity/header assertions and existing ambiguity refusal cases. The one affected daily-output construction case now supplies explicit menu_display and quantity columns, retaining menu_display/include_menu_name assertions. No other daily-output test was edited or broadly rerun. `_apply_label_meal_slot_categories` indentation was corrected without changing its decision logic. `_label_display_category` and physical master workbook remain untouched.

`rg -n '_build_invoice_template_from_fax_columns|_delivery_invoice_header' backend/src backend/tests` returns exit 1 with no matches after removal.

Focused command (cwd assigned WT/backend), exit 0, 30 passed / 52 deselected, 1.41s:
```sh
env -i PATH=/usr/bin:/bin HOME="$PWD/../tmp/delivery-proof/home" TMPDIR="$PWD/../tmp/delivery-proof/temp" PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-main/tmp/v1-hospital-task-sdk-20261007:. DB_URI="sqlite:///$PWD/../tmp/delivery-proof/bootstrap-no-fax.sqlite3" NO_GCE_CHECK=true GOOGLE_CLOUD_DISABLE_GRPC=true /Users/mmorinag/Sawa/2025.12/workspace/backend/.venv/bin/python -m pytest tests/unit/test_h2_output_acceptance_receipt.py tests/integration/test_daily_output_bundle.py::test_authored_delivery_template_uses_display_menu_name -k 'nonempty_delivery_columns or aggregate or authored_delivery_template or captured_invoice or canonical_generic or complete_captured' -q -p no:cacheprovider --basetemp=../tmp/delivery-proof/cases-no-fax --junitxml=../tmp/delivery-proof/results-no-fax.xml > ../tmp/delivery-proof/pytest-no-fax.log 2>&1
```

SHA256 relative to assigned absolute WT:
- `tmp/delivery-proof/results-no-fax.xml`: `f34eba8dbea11011c697d9538ee547212d1c46ae83b849d8e4172c8b96fc761b`
- `tmp/delivery-proof/pytest-no-fax.log`: `ce8a6640594ea92b9d6c3878e37ff0a9f547114ff8c4f756aa665d4e1f66ed6b`
- `backend/src/services/output_builder.py`: `262838545a6a882257b1e122c73d907216c2abb978049428c15188cd913d3885`
- `backend/tests/unit/test_h2_output_acceptance_receipt.py`: `b852f872bafa8ea3852ab2bbe2b4cd3d2b7593c63c57276fc6d07b5fcdf5a99b`
- `backend/tests/integration/test_daily_output_bundle.py`: `81c5d9daae9fc995996d8276d466e4fa69308ac2a0f7b900ae396ecb13bf08d3`

This proves only the removed-generator/migrated-test unit, not physical-variant root acceptance. Earlier first-two failures, original quantities and third six assertions remain retained. Changed-name variants still need a diet-aware contract for consumers currently requiring a single undifferentiated menu_name; no representative was selected. No physical-root production code was edited in this checkpoint. Six-file staged packet frozen; no commit/main write/push/deploy.

## Physical variant producer checkpoint

Base remains `b09e2af8b6b13c4bbc9d3e8d84b9929178ffb70b`. The earlier six-file packet is preserved and extended with order_service, hakodate_physical_menu_row_service, master_order_form_template_service and test_h2_geometry_physical_identity. Physical rows group exact dated daypart/slot; per-diet dated entries remain complete under diet_variants. Only unanimous fields remain on the common row, never a representative name. Same-diet duplication, variant physical identity contradiction, missing diet/name and missing requested variant stop. Normal-only entries retain their own metadata. Receipt geometry snapshots and canonical JSON/digest retain the complete grouped entries. Exact dated item matching in order_serialization_service is unchanged.

The helper now authors dated MonthlyMenuEntry variants at the SAME physical slot as their actual MonthlyMenuItems, and emits one PDF/OCR component row per distinct menu slot, not per diet. First-two quantities/null assertions and third six original XLSX cells pass unchanged. External recognition is a component double, not actual OCR/operator-flow proof.

Changed-name variants survive JSON roundtrip with equal digest and can be selected by explicit diet. Undifferentiated cell-plan and weekly common-name checks visibly stop with physical_menu_common_name_unresolved instead of omitting a row or selecting a representative. Full changed-name cell-plan operation is NOT established: there is no authoritative undifferentiated display name in that input. This remains a bounded limitation, not complete acceptance of every consumer.

First physical focused run: 143 passed, 2 failed, 74 deselected. Both failures compared the producer's canonical Japanese daypart against an English raw-input row ID. Expectations now explicitly assert the established normalized `昼` row ID; uniqueness and quantity assertions were not removed.

Final command, cwd assigned WT/backend, exit 0:
```sh
env -i PATH=/usr/bin:/bin HOME="$PWD/../tmp/delivery-proof/home" TMPDIR="$PWD/../tmp/delivery-proof/temp" PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-main/tmp/v1-hospital-task-sdk-20261007:. DB_URI="sqlite:///$PWD/../tmp/delivery-proof/bootstrap-physical-r2.sqlite3" NO_GCE_CHECK=true GOOGLE_CLOUD_DISABLE_GRPC=true /Users/mmorinag/Sawa/2025.12/workspace/backend/.venv/bin/python -m pytest tests/unit/test_h2_geometry_physical_identity.py tests/unit/test_h2_canonical_workflow_template.py tests/unit/test_h2_output_acceptance_receipt.py tests/integration/test_bagging_delivery_slots.py tests/integration/test_daily_output_bundle.py -k 'not ikebukuro and (not test_daily_output_bundle or test_authored_delivery_template_uses_display_menu_name)' -q -p no:cacheprovider --basetemp=../tmp/delivery-proof/cases-physical-r2 --junitxml=../tmp/delivery-proof/results-physical-r2.xml > ../tmp/delivery-proof/pytest-physical-r2.log 2>&1
```
146 passed, 74 deselected, 95 warnings, 9.82s. Daily-output selection includes only the assigned authored-template case; fifth delivery case remains untouched and pending review. No broad suite, main writes, commit or deploy.

SHA256 (paths relative to this assigned WT):
- tmp/delivery-proof/results-physical-r2.xml: d79f2ecfa55d149facd4f6ff048a11e03b34daab97bff392b44f96837ad634f1
- tmp/delivery-proof/pytest-physical-r2.log: 4e7d193179866050ddc83a0a8a47b946272bd1db526d708108e3cc700879a401
- backend/src/services/order_service.py: d03ff953475570775a63a3a9eedd26bb66e91017dc1a2b3d3d8bbce087e76de6
- backend/src/services/hakodate_physical_menu_row_service.py: 52c53089d7b911985fad7d0d1763baab55d19e738921d49d18ab85f0332d5c0b
- backend/src/services/master_order_form_template_service.py: 4ef3139eb0ff03766ea55d82a9ed883b17b4d807e38ffa2289a299a341573452
- backend/tests/unit/test_h2_geometry_physical_identity.py: 8c5e4c553ef852898c15b49e9eb1eccff2667e2594e64ec3f5d122f24f5a21e7
- backend/tests/helpers/newnarrowbaggingfixture.py: 30a2fa9deaefd078125a1e39dab3b02bbcb48734f75ceb7d92b142534a7be39d

Existing business-policy holds and integration/live acceptance remain unresolved; this component result is not full modernization completion.

## Parent normalization and shared-boundary reconciliation

Read `git show b09e2af8b6b13c4bbc9d3e8d84b9929178ffb70b:backend/src/services/order_service.py`: the base `_normalize_daypart_key` already returns `昼` for `lunch`; the base `_build_position_menu_entries` places that value in `daypart_key`. The base physical-row helper prefers daypart_key and joins it unchanged. Comparing the entire base/current normalization function with `diff -u` returned exit 0, no difference. Therefore the producer expectation `2026-10-07__昼__0` reconciles the unchanged base contract, not a new canonical ID. Raw helper-key cases remain unchanged. Date, slot zero, single row and duplicate refusal assertions remain. The earlier 143/2 failed log remains preserved at tmp/delivery-proof/pytest-physical-final.log; it is historical failed evidence, not baseline green proof.

The changed-name roundtrip case now invokes BOTH actual shared boundaries: master_order_form_template_service._normalize_week_menu_entries and order_service._validate_reparse_lines_against_weekly_menu, asserting physical_menu_common_name_unresolved. The first boundary test used a month-only week value, which intentionally has no date range and returned before entry processing: r3 recorded 145 passed/1 failed. The corrected component input specifies the fixture's exact authored date range `2026-10@2026-10-07~2026-10-07`. No production fallback, date policy or boundary behavior was changed.

Final r4 command is the preceding full focused command with all bootstrap/basetemp/XML/log `physical-r2` identifiers replaced by fresh `physical-r4` identifiers. Exit 0: 146 passed, 74 deselected, 95 warnings, 10.24s. Original first-two quantities and third six cells remain unchanged. New stop assertions prove shared boundary refusal, NOT successful changed-name OCR. The unresolved operator contract is how an undifferentiated physical display/cell plan should represent distinct diet-specific names without selecting a representative; no answer was invented.

- tmp/delivery-proof/results-physical-r4.xml SHA256: a21fdc8744f9957be4c1319cf28037d819e5cabf61c34581948d6ec84df73039
- tmp/delivery-proof/pytest-physical-r4.log SHA256: 33ac0f4368c40f0785116445944c58104c9a53cfa03549a4ad7fad0e34470699
- backend/tests/unit/test_h2_geometry_physical_identity.py SHA256: 20fd12ad7292558040b7fb94cdd50318753bb65ab786ce98e1d6ac8ab7f92860

Ten-file source packet refrozen for parent monitoring. No commit/integration/deploy or full-consumer acceptance claim.

## Complete captured weekly physical inventory

The subsequent output consumer defect was output-subset date/daypart scoping followed by out-of-bounds diet-row expansion. `_build_monthly_entry_physical_rows` now builds ONE complete captured weekly inventory grouped by actual date, normalized daypart and exact nonnegative integer slot. It never scopes by output lines, substitutes ordinal slots or expands diet rows. Same-diet duplicates stop. Source indexes require exact integers in range in both inventory validation and row resolution. Split markers, their exclusion recovery, and the obsolete physical sort helper were removed. Existing diet inheritance/soft_mixer matching and priority remain unchanged; tied best candidates now stop instead of selecting first.

Explicit reconciliation of test_monthly_entry_physical_rows_keep_diet_specific_rows_with_global_source_indexes: the two dinner slot-1 diet variants share full-week index13 (not separate13/14). The following canonical slots occupy14/15, total16 physical rows. Original diet-specific names remain asserted. Old split-row exclusions were replaced by the matching variant assertions on the SAME row. The subset containing only the last output preserves the identical full-week inventory. New tests reject negative/out-of-range/bool/float/string indexes, absent/negative/bool/float/string slots, duplicate same-diet rows and ambiguous equal-priority variant choice. No meal/quantity policy was redefined.

Final command (cwd assigned WT/backend), exit0:
```sh
env -i PATH=/usr/bin:/bin HOME="$PWD/../tmp/delivery-proof/home" TMPDIR="$PWD/../tmp/delivery-proof/temp" PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-main/tmp/v1-hospital-task-sdk-20261007:. DB_URI="sqlite:///$PWD/../tmp/delivery-proof/bootstrap-physical-r6.sqlite3" NO_GCE_CHECK=true GOOGLE_CLOUD_DISABLE_GRPC=true /Users/mmorinag/Sawa/2025.12/workspace/backend/.venv/bin/python -m pytest tests/unit/test_h2_geometry_physical_identity.py tests/unit/test_h2_canonical_workflow_template.py tests/unit/test_h2_output_acceptance_receipt.py tests/integration/test_bagging_delivery_slots.py tests/integration/test_daily_output_bundle.py tests/unit/test_output_builder_diet_overrides.py -k 'not ikebukuro and (not test_daily_output_bundle or test_authored_delivery_template_uses_display_menu_name) and (not test_output_builder_diet_overrides or physical_rows or captured_physical)' -q -p no:cacheprovider --basetemp=../tmp/delivery-proof/cases-physical-r6 --junitxml=../tmp/delivery-proof/results-physical-r6.xml > ../tmp/delivery-proof/pytest-physical-r6.log 2>&1
```
158 passed,103 deselected,95 warnings,9.92s. Earlier r5:157passed,103deselected,exit0. The full previously assigned packet selection was retained, plus only relevant physical/source-index diet-override cases. Original first-two quantities/null and third six cells remain unchanged. Fifth legacy delivery case and unrelated daily/diet tests were not rerun. Changed-name undifferentiated operator contract remains unresolved as documented above; this is not successful changed-name OCR or whole acceptance.

SHA256, relative to assigned WT:
- tmp/delivery-proof/results-physical-r6.xml:2bd7062ee34c440a754b61398717a3c511d7f2ed85b27b8894ec71584a509d16
- tmp/delivery-proof/pytest-physical-r6.log:4735fe5375c59636b87b117f7eb56e4aafe4fee44311ed1c3969f6c0f960ef6a
- backend/src/services/output_builder.py:364d93de73f372d0e1f784e95e4e39355fa888eea25ecfe9eeb9c035aa29e835
- backend/tests/unit/test_output_builder_diet_overrides.py:20925749e121ea39ac3bc23d708620ab74fe02e555b9c1845b006e3232267bda

Eleven-file packet frozen on unchanged base for parent review. No commit/main/develop/release write/push/deploy.

## Explicit side-number preservation correction

Parent monitoring rejected the r6 resolver: comparing every explicit numbered side against enumeration rank incorrectly refused authored 副菜2 alone and explicit gaps. This NOT OK remains historical; r6 green tests did not establish explicit-gap support.

The shared captured resolver now preserves supported explicit numbered categories unchanged. Only generic 副菜 receives its ordinal from the COMPLETE distinct captured side-slot inventory. A generic ordinal colliding with an explicitly assigned number on another slot stops delivery_monthly_slot_ambiguous; numbers are never shifted to escape the conflict. Same-slot diet variants collapse once; contradictory same-slot categories and duplicate explicit number assignments on distinct slots stop.

Added actual shared enrichment/label positive cases: 副菜2 alone, 副菜1+副菜3 gap, generic-before-explicit2 => 副菜1+副菜2. Added generic/explicit collision and conflicting explicit assignment refusals. Existing full-source subset/excluded-first cases, mainA/fullwidth alias, unchanged breakfast display helper, quantity/null and all six third XLSX assertions remain.

Executed the exact preceding r6 command once after this correction, replacing each owned bootstrap/basetemp/XML/log physical-r6 identifier with fresh physical-r7. Same six test files and exact selection expression; no broad suite. Exit0:163 passed,103 deselected,95 warnings,10.05s.

SHA256 relative to assigned WT:
- tmp/delivery-proof/results-physical-r7.xml:7a669a352191f89fcb92c890d7e9ebac0275dc823bd3aa41beb4db2e5f3dd17c
- tmp/delivery-proof/pytest-physical-r7.log:a81dee89a06b719f8a4b090f531e47cc8fafd58f599f27883eb5b79177f4cd5f
- backend/src/services/output_builder.py:87991cc06e07f496d8c10e7e47e9c7d9c29003754e8f9f0d6e5036855d603565
- backend/tests/unit/test_h2_output_acceptance_receipt.py:fd6091d4282fa8c20e8855094ca7d34d37f26c1e3d58a043b0381df0be1ce691

Eleven-file packet refrozen for monitoring, no commit/main writes/deploy. Changed-name undifferentiated operator-contract ambiguity remains; no changed-name OCR/all-fallback-absence/whole-modernization completion claim.

## Repeated explicit number correction

Subsequent parent NOT OK rejected the added distinct-slot explicit-number uniqueness constraint: existing writers legitimately accept several rows with explicit 副菜2. The earlier gap/rank NOT OK and this separate duplicate-number NOT OK both remain historical. Removed the invented uniqueness guard and replaced its refusal test with positive enrichment/label/writer coverage. Explicit numbers, including repeats and gaps, remain literal in shared enrichment/label. A set of explicitly assigned numbers is used ONLY to refuse collision with a generic inferred ordinal. Same-physical-slot contradictory categories still stop; no quantity/slot/receipt or diet rules changed.

The positive writer case retains two separate names own0/own1, quantities5/7, and categories 副菜2/副菜2 through actual enrichment and shared label assignment. Actual delivery rows apply the unchanged `_delivery_display_category_label`, displaying 副② for each XLSX row. Neither this display helper nor breakfast display was changed. r8:162passed/1failed because the new test columns used arbitrary category/name keys instead of actual row keys; r9:162passed/1failed because the test incorrectly expected raw 副菜2 after the existing delivery display conversion. Both failed logs remain preserved. The corrected exact XLSX expectation is two 副② rows with original names and5/7, not row merging or renumbering.

Final coherent focused command: the exact six-file r6 command above with each owned bootstrap/basetemp/XML/log physical-r6 identifier replaced by fresh physical-r10. Exact selection expression retained. Exit0:163 passed,103 deselected,95 warnings,10.71s. Existing original quantities/null/six cells, aliases, full-week inventory, source-index refusals, subset/excluded-first and invoice roots remain in this packet regression.

SHA256 relative to assigned WT:
- tmp/delivery-proof/results-physical-r10.xml:9715012c99c96f39b49c2d0f786930b0a4af3e11839351e99f45137d763c07d4
- tmp/delivery-proof/pytest-physical-r10.log:6714ce2d76ffcd183177a0b92af913c0360d9ebf3133593823dfae6a3d336ac5
- backend/src/services/output_builder.py:f4380ca5430e1605ea8e719b20c7401fb48cee4252da401b6943e8a9c290802d
- backend/tests/unit/test_h2_output_acceptance_receipt.py:eac32890d714e2179f1a8caa4167044dac8ee0620a9347a965b9855970d81ffa

Eleven-file packet refrozen for parent review; no commit/main/deploy. Changed-name OCR and whole-modernization acceptance remain unproved.
