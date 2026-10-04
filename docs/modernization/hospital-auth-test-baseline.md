# Hospital C0 Auth Test Baseline

## Scope and Source

- Date: 2026-10-05 (JST).
- Worktree: `/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c0-auth`.
- Branch: `codex/modernization-c0-auth-20261005`.
- Base and unchanged HEAD: `5ca6c130309413aedcfdc6f95f0b69739a795b90`.
- Approved plan: `/Users/mmorinag/Sawa/2025.12/worktrees/menu-master-provenance-20261001/docs/maintenance/sawa-codex-implementation-plan-20261004.md`.
- Failure class: retired Basic credentials return 401 before existing protected-route business assertions execute. Basic was retired by `e50bc6587f1d36ac44b90e980c875b45f5ddf2df` (`feat: retire Basic authentication`).
- Shared boundary: contract-test authentication setup, not production authorization.
- Invariant: formerly authenticated cases use `AUTH_DISABLED=false`, Bearer parsing, actual registered operator-role lookup, and actual enabled hospital-grant lookup. Only external Google token verification is mocked within authentication.

This is assignment-level evidence from the base plus the uncommitted test files fingerprinted below. It is not evidence of an integrated commit, deployment, complete backend regression, or C0/system completion. Parent review, commit/integration, and independent verification remain outside this worker assignment.

## Changes and Preserved Coverage

Changed existing files, all under `backend/tests/contract/`:

| File | Existing cases | Basic-auth setups replaced | Existing assertions preserved |
| --- | ---: | ---: | ---: |
| `test_monthly_menus_api.py` | 18 | 17 | 63 |
| `test_shipping_api.py` | 6 | 6 | 29 |
| `test_order_forms_api.py` | 1 | 1 | 3 |
| `test_system_admin_api.py` | 8 | 1 | 72 |
| Total | 33 | 25 | 167 |

Added `backend/tests/contract/auth_support.py`, `backend/tests/contract/test_hospital_auth_support.py`, and this document. No other tracked files changed.

The helper creates UUID-scoped test accounts and Bearer tokens. It reuses `_seed_user` and `_cleanup` from the existing `test_portal_google_auth.py`; these insert real `users` and `user_system_access` rows and invalidate the role cache. Monkeypatch changes to environment, auth-provider/audience configuration, and `id_token.verify_oauth2_token` are context-scoped and restored. Cleanup removes only the helper-owned user and grants. A regression verifies that another test user/grant remains, the cache is cleared, and configuration/verifier identity is restored.

`require_role`, `get_current_operator`, `_registered_role`, `_has_system_access`, and `_verify_google_token` are not mocked by the new helper. There are no dependency overrides. Production auth remains unchanged at `backend/src/api/auth.py:93`, `:205`, `:273`, `:303`, and `:334`. Existing business-service stubs in the four files remain unchanged; these are API contract tests, not end-to-end external service tests.

All original test names, decorators, and 167 assert ASTs match the base. After excluding only the obsolete/current auth setup and auth-header expressions, the entire test-body ASTs match, proving original business stubs, input quantities/dates/facilities, request bodies, and business control flow were preserved. The audit script and output are retained below. No cases were skipped or deleted, and no expectations were changed.

One monthly test is a pure helper test. Seven system-admin tests were not previously Basic-auth cases and retain their original setup, including the existing test default that disables auth. They are not claimed as authenticated-route proof. All 25 formerly Basic-auth cases use the new fixture with auth explicitly enabled.

## Grant Schema Finding

`user_system_access` is **not** created by normal test `Base.metadata.create_all` (`backend/conftest.py:17`). Read-only inspection of the fresh pre-edit DB returned `users` only:

```sh
sqlite3 -readonly tmp/c0-auth-20261005/before/test.db \
  "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('users', 'user_system_access');"
```

The new helper reuses the existing explicit test schema support at `backend/tests/contract/test_portal_google_auth.py:14-41`, rather than inventing a schema. Its composite key, user foreign key, boolean enabled flag, and allowed system keys correspond to `backend/migrations/0026_user_system_access.py:17-26`. No model, migration, conftest, runtime fallback, or actual live user was changed. The three existing migration/runtime-schema guard tests passed.

## Authorization Proof

The added suite exercises four real protected entry points: `GET /monthly-menus/latest`, `GET /shipping/status/latest`, `POST /order-forms/generate` (original facility/month input), and `GET /health/backlog`.

| Cases | Expected result | Count |
| --- | --- | ---: |
| Missing header; retired Basic with matching legacy credentials; malformed scheme; empty Bearer token; invalid token; malformed token | 401, `Unauthorized` | 24 |
| Real inactive user; no hospital grant; only shift grant; disabled hospital grant; invalid registered role | 403, correct role/grant denial detail | 20 |
| Active real operator with hospital grant reaches real backlog endpoint, but cannot access admin DB download | 200 / 403 | 1 |
| User/grant cleanup, unrelated user preservation, cache invalidation, and env/verifier restoration | All assertions pass | 1 |
| Total | No skips | 46 |

