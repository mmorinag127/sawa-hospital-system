# Numeric Quantities And OCR Originals

## Scope

- Keep quantity fields numeric or blank; retain uncertain recognition in `quantity_ocr_review` with the exact source cell.
- Do not extract digits from prose, infer zero, or rewrite numeric OCR values.
- Generate, edit and save pending sheets. Require explicit operator resolution before materializing quantities or confirming outputs.
- Project existing saved JSON without backfilling stored history. Preserve manual numeric edits and source evidence.
- No production, IAM or budget changes.

## Progress

| Item | Status |
| --- | --- |
| Shared numeric parsing and original-text separation | Implemented |
| Read-only saved-sheet projection and strict aggregate parsing | Implemented |
| Pending-cell editor, explicit blank confirmation, output boundary | Implemented |
| Regression fixtures and mounted save/reload coverage | Updated; not executed |
| Serial local verification | First batch: backend 910 passed / 4 failed; agent 482 passed; frontend 317 passed / 2 failed; types/build passed. Native intake raw-review preservation fixed; output blocker fixture and explicit review-before-save fixture updated. Final batch pending. |
| Develop integration and manual staging deployment | Pending |
| Staging save/reload and historical preservation | Pending |

## Known Separate Accuracy Issue

Numeric OCR `909` remains unchanged. The source has a crossed-out 90 and red 92; the red correction intersects a column edge. This change does not invent 92 or claim to fix that recognition geometry defect. See the December anomaly audit for source comparisons.
