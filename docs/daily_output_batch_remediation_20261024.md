# Daily output fixture batch remediation

Base: a9376047582d7563bf36b01a3e6960aecefda8d3.
Branch/worktree: codex/agent-batch-remediation-20261024,
/Users/mmorinag/Sawa/2025.12/worktrees/agent-batch-remediation-20261024.

Evidence read: /private/tmp/sawa-hospital-full-f50d8118-20261024/00-backend-tests.log.
It lists 21 failed nodes in test_daily_output_bundle.py. Those failures belong to
the previous source/run, not to this unexecuted patch. No test, collection, app/test
import, typecheck, build, cloud operation, commit, push or deployment was run.

## Fixed boundaries

Only backend/tests/integration/test_daily_output_bundle.py and this document
are changed. Parent-owned helpers, implementation and the previous MCP changes
are untouched. Changes use actual authored registration/native selection,
formal saved-sheet revisions, bagging/confirmation and accepted reads.
Pure workbook/PDF/label components are identified separately and do not prove
an operator/live FAX workflow, OCR accuracy or full production parity.

Forbidden diets remain excluded from aggregation. Their saved quantities are
not erased or changed. Missing canonical sources/policies are not given
application defaults. Deliberately invalid persisted state is only used for
negative boundaries, never as a forged successful receipt.

## All known failures and replacements

Names below are in backend/tests/integration/test_daily_output_bundle.py.
Unless a replacement name is given, the original node name remains.

| Failed node | Cause / invariant / patch |
| --- | --- |
| test_build_outputs_download_path_does_not_write_canonical_rows | Positioned PDF glyph extraction inserted whitespace into Latin text. Retain all declared date/facility/menu/diet/area/quantity characters with whitespace-insensitive extraction comparison; separately rasterize and check nonblank rendering. This does not claim exact glyph spacing/visual typography. Existing DB snapshots, SQL no-write observer and real labels/delivery/aggregate files remain. Explicit regular comparison policy. |
| test_daily_label_csv_zero_quantity_rows_keep_facility_diet_suffix | Missing explicit label comparison policy can make an accepted output fail enrichment and leave no bundle. Declare soft/regular_bag before capture. Keep CSV diet suffixes, 0g and 0 servings; add actual saved-sheet zero/zero and typed-field assertions. No zero becomes a positive quantity. |
| test_nonwriting_materialization_rebuilds_blank_weekly_menu_from_canonical_bootstrap | Fixture now produces owned H2, so expecting lineage_missing and generic table_rows is stale. Assert actual native projection 12 and original selected-PDF digest/artifact packet. Keep formal operator 4, subsequent blank, draft_lines_empty rejection, no bootstrap and no-write assertions. |
| test_workflow_v2_lines_blocks_mismatched_bagging_lineage_with_persisted_draft | Fake session could not provide canonical facility source. Replace with actual source/select/save/bag, preserve real old candidate, then formally save again. Real receipt validator rejects the stale saved identity; public workflow output refuses missing new acceptance. No fake artifact enrichment or receipt SQL. Quantity 4 retained. |
| test_totals_blocks_persisted_workflow_without_draft_when_payload_omits_workflow_state | Removed serialize_order mock. Actual owned source without saved draft, deliberately inconsistent confirmed status plus matching quantity-4 line, actual totals query rejects accepted saved sheet missing under no-write snapshot. No fake confirmation receipt. |
| test_build_order_lines_for_outputs_requires_saved_sheet_for_output_review_state | Fake config/session fails source lookup first. Real selected source with no saved draft, caller output_review hint; actual output still rejects accepted saved sheet missing, quantity 4 unchanged. |
| test_weekly_weight_collect_rows_counts_diabetes_as_regular_and_excludes_forbidden | Replacement: test_weekly_weight_collect_rows_preserves_raw_diabetes_and_matches_current_production_buckets. Direct git source read of verified serving SHA 9c16aa0711f1864271f83050c2e889b00fddd12e shows the same weekly regular bucket as current (regular/regular_bag/staff/daycare/1600kcal), not diabetes. Original raw diabetes 5 and no_meat 6 remain; canonical diabetes identity uses the existing delivery identity function, not quantity inference. Actual saved sheet retains 5/6. Current/prod weekly expectation is 440 people/44000g, soft+mixer remains 49/4900g. No forbidden diet is restored. Source raw list is checked unchanged. |
| test_reference_daily_delivery_materializes_static_formula_labels | Current template is intentionally blank, not a live facility menu source. Explicit pure-component evening menu declarations retain all three original names and target date at zero quantity, plus the physical five quantity-column meanings. Rendered D17-D19 now explicitly include current category labels; weekday remains checked. No source-free legacy menu fallback. |
| test_reference_daily_delivery_rewrites_static_menu_cells_for_target_date | Declare physical columns and menu context. Old FAC00007/name/expected-Yamashiro mix was not a valid facility identity. Pure rendering uses explicit AUTHORED-YAMASHIRO display identity, not a borrowed live facility alias. Target date/menu/weekday assertions remain. |
| test_reference_daily_delivery_preserves_table_borders | Declare all physical reference columns; use explicit AUTHORED-FUREAI display identity rather than mismatched seed FAC00012. Retain saved workbook and every dotted-border refusal. |
| test_reference_daily_delivery_writing_values_does_not_change_template_borders | Declare physical columns and unowned pure rendering context without claiming an OCR receipt. Preserve before/after complete table border signatures and E12 quantity 3; retain current FAC00009 sheet alias soyokaze. |
| test_reference_daily_delivery_writes_excel_readable_workbook | Same physical-column declaration and explicit AUTHORED-FUREAI identity. Save/reopen through openpyxl and expected sheet identity remain. |
| test_daily_label_jp_sorts_floor_rows_by_menu_before_floor | Explicit soft comparison policy on every supplied bag, all original menu/floor/quantity/unit inputs and exact sort assertion retained. |
| test_delivery_rows_use_reference_slot_labels_and_order_for_html | Original raw order input remains. Explicit per-menu dated canonical categories/slots are authored before actual capture; real registration/native selection/save/bagging supplies accepted menu/default receipts. Exact original final five menu/slot/order assertions remain; no inferred category from raw row order. |
| test_delivery_rows_use_four_lunch_reference_slots_for_event_food | Same actual owned preparation, explicitly declare main A plus three side slots for the four unchanged event menus, dates and quantity 3. All four output assertions retained. |
| test_delivery_rows_use_reference_slots_when_source_categories_are_raw | Preserve original raw category inputs. Separately declare the eight canonical monthly slots/categories before capture, not a runtime fallback based on raw headers/order. Actual owned accepted output checks the original eight final labels and order. |
| test_authored_delivery_template_uses_display_menu_name | Required template URI was absent. Declare an actual local synthetic workbook, retain explicit menu_display/quantity mapping and include_menu_name assertions. No production template guessed. |
| test_label_bags_apply_monthly_bag_max_before_splitting | Removed cache helper mock. Prepare source first, update the exact facility/month/diet monthly item to declared 9 slices through menu_service, then register/produce/select/save/confirm using the same original descriptor. Original quantity 25 and split 9/9/7 plus per-bag limit 9 assertions retained. Explicit target date replaces contradictory stale June display-only metadata. |
| test_reference_daily_delivery_removes_static_artifacts | Empty grouped_outputs produces no named legacy sheets. Explicit empty Yamashiro and Ikebukuro groups now exercise artifact removal on actual named generated worksheets. C27, row20 and empty quantity-cell assertions retained. |
| test_daily_output_both_bundle_uses_reference_delivery_and_label_rows | Shared local selected fixture explicitly declares regular comparison policy, not an application default. Actual native/save/bag/confirm path retained, raw regular 3/no_fish 1 saved values retained, output excludes no_fish. Exact label 210g/70g/3 servings and real workbook assertions remain. |
| test_list_orders_by_line_date_returns_latest_upload_first | The log reaches MenuReadUnresolved in the first registration: no explicit recipe, not yet evidence of a second-order overwrite. Declare both facility-scoped June7 physical entries and 40g recipe before either registration. Preserve both received timestamps, messages, facilities, period and quantity 1, and exact newest-first assertion. No clear_all/reset between orders. |