The 25 repaired original cases independently reach their preserved business expectations: 23 expected 200 responses, one expected review-required 409, and one expected invalid-view 400. Before repair all 25 instead returned 401. No newly reached business assertion failed.

## Isolated Execution

An own-WT locked environment was attempted from `backend`:

```sh
uv sync --locked --all-extras --offline \
  --python /Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c0/backend/.venv/bin/python \
  --cache-dir ../tmp/c0-auth-20261005/dependencies/uv-cache
```

Exit 2: the offline cache did not contain the macOS arm64 wheel for locked `opencv-python-headless==4.13.0.92`. The resulting own `.venv` was not used. No dependency specification or lockfile changed.

The explicitly permitted read-only interpreter was then used: `/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c0/backend/.venv/bin/python` (Python 3.11.15, pytest 9.0.2). Each run's `import-proof.log` asserts and prints that `src` and `src.api.auth` resolve inside **hospital-c0-auth**, not the interpreter's WT.

The ignored helper `/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c0-auth/tmp/c0-auth-20261005/run-tests.sh` runs with `env -i` and these explicit settings:

- `PYTHONPATH=$WT/backend`, `PYTHONDONTWRITEBYTECODE=1`; the shared interpreter is not installed into or written to.
- `DB_URI=sqlite:///$RUN/test.db`; an existing run DB causes a stop instead of reuse/deletion.
- Own run `HOME`, `TMPDIR`, pytest `--basetemp`, pytest cache, JUnit/log outputs, and `OCR_PIPELINE_STATE_URI`.
- `AUTH_PROVIDER=local`; formerly Basic-auth tests set `AUTH_DISABLED=false` in their fixture.
- Dummy ADC (`GOOGLE_APPLICATION_CREDENTIALS=/dev/null`), metadata host `127.0.0.1:9`, metadata IP `127.0.0.1`, timeout 1.
- HTTP/HTTPS/ALL proxy `http://127.0.0.1:9`; `NO_PROXY=localhost,127.0.0.1,testserver`; `OCR_AUTO_LLM_REPARSE_ON_INGEST=0`.

All databases, caches, temporary files and test outputs were in the assigned WT. Requests used in-process FastAPI TestClient; no browser or server port was used. External Google verification was stubbed at its SDK boundary. No live user, legacy data, production endpoint, or master workbook was used.

## Commands and Results

Commands below were run from the assigned WT; the helper switches to its `backend`. `before` was executed before any target-file edit. Use a fresh run name for any rerun; the saved run directories must not be reused.

```bash
targets=(tests/contract/test_monthly_menus_api.py
  tests/contract/test_shipping_api.py
  tests/contract/test_order_forms_api.py
  tests/contract/test_system_admin_api.py)
auth=(tests/contract/test_auth_guardrails.py
  tests/contract/test_portal_google_auth.py
  tests/contract/test_automation_auth.py
  tests/contract/test_school_lunch_automation_auth.py
  tests/contract/test_user_system_access_migration.py)
bash tmp/c0-auth-20261005/run-tests.sh before "${targets[@]}"
bash tmp/c0-auth-20261005/run-tests.sh after-targeted "${targets[@]}" \
  tests/contract/test_hospital_auth_support.py
bash tmp/c0-auth-20261005/run-tests.sh after-combined "${auth[@]}" "${targets[@]}" \
  tests/contract/test_hospital_auth_support.py
bash tmp/c0-auth-20261005/run-tests.sh after-reversed \
  tests/contract/test_hospital_auth_support.py \
  tests/contract/test_system_admin_api.py tests/contract/test_order_forms_api.py \
  tests/contract/test_shipping_api.py tests/contract/test_monthly_menus_api.py \
  tests/contract/test_user_system_access_migration.py \
  tests/contract/test_school_lunch_automation_auth.py tests/contract/test_automation_auth.py \
  tests/contract/test_portal_google_auth.py tests/contract/test_auth_guardrails.py
```

The helper invokes `python -m pytest <files> -vv --tb=short --basetemp "$RUN/pytest-temp" -o "cache_dir=$RUN/pytest-cache" --junitxml "$RUN/results.junit.xml"`, with pipefail and output tee. Exit codes below are actual command exit codes, not inferred from file presence.

| Run | Passed | Failed | Errors | Skipped | Warnings | Exit | Pytest duration |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `before` | 8 | 25 | 0 | 0 | 7 | 1 | 2.01 s |
| `after-targeted` | 79 | 0 | 0 | 0 | 7 | 0 | 1.41 s |
| `after-combined` | 304 | 0 | 0 | 0 | 8 | 0 | 2.04 s |
| `after-reversed` | 304 | 0 | 0 | 0 | 8 | 0 | 2.03 s |

