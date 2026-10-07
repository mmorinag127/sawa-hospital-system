# New bagging source capture

Assigned WT: /Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-v1-source-capture
Branch: codex/modernization-v1-hospital-source-capture-20261008
Generating base HEAD: 3dd2f5ffdbee78707a22ab0639ce8220ac62b97e plus this frozen source diff. Not parent integration proof.

Failure class: revision-independent 300-second lookup caches can substitute stale mutable monthly/master/rule sources into NEW bagging capture. Shared boundary is build_order_lines_for_outputs producer branch and run_bagging before artifact persistence. Invariant: new producer captures actual current sources and verifies them before adoption; accepted output_lines/receipts remain frozen.

## Source changes

- output_builder.py removes four TTL caches and their bucket/conversion wrappers. One current source snapshot captures complete weekly items/entries, rules, and every actually used master-default name group. Repeated overlapping master reads compare values and existing source revisions, refusing changes instead of replacing the earlier snapshot. Existing resolve_menu_defaults_in_session supplies exact master source revisions. Existing canonical JSON serialization preserves supported typed dates and raw metadata.
- order_workflow_v2_service.py copies this snapshot into materialization_candidate, covered by its existing digest, then rereads/compares at run_bagging before either artifact save. No export consumer recapture or new field requirement on accepted old receipts. No capture-at-bagging policy change.
- menu_rule_service.py turns an actual active-rule read exception into menu_rules_source_unresolved instead of empty rules. Unexpected other-source exceptions are not silently converted into valid snapshots.
- Removed the shadowed first index/override definitions and exclusive legacy resolver after bounded source inventory established no early references/default bindings/module calls/external resolver callers. One index/override implementation remains. No source:name or old-category fallback reintroduced.
- Existing tests referring to deleted private cache readers now reference equivalent uncached current readers, preserving their original assertions. No business serializer, quantity rule, diet inheritance, physical master workbook or schema change.

Three touched handwritten production files (line counts include blank/comments, exclude tests/docs/generated): output_builder 6581->6496; menu_rule_service 230->230; order_workflow_v2_service 4446->4452. Total11257->11178, net-79. This is source size accounting, not progress/acceptance credit.

## Owned proof and boundaries

Seven new cases use real formal SQLite migrations and actual selected-date synthetic PDF/JSON registration, original receipt/evidence, save and bagging. Only external recognition is a component double; these are NOT live/customer/operator OCR proof. Actual item/master/rule writes on the same selected facility/week under a fixed clock are seen by subsequent bagging. Actual output values are item quantity-per-serving2, rule quantity-per-serving3, master temperature 冷; raw order quantity remains7. Stored old receipt payload/digest and its captured output_lines remain unchanged. Master snapshot normalized temp is cold, while existing item/output serialization retains 冷; no production normalization changed to satisfy tests.

Read-fault cases explicitly use doubles: changed rule reread and active-rule read failure occur before artifact persistence, leaving receipt inventory unchanged. Overlapping default-name reads with changed values/revisions stop. Direct real list_active_rules error handling is tested with a session read fault. These fault doubles are not the sole adoption proof; the three positive updated-source cases use actual database writes.

Adjacent fixture reconciliation: only fax_template_override->fax_template changed in test_daily_labels_derive_comparable_soft_from_facility_quantity_columns; exact 軟菜 label/quantity assertions remain. The base/current _daily_label_comparable_diet_types_for_facility static lookup function diff is empty (exit0); it already reads canonical fax_template. Formatter/display production functions were not edited. Parent historical171passed/1failed is not a new success claim.

## Executions and retained failures

Initial selection accidentally included the whole legacy diet-override file:143passed/15failed/1deselected, exit1 (tmp/source-proof/pytest.log). Two new cases failed: condiment update legitimately added another quantity7 row, and an attempted concurrent SQLite writer was blocked. The fixture now tests a real master temperature update, and the concurrent reread fault is an explicitly labelled fault double. The other13 diet-override failures remain unresolved outside this narrow source-capture task; no assertions were removed to claim full-file success.

r2:170passed/1failed/29deselected, exit1; expected raw 冷 in normalized defaults, which existing source normalizes to cold. r3:171passed/29deselected, exit0. A subsequent strict actual-output assertion run (results-values.xml) was6passed/1failed, exit1: it expected cold in output, while unchanged item/output serialization retained actual 冷. The assertion now checks the existing exact source representation. All failed logs retained, not overwritten.

Final coherent command, cwd assigned WT/backend:
```sh
env -i PATH=/usr/bin:/bin HOME="$PWD/../tmp/source-proof/home" TMPDIR="$PWD/../tmp/source-proof/temp" PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-main/tmp/v1-hospital-task-sdk-20261007:. DB_URI="sqlite:///$PWD/../tmp/source-proof/bootstrap-r4.sqlite3" NO_GCE_CHECK=true GOOGLE_CLOUD_DISABLE_GRPC=true /Users/mmorinag/Sawa/2025.12/workspace/backend/.venv/bin/python -m pytest tests/unit/test_h2_output_source_capture.py tests/unit/test_h2_output_acceptance_receipt.py tests/unit/test_h2_geometry_physical_identity.py tests/unit/test_h2_canonical_workflow_template.py tests/integration/test_bagging_delivery_slots.py tests/integration/test_daily_output_bundle.py::test_authored_delivery_template_uses_display_menu_name tests/unit/test_output_builder_diet_overrides.py -k 'not ikebukuro and (not test_output_builder_diet_overrides or physical_rows or captured_physical or daily_labels_derive_comparable)' -q -p no:cacheprovider --basetemp=../tmp/source-proof/cases-r4 --junitxml=../tmp/source-proof/results-r4.xml > ../tmp/source-proof/pytest-r4.log 2>&1
```
Exit0:171passed,29deselected,131warnings,14.55s. Original first-two quantities/null, third six XLSX cells, physical whole-week/category/invoice tests remain. Fifth legacy delivery case and unrelated diet cases are not acceptance proof.

## SHA256

Paths relative to the assigned absolute WT above:
- tmp/source-proof/results-r4.xml:7bfb5e13e08e494c5c0a3693a26b482f78ee93ec4524ec6a3fff604e07f997c3
- tmp/source-proof/pytest-r4.log:8d2b4eeeafb79fb8b27da519aa9b9409145e232fa6063f5c9349002b4afdd1af
- backend/src/services/output_builder.py:a85e5d880d51c98c81f73c99bfbd2267748efe495a46d5c80f995681ab87e008
- backend/src/services/menu_rule_service.py:2115b1d0f3d7648eebb666b4c042c5beba775cf28b3de5f74973294f2a8b65da
- backend/src/services/order_workflow_v2_service.py:b34e0c762eb1498b62dd1515973716007cf843e9186eff3177fcf9b8053cd18a
- backend/tests/unit/test_h2_output_source_capture.py:9193739834ff3ed348171f80c2a949e5368947effc1ac533aac48a6f3a7016aa
- backend/tests/unit/test_h2_output_acceptance_receipt.py:ecb5756e30c71954a7f8eb8320bdbf6ab6b79c1f179d7a293b8c670f6e8dd765
- backend/tests/unit/test_output_builder_diet_overrides.py:637c170ac9417cb52d4f1ca5229bb1758fa9d614b998320934f301ca1fd18d5e

Seven-file packet frozen for parent monitoring. No commit/main/develop/release writes/push/deploy/browser. No all-code/DB/whole-modernization completion claim. Existing changed-name physical display, units and date-policy holds are unaffected. Parent integrated-SHA regressions remain required.
