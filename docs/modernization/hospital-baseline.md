# Hospital C0-2 Baseline

Date: 2026-10-05 (Asia/Tokyo)

## Source and isolation

- Worktree: `/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c0`
- Branch: `codex/modernization-c0-hospital-20261004`
- Baseline commit: `7ae61ee1033776e2c413a621a2013c4c93002df4`
- Backend SQLite DBs and logs: `tmp/c0-2/`
- Backend primary environment: `backend/.venv`, created with Python 3.11.15 and `uv sync --locked --all-extras` from `backend/uv.lock`.
- The primary environment did not contain `torch`; full pytest collection stopped before execution. No lockfile was changed.
- A read-only existing environment at `/Users/mmorinag/Sawa/2025.12/worktrees/daily-output-label-requests-20260616/backend/.venv` supplied `torch 2.6.0` for a second attempt. `PYTHONPATH` and `src.__file__` proved imports came from this worktree's `backend/src`.
- Import and SQLite path proof: `tmp/c0-2/import-proof.txt`; result-file SHA256 values: `tmp/c0-2/evidence.sha256`.
- The completed backend run used an absolute SQLite `DB_URI` under `tmp/c0-2`, disabled automatic OCR rerun, `/dev/null` credentials, local GCE metadata addresses, and localhost-only HTTP(S) proxy endpoints. No stg/prod or external OCR API was used.

## Results

