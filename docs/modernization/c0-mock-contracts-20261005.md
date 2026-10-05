# C0 Mock Contract Correction

Worktree: `/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c0-direct-mocks`
HEAD: `9f7ca90a049b84a33eaea9a1f23c232aceb3149f`

## Scope

Failure class: stale test double no longer accepts a current production callee keyword. The shared decision point is the corresponding `monkeypatch` definition. The invariant is that each double accepts the real callee contract while its existing business assertions remain unchanged.

Only one stale double remained in this worktree. In `backend/tests/contract/test_orders_ocr_status_api.py`, `test_download_document_falls_back_to_archived_ocr_input_when_canonical_uri_is_missing` now accepts keyword-only `persist_cache` and asserts `order_id` plus `persist_cache is False`. No product files, expected results, skips, fixtures, or existing business assertions changed.

The other identified historical TypeError nodes were already contract-current at this HEAD:

| Category | Nodes | Current state |
| --- | ---: | --- |
| `persist_cache` | 2 | One was already current; one was corrected here. |
| `template` | 3 | All already accepted `template=`. |
| `order_id` | 1 | Already accepted `order_id=` and the other current `create_job` keywords. |

## Isolated execution

Runner: `scripts/run_c0_mock_contracts_isolated.py`. It uses the read-only borrowed interpreter `/Users/mmorinag/Sawa/2025.12/worktrees/daily-output-label-requests-20260616/backend/.venv/bin/python`, a unique run SQLite DB, unique HOME/TMP/cache/artifact directories, `PYTHONDONTWRITEBYTECODE=1`, `sys.dont_write_bytecode=True`, cleared credentials, disabled AWS metadata, loopback-only proxy settings, and audit-hook rejection of DNS/socket/subprocess events. `AUTH_DISABLED=true` follows test configuration only; this does not prove production authentication or any live business flow.

Current-source and DB proof: `tmp/c0-direct-mocks/after-affected-files/import-proof.log` records this worktree's `backend/src` modules, `dont_write_bytecode: True`, and `sqlite:////Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c0-direct-mocks/tmp/c0-direct-mocks/after-affected-files/test.sqlite`.

Production contract source: [`backend/src/services/order_service.py:20189`](/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c0-direct-mocks/backend/src/services/order_service.py:20189) declares `get_ocr_output(order_id, *, persist_cache: bool = False)`. [`backend/src/api/orders.py:1569`](/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c0-direct-mocks/backend/src/api/orders.py:1569) defines `_load_archived_original_document_bytes`, and [`backend/src/api/orders.py:1570`](/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c0-direct-mocks/backend/src/api/orders.py:1570) calls `order_service.get_ocr_output(order_id, persist_cache=False)`. The test's original response and document assertions remain unchanged.

## Evidence Identity

All evidence below is from precommit candidate runs at base `9f7ca90a049b84a33eaea9a1f23c232aceb3149f` plus the uncommitted test diff. It is not proof for an integrated or released commit. The parent must rerun the exact nodes after commit before citing integrated-source proof.

| Evidence | Absolute path | SHA256 |
| --- | --- | --- |
| `before-exact-v2` XML | [results.xml](/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c0-direct-mocks/tmp/c0-direct-mocks/before-exact-v2/results.xml) | `e846351064ecbbd593667e0f8bc14d48cbc95dfdb2f1363ab33a2178acf8c22d` |
| `after-exact` XML | [results.xml](/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c0-direct-mocks/tmp/c0-direct-mocks/after-exact/results.xml) | `92e4ec094d89fd906a6f6c8d128f37405cd17b1aad6f1b573496ca3d5a633f4b` |
| `after-siblings` XML | [results.xml](/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c0-direct-mocks/tmp/c0-direct-mocks/after-siblings/results.xml) | `49b4d97bbf36d8f5548943d9e65a2e48d8a85caadd2ff39780d8e48d280c1b3f` |
| Incomplete `after-affected-files` XML | [results.xml](/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c0-direct-mocks/tmp/c0-direct-mocks/after-affected-files/results.xml) | `268eff0e24fee2a38dfe3ed939e7a01f2f0db47260eaf348a27974c4386e3f96` |
| Current-source/import/DB proof | [import-proof.log](/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c0-direct-mocks/tmp/c0-direct-mocks/after-affected-files/import-proof.log) | `281031a06fbbc93f6d927ad8d381fa4ffef8343f4597b75183758aaefb883c84` |
| Current runner | [run_c0_mock_contracts_isolated.py](/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c0-direct-mocks/scripts/run_c0_mock_contracts_isolated.py) | `3962513eedccb056b159fbc56320fc6eb0ba3db789e9e1123dfd0f9e4a094147` |

The runner changed during harness correction. No SHA256 was captured for the earlier runner version, and this report does not retrofit one.

| Run | Result | Evidence |
| --- | --- | --- |
| `before-exact-v2` | 3 passed, 3 failed | `tmp/c0-direct-mocks/before-exact-v2/results.xml` |
| `after-exact` | 4 passed, 2 failed | `tmp/c0-direct-mocks/after-exact/results.xml` |
| `after-siblings` | 1 passed, 1 failed | `tmp/c0-direct-mocks/after-siblings/results.xml` |
| `after-affected-files` | Incomplete: stopped after 4:56; 270 passed, 147 failed, `KeyboardInterrupt` | `tmp/c0-direct-mocks/after-affected-files/results.xml` |

`before-exact` is a harness-path setup failure with zero collected nodes; its target paths were corrected before `before-exact-v2`. No test result is attributed to that run.

## Residual failures retained

