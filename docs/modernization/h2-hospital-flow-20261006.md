# H2 Hospital Flow Freeze Record

- Worktree: `/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-h2-flow`
- Branch: `codex/modernization-h2-hospital-flow-20261006`
- Base: `d3d39bf3198f3ba631c82c7c8bd396323dcd2484`
- Status: implementation diff is in progress; tests were not run by instruction.

## Implemented in this worktree

### H2-1: intake and pending-review routing

- `frontend/src/pages/orders/index.tsx` no longer reads an OCR candidate's first facility as a facility assignment.
- Only the persisted `order.facility` is eligible for a facility slot in the weekly list. Unconfirmed orders remain unmatched and require an explicit operator selection in the workflow.
- Unconfirmed candidates remain available as display-only operator context. Candidate-fetch failures are visible on the matching order and are not ignored.

### H2-3: selected OCR, manual draft, review, and confirmation updates

- `frontend/src/pages/orders/[id]/workflow-v2.tsx` keeps a dirty manual sheet during polling or manual reload. A response from an older refresh generation or another order ID is discarded.
- Direct JSON, cell, and row editing all advance the dirty generation. An explicit existing discard action advances the generation before clearing the sheet.
- Sheet save marks the editor clean only when no newer local edit occurred while the save request was pending. Failed saves retain the edited value.
- HTTP 409 is presented as an explicit conflict while retaining local edits.

## Superseded API-contract note

The earlier handoff recorded that workflow expected-version support was unavailable. This is historical only: the current frontend implementation expects integer `revision` responses and quoted `If-Match` mutation headers. Backend implementation and integration verification remain outside this worktree.

The frontend now reserves `WorkflowV2.revision` and routes workflow mutation requests through one `If-Match` boundary when an actual GET or command response supplies that revision. It never derives a revision from local time, a candidate, or local editor state. The backend contract remains required before the header can be present in live requests.

## Current implementation and remaining work

- H2-1: `pdf-upload.tsx` is in the allowed scope and has an RHF/MUI intake form; multi-file intake and PDF preview remain in the page.
- H2-2/H2-3: workflow quad, header, row, sheet, OCR selection, and confirmation preserve their existing special editors. Mutation calls now use editor-specific input revisions; backend integration is unverified here.
- H2-4: `daily-delivery-notes.tsx` and `weekly-weight-output.tsx` use common RHF/MUI/Query condition surfaces. `weekly-orders.tsx` remains the canonical `/orders` redirect. `facility-orders.tsx` uses RHF/MUI/Query/DataTable while retaining its weekly state-count and daily-order surfaces; candidate facilities are display-only.
- Remaining: no test/build/typecheck verification was run; backend revision-contract compatibility and the full H2 end-to-end operator flow remain unverified.

## Prohibited work not performed

- No test, build, lint, typecheck, audit, or server command was run.
- No DB, network, browser, original-document write, stage, commit, merge, push, or deploy was performed.

## Workflow revision caller table

All workflow mutations use a captured order ID, editor generation, and actual GET/command revision. The shared boundary sends that captured revision as `If-Match`; a successful response revision advances only the originating editor. Poll/refresh updates display state but never replaces an editor base revision.

| Editor / command | Mutation path | Response revision use |
| --- | --- | --- |
| context | `POST workflow-v2/context` | advances and clears only `context` |
| template columns | `PUT workflow-v2/facility-template-columns` | advances and clears only `templateColumns` |
| expanded-cell mode | `PUT workflow-v2/expanded-cell-copy-mode` | advances command revision |
| quad | `PUT quad-review` -> `POST ocr-runs` | PUT response revision is sent to POST; clears only `quad` after POST success |
| header axis | `PUT header-axis-review` -> `POST ocr-runs` | PUT response revision is sent to POST; clears only `header` after POST success |
| row axis | `PUT row-axis-review` -> `POST ocr-runs` | PUT response revision is sent to POST; clears only `row` after POST success |
| OCR run / select / delete | `POST ocr-runs`, `POST ocr-results/:id/select`, `DELETE ocr-results/:id` | command-local revision only |
| sheet auto edit / anomaly dismiss / review | `POST sheet/auto-edit`, `POST sheet/anomaly-review/dismiss`, `POST sheet/anomaly-review`, `POST sheet/review-confirm` | same sheet input generation; success advances sheet base without clearing dirty; auto-edit poll accepts only a matching `done`/`failed` completion revision. `stopped` and missing completion revision terminate without advancing the base. |
| sheet save | `PUT sheet` | advances and clears only `sheet` when its edit generation still matches |
| bagging / final confirm | `POST bagging`, `POST confirm` | command-local revision only |

## Added, Not Executed Test Source

- `frontend/tests/e2e/workflow_v2_sheet_edit.spec.ts` adds the quad-save regression: the quad `PUT` must send `If-Match: "7"`, and its following OCR `POST` must send the returned `If-Match: "8"`.
- The same source adds canonical-facility guards for a matching OCR suggestion, an unregistered suggestion, and facility-list retrieval failure. These tests were not executed by instruction.

## Canonical Facility Context Guard

- `workflow-v2.tsx` treats `/facilities` as the only facility authority for Step1 context input, OCR suggestion reflection, context save, and OCR start.
- A facility ID must match exactly one returned authority option. Pending, retrieval failure, empty input, unregistered IDs, and duplicate IDs are visible blocking states.
- A pre-existing legacy unregistered ID remains visible as an item requiring confirmation; it is not silently deleted or rendered as a selectable facility option. It cannot be saved or used to start OCR.
