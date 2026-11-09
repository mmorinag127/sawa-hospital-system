# OCR geometry review

## Scope

User-directed recovery for failed four-point, column, and row estimation.
Preserve original PDFs, quantities, saved edits, facility settings, and source receipts.
No production deployment or production mutation. Complete implementation before batch verification.

## Implementation Checklist

- [x] Classify recoverable geometry failures as operator review, not terminal UI errors.
- [x] Use unannotated rectified input for both diagnostic and recognition paths.
- [x] Preserve rotation when downstream code resolves the four-point input again.
- [x] Return visible correction targets and stage-specific reasons, including missing/extra boundaries.
- [x] Show one editable overlay with clear selected boundary and day/column identities.
- [x] Make quad/header failures reachable from row review and vice versa without losing source checks.
- [ ] Update regression fixtures and run one serial local batch after implementation.
- [ ] Review integration and staging verification; never deploy production.

## Investigation Evidence

- ORDcbfc37f4: raw geometry misses final date boundary 56; the diagnostic red frame falsely supplies it.
- ORD8b3b2ede: original PDF is upside down. Captured failed attempt has no rotation instruction.
  Upright raw reconstruction additionally loses the left header frame: its actual vertical mask is
  at x=4..9 while the header ROI begins at x=13. Structural subset matching then maps the first
  internal line x=100.5 to the outer frame and interpolates a nonexistent x=168.529 boundary.
  The next header-topology check rejects that mapping. Downstream quad resolution also discards
  a rotated automatically estimated quad and estimates the unrotated quad again.
- ORD7ceeb0b1: previous arbitrary subset/gap matching shifted outer-frame/column identities;
  current outer-index anchoring does not protect against an outer frame absent from detection.
- ORDf021b65f: one sloping physical ruling produced separate column-fragment Y clusters;
  the old code counted 58 instead of 56 body bands. Existing continuity repair is retained.

Evidence is in ignored tmp/*-diagnostic.json and tmp/*-root-cause-proof.json.
Investigation uses captured data locally, with no recognizer call or live rerun.