| Scope | Command | Result | Evidence |
| --- | --- | --- | --- |
| Backend full pytest, lock environment | `backend/.venv/bin/python -m pytest tests` | Collection stopped: 1,993 collected, 2 errors. Missing `torch` in `test_hakodate_best_method_runtime.py` and `test_yomitoku_text_recognizer_topk.py`. | `tmp/c0-2/logs/backend-pytest.log` |
| Backend full pytest, read-only dependency environment | `existing-venv/bin/python -m pytest tests` | 2,000 collected; host foreground execution limit interrupted before a final summary, after progress reached 14%. This is not a pass/fail aggregate. | `tmp/c0-2/logs/backend-pytest-existing-venv-full.log` |
| Backend full pytest, offline isolated environment | `existing-venv/bin/python -m pytest tests --junitxml=... -o faulthandler_timeout=120` | Completed: 1,558 passed, 421 failed, 21 skipped, 9 warnings in 587.79s. | `tmp/c0-2/logs/backend-pytest-full-offline.log`, `tmp/c0-2/backend-pytest-full-offline.junit.xml` |
| Backend failure sample | `existing-venv/bin/python -m pytest tests/contract/test_monthly_menus_api.py -x` | 1 failed. `AUTH_DISABLED=false`の`Basic operator:secret`が401で、旧テストが200を期待する。 | `tmp/c0-2/logs/backend-failure-sample.log` |
| Backend Basic-auth contract recheck | isolated SQLite with `test_monthly_menus_get_requires_operator_and_allows_operator --junitxml=...` | 1 failed as expected for the obsolete contract: unauthenticated request is 401, `Basic operator:secret` is also 401; test line 46 still expects 200. | `tmp/c0-2/logs/auth-contract-basic-recheck.log`, `tmp/c0-2/auth-contract-basic-recheck.junit.xml` |
| Backend menu-master baseline | isolated SQLite `DB_URI` with `test_menu_master_crud_baseline.py test_monthly_menu_master_source.py --junitxml=...` | Passed: 17 passed in 0.43s. CRUD input preservation (including `cut`/`count`, nullable fields, zero, duplicate, unknown update, and search ordering) and monthly source selection covered. | `tmp/c0-2/logs/menu-master-baseline-pytest.log`, `tmp/c0-2/menu-master-baseline.junit.xml` |
| Frontend existing test/config | `npm test` | Passed: 78 passed, 0 failed. | `tmp/c0-2/logs/frontend-npm-test.log` |
| Frontend typecheck | `npx tsc --noEmit` | Passed. | `tmp/c0-2/logs/frontend-typecheck.log` |
| Frontend production build | `npm run build` | Passed: Next.js compiled and generated 37 static pages. | `tmp/c0-2/logs/frontend-build.log` |
| Frontend production build, repeat | `npm run build` | Passed before the dedicated-port UI comparison. | `tmp/c0-2/logs/frontend-production-build.log` |
| Frontend UI mock, root entry | bundled Chromium with port 31310, production `next start`, `reuseExistingServer:false` | Failed: no redirect loop; mocked POST was not observed and `createBody` remained null. | `tmp/c0-2/logs/frontend-menu-masters-production-root.log` |
| Frontend UI mock, hospital entry | bundled Chromium with port 31311, production `next start`, `reuseExistingServer:false` | Failed: the unit selector remained `cut` after selecting `count`. | `tmp/c0-2/logs/frontend-menu-masters-production-hospital.log` |
| Frontend UI mock, root entry after synchronization fix | bundled Chromium, port 31314, production `next start`, `reuseExistingServer:false` | Passed: POST 200, reload GET, created row rerender, PUT 200, reload GET, and saved values rerendered. | `tmp/c0-2/logs/frontend-menu-masters-production-root-final.log` |
| Frontend UI mock, hospital entry after synchronization fix | bundled Chromium, port 31315, production `next start`, `reuseExistingServer:false` | Passed: same assertions through `/hospital/menu-masters`. | `tmp/c0-2/logs/frontend-menu-masters-production-hospital-final.log` |
| Frontend UI mock, root entry WebKit | Playwright bundled WebKit 26.0 (build 2227), port 31316, production `next start`, `reuseExistingServer:false` | Passed: `/menu-masters`, with POST/PUT response, reload, and rerender assertions. | `tmp/c0-2/logs/frontend-menu-masters-production-webkit-root.log` |
| Frontend UI mock, hospital entry WebKit | Playwright bundled WebKit 26.0 (build 2227), port 31317, production `next start`, `reuseExistingServer:false` | Passed: `/hospital/menu-masters`, with the same assertions. | `tmp/c0-2/logs/frontend-menu-masters-production-webkit-hospital.log` |
| Frontend UI mock, tracked config | `playwright.menu-masters.config.js`, Playwright bundled WebKit 26.0 (build 2227), port 31318, production `next start`, `reuseExistingServer:false` | Passed: one run executed exactly two tests, `/menu-masters` and `/hospital/menu-masters`. | `tmp/c0-2/logs/frontend-menu-masters-tracked-config-build.log`, `tmp/c0-2/logs/frontend-menu-masters-tracked-webkit.log` |
| Frontend UI mock, tracked config URL validation | `playwright.menu-masters.config.js`, Playwright bundled WebKit 26.0 (build 2227), port 31319, production `next start`, `reuseExistingServer:false` | Rejected injected/non-integer port, HTTPS URL, and query URL at config load; valid localhost URL then completed both entry tests. | `tmp/c0-2/logs/frontend-menu-masters-config-validation.log`, `tmp/c0-2/logs/frontend-menu-masters-tracked-webkit-url-validated.log` |
| Frontend UI mock, loopback-bind comparison (not baseline config) | WebKit, local Next CLI bound to `127.0.0.1`, ports 31320 and 31321 | Failed: both root and hospital entries returned too many redirects for `127.0.0.1` and `localhost`. This is an unresolved bind-dependent existing behavior. | `tmp/c0-2/logs/frontend-menu-masters-tracked-webkit-loopback-final.log`, `tmp/c0-2/logs/frontend-menu-masters-tracked-webkit-localhost-loopback.log` |
| Frontend UI mock, current production baseline config | `playwright.menu-masters.config.js`, Playwright bundled WebKit 26.0 (build 2227), port 31325, production `npm run start`, `reuseExistingServer:false` | Passed: one run executed exactly two tests, `/menu-masters` and `/hospital/menu-masters`. | `tmp/c0-2/logs/frontend-menu-masters-tracked-webkit-production-baseline.log` |
| Frontend UI mock, fresh-document saved-value verification | Same tracked config and WebKit; port 31327; production `npm run start`; no server reuse | 2 passed, 0 skipped/retried: PUT and list GET succeeded, then `page.reload()` created a new document and a new API GET supplied `MNU001` with `count`/`cut`, verified in the rendered controls. | `tmp/c0-2/logs/frontend-menu-masters-reload-final-proof.log`, `tmp/c0-2/menu-master-reload-final.junit.xml`, `tmp/c0-2/menu-master-reload-final-proof.json`, `tmp/c0-2/playwright-reload-final-output/` |

