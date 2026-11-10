# Connected FAX ruling repair: local verification

## Scope and Source

- Base: `a3be3637474f01fd095d26b51460666a43c07cce`.
- Local branch: `codex/ocr-connected-rulings-local-20261110`.
- Worktree: `/Users/mmorinag/Sawa/2025.12/worktrees/fax-orientation-template-20261108`.
- No commit, push, Actions, deployment, remote API request, paid AI request, live DB write or order mutation.
- The pre-existing local shipping-page styling experiment is preserved, not part of this OCR repair.

## Root Cause

The original contains connected, stepped junctions. Horizontal morphological opening removes short arms at a different Y from the surviving long segment. Existing same-scanline restoration cannot recover those source pixels. Missing intersections then cause the strict header topology matcher to reject the observed body boundary.

The production seed width remains `max(18, image_width // 55)`. It is not increased or decreased to make this order pass. After the repair, the seed mask is no longer the sole evidence for endpoint connectivity. The tracing search reaches at most the opening width because the target is a short arm removed by that opening, not an arbitrary distant cell.

## Implementation

- Label surviving horizontal components and inspect each endpoint.
- Measure the local seed stroke width to bound the vertical tracing corridor.
- Stop at the nearest observed vertical axis; do not search past an unconnected nearer axis for a successful farther match.
- Use 8-connected source-ink components to verify an actual path from the seed to that vertical. Copy only source foreground pixels, never fill white gaps.
- Use the same restoration in header-X and body-Y intersection detection.
- Preserve existing conservative dropout handling, manual coordinates, canonical source checks, strict header topology, quantity contents and saved drafts.

## Exact Original Replay

`ORDd8b83c80`, `DOC9083d945`, `FAC00015`, 2026-11-22 through 2026-11-28.

Original SHA256: `1f5b46cb67a60a13d5e4c74df65415bb5190977f3bb81882460923bf5531de07`.

Same PDF, saved quad, captured facility/week/template/menu and slant-corrected image were used for HEAD and the modified functions. Source hashing is checked before and after replay.

| Observation | HEAD | Local repair |
| --- | --- | --- |
| Intersections | 629 | 632 |
| Top-edge column 0 | Missing | Detected |
| Internal-header column 8 | Missing | Detected |
| Body-start column 0 | Missing | Detected |
| Row mapping | `fax_body_header_boundary_unresolved` | `applied` |
| Day boundaries | Unresolved | 0, 8, 16, 24, 32, 40, 48, 56 |

Evidence: `tmp/ORDd8b83c80-geometry/before-after-connected.json`.
Replay script: `tmp/compare_connected_rulings.py`.
The replay is geometry verification, not recognition-provider execution or a live save/reload flow.

## Batch Verification

- Initial focused batch: 108 passed in 13.04 seconds, including exact original and two preserved sibling originals.
- Initial adjacent batch: 286 passed, 1 failed, 2 skipped in 83.65 seconds.
- The failed thin-line fixture also fails with the old restoration (`tmp/connected-baseline-probe.log`). It draws every vertical through merged headers, contradicting its own authored worksheet. Only the stimulus was corrected to start leaf separators at their canonical header boundary. Existing row-count, day-boundary and month-target assertions remain unchanged.
- Final combined batch: **397 passed, 0 failed, 0 skipped in 100.48 seconds**. All 13 relevant files were included with all five preserved-original fixture locations supplied. The 2,320 warnings are retained in the test result (SQLAlchemy/Starlette/datetime/SWIG deprecation and reflection warnings); warnings are not treated as test failures or hidden success records.
- The local persistence/revision/operator-edit regressions passed. These are automated local cases, not the reported original's real recognition-provider run.
- `git diff --check` passed. No existing assertions were removed or weakened.

Runtime: `/Users/mmorinag/Sawa/2025.12/worktrees/prod-preparation-20261105/tmp/release-verification-venv/bin/python` (existing Python 3.12 environment).

Environment:

```text
PYTHONPATH=backend
DB_URI=sqlite:////Users/mmorinag/Sawa/2025.12/worktrees/fax-orientation-template-20261108/tmp/connected-rulings-final.sqlite
SAWA_STEPPED_JUNCTION_FIXTURE=<worktree>/tmp
SAWA_PIA_GEOMETRY_FIXTURE=<worktree>/tmp
SAWA_KASUGA_GEOMETRY_FIXTURE=<worktree>/tmp
SAWA_SHONAN_GEOMETRY_FIXTURE=<worktree>/tmp
SAWA_REPORTED_RULING_FIXTURE=<worktree>/tmp/ORDf021b65f
```

Arguments: `-m pytest -q --disable-warnings` followed by these paths under `backend/tests/unit/`:

```text
test_connected_ruling_arms.py
test_observed_ruling_junctions.py
test_header_topology_alignment.py
test_ocr_row_recovery.py
test_modernization_printed_header_boundaries.py
test_h2_operator_repair_revision.py
test_geometry_review_contract.py
test_ocr_header_recovery.py
test_ruling_slant_before_detection.py
test_ocr_row_recovery_persistence.py
test_row_ruling_continuity.py
test_canonical_saved_projection_regression.py
test_hakodate_observed_fax_rows.py
```

JUnit argument: `--junitxml=tmp/connected-rulings-final.xml`.
Complete output: `tmp/connected-rulings-final.log`.

## Limits

- Actual provider OCR and this original's sheet generation/save/reload have not been executed. Local persistence regression tests are not a substitute for that complete operator flow.
- Truly disconnected ink, no surviving long-line seed, or an ambiguous path may still require manual correction. This is not a guarantee for all FAX inputs.
- No independent subagent review: the user-specified `gpt-5.6-terra / low` is not among the currently available models. No replacement model was spawned.
- stg/prod are unchanged.
