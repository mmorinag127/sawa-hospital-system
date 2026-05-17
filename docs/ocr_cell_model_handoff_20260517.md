# OCR Cell Recognition Current-State Handoff

Date: 2026-05-17

## Purpose

This document summarizes the current Sawa OCR implementation and the constraints that matter when considering a custom cell-recognition model in a separate discussion.

The target problem is not full-page OCR. The important path is already narrowed to known or inferred table cells, especially quantity cells, then recognizing small digit values from those crops. The current implementation is therefore a good candidate for a small custom recognizer if the recognizer can outperform the current Yomitoku-based cell path under noisy FAX conditions.

## Desired Custom Model Requirements

- Fast CPU-only inference.
- Batch inference over many cropped cells.
- Robustness to imperfect border removal, FAX noise, thresholding artifacts, and slight crop misalignment.
- Correct output even when a value has been corrected with marks such as diagonal cancellation lines.
- Input/output compatible with the existing preprocessing and postprocessing surfaces.
- Trainable locally on one GPU.
- Recognition accuracy target: 98% at the cell level, measured on labeled production-like cells, including hard negatives and corrected cells.

## Current Production-Like OCR Flow

The main OCR pipeline lives under `workspace/ocr_pipeline/app/`.

High-level flow:

1. Render PDF page to PNG.
2. Build three page images:
   - template matching image
   - OCR image with table lines removed
   - alternate OCR image retaining lines
3. Match/resolve the template and warp the page to template coordinates.
4. Crop configured or inferred ROIs from the warped image.
5. For quantity cells, OCR each cell individually.
6. Parse digit-only candidates, vote across variants, reject low-confidence or impossible values.
7. Return structured `qty`, diagnostics, failed cells, overlay rows, and cell issue records.

Key files:

- `workspace/ocr_pipeline/app/main.py`
- `workspace/ocr_pipeline/app/preprocess.py`
- `workspace/ocr_pipeline/app/template_match.py`
- `workspace/ocr_pipeline/app/rois.py`
- `workspace/ocr_pipeline/app/postprocess.py`
- `workspace/ocr_pipeline/app/yomitoku_runner.py`
- `workspace/ocr_pipeline/src/data/fax_templates.yaml`
- `workspace/backend/src/data/fax_templates.yaml`

## Page Preprocessing

`build_images_for_match_and_ocr()` decodes a rendered PNG, converts it to grayscale, denoises with OpenCV fast non-local means, binarizes by Otsu threshold, then builds:

- `match`: binarized image for template matching.
- `ocr`: line-suppressed image for OCR.
- `ocr_keep_lines`: binarized image retaining lines as an alternate source.

Line suppression currently removes horizontal and vertical components using fixed morphology kernels:

- horizontal kernel: `(40, 1)`
- vertical kernel: `(1, 40)`

Important implication for a custom model:

- The model should not assume perfect line removal. The pipeline already has both line-removed and line-retained variants because line removal can erase useful strokes or leave border remnants.
- Training should include both cleaned and line-retained/line-damaged crops.

## Template, Warping, And ROI Cropping

Template ROI extraction is in `_run_template_roi_extraction()` in `workspace/ocr_pipeline/app/main.py`.

The pipeline:

- renders the PDF page;
- builds match/OCR/alternate images;
- calls `choose_template_and_warp()`;
- loads template config;
- calls `crop_rois()`;
- passes crops into `postprocess_and_retry()`.

`crop_rois()` supports quantity-cell boxes from:

- explicit `boxes_row_major`;
- configured `column_edges` / `row_edges`;
- dynamic grid-edge detection;
- dynamic row detection from text bands;
- inferred quantity-column schema from header words when configured.

For quantity cells it outputs:

- `qty_cells`: crops from the line-suppressed warped image.
- `qty_cells_alt`: matching crops from the alternate line-retained warped image.
- `qty_schema`: row count, column count, row names, column names.

Important implication for a custom model:

- The recognizer input boundary can be `qty_cells` plus optional `qty_cells_alt`.
- The recognizer should preserve row-major ordering and return per-cell values plus confidence/diagnostics so downstream code can keep existing failure handling.

## Current Quantity Cell Recognition

Quantity cell OCR is handled in `postprocess_and_retry()` in `workspace/ocr_pipeline/app/postprocess.py`.

For each cell:

1. Build image variants.
2. Run OCR on each variant.
3. Normalize full-width digits.
4. Extract digits only.
5. Reject outputs that do not match `qty_regex`, default `^\d{0,2}$`.
6. Compute a heuristic confidence from:
   - agreement votes
   - digit purity
   - exact digit-only match
   - ink quality
   - conflict penalty
7. Accept if:
   - enough votes and confidence exceeds `qty_min_confidence`; or
   - only one candidate value exists and confidence exceeds `qty_high_confidence`.
8. Reject values above configured `qty_max_value` / `qty_max_value_by_col`.

Default and configurable knobs include:

- `qty_ocr_engine`: currently Yomitoku; `tesseract_digits` has been removed and now raises.
- `qty_regex`: default two digits or empty.
- `qty_agree_votes`: default 2.
- `qty_min_confidence`: default 0.58.
- `qty_high_confidence`: default 0.67.
- `qty_min_digit_purity`: default 0.5.
- `qty_min_ink_ratio`: default 0.003.
- `qty_max_ink_ratio`: default 0.35.
- `qty_reject_multiline_bands`.
- `qty_tight_crop`, `qty_tight_crop_padding_px`, `qty_tight_crop_min_ink_ratio`.
- `qty_target_min_dim_px`: default 72.
- retry `max_attempts`, `crop_inset_px`, `alt_binarize`.

Current output per cell includes:

- parsed numeric value or `None`;
- confidence;
- vote count;
- route such as `agree_votes_2`, `high_conf_single`, `reject_low_confidence`, `reject_sanity_fail`;
- max ink ratio;
- raw OCR texts.

Important implication for a custom model:

- The model should return enough information to replace or simplify `_choose_qty_candidate()`: `value`, `confidence`, optional top-k candidates, and a reason for blank/uncertain/rejected.
- It should distinguish true blank cells from unreadable cells. Existing code treats blank, low-confidence, sanity failure, and missing ROI differently.

## Existing Cell Variant Preprocessing

For each quantity cell, `_build_qty_variants()` currently creates variants such as:

- raw crop;
- fallback prepared crop from alternate or primary image;
- primary prepared crop;
- alternate raw crop.

Preparation can include:

- crop inset;
- grayscale conversion;
- line suppression;
- stroke connection;
- adaptive binarization;
- tight crop to ink;
- upscaling to a minimum dimension.

Important implication for a custom model:

- There are two reasonable integration choices:
  - model consumes the same variants and performs batched prediction over variant groups;
  - model owns preprocessing and consumes minimally processed crop pairs.
- For robustness, training should include the failure modes created by current preprocessing: clipped strokes, leftover borders, line-erased digit segments, connected noise, and over-tight crops.

## Yomitoku Dependency Today

The current ROI OCR function calls `ocr_image_text(image_bgr, device=YOMITOKU_DEVICE)`.

Yomitoku is also used for:

- page/table OCR and layout extraction;
- OCR words for inferred header positions;
- the quantity-cell recognizer experiments described below.

Important implication:

- A custom quantity recognizer does not have to replace all Yomitoku usage initially. It can replace only the cell-level quantity recognizer while leaving template selection, header-word inference, and full-page/table extraction intact.

## Hakodate Cell OCR Batch / Contact Sheet Path

There is a separate Hakodate-focused cell OCR batch path in `workspace/backend/src/services/hakodate_cell_ocr_batch_service.py`.

This path:

- rectifies the FAX page to template coordinates;
- expands each target cell crop;
- erases known cell borders from the crop;
- removes only tiny connected-component noise;
- pastes crops into a contact sheet;
- runs OCR over the contact sheet;
- maps recognized words back to cell slots;
- emits assignment grids, overlays, and diagnostics.

Relevant preprocessing functions:

- `_expanded_cell_box()`
- `_erase_known_cell_frame()`
- `_remove_small_noise_only()`
- `_preprocess_cell_crop()`
- `build_cell_contact_sheet()`

Important implication:

- This path is closer to the desired custom-model inference pattern: many cells batched together, stable geometry, and per-cell mapping.
- The contact-sheet approach was used for batching OCR through Yomitoku, but a custom model can instead batch tensors directly and avoid contact-sheet word assignment.

## Yomitoku TextRecognizer Direct Trial

`workspace/backend/src/hakodate_best_method_runtime/run_text_recognizer_trial.py` contains a direct TextRecognizer experiment.

It builds recognizer-ready contact sheets with modes:

- `raw`
- `clean`
- `dynamic`

It uses:

- frame removal;
- table-line mask removal;
- Gaussian blur;
- histogram equalization;
- Otsu threshold;
- noise component filtering;
- foreground centering into fixed-size slots;
- candidate skipping by ink area and ink height;
- top-k sequence candidates from a custom `YomitokuTextRecognizerTopKWrapper`;
- score threshold acceptance.

The recognizer trial records:

- raw text;
- normalized digits;
- score;
- direction;
- top-k candidates;
- accepted candidate;
- crop mode;
- ink stats;
- skipped state.

Failure analysis exists in `workspace/backend/src/hakodate_best_method_runtime/analyze_text_recognizer_failures.py`.

Known failure buckets include:

- false positive on blank truth;
- no raw text;
- correct raw text rejected by score;
- wrong digits rejected;
- extra digit or correction mark;
- missing digit;
- wrong digit;
- ink present but low score/no digit;
- expected value but no usable ink.

Important implication:

- These failure buckets should become evaluation labels or analysis dimensions for the custom model.
- Correction marks and diagonal cancellation lines are already recognized as a failure class: they can become explicit training augmentation and test slices.

## Current Downstream Contract

The ROI extraction result includes:

- `template_id`
- `facility_name`
- `menu_band`
- `qty`
- `qty_row_order`
- `qty_col_order`
- `qty_cell_diagnostics`
- `failed_cells`
- `notes`
- `metrics`
- `disable_overlay_rows`
- optional table rows/errors/fax datetime

`qty` shape:

```json
{
  "row_key": {
    "col_key": 12
  }
}
```

Cell diagnostics shape is approximately:

```json
{
  "row_index": 0,
  "row": "r0",
  "field": "qty.regular_x",
  "col": "regular_x",
  "value": 12,
  "confidence": 0.82,
  "votes": 2,
  "route": "agree_votes_2",
  "max_ink_ratio": 0.041,
  "max_allowed": 99,
  "raw_texts": ["12"]
}
```

Important implication:

- A custom recognizer should keep this contract or provide a thin adapter into it.
- Confidence must be calibrated enough for existing review/block behavior. A high raw accuracy model without reliable uncertainty will still create operational risk.

## Data Already Available Or Implied

Potential training/evaluation sources in the repository:

- Generated/verified cell crops under `out/label_pack_*`.
- Label-pack generation script: `scripts/make_label_pack.py`.
- No-VLM local OCR PoC: `scripts/run_batch_no_vlm.py`.
- Hakodate OCR batch outputs under `workspace/tmp/` when present.
- TextRecognizer trial artifacts under `workspace/tmp/hakodate_text_recognizer_trial_20260428/` when present.
- Correct/incorrect failure analysis generated by `analyze_text_recognizer_failures.py`.
- Production-like order PDFs and generated overlays in `out/`, `tmp/`, and backup directories.

The master template workbook is locked by project policy and must not be modified for data generation. Any template-derived experiments should use copied/disposable outputs.