- `test_reparse_order_llm_prompt_includes_previous_saved_candidate_rows`: `llm_full_table_baseline_missing`, before and after.
- `test_reparse_order_large_structural_projection_requires_manual_review`: expected `sheet_structural_projection_requires_review`, actual `llm_full_table_baseline_missing`, before and after.
- Sibling `test_reparse_order_blocks_llm_reparse_without_first_pass_context`: expected `llm_full_table_baseline_missing`, actual `main_ocr_failed:gemini:Gemini OCR HTTP 400 INVALID_ARGUMENT: Budget 0 is invalid. This model only works in thinking mode.`

These are business assertion failures, not guard-induced environment failures. They were retained without expectation, fixture, product, or fallback changes.

## Commands

```sh
/Users/mmorinag/Sawa/2025.12/worktrees/daily-output-label-requests-20260616/backend/.venv/bin/python -B scripts/run_c0_mock_contracts_isolated.py before-exact-v2 exact
/Users/mmorinag/Sawa/2025.12/worktrees/daily-output-label-requests-20260616/backend/.venv/bin/python -B scripts/run_c0_mock_contracts_isolated.py after-exact exact
/Users/mmorinag/Sawa/2025.12/worktrees/daily-output-label-requests-20260616/backend/.venv/bin/python -B scripts/run_c0_mock_contracts_isolated.py after-siblings siblings
PYTHONDONTWRITEBYTECODE=1 /Users/mmorinag/Sawa/2025.12/worktrees/daily-output-label-requests-20260616/backend/.venv/bin/python scripts/run_c0_mock_contracts_isolated.py after-affected-files affected-files
```

## Targeting correction

The first relative `apply_patch` created `run_c0_mock_contracts_isolated.py` at `/Users/mmorinag/Sawa/2025.12/scripts/`; it was deleted with an absolute-path patch. The runner now exists only at this worktree's `scripts/run_c0_mock_contracts_isolated.py`. The first attempted execution failed before collection because that runner was absent from the assigned worktree.

No staging, commit, push, deploy, or cross-thread action occurred. This bounded correction is frozen for parent and monitor review. The remaining C0 inventory and modernization are incomplete.

## Postcommit Verification

Source commit: `1d472726419e08ca08c706d5116fd3acd7c6ece2`. `git status --short` was empty before the runs and empty again after both runs, before this documentation-only edit. The source-bound test diff is `HEAD^..HEAD` for [`backend/tests/contract/test_orders_ocr_status_api.py:1886`](/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c0-direct-mocks/backend/tests/contract/test_orders_ocr_status_api.py:1886): it replaces only the stale `get_ocr_output` lambda with a double that accepts keyword-only `persist_cache` and preserves the response/document assertions. Source SHA256: `7a73387ae85272a761250188fcc346b5ec06562704dd74707bfebf3f74a8cd6f`.

The committed runner SHA256 is `3962513eedccb056b159fbc56320fc6eb0ba3db789e9e1123dfd0f9e4a094147`: [scripts/run_c0_mock_contracts_isolated.py](/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c0-direct-mocks/scripts/run_c0_mock_contracts_isolated.py). Both commands were run from this worktree with the borrowed interpreter, `PYTHONDONTWRITEBYTECODE=1`, and `-B`:

```sh
PYTHONDONTWRITEBYTECODE=1 /Users/mmorinag/Sawa/2025.12/worktrees/daily-output-label-requests-20260616/backend/.venv/bin/python -B scripts/run_c0_mock_contracts_isolated.py postcommit-exact exact
PYTHONDONTWRITEBYTECODE=1 /Users/mmorinag/Sawa/2025.12/worktrees/daily-output-label-requests-20260616/backend/.venv/bin/python -B scripts/run_c0_mock_contracts_isolated.py postcommit-siblings siblings
```

| Scope | Result | XML evidence |
| --- | --- | --- |
| exact6 | 4 passed, 2 failed | [postcommit-exact/results.xml](/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c0-direct-mocks/tmp/c0-direct-mocks/postcommit-exact/results.xml), SHA256 `b1b68875815e2e72aac64af94ddcd9614d25036ae2cafab3369d21dea50165df` |
| sibling2 | 1 passed, 1 failed | [postcommit-siblings/results.xml](/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c0-direct-mocks/tmp/c0-direct-mocks/postcommit-siblings/results.xml), SHA256 `e653b7e45f14670005980e4a0210c7550e037143cb86a1444c44fc9de744d7ee` |

The exact6 failures remain `llm_full_table_baseline_missing` in `test_reparse_order_llm_prompt_includes_previous_saved_candidate_rows` and the mismatch between expected `sheet_structural_projection_requires_review` and actual `llm_full_table_baseline_missing` in `test_reparse_order_large_structural_projection_requires_manual_review`. The sibling failure remains the expected `llm_full_table_baseline_missing` versus actual `main_ocr_failed:gemini:...thinking mode.` No failure was skipped, weakened, or reclassified as passing.

Import/source/isolated-DB evidence: [postcommit-exact/import-proof.log](/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c0-direct-mocks/tmp/c0-direct-mocks/postcommit-exact/import-proof.log), SHA256 `a27593a7a1ef90127db3f894be8a99183aabb8039ec8d54d04e3854548334e13`; [postcommit-siblings/import-proof.log](/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c0-direct-mocks/tmp/c0-direct-mocks/postcommit-siblings/import-proof.log), SHA256 `a707491a3626abe007e7cf1bcfd666a7dd2b12770efd19e5842e1e3c4baef2e8`. They record this worktree's `backend/src` imports, `dont_write_bytecode: True`, and separate SQLite DBs under `tmp/c0-direct-mocks/postcommit-exact/test.sqlite` and `tmp/c0-direct-mocks/postcommit-siblings/test.sqlite`.

This section is a separate uncommitted documentation edit. It records evidence for the source commit above; it does not mean this documentation change modified the verified source.