## Failure classification and limits

1. Dependency-lock coverage: `torch` is required by collected OCR tests but absent from the synchronized `uv.lock` environment.
2. Obsolete Basic-auth contract: commit `e50bc6587f1d36ac44b90e980c875b45f5ddf2df` (`feat: retire Basic authentication`, 2026-07-30) deleted Basic parsing, environment credential matching, and Basic challenges from `backend/src/api/auth.py`. Current `get_current_operator`, `get_current_admin`, and `require_role` accept only `AUTH_DISABLED`, portal Bearer authentication, or Bearer token verification followed by registered-role and hospital system-access checks. `test_monthly_menus_api.py` still creates `Basic operator:secret` and expects 200, so its 401 failures are old-contract failures, not evidence of auth-module reload/dependency binding inconsistency. C1 API auth tests must use current portal/Bearer authentication and required system access; product authentication must not be restored or relaxed for these tests.
3. Canonical facility/template schema: failures in facility config, master order form, OCR projection, and workflow state disagree on canonical field/template resolution or reject missing template identifiers.
4. OCR evidence and sheet projection: failures cluster in `test_ocr_pipeline` (186 cases), `test_ocr_sheet_history` (169 cases), redesign phase support, candidate resolution, and draft sheet services.
5. OCR geometry/runtime: Hakodate assignment, grid alignment, overlay, snap, and edge-lock validation expectations disagree with runtime results.
6. Runtime schema guard: `test_runtime_schema_mutation_guard` detected read/import-time schema repair SQL.
7. The first read-only-venv run stalled near 60% in `google.api_core.retry`; sampling showed sleep/retry and the interrupted run ended in `KeyboardInterrupt` inside that retry. The completed offline run passed this point without faulthandler timeout output. Its localhost-only proxy and metadata settings prevent external GCP/OCR calls.
8. The UI tests use isolated temporary Playwright profiles (the latest run uses the OS temporary directory), mock all `/api/**` calls, and are UI mock partial tests; they do not prove real authentication or backend persistence. The mock rejects and records every unmatched `/api/**` request as HTTP 500, then asserts the unmatched-request list is empty. Earlier passing runs, including port 31325, rechecked values that were already present in React edit state: those results do not prove that saved GET values were rendered. The latest port-31327 run verifies PUT 200, the subsequent list GET 200 and complete JSON body, a full document reload, another list GET 200 and complete JSON body, and the rendered saved values for the same record. It additionally asserts navigation type `reload` and an increased `performance.timeOrigin`. The observed bind-dependent redirect behavior remains unresolved as described below.
9. The earlier production UI passes on ports 31314 and 31315 used Playwright's default `chromium` engine because the temporary config had no `browserName`. Its browser directory contains `Google Chrome for Testing.app`; those results are recorded as Chromium, not as Google-Chrome-free proof. The tracked `frontend/playwright.menu-masters.config.js` fixes `browserName: "webkit"` and `headless: true`. It accepts only `http` localhost root URLs with the same 1024..65535 integer `E2E_PORT`, rejects query/hash/userinfo, starts production with the current `npm run start` command, and forbids server reuse. The spec does not read `E2E_BASE_URL`; relative `page.goto(entryPoint.path)` uses only Playwright's configured `baseURL`. The fresh-document verification row is the latest passing no-Google-Chrome UI evidence. No user browser was opened or controlled.
10. `frontend/AGENTS.md` and `frontend/CLAUDE.md` are untracked, scope-external Next.js generated files. Their contents and `frontend/node_modules/next/dist/server/lib/generate-agent-files.js` establish that `next dev` writes them. Their mtime is 02:18:07, while retained production E2E logs begin at 02:33; the responsible `next dev` invocation has no retained process log, so this worktree cannot attribute their creation to this agent or another actor. The production runs recorded here used `npm run start`, which does not invoke that generator. Both files are retained unchanged and excluded from commit.
11. The prior 1,610-pass/414-fail figures were not used as current results.

## Bind-dependent redirect observation

With `npm run start` (`next start -H 0.0.0.0`) on port 31322, both `127.0.0.1` and `localhost` returned `308 Location: /hospital/menu-masters` for `/menu-masters`, then `200 x-middleware-rewrite: /menu-masters` for `/hospital/menu-masters`.

