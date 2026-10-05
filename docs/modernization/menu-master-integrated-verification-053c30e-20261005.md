# Hospital C1 Integrated SHA Verification

## Source and Scope

- Date: 2026-10-05 JST. WT: `/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c1-verify` (below: `W`).
- Branch: `codex/modernization-c1-hospital-verify-20261005`; tested HEAD: `053c30e23b9f1b89e4d557b2bac309a0412acc51`.
- All results below were generated anew in this WT. Previous XML supplied test-file selection only; previous harness source was copied, not previous outputs, dependencies, servers or clusters.
- Product, lock and committed test sources were not edited. No stage, commit, merge, push, deploy, production operation, Chrome or user-browser control occurred.
- This is integrated component verification, not full-backend-suite acceptance, C1 completion, staging deployment, real Google login success or user acceptance. **Two unresolved test findings and an auth-preflight permission blocker remain.**

## Results

| Execution | Actual result | Exit |
| --- | --- | --- |
| Fresh `npm ci`, Node 20.17.0 / npm 10.8.2 | Installed from committed lock, lock unchanged | 0 |
| Frontend unit/config | 118 passed, 0 failed/skipped | 0 |
| `tsc --noEmit`, `npm run lint` | Both passed; lint emitted no warnings | 0, 0 |
| Production Next 16.3.8 build | Passed in each production run | 0 |
| Tracked config WebKit, first run | 26 passed, 1 failed; see finding below | 1 |
| Same source/config, full second run | 27 passed, 0 skipped | 0 |
| StrictMode history-related cases | 6 passed, 0 skipped | 0 |
| Invalid-query diagnostic, trace on, repeat 3 | 3 passed, no test/source change | 0 |
| Full and production npm audit | Both 0 advisories; valid raw JSON, statuses separate | 0, 0 |
| Installed production tree / committed HEAD lock audits | `npm ls` valid; HEAD full/prod also 0 | 0 |
| Installed `@sawa/ui` vs committed tgz | All 12 regular files byte-identical; lock integrity matches | 0 |
| Backend 17 unique files, SQLite and two owned PG16 clusters | **743 passed, 6 failed, 0 errors, 0 skipped; 749 collected** | 1 |
| Real staging Google/OIDC preflight | Audience and IAM read; impersonation denied; `/auth/me` not requested | mint 1 |

Counts are observed, not taken from prior totals. Audit zero means no advisories reported by that audit at execution, not general security assurance. React is 18.3.1; Node 20.17.0 is the tested installed version, not a claim about latest Node 20.

## Unresolved Findings

1. **Unauthenticated legacy system-admin tests:** six cases in `backend/tests/contract/test_system_admin_api.py` expect 200 from unauthenticated requests at lines 54, 158, 180, 231, 327 and 425, but receive 401 with `AUTH_DISABLED=false`. The runner keeps authentication enabled as requested. `backend/tests/conftest.py:4` historically defaults it to true when unset; these six cases do not use `hospital_operator`. The registered-operator auth case in the same file passes. No fixture, source, expectation or authentication setting was weakened to obtain a green run. This is a test-auth setup gap, not evidence that menu authorization failed. All six names, assertions and file group counts are in `W/tmp/integrated-c1/backend-groups.json` and the fresh JUnit below.
2. **Intermittent WebKit pageerror:** first full run failed `frontend/tests/e2e/menu_masters.spec.ts:480` at the strict `finish()` pageerror check, line 95. Its four invalid-query alerts and zero menu-list API assertions had passed. Five errors mention same-localhost `/_next/data/<build>/hospital/{system-status,system-process-logs,ocr-queue,ocr-results,ocr-training-data}.json` and “access control checks”. The fixture recorded no blocked external requests, unexpected API calls or hydration console messages. The source loops through four document navigations; a navigation/prefetch timing interaction is a hypothesis, **not a confirmed root cause**. Unchanged full rerun and three traced repetitions passed. Passing traces also contain incomplete requests during document changes; they do not establish why the initial error was raised. Initial failure/log/attachments are retained, not suppressed or reclassified as a pass. No product fix is authorized in this verification WT.

The six backend failures are distinguished from common menu-change regression results below and from the historical full-backend failure ledger, which was neither rerun nor closed here.

## Backend Scope and Isolation

Read the committed `menu-master-api.md`, `menu-master-staging-migration.md`, `hospital-auth-test-baseline.md` and `hospital-master-auth-test-baseline.md`. `select_tests.py` structurally parsed the three requested parent XMLs, mapped classnames to existing source files, and deduplicated 17 files. Exact input paths/hashes and selected files are in `W/tmp/integrated-c1/test-selection.json`.

