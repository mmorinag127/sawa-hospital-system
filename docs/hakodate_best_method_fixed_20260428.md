# Hakodate OCR Best Method Fixed On 2026-04-28

## Fixed Scope

This document freezes the currently accepted Hakodate-style preprocessing and OCR-positioning method as the baseline for the next production-oriented refactor.

The accepted method is fixed only as a verified local baseline. It is not yet the final production service boundary.

## Accepted Inputs

- FAX PDF page for one order/facility.
- Facility-derived template geometry from the existing Hakodate preprocessing manifest.
- Accepted outer four points from the existing four-point detection pipeline.
- Existing target cell regions generated from the facility template.

## Accepted Pipeline

1. Estimate and accept the outer four points.
2. Rectify the original FAX page using those four points.
3. Generate target cell regions from the known template structure.
4. Snap target-cell X boundaries to actual vertical FAX lines detected inside the rectified FAX.
5. Keep Y boundaries from the accepted template-derived target regions.
6. Draw green target grid lines from the snapped target-cell boundaries.
7. Draw red points at OCR target-cell centers.
8. Draw Q markers for the accepted four points.
9. Optionally draw OCR labels for local review only.

The local OCR labels used during verification are not part of the production contract. The existing OCR step, including any fixed `cell coordinate -> crop -> OCR engine` path, may be discarded during the production redesign.

## Fixed Verification Artifacts

Single-facility accepted check:

- Facility/order: `FAC00003 / ORD9d8f9c2b`
- PDF: `/Users/mmorinag/Sawa/2025.12/workspace/tmp/hakodate_text_recognizer_trial_20260428/kasuga_best_method_overlay/best_method_overlay.pdf`
- PNG: `/Users/mmorinag/Sawa/2025.12/workspace/tmp/hakodate_text_recognizer_trial_20260428/kasuga_best_method_overlay/best_method_overlay.png`

All-facility accepted check:

- Facility count: `14`
- PDF: `/Users/mmorinag/Sawa/2025.12/workspace/tmp/hakodate_text_recognizer_trial_20260428/best_method_overlay_all_facilities/best_method_overlay_all_facilities.pdf`
- Preview PNG: `/Users/mmorinag/Sawa/2025.12/workspace/tmp/hakodate_text_recognizer_trial_20260428/best_method_overlay_all_facilities/best_method_overlay_all_facilities_vertical_preview.png`
- Summary JSON: `/Users/mmorinag/Sawa/2025.12/workspace/tmp/hakodate_text_recognizer_trial_20260428/best_method_overlay_all_facilities/best_method_overlay_all_facilities_summary.json`

## First-Row Issue Verification Rule (`大豆のトマト煮`)

This verification is not an OCR-accuracy check. It is a coordinate-correspondence check for `target cell map / grid row mapping / overlay bbox / OCR crop bbox`.

### Fixed Procedure

- `一行目` とは、対象FAX上でメニュー名 `大豆のトマト煮` が見えている最初のデータ行を指す。
- `一行目` という表現だけでは不足であり、メニュー名 `大豆のトマト煮` を必ず確認する。
- 確認画像は、メニュー名 `大豆のトマト煮` と、その同一水平行の数量セルが同時に見える crop でなければならない。
- 判断前に crop 画像を提示し、目視してから結論を出す。
- `step6` と `best_method overlay` の両方で確認する。
- この確認は OCR 精度の確認ではなく、`target cell map / grid row mapping / overlay bbox / OCR crop bbox` の座標対応確認である。
- 目視確認はすべてサブエージェントに行わせる。親エージェントは crop を作る、提示する、サブエージェント結果を報告することはできるが、目視確認の完了判定をしてはいけない。
- fresh 確認では、参照している artifact が今回の fresh 実行から生成されたものかを必ず確認する。`order_id`、施設、入力FAX、template、quad/source、生成コードまたは commit/working-copy、生成時刻、artifact path を確認し、`current_fresh` 等の今回 run を示す provenance がないものは fresh evidence として使わない。
- 古い commit、別 run、過去の `stepreview`、過去 montage、または stale path を、現在コードの fresh 実行結果として提示してはいけない。

