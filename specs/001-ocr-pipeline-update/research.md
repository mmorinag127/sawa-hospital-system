# Research: Facility OCR Pipeline

## Scope

Define how the OCR pipeline should classify facility-specific order formats, extract ROI-level values, and produce structured outputs with auditability.

## Current State Observations

- Inputs are weekly order PDFs with facility-specific layouts.
- Extraction must be template-driven to avoid facility hardcoding.
- Quantity grids are the primary output; notes and facility name are secondary.

## Decisions

- Template selection is based on a best-match score with thresholds; no match yields an unclassified result.
- Extraction is ROI-based and aligned to a normalized template coordinate space.
- Quantity cells are validated for numeric-only values and retried with alternate preprocessing before being marked as failed.
- Duplicate input generations do not create additional jobs or outputs.

## Risks and Mitigations

- Template drift or scan variation can reduce match confidence.
  - Mitigation: versioned templates and alignment thresholds per template.
- OCR noise can produce repeated garbage values.
  - Mitigation: repetition detection and targeted retries on failed cells.
- Missing template registration blocks extraction.
  - Mitigation: unclassified queue with stored artifacts for review.

## Out of Scope

- Training custom OCR models.
- Automating template registration from unknown formats.
- Multi-page order PDF handling.