- `test_menu_master_revision.py`: **255/255** including SQLite/PG fields, strict revision validation, read-only fields, stale revisions, two-session/flush conflicts, Bearer/local/portal authorization, schema refusal and first/second explicit migration.
- `test_stg_menu_master_migration.py`: **109/109**, including real isolated PG 0027 DDL/data preservation, read-only second run, incompatible schema refusal, lock/transaction checks and workflow gates. Cloud transport remains fixture-controlled, not live staging.
- `test_menu_master_crud_baseline.py` **9/9**, `test_monthly_menu_master_source.py` **8/8**, `test_monthly_menus_api.py` **18/18**, master authorization **5/5**. Remaining auth/bootstrap/shipping/order-form/access cases are itemized in `backend-groups.json`.
- Python 3.11.15 / pytest 9.0.2 came read-only from `/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c0/backend/.venv/bin/python` (`P`). Import proof resolves product modules to this WT; `PYTHONPATH=W/backend`, `PYTHONDONTWRITEBYTECODE=1`.
- PostgreSQL **16.14**, fresh data `W/tmp/c1-pgdata-locale`, socket `W/tmp/pgs`, port 55437, database `c1_menu_master`; separate data `W/tmp/stg-migration/pgdata`, socket `/private/tmp/sawa-c1stg-pg.y9Q0zQ` with OWNER equal to W, port 55447, database `c1_stg_migration`. `listen_addresses=''`; no TCP listener, existing cluster or real DB reused. Both URI variables were set explicitly and passed the source fixtures' ownership assertions.
- Own SQLite, HOME, TMPDIR, cache, pytest basetemp and artifacts. ADC `/dev/null`, metadata localhost:9, rejecting external HTTP/HTTPS/ALL proxies, no LLM auto-reparse. Existing in-process auth/Google fixtures remain unchanged. These are not production Google signature tests.
- Initial PG startup failed with `postmaster became multithreaded during startup; Set LC_ALL ...`. First attempt is retained in `W/tmp/integrated-c1/backend/`. A new owned data directory with `LC_ALL=C`, `LANG=C` started successfully; no product change was needed. Successful startup/test run is `backend-locale/`.
- PG migration fixtures exercise their documented prerequisite schema, not the entire historical migration chain or resolution of the older 0023/C2 prerequisite issue.

## Frontend and Artifact Evidence

Evidence root is `W/frontend/tmp/menu-c1/`. Fresh install: `fresh-install/`; unit/type/lint: `units-2026-10-05T02-09-49-219Z/`; audits: `audit-2026-10-05T02-11-39-040Z/`.

| Run directory under `runs/` | Scope |
| --- | --- |
| `2026-10-05T02-10-21-931Z` | First tracked-config run, retained 26/27 result |
| `2026-10-05T02-11-56-577Z` | StrictMode six cases |
| `2026-10-05T02-13-46-658Z` | Full tracked-config rerun, 27/27 |
| `2026-10-05T02-15-10-133Z` | Three diagnostic repetitions with trace |

The committed localhost config owns its server (`reuseExistingServer=false`); `E2E_BASE_URL` was unset to exercise its default. Browsers used actual Next pages, existing shell/styles/auth storage, local synthetic Bearer and API responses; external browser/server network was rejected. Both entry documents returned 200. The cases cover nine-field POST/PUT and fresh-document saved values, null/0/all units/duplicate, 409 failed/successful reload, 500/input retention/double submit, pending Select, async ownership, URL/history/dirty/unknown history, IME events, guest/401/403 and account-session disposal. IME event simulation is not OS-native Japanese IME certification. No live saving occurred.

Fresh images from full rerun, visually inspected; label/value positions are separate, quantity 1500 stays on one line, long names wrap, mobile table remains horizontally keyboard-scrollable:

- [Actual 360px page](/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c1-verify/frontend/tmp/menu-c1/runs/2026-10-05T02-13-46-658Z/readable-attachments/prefixed-actual-next-360.png), SHA256 `dbcf9efa620a490f04151e2dae6b1650c63d82bf3f115df26fcc8e06d5bec394`.
- [Actual 1280px page](/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c1-verify/frontend/tmp/menu-c1/runs/2026-10-05T02-13-46-658Z/readable-attachments/prefixed-actual-next-1280.png), SHA256 `5a0d87b1a4ccc8f6d5a86437c2765354e1bb9d015cc1389669ccae2f48435247`.
- [Actual mobile quantity table](/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c1-verify/frontend/tmp/menu-c1/runs/2026-10-05T02-13-46-658Z/readable-attachments/7-actual-quantity-table-360.png), SHA256 `95ea46791c36da44724d795103d3b473dbe084c1c0093337668210873ce4bbc4`. Measured 1500 cell: 74px wide, one text line; matching label/table measurements and keyboard-end image are adjacent.