### Mandatory Visual Reviewer

- 目視 OK/NG 判定はサブエージェントだけが行う。
- 親エージェントは、サブエージェントに `step6` crop と `best_method overlay` crop を渡し、`大豆のトマト煮` と同一水平行の数量セルを確認させる。
- サブエージェントは、`大豆のトマト煮` が crop 内に見えること、判定対象の数量セルが同じ水平行にあること、緑枠・赤/青点・OCR数字がそのセル中心に対応していることを明示して判定する。
- サブエージェントが NOT OK または判定不能とした場合、親エージェントは完了扱いしてはいけない。

### Pass Condition

- 同じ `大豆のトマト煮` 行の数量セル中心に、緑枠・赤/青点・OCR数字が正しく対応していること。

### Fail Conditions

- 点や OCR 数字が次行以降に出ていても、`大豆のトマト煮` 行に乗っていなければ NG とする。
- 別行の点や OCR 数字を見つけても、`大豆のトマト煮` 行そのものに対応していなければ NG とする。
- crop 内に `大豆のトマト煮` が見えていなければ NG とする。
- `step6` または `best_method overlay` の片方しか確認していなければ NG とする。
- artifact provenance が不明、または古い commit/別 run を指していれば NG とする。
- 親エージェントだけで目視 OK 判定していれば NG とする。

### Forbidden Judgment Bases

- `montage` 全体
- 件数
- `row count`
- JSON 上の存在
- 別行の点
- `target_regions` の存在
- OCR 精度 metric
- 親エージェント単独の目視判断
- 古い成果物や stale montage

The items above must not be used as OK evidence or as substitute judgment bases.

## Fixed Local Scripts

These scripts are local verification scripts under `tmp/` and are not production entry points.

- `/Users/mmorinag/Sawa/2025.12/workspace/tmp/hakodate_text_recognizer_trial_20260428/render_best_method_overlay_pdf.py`
- `/Users/mmorinag/Sawa/2025.12/workspace/tmp/hakodate_text_recognizer_trial_20260428/render_best_method_overlay_all_facilities.py`
- `/Users/mmorinag/Sawa/2025.12/workspace/tmp/hakodate_text_recognizer_trial_20260428/compare_kasuga_digit_preprocess_methods.py`

## Production Refactor Boundary

The next step must not reinterpret the accepted method. It should first move this behavior into a production-oriented module boundary with the same observable behavior.

Required production inputs:

- FAX PDF or rendered page image.
- Facility information.
- Facility template geometry.
- `merged_cell` support from the template structure.

Required production outputs:

- Rectified FAX image/PDF for review.
- Overlay PDF/PNG containing four points, green grid lines, red target-cell centers, and optional OCR labels.
- Machine-readable target-cell map for downstream evidence assignment.
- Alignment evidence and quality gate state.
- OCR evidence records only after the redesigned OCR evidence layer runs.

Forbidden during refactor:

- Do not replace four-point rectification with a different alignment method without explicit approval.
- Do not remove the FAX vertical-line X snap.
- Do not add facility-specific exceptions unless explicitly approved.
- Do not treat OCR digit accuracy as proof of cell-positioning correctness.
- Do not hide failed alignment behind a cleaner preview.

## Current Tracked Code State

The tracked code change at this fix point is limited to cell crop preprocessing in:

- `backend/src/services/hakodate_cell_ocr_batch_service.py`
- `backend/tests/unit/test_hakodate_cell_ocr_batch_service.py`

That change expands OCR crops with fixed pixel padding, erases known cell borders from the crop, and removes only small noise before OCR contact-sheet generation.

This tracked change is a baseline verification aid, not a requirement that production OCR must use cell crops.