## Model Design Considerations

Recommended first target:

- Quantity-cell recognizer only.
- Output classes: blank plus digit sequences `0` through `99`, or a sequence model constrained to 0-2 digits plus blank.
- Batch input: normalized cell crop tensor, optionally paired variants.
- Output: top-k values with probabilities/confidence and a blank probability.

Why not full OCR first:

- The task is mostly constrained to small numeric cells.
- Existing code already handles page alignment, ROI extraction, schema mapping, sanity bounds, and diagnostics.
- Full-page OCR replacement would have a larger blast radius and weaker immediate measurement.

Recommended robustness training slices:

- clean handwritten/printed numbers;
- blank cells;
- leftover horizontal/vertical borders;
- partially erased strokes from line suppression;
- diagonal cancellation/correction marks;
- overwritten corrections where old and new values coexist;
- small speckles and FAX noise;
- low contrast / faded FAX;
- tight-cropped digits;
- shifted crops;
- merged cells or neighboring text leakage;
- unusually thick handwriting;
- one-digit vs two-digit ambiguity;
- values above configured max that should be rejected downstream.

Recommended augmentations:

- random crop jitter;
- border remnants;
- synthetic horizontal/vertical table lines;
- diagonal strike lines;
- blur;
- threshold variation;
- dilation/erosion;
- salt-and-pepper noise;
- contrast/gamma changes;
- small rotation/shear after rectification;
- partial occlusion near borders.

## Accuracy Measurement

The 98% target should be defined before model selection.

Suggested metrics:

- cell exact match accuracy over non-empty cells;
- blank/non-blank classification accuracy;
- false positive rate on blank cells;
- false negative rate on filled cells;
- exact match accuracy on correction-mark cells;
- exact match accuracy on noisy/border-remnant cells;
- accuracy by field/column;
- confidence calibration, especially error rate above review auto-accept threshold;
- throughput on CPU for a realistic order batch.

Minimum evaluation set:

- reported production-like cases;
- at least one close sibling case from the same failure class;
- explicit stop/review behavior when confidence is low or inputs are ambiguous.

## Integration Shape For A Custom Recognizer

Lowest-risk integration point:

- replace the per-cell `ocr_fn()` calls inside `postprocess_and_retry()` with a batched recognizer adapter for quantity cells.

Better medium-term shape:

- add a `qty_recognizer` interface that receives:
  - `qty_cells`
  - `qty_cells_alt`
  - schema metadata
  - template postprocess config
- returns:
  - row-major predictions
  - confidence
  - top-k candidates
  - blank/unreadable state
  - preprocessing route
  - debug stats

The existing downstream result contract can stay unchanged:

- `qty`
- `qty_cell_diagnostics`
- `failed_cells`
- `metrics`

## Open Questions For The Model Discussion

- Should the model predict classes (`blank`, `0`-`99`) or digit sequences?
- Should correction marks be treated as visual noise, or should there be a separate `corrected/ambiguous` signal?
- Should the model consume raw, line-suppressed, alternate, or paired crops?
- What is the acceptance policy when top-1 is high but top-2 is semantically close?
- How much labeled real data exists for blank cells, corrected cells, and difficult noisy cells?
- Is 98% required across all cells, non-empty cells only, or auto-accepted cells only?
- What CPU latency target is acceptable per page/order?
- Should model confidence replace the current vote heuristic or feed into it as one candidate source?

## Practical Recommendation

Start with a small quantity-cell recognizer that preserves the current ROI and postprocess contract. Train/evaluate it on real cropped cells plus synthetic augmentations for border remnants, FAX noise, diagonal corrections, and crop jitter. Treat blank detection and calibrated confidence as first-class outputs, not side effects.

The first success criterion should be not just aggregate 98% exact match, but 98% with bounded false positives on blank cells and a clearly lower failure rate on correction-mark/noisy-border cells than the current Yomitoku cell path.