Artifact: `W/frontend/vendor/sawa-ui-0.1.0-8004321e0364f54d1294df4bb3143dc091fc5a86.tgz`; platform source commit `8004321e0364f54d1294df4bb3143dc091fc5a86`; SHA256 `eb85dd9a368cbac702840289ff4c1e964de09707a07831b7043755130d82dfcb`; integrity `sha512-nfC57vICD7+C5BE8TZbf48sKoW9DnGeesUTBMpQ9CR+3Zw/mbFFdXAKK4mr5wGkWgVlI4zRwzUvh6AjfVVkEfw==`. Full 12-file installed comparison: `W/tmp/integrated-c1/installed-artifact.json`. No repack/vendor edits occurred.

## Staging Auth Preflight

Read-only commands and filtered results: `W/tmp/integrated-c1/auth-preflight/result.json`, SHA256 `8788f5f03f67c512a13af79d0765dd8497f6a29f466e78c56740ba024538135e`.

- GitHub `STG_GOOGLE_OAUTH_CLIENT_ID` equals worker-stg `GOOGLE_OAUTH_CLIENT_ID`; both Cloud Run services have `AUTH_DISABLED=false`. The web service's separate runtime `GOOGLE_OAUTH_CLIENT_ID` hash differs; the browser build-time client ID was not inspected and no client-config equivalence is claimed.
- Existing SA is `sawa-github-deploy-stg@sawahospitalsystem.iam.gserviceaccount.com`. SA policy has self `roles/iam.serviceAccountTokenCreator` plus the two existing GitHub WIF principals. Active account has direct project `roles/owner` and `roles/iam.serviceAccountAdmin`; group/ancestor grants were not enumerated.
- Existing CI-script-equivalent `gcloud auth print-identity-token --impersonate-service-account=<SA> --audiences=<verified-stg-client-ID> --include-email` failed with **PERMISSION_DENIED: `iam.serviceAccounts.getAccessToken`**. This is observed denial, not an inference from role names. No token was produced or saved; `/auth/me` was not attempted. No Basic bypass, permission/SA/user creation or credential fallback.
- Google documents [ID-token impersonation arguments](https://docs.cloud.google.com/sdk/gcloud/reference/auth/print-identity-token) and [short-lived credential permissions](https://docs.cloud.google.com/iam/docs/create-short-lived-credentials-direct). No access changes were performed.
- Observed revisions: `web-stg-00351-z92`, image `stg-frontend-7ae61ee10337-20261001-064636`; `worker-stg-00771-t8s`, image `stg-backend-7ae61ee10337-20261001-065033`. These are pre-C1 staging images, not deployment proof for 053c30e.

## Commands and Cleanup

`N=/Users/mmorinag/.anyenv/envs/nodenv/versions/20.17.0/bin/node`; `P` and `W` are absolute paths above. All expanded argv/cwd/status values are saved in run manifests and separate status files; JSON has no appended exit text.

```sh
# cwd W/frontend
"$N" tmp/menu-c1/fresh-install.mjs
"$N" tmp/menu-c1/checks.mjs units
"$N" tmp/menu-c1/run.mjs --tracked-config
MENU_GREP='unknown.*history|URL search/page|dirty link and history|document reload' "$N" tmp/menu-c1/run.mjs --dev
"$N" tmp/menu-c1/checks.mjs audit
"$N" tmp/menu-c1/run.mjs --tracked-config
MENU_GREP='invalid query stops' MENU_REPEAT=3 MENU_TRACE=on "$N" tmp/menu-c1/run.mjs --tracked-config
# cwd W
"$P" tmp/integrated-c1/select_tests.py
"$P" tmp/integrated-c1/run_backend.py
"$P" tmp/integrated-c1/auth_preflight.py
"$P" tmp/integrated-c1/verify_artifact.py
"$P" tmp/integrated-c1/finalize.py
```

The backend runner expands the selected 17 files into `python -m pytest <files> -vv --tb=short --basetemp <owned> -o cache_dir=<owned> --junitxml <owned/results.xml>`. Its fresh result SHA256 is `29c05de381d393b6602d8e1ecdd31a538f0ea450e88cbf6e493d395ba3c06312`. The preserved initial locale-failing attempt preceded the corrected runner invocation.

`W/tmp/integrated-c1/final-manifest.json` records absolute artifact paths/SHA256, source commit, per-run statuses, listener and PG shutdown checks. All owned Next/mock servers, Playwright/WebKit jobs and both PG clusters stopped; all eight reserved HTTP ports are closed; all three owned PG data directories report `pg_ctl status` 3 (not running), and socket files are absent. No process is left for monitoring.

Tracked product/test/lock diff is empty before and after; generated `next-env.d.ts` was restored to its own pre-run bytes. Frontend lock SHA256 remains `e17f31cebdec7ed1d7ba77096495e3fc729a5eddd12bb808e3bb0c8422d5c590`. Empty source diff SHA256: `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`. Only this new report is a non-ignored change; all new harness/evidence/dependency/build output is owned scratch. Parent must decide the two findings and stg permission precondition; no source fix or staging action was performed here.
