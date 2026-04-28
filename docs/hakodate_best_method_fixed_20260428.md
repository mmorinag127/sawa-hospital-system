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
9. For OCR evaluation, crop each target cell with `pad_x=1`, `pad_y=8`.
10. Erase the known cell frame from the crop using the cell bbox.
11. Remove only small connected-component noise.
12. Use the current local evaluation OCR method to overlay predicted labels for review.

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
- Machine-readable cell coordinate JSON for downstream OCR.
- Cell crop artifacts or in-memory crop objects for batch OCR.
- OCR result records keyed by worksheet cell/semantic target.

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
