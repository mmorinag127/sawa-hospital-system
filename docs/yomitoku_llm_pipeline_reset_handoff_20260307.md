# Yomitoku -> LLM Reparse Reset Handoff

## User Directive

The correct target architecture is:

1. Apply page-level corrections to the fax PDF/image.
2. Run `yomitoku` as the primary OCR/structure extractor.
3. Show `yomitoku` output to the user first.
4. Only when the user decides re-analysis is needed, run LLM reparse.
5. LLM reparse must receive both:
   - the original/normalized page image
   - `yomitoku` structured output
6. LLM should act as a repair/re-interpretation layer, not as a replacement OCR engine.

The ROI/template-first direction that was explored is not the desired architecture and should not be the main path.

## What Must Be Discarded

- Fixed ROI quantity OCR as the primary extraction path.
- Quantity-only LLM reparse that loses left-side anchors and later re-merges by row index.
- Treating template crop OCR as the main solution for layout drift.

These were explored and produced regressions or dead ends relative to the user requirement.

## Validated Findings So Far

### 1. Fixed ROI geometry is brittle

For row-based templates:

- `facility_name_box` frequently captured body text instead of the facility name.
- `menu_band` often captured only a few lines despite many inferred rows.

Artifacts:

- `out/hypothesis_check/roi_overlay_geometry_check.json`
- `out/hypothesis_check/overlays/ORDc6003bba_fax_layout_regular_forbidden_v1_overlay.png`
- `out/hypothesis_check/overlays/ORD1defabff_fax_layout_regular_diabetes_v1_overlay.png`

Implication:

- Fixed ROI boxes are too fragile to be the main quantity path unless normalization is very strong.

### 2. ORDc71dce69 is likely a hybrid layout

Observed structured header tokens include:

- `常食`
- `軟菜`
- `ミキサー`
- `禁食`
- `魚禁`

Artifact:

- `out/hypothesis_check/pipeline/ORDc71dce69.pipeline.json`

Implication:

- Image similarity matched `floor_2f3f`, but header semantics suggest a mixed floor/forbidden layout.
- Template image matching alone is insufficient.

### 3. ORDd2f601d8 corruption already exists in yomitoku table cells

Observed structured cell values already contain:

- `91`
- `6\n9`
- `22`
- `3\n3\n...`

Artifacts:

- `out/hypothesis_check/pipeline/ORDd2f601d8.pipeline.json`
- extracted crops under `out/hypothesis_check/yomitoku_cells/`

Implication:

- This is not only a downstream parser bug.
- `yomitoku` table cell segmentation / row span handling is part of the failure.

### 4. Header-driven semantics are still useful

Even when fixed ROI failed, header tokens helped reveal:

- `ORD1defabff` is `regular + diabetes`
- `ORDc71dce69` is not cleanly pure-floor

Artifact:

- `out/hypothesis_check/header_tokens_sample.json`

Implication:

- Header semantics should be used for classification and audit.
- They should not by themselves force fixed ROI quantity crops.

## Current Safety Guards Added

These changes were added as damage control, not as the final architecture:

1. Floor template quantity ROI can be disabled.
2. Overlay rows are suppressed when menu line count and ROI row count are inconsistent.
3. Multiline quantity cell rejection was added.

Relevant code:

- `workspace/ocr_pipeline/app/postprocess.py`
- `workspace/ocr_pipeline/app/main.py`
- `workspace/backend/src/data/fax_templates.yaml`

These guards reduce bad merges but do not solve the root architecture issue.

## Correct Pipeline To Build Next

### Stage A. Pre-correction / normalization

Apply page-level corrections before `yomitoku`:

1. Orientation correction
2. Rotation / skew correction
3. Perspective alignment
4. Optional dewarp / local distortion correction
5. Contrast / denoise / binarization variants as OCR aids

Important:

- These are page-level corrections, not fixed quantity ROI crops.
- After this stage, the page should be stable enough for structure extraction.

### Stage B. Primary OCR = Yomitoku

Run `yomitoku` on the normalized page and persist:

1. markdown
2. structured tables
3. cell bboxes
4. row/col spans
5. words/header tokens
6. page image references

This is the source of truth for first-pass OCR.

### Stage C. Issue detection

Before any LLM call, detect suspicious regions from `yomitoku` output:

1. multiline numeric cells
2. numeric cells with row spans / merged cells
3. header ambiguity
4. outlier values
5. inconsistent column meaning across rows
6. low-coverage columns
7. date/menu anchor drift

### Stage D. User-visible baseline

Show the user the `yomitoku` result first.

The user decides whether re-analysis is needed.

### Stage E. LLM reparse / repair

When explicitly requested or policy-triggered, send to the LLM:

1. normalized page image
2. original page image if useful
3. `yomitoku` markdown
4. `yomitoku` structured rows/cells
5. suspicious cell list with bbox and reason
6. facility config / known column meanings if available

LLM output should be a patch, not a full free-form replacement.

Recommended patch shape:

```json
{
  "patches": [
    {
      "page_index": 1,
      "table_id": "p1_t1",
      "row_index": 12,
      "col_index": 5,
      "field": "qty.no_fish_x",
      "old_text": "48",
      "new_text": "4",
      "reason": "yomitoku cell likely merged two rows",
      "confidence": 0.82
    }
  ]
}
```

### Stage F. Patch application and audit

Apply LLM patches only if:

1. target cell exists
2. patch is semantically consistent with header meaning
3. confidence / audit thresholds pass

If not, keep the original `yomitoku` value and surface review-required status.

## Immediate Implementation Priorities

1. Implement page-level correction pipeline before `yomitoku`.
2. Stop building new primary logic around fixed quantity ROI crops.
3. Rework LLM reparse so it consumes `yomitoku` output instead of replacing it.
4. Make LLM return cell-level patches.
5. Add cell-level issue detection from `yomitoku` structured output.

## Order-Specific Notes

### ORDc71dce69

- Likely hybrid layout.
- Needs semantic header-based layout override, not pure image-template matching.

### ORDd2f601d8

- Needs `yomitoku` cell repair for merged numeric cells.
- The issue exists before downstream parsing.

### ORDc6003bba

- Fixed ROI geometry is not trustworthy.
- Need normalized-page + yomitoku-first analysis.

### ORD1defabff

- `regular + diabetes` semantics are real.
- Header OCR can still misread `常食`, so LLM should see both image and yomitoku tokens.

## Summary

The main reset is:

- do page correction first
- keep `yomitoku` as the primary extractor
- use LLM only as a second-pass repair layer with `yomitoku` context
- avoid fixed ROI quantity OCR as the main path

