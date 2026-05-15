# yomitoku 0.13.0 exact stg comparison memo

Date: 2026-05-15

## Purpose

Evaluate whether yomitoku can be upgraded from 0.9.5 to 0.13.0 without changing the Hakodate OCR pipeline.

The comparison is intentionally limited to the OCR runtime dependency. It must not include preprocessing, crop, threshold, postprocess, template, or assignment changes.

## Fixed Inputs

- Source code: exact current stg backend deploy source
  - `/Users/mmorinag/Sawa/2025.12/worktrees/integration/backend-deploy-20260514-222231-stg-backend`
- Target orders: stg live 2026-04-26 to 2026-04-30 active 14 orders
- Correct labels: stg live workflow-v2 sheet values for each order
- Baseline runtime: yomitoku 0.9.5
- Test runtime: yomitoku 0.13.0

## Label Verification

The generated `best_method_records.json` files are derived artifacts, not direct API responses. The script fetched the stg live workflow-v2 sheet and embedded those values into `expected_digits`.

Verification was run against current stg live sheets after generation:

- Orders checked: 14
- Saved fixture vs current stg live sheet mismatch: 0
- `best_method_records.json expected_digits` vs current stg live sheet mismatch: 0

Verification artifact:

- `/Users/mmorinag/Sawa/2025.12/workspace/tmp/stg_live_label_verify_20260515/stg_live_label_verification.json`

## Results

All cells:

- 0.9.5: 3484 / 4005 = 86.99%
- 0.13.0: 3429 / 4005 = 85.62%

Non-empty correct-label cells:

- 0.9.5: 907 / 1126 = 80.55%
- 0.13.0: 835 / 1126 = 74.16%

Version delta:

- 0.9.5 correct, 0.13.0 wrong: 139
- 0.9.5 wrong, 0.13.0 correct: 84
- Both wrong: 437
- Both correct: 3345
- Prediction changed between versions: 324

## Observed Tendency

0.13.0 is worse under the current Hakodate OCR pipeline.

The most important degradation is `wrong_length`:

- 0.9.5 wrong_length: 22
- 0.13.0 wrong_length: 71

Typical failures:

- `2 -> 20`
- `1 -> 11`
- `0 -> 00`
- short handwritten values are more likely to gain extra digits.

Facility-level degradation is largest in:

- FAC00008
- FAC00009
- FAC00012

Stable or equivalent cases:

- FAC00007
- FAC00014
- FAC00015

## Artifacts

Primary outputs:

- 0.9.5 overlay:
  - `/Users/mmorinag/Sawa/2025.12/workspace/tmp/yomitoku_exact_stg_orders_095_all/exact_stg_order_overlay_all.pdf`
- 0.13.0 overlay:
  - `/Users/mmorinag/Sawa/2025.12/workspace/tmp/yomitoku_exact_stg_orders_013_all/exact_stg_order_overlay_all.pdf`
- Side-by-side overlay:
  - `/Users/mmorinag/Sawa/2025.12/workspace/tmp/yomitoku_exact_stg_compare_095_vs_013/overlay_compare_095_vs_013.pdf`
- Accuracy summary:
  - `/Users/mmorinag/Sawa/2025.12/workspace/tmp/yomitoku_live_label_accuracy_095_vs_013_ocr_input_fixed/accuracy_summary.json`
- Cell accuracy CSV:
  - `/Users/mmorinag/Sawa/2025.12/workspace/tmp/yomitoku_live_label_accuracy_095_vs_013_ocr_input_fixed/cell_accuracy.csv`
- Incorrect-cell comparison using actual OCR input crops:
  - `/Users/mmorinag/Sawa/2025.12/workspace/tmp/yomitoku_live_label_accuracy_095_vs_013_ocr_input_fixed/incorrect_cell_samples_095_vs_013.pdf`

## Important Correction

An earlier incorrect comparison must not be used. It was invalid because it mixed non-stg OCR processing changes and used a zero-target/old fixture path.

Another intermediate sample PDF was also visually misleading because it reconstructed OCR crops incorrectly:

- `ocr_contact_crop_box` is an absolute contact-sheet coordinate.
- It was mistakenly applied after first cropping the slot, producing black crops.
- The fixed artifact uses `ocr_contact_crop_box` directly against `best_method_contact_sheet.png`.

## Decision

Do not upgrade to yomitoku 0.13.0 as-is.

If 0.13.0 is revisited later, it needs a separate adjustment branch focused on recognizer output normalization or thresholding, with the same exact-stg-source comparison harness.