With the local Next CLI bound to `127.0.0.1` on port 31324, `/hospital/menu-masters` instead returned `308`, `x-middleware-rewrite: http://localhost:31324/menu-masters`, and `Location: /hospital/menu-masters`. That absolute rewrite plus the root redirect repeats until the browser reports too many redirects. This is an observed, unresolved bind-dependent behavior in the existing proxy path; no product proxy code was changed in C0.

## Tracked menu-master UI reproduction

Run from `frontend/` after a production build:

```sh
npx playwright install webkit
npm run build
E2E_PORT=31318 E2E_BASE_URL=http://127.0.0.1:31318 npx playwright test --config=playwright.menu-masters.config.js
```

The tracked config selects only `tests/e2e/menu_masters.spec.ts`; that spec executes the root and `/hospital` entries as two independently mocked tests.

## Fresh-document evidence after review

The review identified a false-positive path in the previous final assertions: `count`/`cut` already existed in the edited React state. The spec now waits for the successful PUT and subsequent API GET, then reloads the document and validates the new GET record and both rendered selects. Request/response predicates use the exact `/api/menu-masters` path, so the HTML document `/hospital/menu-masters` cannot satisfy an API wait. The initial attempt with a suffix-only predicate failed twice on HTML-as-JSON; its log and trace remain in `tmp/c0-2/logs/frontend-menu-masters-reload-proof.log` and `tmp/c0-2/playwright-reload-output/`.

No mock canonicalization or name-trimming rule was added. This proves retrieval and display of the existing mock's saved values after React state is discarded; it does not establish real backend persistence or normalization.

| Entry | Previous document timeOrigin | Reloaded document timeOrigin | Navigation / GET / same record |
| --- | --- | --- | --- |
| `/menu-masters` | 1791137406878 | 1791137407157 | `reload` / 200 / `MNU001`: `unit_type=count`, `bag_max_unit=cut` |
| `/hospital/menu-masters` | 1791137407443 | 1791137407706 | `reload` / 200 / `MNU001`: `unit_type=count`, `bag_max_unit=cut` |

Exact successful command from this worktree's `frontend/` (the earlier production build is unchanged; only test code was edited):

```sh
PLAYWRIGHT_BROWSERS_PATH="$PWD/../tmp/c0-2/playwright-browsers" \
E2E_PORT=31327 E2E_BASE_URL=http://127.0.0.1:31327 \
PLAYWRIGHT_JSON_OUTPUT_FILE="$PWD/../tmp/c0-2/menu-master-reload-final-report.json" \
PLAYWRIGHT_JUNIT_OUTPUT_FILE="$PWD/../tmp/c0-2/menu-master-reload-final.junit.xml" \
CI=true ./node_modules/.bin/playwright test --config=playwright.menu-masters.config.js \
  --reporter=list,json,junit --trace=on --output=../tmp/c0-2/playwright-reload-final-output \
  > ../tmp/c0-2/logs/frontend-menu-masters-reload-final-proof.log 2>&1
./node_modules/.bin/tsc --noEmit > ../tmp/c0-2/logs/frontend-menu-masters-reload-typecheck.log 2>&1
```

Result: E2E exit 0, 2 passed in 2.2s; typecheck exit 0. The JSON reporter stores the `saved-record-after-document-reload` attachments; `tmp/c0-2/summarize-menu-master-reload.cjs` validates and decodes them to `tmp/c0-2/menu-master-reload-final-proof.json`. Each entry has a full trace under `tmp/c0-2/playwright-reload-final-output/`. Artifact hashes are in `tmp/c0-2/menu-master-reload-evidence.sha256`, tied to base SHA `7ae61ee1033776e2c413a621a2013c4c93002df4` plus the uncommitted spec hash. No staging or commit was performed for this correction; the parent-owned staged contents are preserved pending re-review.

## Changed files

- `docs/modernization/hospital-baseline.md`
- `README.md`
- `frontend/tests/e2e/menu_masters.spec.ts`
- `frontend/playwright.menu-masters.config.js`
- `backend/tests/unit/test_menu_master_crud_baseline.py` (parent-created and moved into this worktree; reviewed and executed)
- Ignored test-only files under `tmp/c0-2/`, `backend/.venv/`, `frontend/node_modules/`, and `frontend/.next/`
