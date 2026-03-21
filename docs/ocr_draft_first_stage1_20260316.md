# OCR Draft-First Stage 1

## Goal
- Keep current OCR precision-oriented pipeline.
- Stop treating every recoverable OCR/LLM issue as a total failure.
- Preserve `last-known-good` confirmed lines.
- Expose `draft ready / auto apply blocked / confirm blocked` as explicit states.

## Non-Goals
- No OCR engine replacement.
- No DB migration in Stage 1.
- No full reparse pipeline decomposition yet.
- No change to existing confirmed `OrderLine` semantics.

## Current Problem
- `reparse_order()` is effectively fail-closed.
- Recoverable sheet states still become `400/500`.
- Rejected LLM candidates are often discarded even when Step2 review could use them.
- UI still centers `明細へ反映` more than `下書き保存`.
- List view cannot distinguish `下書きあり` from `OCR自体が失敗`.

## Stage 1 Design
### Truth Separation
- `OrderLine` stays as confirmed truth.
- `OrderOcrCache._edited_ocr` is treated as draft storage.
- Reparse reject should store a reviewable draft instead of only failing the job.

### New State Model
- `ocr_review_state`
  - `none`
  - `processing`
  - `processing_failed`
  - `draft_saved`
  - `draft_ready`
- `draft_ready` means:
  - saved draft exists, and
  - either auto-apply was blocked or the saved draft is newer than confirmed lines.

### OCR Sheet API Direction
- Recoverable sheet errors should return payloads, not just `400`.
- Payload should include:
  - `review_state`
  - `can_apply`
  - `can_confirm`
  - `apply_blockers`
  - `confirm_blockers`
  - `confirm_warnings`
  - `has_saved_draft`
  - `draft_updated_at`
  - `draft_newer_than_lines`
  - `auto_apply_blocked`
  - `reject_reasons`

### Confirm Direction
- `confirm` should stop when:
  - monthly menu / week skeleton is missing
  - a newer saved draft exists than current confirmed lines
- `confirm` should not silently confirm stale lines after Step2 edits.

## Implementation Scope
### Backend
- Save reparse reject candidate as draft revision.
- Add review summary metadata for order detail and list.
- Return recoverable `/ocr-sheet` payloads where possible.
- Block confirm on explicit blockers.

### Frontend
- Step2 primary CTA becomes `下書き保存`.
- `明細へ反映` becomes secondary.
- Order list shows `下書きあり` vs `OCR失敗`.
- Recoverable sheet states show blockers/warnings cleanly instead of generic failure.

## Operator Impact
- Users can keep working even if LLM reparse is rejected.
- Step2 becomes the main work area for imperfect OCR.
- Confirming after saving a newer draft requires explicit re-apply first.

## Test Plan
### Backend
- Recoverable `/ocr-sheet` returns `200` with blockers when menu entries are missing.
- Saved sheet draft + reject reason produces `ocr_review_state=draft_ready`.
- `confirm` returns `409` when draft is newer than lines.

### Frontend
- Typecheck passes.
- Order detail renders draft-first controls.
- Order list visually distinguishes draft-ready vs harder failure.

### Regression
- Existing OCR integration/contract tests that cover:
  - OCR sheet history
  - OCR status API
  - status flow

## Rollout
1. Ship backend metadata and recoverable sheet behavior.
2. Ship frontend draft-first UX.
3. Watch:
   - order list classification
   - confirm blockers
   - Step2 save/apply flow

## Rollback
- Backend rollback is safe because Stage 1 uses existing schema.
- Drafts remain in `order_ocr_cache`; rolling back only removes new interpretation, not data.