## Shared preparation / scope

_owned_delivery_rows prepares unchanged quantities and explicit synthetic 1g
recipes for the slot-only components, authors the exact facility-scoped monthly
categories before capture, then uses the existing prepared-original descriptor
for actual native production and acceptance. It does not edit helpers or weaken
their source/physical-cell assertions. The 1g recipe is a declared component
prerequisite, not recognized content or a serving-weight claim for a real menu.

_declared_reference_groups declares current reference headers (regular, staff,
no_meat, no_fish, change_1) and their physical columns for pure workbook tests.
These are not claimed as accepted OCR receipts. Explicit empty/pure contexts
use no OCR metadata instead of inventing receipt fields.

Static XML inspection also confirms current blank-template A17 has no weekday
placeholder. The two weekday-rewrite cases therefore copy the real physical
template to a test-local file and explicitly author A17=(Monday) before invoking
the actual writer. The writer must change it to Sunday/Tuesday for the original
target dates. The original weekday assertions remain; the shared template/body
are not changed and a cached formula result is not fabricated.

Label comparison policy is declared before capture: regular for regular-only
cases, soft/regular_bag for zero-label case, and regular/daycare/staff/diabetes/
soft/mixer for the weekly case. Forbidden no_meat/no_fish quantities remain in
the saved source, not in the aggregation comparison policy. Missing-policy
negative specimens elsewhere are not modified.

## Parent dependencies and freeze

All 21 known causes have fixture-side source changes or a documented
production-contract correction; none is reported passed. Parent-owned common
helper/body corrections must still be integrated before any batch. In
particular, do not replace absent source/acceptance or comparison policy with a
body fallback to obtain output. Parent review must check real shared capture,
saved-only/confirmed-only date listing and receipt shape after integration.

No new evidence of application output correctness, exact rendered typography,
live OCR or staging is claimed. Static freeze is only this assigned slice;
parent review, exact integrated-SHA serial local batch and later approved
manual staging remain outstanding.