The combined 304 unique cases are 33 original target cases + 46 new cases + 225 existing auth/schema cases (guardrails 17, portal Google 8, automation 86, school-lunch automation 111, migration 3). The reversed run repeats these same cases in reversed file order to check ordering/cache contamination; it is not another 304 distinct cases. Warnings are existing SWIG, FastAPI startup-event, and (combined runs) TestClient per-request-cookie deprecations.

Coverage audit command (exit 0; 33 cases / 167 identical asserts and non-auth bodies):

```sh
PYTHONDONTWRITEBYTECODE=1 \
  /Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c0/backend/.venv/bin/python \
  tmp/c0-auth-20261005/audit-coverage.py
```

`git diff --check` also returned 0. There are no remaining failures within the executed assignment scope. Full backend, frontend, browser, deployed Google verification and integrated-SHA testing were not performed or claimed.

## Evidence Fingerprints

Every artifact below is local, not staged. Artifact root (absolute): `/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c0-auth/tmp/c0-auth-20261005/`. Resolve the relative path column against that root. `before` was generated from the unchanged base; after-runs were generated from the base plus the six test-file SHA256 values below. The commands above and `run-tests.sh` define generation. No deploy target/revision applies.

| Artifact relative path | SHA256 |
| --- | --- |
| `run-tests.sh` | `75c841a70ca60c5b7c789f0ab902c38d2dc675e7289f2fbce8c8d4b9b7082f7b` |
| `audit-coverage.py` | `ea39515892fdc7327617deb7b133508430721274cc2b6d9a1caf2be0de517982` |
| `coverage-audit.log` | `19e966c3571a9e3298b0364fbdba80f07857d173e3b56876dfb503c566f95f98` |
| `before/pytest.log` | `2f315dcce307d413cf48c362d1752189fdc58a37ee311f415abd07530795f65b` |
| `before/results.junit.xml` | `8748c9f78ff9dbdb437d6409b82826cbb83693cfabee356956c474b549d64014` |
| `before/import-proof.log` | `22ef26f92245cc8012f3fe1ad282a524574569ee49d3f8db74075d905b0fbcfb` |
| `after-targeted/pytest.log` | `9e0ad4bfb0e9af0ee2dc94bc42ec9e4818775a903190a3d96717e3ed7fc95477` |
| `after-targeted/results.junit.xml` | `39d86e73a245c219092b81ed2921c0a82c3298f13f67b4f1c774152f3c8bf290` |
| `after-targeted/import-proof.log` | `91457a7eb7bdc5971e0d270f433c418b94793e6984a733a846ea21a477539f21` |
| `after-combined/pytest.log` | `e2fbfb2c2a4ddebac20f5d70629abfda307471cab10a27d6cfef761bdf16c585` |
| `after-combined/results.junit.xml` | `a55df50c2e06915f768e1ba13c9523c2c6bee44da30f33cb93bdfbf12e415a08` |
| `after-combined/import-proof.log` | `e90cb264a8aaff50fb0ed55707ad65970daae4abaec88d6fc0e003e63c040a3b` |
| `after-reversed/pytest.log` | `43636ae171c6381ec73cdf7c087cac0f31ce7e84be70426e17c8ad91c852acec` |
| `after-reversed/results.junit.xml` | `5dcb1f05e3928879d5c7150cc28a4bfdfedcc37c4ca3619d8d15a43865c7c969` |
| `after-reversed/import-proof.log` | `b4b7daf5a6d4dd763c0533badffdc0e79637e3bf626079aabc794d5501719648` |

Test-file root (absolute): `/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c0-auth/backend/tests/contract/`.

| Test source | SHA256 at all after-runs |
| --- | --- |
| `auth_support.py` | `408fcc00eb03a48694d1b8d2947176c6bb604c0fd8b20bfcc4febf7e5e84200e` |
| `test_hospital_auth_support.py` | `39f1ee81ae58cad0fe21b1f43f72bf2760e3ffbea09868382cd5b956cb6c6c7f` |
| `test_monthly_menus_api.py` | `13fd5c8561eecd9d74ad2b61332393feeabb7e0d75d5ae68edf651d8af850d72` |
| `test_shipping_api.py` | `f85509b9a44cab9dedc0d83fe185cb265fbc8bb5cb0c6a52987496b0e15b7696` |
| `test_order_forms_api.py` | `b8ff5f076da2c2b5d1610bfef492193cbdb010c8585d74e846cd9b164ce46361` |
| `test_system_admin_api.py` | `43bee8d25ca28e3761078359a288aba79e554030e6eb4be620e50e88d1c57317` |

## Handoff Constraints

Product auth/runtime, schemas/migrations, conftests, `user2_permissions`, canonical business inputs, expectations, and master workbook were not modified. No auth bypass, skip, dependency override, live-user mutation, browser launch, other-WT write, stage, commit, merge, push or deploy was performed. Only the assigned four existing tests, one shared test helper, its focused test file and this baseline are review targets. Ignored dependency/tmp evidence remains in this WT. Worker editing stops at this handoff; parent owns review and integration.
