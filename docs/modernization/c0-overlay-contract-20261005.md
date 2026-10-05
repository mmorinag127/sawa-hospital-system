# C0 Overlay Contract Repair

Source HEAD: `58f41b976c8d52a6836a6c66e6d70bd5c33b8cbb`. The production signature at `backend/src/hakodate_best_method_runtime/render_best_method_overlay_all_facilities.py:376-385` requires keyword-only `header_intersection_points`. No application, OCR, geometry, render, master, expected-pixel, or metadata assertion changed.

## Change

Only `backend/tests/unit/test_hakodate_best_method_runtime_regions.py` changed: the existing `_draw_overlay` call in `test_best_method_overlay_does_not_draw_internal_merge_boundary` now passes `header_intersection_points=[]`. This fixture has no header points. Test-file SHA256 after the edit: `da1fc76df8a21740299dfa7eaf00eb727cd8dbd3734388094f8123b8240c402a`.

## Isolated Evidence

The ignored owned runner `tmp/c0-overlay-contract/run_overlay_contract_guarded.py` loads committed `scripts/run_c0_mock_contracts_isolated.py` through `runpy`, uses its environment/audit functions unchanged, and substitutes only its exact three nodes. The committed guard SHA256 is `3962513eedccb056b159fbc56320fc6eb0ba3db789e9e1123dfd0f9e4a094147`. Both runs used `PYTHONDONTWRITEBYTECODE=1` and `-B`, a fresh run-local SQLite DB, and the guard's socket/subprocess audit block.

Before command: `PYTHONDONTWRITEBYTECODE=1 /Users/mmorinag/Sawa/2025.12/worktrees/daily-output-label-requests-20260616/backend/.venv/bin/python -B tmp/c0-overlay-contract/run_overlay_contract_guarded.py before-20261005`.

- Result: `2 passed, 1 failed`.
- Failure: `TypeError: _draw_overlay() missing 1 required keyword-only argument: 'header_intersection_points'` at the existing test call.
- XML: [/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c0-overlay-contract/tmp/c0-overlay-contract/before-20261005/results.xml](/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c0-overlay-contract/tmp/c0-overlay-contract/before-20261005/results.xml), SHA256 `7bb52c169cb2a24cdd8ae5a6185b8b6d1bf2621012fdfd126abca144ce6895e7`.

After command: `PYTHONDONTWRITEBYTECODE=1 /Users/mmorinag/Sawa/2025.12/worktrees/daily-output-label-requests-20260616/backend/.venv/bin/python -B tmp/c0-overlay-contract/run_overlay_contract_guarded.py after-20261005`.

- Result: `3 passed`.
- Nodes: template-owned regions, JSON merged-cell metadata, and the close sibling merged-boundary pixel assertion.
- XML: [/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c0-overlay-contract/tmp/c0-overlay-contract/after-20261005/results.xml](/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c0-overlay-contract/tmp/c0-overlay-contract/after-20261005/results.xml), SHA256 `453d272ef2307a7695e2b29acf2c3977afec2f29e68196624f6639049f73b978`.
- Current-source and isolated-DB proof: [/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c0-overlay-contract/tmp/c0-overlay-contract/after-20261005/import-proof.log](/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c0-overlay-contract/tmp/c0-overlay-contract/after-20261005/import-proof.log).

No external requests, credentials, legacy DB, staged changes, commits, pushes, or deployments occurred. All owned test processes terminated. This bounded test-contract repair is frozen for parent monitoring and integration.
