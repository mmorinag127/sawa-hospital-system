# workflow-v2 confirmed sheet edit path

## Context

Target order investigated on stg:

- `ORDe608fed7`
- Facility: `FAC00007`
- Week: `2026-04-26..2026-04-30`
- Current workflow state: `confirmed`
- Selected OCR result is present and appears reusable.

The operator need is not OCR rerun. The needed path is:

- Keep facility/week/template fixed.
- Keep selected OCR result fixed.
- Reopen only the sheet editing step.
- Let the operator make small quantity corrections.
- Rebuild bagging/output from the corrected sheet.
- Reconfirm explicitly.

## Observed Problem

Before the user edited the sheet, current stg daily output had `placeholder (-)` rows:

```text
2026-04-28 昼 豆腐ﾊﾝﾊﾞｰｸﾞ placeholder 50
2026-04-28 昼 ピーマンしりしり placeholder 50
2026-04-28 昼 オーロラサラダ placeholder 50
```

After the user edited/saved the sheet, those `-` rows disappeared, but the same quantities moved into `regular`:

```text
2026-04-28 昼 豆腐ﾊﾝﾊﾞｰｸﾞ regular 50
2026-04-28 昼 ピーマンしりしり regular 50
2026-04-28 昼 オーロラサラダ regular 50
2026-04-28 夕 豚肉とじゃが芋の醤油炒め regular 48
2026-04-28 夕 冬瓜の水晶煮 regular 48
2026-04-28 夕 春雨サラダ regular 48
```

## Root Cause

The direct cause is not the daily output page. Daily output is reading confirmed order lines correctly.

The shift happens earlier, when a saved sheet is materialized into order lines:

- `order_workflow_v2_service.run_bagging()`
- `_build_materialization_candidate_for_saved_sheet()`
- `order_service._build_materialization_candidate_from_draft_record()`

The saved sheet currently has explicit date/menu rows. For `ORDe608fed7`, the saved sheet rows around the problem are:

```text
row 10: 04/27 昼 オムレツミートソース regular=50
row 11: 04/27 昼 白菜のｽｰﾌﾟ煮 regular=50
row 12: 04/27 昼 かにマヨ大根サラダ regular=50
row 13: 04/27 夕 鶏肉の和風あんかけ regular=48
row 14: 04/27 夕 竹輪の磯辺揚げ regular=48
row 15: 04/27 夕 茄子のポン酢和え regular=48
row 18: 04/28 昼 豆腐ﾊﾝﾊﾞｰｸﾞ empty
row 19: 04/28 昼 ピーマンしりしり empty
row 20: 04/28 昼 オーロラサラダ empty
```

But the materialization candidate produced:

```text
source_row_index=10 -> 2026-04-28 昼 豆腐ﾊﾝﾊﾞｰｸﾞ regular=50
source_row_index=11 -> 2026-04-28 昼 ピーマンしりしり regular=50
source_row_index=12 -> 2026-04-28 昼 オーロラサラダ regular=50
```

This means saved-sheet semantic row values were parsed, then the later position mapping step reassigned the date/menu by row position.

The problematic shared decision point is:

```text
_build_materialization_candidate_from_draft_record()
  -> _build_materialization_lines_from_sheet_rows()
  -> _apply_menu_position_mapping_safe()
```

For a user-edited saved sheet with explicit date/daypart/menu cells, `_apply_menu_position_mapping_safe()` must not overwrite date/daypart/menu. Position mapping is useful for raw OCR/evidence projection, but it is unsafe after the operator has edited and saved a semantic sheet.

## Required Invariant

When the source is a saved semantic sheet:

- The saved sheet's explicit `date`, `daypart`, and `menu` cells are canonical.
- Position mapping may validate alignment, but must not rewrite date/menu onto a different row.
- If the saved sheet row cannot be materialized safely, the system should block bagging/confirm instead of silently remapping.

## Proposed Confirmed-Order Small Fix Path

Add a dedicated action such as `シートだけ修正` for confirmed orders.

Behavior:

1. Keep `facility_id`, `week_id`, `template_version_id`, and `selected_ocr_result_id` unchanged.
2. Open Step3 with the current saved sheet or confirmed snapshot sheet.
3. Do not rerun OCR.
4. Do not delete the selected OCR result.
5. On sheet save, invalidate only derived downstream artifacts:
   - bagging result
   - output bundle
   - confirmed snapshot for the new revision
6. Keep existing confirmed order lines unchanged until the operator explicitly reconfirms.
7. Show the order as having an unapplied sheet revision, so the operator understands daily output has not changed yet.
8. On final confirm, materialize from the corrected saved sheet and replace confirmed lines atomically.
9. If the operator cancels, discard the edit draft and keep the existing confirmed lines.

This path avoids using OCR selection as a workaround for a small sheet correction.

## Why OCR Selection Is Not The Right Path

Re-selecting OCR can technically recreate a sheet, but it is too broad:

- It can invalidate the operator's current sheet edits.
- It can change the OCR projection even when OCR is not the problem.
- It makes small corrections depend on OCR artifacts and overlay state.
- It encourages rerunning upstream steps when the desired operation is only sheet-level correction.

The correct UX is a sheet revision path from the confirmed state.

## Required Fix Before This Path Is Safe

Before adding or enabling the small-fix path, saved-sheet materialization must be hardened:

- Disable menu position remapping for saved semantic sheets with explicit date/daypart/menu cells.
- Or make it validation-only and block when saved row semantics disagree with position mapping.
- Add a regression using the `ORDe608fed7` pattern:
  - row 10 has `04/27` and quantity `50`.
  - row 18 has `04/28` and empty quantity.
  - materialization must not create a `04/28` line from row 10.

