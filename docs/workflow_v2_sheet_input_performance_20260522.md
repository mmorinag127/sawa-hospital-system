# workflow-v2 sheet input performance notes 2026-05-22

## Scope

This note records the stg investigation and mitigation for slow numeric input in the order processing sheet editor.

Target surface:

- Page: `/orders/[id]/workflow-v2`
- Step: `STEP3 シート編集`
- File: `frontend/src/pages/orders/[id]/workflow-v2.tsx`
- Measurement order: `ORDb1702157`
- stg table size during measurement: 560 inputs, 392 editable inputs

Hard constraints for this work:

- Keep numeric input visually immediate.
- Do not reduce the displayed sheet range.
- Do not virtualize or hide rows/columns as a workaround.
- Keep the existing sheet processing and saved sheet payload shape unchanged or nearly unchanged.
- Deploy stg only through GitHub Actions.

## Initial Finding

The STEP3 sheet editor is implemented in the frontend as a regular React table with one `<input>` per sheet cell. It is not backed by a dedicated grid library such as Handsontable, AG Grid, or react-data-grid.

Before mitigation, each numeric edit synchronously updated React sheet state and regenerated a large JSON preview string. On stg, this made input feel delayed:

| Metric | Before mitigation |
| --- | ---: |
| Per-character input p50 | about 440 ms |
| Per-character input p95 | about 470 ms |
| 3-character input | about 1.3 s |

This matched the user complaint that numbers entered into the sheet were slow to appear.

## Changes Applied

### 1. Defer React sheet state updates during typing

Commit: `00343364d102e7d372fa048cc9cb366f126d82e6`

Change summary:

- Changed editable sheet inputs from fully controlled `value` updates to DOM-owned input display with `defaultValue`.
- Stored edits in a pending ref during typing.
- Flushed pending edits before explicit actions such as save, anomaly review, sheet review, AI auto edit, JSON preview, and bulk sheet operations.

Result:

| Metric | After change 1 |
| --- | ---: |
| 3-character input p50 | 10 ms |
| 3-character input p95 | 11 ms |
| Per-character input p50 | 3 ms |
| Per-character input p95 | 4 ms |
| Blur/next-cell click p50 | 74 ms |
| Blur/next-cell click p95 | 79 ms |
| Blur/next-cell click max | 92 ms |

### 2. Generate the JSON preview lazily

Commit: `e4b271912b51ae4aac7964b9ed3a8c3c03cba159`

Change summary:

- Removed `formatJson(nextSheet)` from normal sheet edit and blur/flush paths.
- Marked the JSON preview as stale when sheet payload changes.
- Regenerated the 3 MB-class JSON preview only when `保存予定JSONを確認` is opened.

Result:

| Metric | After change 2 |
| --- | ---: |
| 3-character input p50 | 10 ms |
| 3-character input p95 | 11 ms |
| Per-character input p50 | 3 ms |
| Per-character input p95 | 4 ms |
| Blur/next-cell click p50 | 39 ms |
| Blur/next-cell click p95 | 45 ms |
| Blur/next-cell click max | 57 ms |
| JSON preview open | 516 ms |

The JSON preview cost still exists, but it is now paid only when the operator explicitly opens the JSON details panel.

### 3. Do not flush pending edits on blur or Enter cell movement

Commit: `c9f4e4c5627169f1d3aa381c202249045011515d`

Change summary:

- Removed pending edit flush from blur.
- Removed pending edit flush from Enter-based cell movement.
- Kept flush before save, anomaly review, sheet review, AI auto edit, JSON preview, and bulk sheet operations.

Result:

| Metric | After change 3 |
| --- | ---: |
| 3-character input p50 | 10 ms |
| 3-character input p95 | 11 ms |
| Per-character input p50 | 3 ms |
| Per-character input p95 | 4 ms |
| Per-character input max | 7 ms |
| Next-cell click p50 | 31 ms |
| Next-cell click p95 | 32 ms |
| Next-cell click max | 49 ms |
| JSON preview open | 514 ms |

The JSON preview check confirmed that pending input is still included when the JSON details panel is opened.

## Current Assessment

The main complaint should be materially improved:

- Numbers now appear immediately during typing.
- The original per-character delay of roughly 440-470 ms is no longer on the hot path.
- Cell movement still has some cost, but it is down from about 74-79 ms p50/p95 after the first mitigation to about 31-32 ms p50/p95.

For low-spec operator PCs, the relative improvement should still be significant because the most expensive per-keystroke work was removed. However, low-spec devices may still show higher absolute latency than the stg measurement machine.

## Remaining Cost

The remaining cell movement cost is likely from:

- Browser focus/click handling across a 560-input table.
- `onFocus` updating `focusedSheetCell`.
- Recalculation and rendering tied to current-cell display, OCR overlay highlighting, target cell boxes, and sheet cell classes.
- The table remaining fully rendered, as required by the no-display-range-reduction constraint.

The 3 MB JSON preview generation remains about 0.5 seconds, but it is now isolated to explicit JSON inspection.

## Future Options

Stop point for this round: no further performance implementation is planned in this branch unless requested.

If more improvement is needed later, the next candidates are:

1. Defer `focusedSheetCell` updates with `requestAnimationFrame`.
   - Expected benefit: reduce synchronous work during focus/cell movement.
   - Risk: current-cell overlay/highlight may update one frame later.

2. Reduce focus-driven recomputation.
   - Memoize or refactor derived overlay boxes and current-cell display so focus changes do not cause broad recalculation.
   - Higher implementation risk than option 1.

3. Avoid selecting text on Enter movement unless required.
   - Current focus movement uses `focus()` and `select()`.
   - Removing or narrowing `select()` could reduce movement cost.
   - Risk: changes existing operator editing behavior.

4. Extract and memoize sheet body rendering.
   - Goal: prevent unrelated focus and overlay state changes from re-rendering the full sheet body.
   - This is the largest frontend refactor option and should be treated as a separate task.

Display range reduction, row virtualization, and hiding rows remain out of scope because they can make operators think input was not reflected.

## Verification

Local verification run before deploys:

- `npm run lint`
- `npx tsc --noEmit`

stg deploys were performed through GitHub Actions only:

- `00343364d102`: https://github.com/mmorinag127/sawa-hospital-system/actions/runs/26268752021
- `e4b271912b51`: https://github.com/mmorinag127/sawa-hospital-system/actions/runs/26269244172
- `c9f4e4c56271`: https://github.com/mmorinag127/sawa-hospital-system/actions/runs/26269442757

