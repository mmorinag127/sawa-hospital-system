# Hospital C1 Local Verification (2026-10-05)

## Result and Source

Local candidate verification passed: tracked-config WebKit **27/27**, StrictMode history **6/6**, frontend unit/config **118/118**, types/build/lint exit **0**, actual full/production audit **0/0**. Nothing was staged, committed, pushed or deployed. This is not post-commit integration, stg, production OIDC or overall C1 acceptance.

- WT: `/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c1-menu-ui`
- Branch: `codex/modernization-c1-menu-ui-20261005`; HEAD: `8d4bc79142f7ec526b4abfed1461842c0d3ec356` plus the preserved uncommitted candidate.
- Verified source-diff SHA256: `d2261b319c5a41dacbe7a0b4a425e545ecbfd55e4a123055f61aea5b68287a52`. Both final WebKit runs recorded identical before/after source hashes. This report was added afterwards and is excluded from that tested-source hash.
- Final lock SHA256: `e17f31cebdec7ed1d7ba77096495e3fc729a5eddd12bb808e3bb0c8422d5c590`.
- Runtime: Node **20.17.0**, npm **10.8.2**, Next **16.3.8**, React **18.3.1**, React types **19.2.7**. Node 20.17.0 is the tested version, not a claim about the latest Node 20.
- Machine evidence, exact argv/status/cwd, per-file/image hashes and final binary-inclusive candidate diff hash: [final-evidence.json](../../frontend/tmp/menu-c1/final-evidence.json). Stage candidates: [stage-candidates.json](../../frontend/tmp/menu-c1/stage-candidates.json). No stage operation was performed.

## Product Changes

- Shared UI: immutable `@sawa/ui@0.1.0` from platform commit `8004321e0364f54d1294df4bb3143dc091fc5a86`, consistently referenced by vendor filename, package.json, lock and Docker COPY. Installed form/list/package bytes were compared with the tarball. No shared fix was duplicated in hospital source.
- Artifact SHA256: `eb85dd9a368cbac702840289ff4c1e964de09707a07831b7043755130d82dfcb`; integrity: `sha512-nfC57vICD7+C5BE8TZbf48sKoW9DnGeesUTBMpQ9CR+3Zw/mbFFdXAKK4mr5wGkWgVlI4zRwzUvh6AjfVVkEfw==`.
- Small [portable provenance](../../frontend/vendor/sawa-ui-0.1.0-8004321e0364f54d1294df4bb3143dc091fc5a86.provenance.json) records source repository/commit/lock hash, generation command and references to platform verification. Old hospital-generated vendor candidates were moved to ignored `tmp/menu-c1/pack-integration-2026-10-05T01-29-56-837Z/previous-vendor/`; platform historical artifacts remain untouched.
- `useHydratedPath` uses React's server/client snapshot boundary for equal SSR/first hydration labels, then the real route. No warning suppression, proxy changes or authentication bypass.
- `NavigationBoundary` owns document-stable history metadata while retaining Next state. Known-entry cancellation uses its measured delta; query navigation retains input. Guard activity is separate from discard confirmation: absent/clean guards permit ordinary unknown-history navigation, and becoming clean clears a prior stop. Unknown dirty history explicitly stops without guessing a delta or replacing the previous URL. A later actual push starts a new owned segment rather than inventing an index for the unknown entry.
- Editor generation/current-dirty ownership prevents late save/create completion from taking over another editor. Session-generation checking prevents an old request's 401 from clearing a newer login. Current-session 401/logout still discard cache/drafts without an unsaved-input prompt.
- Standard `playwright.menu-masters.config.js` now defaults to `localhost` and binds its dedicated server there. WebKit, local URL/port restrictions and `reuseExistingServer: false` remain. The final run used this tracked config, not only a temporary fixture config. Existing explicit URL validation acceptance is retained; the demonstrated 127.0.0.1 redirect-loop path is not used as the default or claimed verified.

## Commands and Evidence

Commands below ran from this WT's `frontend`, with `/Users/mmorinag/.anyenv/envs/nodenv/versions/20.17.0/bin` first on PATH and npm cache under `frontend/tmp/menu-c1/npm-cache`.

| Command | Exit/result | Evidence under `frontend/tmp/menu-c1/` |
| --- | --- | --- |
| `npm install --package-lock-only --ignore-scripts --no-audit --no-fund --registry=https://registry.npmjs.org` | 0; only root file spec and `node_modules/@sawa/ui` changed in this step | `pack-integration-2026-10-05T01-29-56-837Z/` |
| `npm ci --registry=https://registry.npmjs.org`; `npm ls --all --json` | 0 / 0; fresh installed artifact matches | same directory |
| `node tmp/menu-c1/run.mjs --tracked-config` | 0; fresh `next build` and 27/27 WebKit | `runs/2026-10-05T01-43-02-126Z/` |
| tracked inner command: `node node_modules/@playwright/test/cli.js test --config=playwright.menu-masters.config.js --workers=1 --retries=0 --reporter=list,json --output=<run>/browser` | 0; `E2E_PORT=53770`, `E2E_BASE_URL` unset to exercise the default | same directory |
| `MENU_GREP='unknown.*history\|URL search/page\|dirty link and history\|document reload' node tmp/menu-c1/run.mjs --dev` | 0; 6/6, including equal token/index across StrictMode setup/cleanup | `runs/2026-10-05T01-43-58-459Z/` |
| `node tmp/menu-c1/checks.mjs units` | 0; `node --test tests/*.test.cjs tests/config/*.test.js` 118/118, `tsc --noEmit` 0, `npm run lint` 0 | `units-2026-10-05T01-44-45-058Z/` |
| `npm audit --json`; `npm audit --omit=dev --json`; production `npm ls --all --json` | 0 / 0 / 0 | `audit-2026-10-05T01-44-29-886Z/` |
| HEAD-lock baseline audit, full / production | 1 (5 packages) / 0 | same audit directory, `baseline-*.json` |
| `node tmp/menu-c1/finalize-evidence.mjs units-2026-10-05T01-44-45-058Z` | 0; hash/source/artifact checks and owned ports closed | `final-evidence.json` |

The audit harness exits 1 because it also executes the intentionally unchanged vulnerable HEAD baseline. Actual-tree commands each exit 0. JSON is valid; exit codes are separate status files. The interrupted initial installer is recorded separately, not reported as success.

## Coverage and Images

The 27 actual Next-page cases cover both entry URLs, nine-field POST/PUT plus fresh-document saved values, all units, hot/cold, null/0, duplicate non-overwrite, explicit 409 reload with failure/success, 500 input retention/double-submit, pending Select, async editor ownership, same-ID/stale revision, URL search/page/size/back/forward/invalid bounds, dirty link/history/record/hard-navigation cancel and allow, unknown-history clean/dirty recovery, 401/403, A-to-B/late-A-401/logout/relogin, guest/adjacent-page hydration and IME composition/Enter.

`/menu-masters` returns 308 with Location `/hospital/menu-masters`; the prefixed document returns 200. Guest document delivery is 200 before client authentication redirects to login. HTTP statuses, response bytes and browser exceptions are attached separately. No unexpected API request, page exception or hydration mismatch was observed in the final cases; expected synthetic 4xx/5xx console messages remain in raw evidence.

Images were inspected from the actual Next page, not the shared-package preview:

- [360px actual page](../../frontend/tmp/menu-c1/runs/2026-10-05T01-43-02-126Z/readable-attachments/prefixed-actual-next-360.png), [1280px actual page](../../frontend/tmp/menu-c1/runs/2026-10-05T01-43-02-126Z/readable-attachments/prefixed-actual-next-1280.png).
- [360px quantity 1500/0/g](../../frontend/tmp/menu-c1/runs/2026-10-05T01-43-02-126Z/readable-attachments/7-actual-quantity-table-360.png), [keyboard-scrolled edit action](../../frontend/tmp/menu-c1/runs/2026-10-05T01-43-02-126Z/readable-attachments/7-actual-quantity-mobile-keyboard-end.png), [1280px quantity table](../../frontend/tmp/menu-c1/runs/2026-10-05T01-43-02-126Z/readable-attachments/7-actual-quantity-table-1280.png).

Label/value rectangles and heading separation passed at both widths. The 1500/0/g cells and headings each remain one line, long Japanese text wraps, the document does not overflow, table-local keyboard scrolling reaches edit, and edit/save preserves numeric values. The existing hospital navigation layout was not redesigned.

## Dev Audit and Migration Gate

Only five existing dev lock nodes were updated, within every incoming semver range. No new direct dependency, force fix, override edit, Next/React update or audit suppression was used. Official selected-version registry metadata, integrity checks, before/after locks and narrow lock diff are in `dev-update-2026-10-05T01-19-51-150Z/`.

| Dev node (all `node_modules/<name>`) | Before -> selected official version | Advisory and fixed compatible line |
| --- | --- | --- |
| ajv, through ESLint/eslintrc | 6.12.6 -> 6.15.0 | [GHSA-2g4f-4pwh-qvx6](https://github.com/advisories/GHSA-2g4f-4pwh-qvx6), 6.14.0 |
| brace-expansion, through minimatch | 1.1.12 -> 1.1.21 | [GHSA-q2hr-2g5m-vwhr](https://github.com/advisories/GHSA-q2hr-2g5m-vwhr), 1.1.21 |
| flatted, through flat-cache/file-entry-cache | 3.3.3 -> 3.4.4 | [GHSA-rf6f-7fwh-wjgh](https://github.com/advisories/GHSA-rf6f-7fwh-wjgh), 3.4.2 |
| js-yaml, through ESLint/eslintrc | 4.1.1 -> 4.3.2 | [GHSA-2883-xcg3-v3hh](https://github.com/advisories/GHSA-2883-xcg3-v3hh), 4.3.2 |
| minimatch, through ESLint/glob | 3.1.2 -> 3.1.5 | [GHSA-23c5-xmqv-rm74](https://github.com/advisories/GHSA-23c5-xmqv-rm74), 3.1.4 |

Before: full high4/moderate1, production0. After: full0/production0. Counts are affected packages, not independent CVEs; the full advisory/path inventory is retained in raw baseline audit and `dev-advisory-paths.json`. Production tree has no React-admin, ra-core, query-string or decode-uri-component. Existing nanoid/postcss/sharp overrides are unchanged. npm still reports deprecated inflight, config-array, rimraf, glob, object-schema and ESLint8; these warnings were not hidden. Audit0 is not a general safety guarantee.

The deploy test's former two-job expectation belonged to `7ae61ee`. `e8ee6b1`, integrated by `51fb7b6`, intentionally added the migration gate. `google-auth-deploy.test.js` now asserts exact migration/build/deploy needs and positive success expressions while retaining every OIDC/token check. No workflow was weakened or changed. Historical/current checks plus four deliberately weakened in-memory workflow variants are recorded in `migration-gate-review/results.json` (7 checks); the 8d4bc79 failure is not left unresolved as a baseline exception.

## Preserved Failures and Limits

- Old 259daaa pending-Select failure: `runs/2026-10-05T01-20-27-178Z/` (22/23); retained separately from new-pack success.
- Unknown-history absent/clean failure: `runs/2026-10-05T01-33-49-988Z/`; dirty-to-clean permanent stop reproduced in `runs/2026-10-05T01-34-30-347Z/`. Both fixed at the common boundary, with all related history tests retained.
- `01-35-40-707Z` contains a synthetic direct-keydown MUI failure and an unfocused/single-arrow scroll test failure. MUI's disabled semantics remove pointer activation and focusability but a JavaScript `dispatchEvent(keydown)` still invokes its handler. Final tests retain the original raw pointer probe and test real pointer, keyboard and Tab input; arbitrary script event dispatch is not claimed prevented. Scroll verification uses a focused owned WebKit page and real repeated arrows.
- `01-40-14-825Z` records an added HTTP-proof variable shadowing TypeScript's DOM `document`, fixed before rerun. `01-41-03-444Z` records the incorrect test assumption that same-URL push creates an entry; the corrected test measures real new-segment positions 0/1/2 without rewriting the unknown entry.
- APIs and Bearer identities are synthetic local fixtures with full id/revision/nine-field/list envelopes. No real DB, live Google/OIDC or external product service was used. Composition events are synthetic plus real Enter, not OS-native Japanese IME proof.
- Known-position cancellation restores URL/render/history. Unknown dirty position intentionally shows an explicit stop with the draft retained; it does not claim a guessed URL restoration. Becoming clean recovers, and new owned entries regain normal cancel/allow behavior.
- All owned processes finished. Final Next/API ports 53770/53769 and StrictMode/API ports 54039/54038 returned ECONNREFUSED. User browsers and unrelated processes were not touched.

## Handoff Candidates

23 paths including this report; exact absolute paths and SHA256 values are in `stage-candidates.json`:

- Distribution: `frontend/Dockerfile`, `frontend/package.json`, `frontend/package-lock.json`, new SHA-named vendor tgz and provenance JSON.
- Shared navigation/session: `NavigationBoundary.tsx`, `TopNav.tsx`, `UnifiedShell.tsx`, `useHydratedPath.ts`, `useMenuMasterLeaveGuard.ts`, `_app.tsx`, `apiClient.ts`, `browserSession.ts`.
- Menu page/contract: `src/pages/menu-masters.tsx`, `src/services/menuMasters.ts`.
- Tests/config: `playwright.menu-masters.config.js`, `api-client-session.test.cjs`, `browser-session.test.cjs`, `menu-masters-contract.test.cjs`, `config/google-auth-deploy.test.js`, `config/menu-masters-playwright.test.js`, `e2e/menu_masters.spec.ts`.
- This report only; existing historic/progress reports remain unchanged. Generated `next-env.d.ts` was restored; untracked generated frontend AGENTS/CLAUDE files are absent and excluded. Ignored harnesses/logs/images are evidence, not product stage candidates.

Implementation/test writes stop after this handoff. Parent review/commit/integration and separate integrated-SHA/stg tests remain the next authorized task, not completed here.

## Follow-up Handoff (2026-10-05, HEAD 053c30e)

This addendum freezes the assigned follow-up at the parent's request; it is not stg/C1 completion. The integrated 053 verification is closed separately in `hospital-c1-verify/docs/modernization/menu-master-integrated-verification-053c30e-20261005.md`, retaining its original failures.

- Backend: six missing-auth system tests now use registered helper users and explicit Bearer headers. Only admin endpoints use the new admin fixture; operator defaults/rejections remain. The original eight system-test functions and all 72 assertion ASTs are preserved. The original 749 cases all pass, plus one admin-fixture case: **750 passed, 0 failed, 0 skipped**, with `AUTH_DISABLED=false`, fresh owned SQLite and two independent PG16 clusters. Earlier 749-success evidence included tests depending on disabled auth and must not be called fully auth-enabled coverage. Exact 17-file pytest command/status/environment: `tmp/c1-followup/backend-short-socket/manifest.json`; assertion/JUnit identity proof: `tmp/c1-followup/assertion-preservation.json`. This is the selected regression set, not the full backend suite.
- Frontend: `node tmp/c1-followup/checks.mjs units` using Node20.17.0 gives **120/120**, type/lint exit0 (`units-2026-10-05T02-39-33-797Z`). Full/prod audit and production tree exit0, vulnerabilities0 (`audit-2026-10-05T02-32-21-867Z`); package/lock/vendor unchanged from 053. Audit0 is not a general security guarantee.
- Final tracked-config commands: `MENU_TRACE=on node tmp/c1-followup/run.mjs --tracked-config`, twice, **27/27 each, failed0/skipped0, retries0**, including fresh builds. Evidence: `frontend/tmp/c1-followup/runs/2026-10-05T02-39-32-647Z/` and `2026-10-05T02-45-44-907Z/`. `MENU_GREP='unknown.*history|URL search/page|dirty link and history|document reload' node tmp/c1-followup/run.mjs --dev`: **6/6**, failed0/skipped0 (`2026-10-05T02-47-06-668Z`). All commands exit0 and all three manifests record unchanged tested source/test diff SHA256 `54517d911a2b528f24d39505e0af91dba98a4c5f697265de2f23de3c3de8cd86` and stopped owned processes.
- Failure retained: 053 invalid-query reported five prefetch pageerrors; its first failed run had no trace. Before-fix diagnostic traces (`02-22-52-707Z`, 12 passing repetitions) nevertheless show 1-33 canceled prefetches per document sequence. After the first fix, `02-36-16-369Z` failed the guest case (26/27) when `/about` replaced the login document while `/api/auth/config` was starting. Traces/logs remain; `02:28/29/33` successes are not substituted for the final source. The tracked fixture now waits for finite same-origin traffic before these explicit document replacements and asserts no pending/failed request. Both final invalid-query traces have 76 successful prefetches and zero cancellations (`tmp/c1-followup/prefetch-comparison.json`). Existing pageerror/hydration assertions remain; no product prefetch disabling, error filtering, arbitrary sleep or timeout increase was added. Other startup-only failures (missing copied harness file, PG socket path length, sandbox bind EPERM) are retained separately, not counted as passing tests.
- Display regression: Fields and table share unit/temperature options; labels are `グラム (g)` / `切れ` / `個` / `温` / `冷`. Unknown strings remain raw, null remains unset and zero remains zero. Nine-field POST/PUT assertions and fresh-document saved-value checks remain. New unit tests cover labels/unknown values; real-page tests cover Japanese rows and 360px quantity/label wrapping and keyboard scrolling. No navigation redesign, backend product/auth/workflow or domain/OCR change was made.

Latest inspected images: [1280px actual page](../../frontend/tmp/c1-followup/runs/2026-10-05T02-45-44-907Z/readable-attachments/prefixed-actual-next-1280.png), [360px actual page](../../frontend/tmp/c1-followup/runs/2026-10-05T02-45-44-907Z/readable-attachments/prefixed-actual-next-360.png), [360px Japanese unit and 1500/0](../../frontend/tmp/c1-followup/runs/2026-10-05T02-45-44-907Z/readable-attachments/7-actual-quantity-table-360.png), [keyboard-scrolled edit](../../frontend/tmp/c1-followup/runs/2026-10-05T02-45-44-907Z/readable-attachments/7-actual-quantity-mobile-keyboard-end.png). Image hashes are in that run's `readable-attachments/manifest.json`. Browser APIs/Bearer are local synthetic fixtures, not live Google login evidence.

GHA live design, **not implemented/run**: append an owned WebKit live-only verification after successful `deploy-frontend` web checks, preserving all source/bootstrap/migration/backend gates. Reuse the existing WIF, deploy SA and `STG_GOOGLE_OAUTH_CLIENT_ID` audience; mint a fresh ID token with the existing `google-github-actions/auth@v3` pattern immediately before the test. Keep token only in env/ephemeral context, never argv/log/trace/HAR/storageState; use no API mock or Basic path. Verify expected deployed SHA plus real `/auth/me` and `/portal/auth/me?system=hospital` before writes. SA token injection proves a machine-authenticated session, not the human GIS login interaction. The previous local `iam.serviceAccounts.getAccessToken` refusal does not establish failure of the existing GitHub WIF principal.

The next investigation point is **whether that existing SA already has active operator/admin role and hospital grant in the application**; local preflight could not reach this check. The existing workflow's ID-token inputs and SA self-TokenCreator/WIF policy were read, but no fresh successful GHA/app preflight is claimed. UI's deployed build-client-ID parity also remains to confirm. No new SA, user or permission is proposed.

Live write design is limited to a new exact UUID-named test record: record run/source/revision and UUID ownership ledger before POST, assert name absent, record returned id/revision/nine fields, and only PUT/reload that owned ID. Existing API has no DELETE. Safe cleanup needs a separate approval and verification that an existing GHA/DB path already has the necessary privileges: transactionally match exact ledger id/name/revision/fields, verify no references, delete exactly that row or stop on mismatch. No wildcard/clear-all/cascade or unapproved DB deletion. Existing SQL DELETE privileges and complete reference checks remain **uninvestigated**; until cleanup or explicit retention is approved, do not enable live writes. No live record was created by this follow-up.

Frozen candidates are four backend test/helper files, `frontend/src/pages/menu-masters.tsx`, new `frontend/src/components/menuMasterOptions.ts`, `frontend/tests/e2e/menu_masters.spec.ts`, new `frontend/tests/menu-master-options.test.cjs`, and this addendum (9 paths). `tmp/c1-followup/handoff-20261005.json` records exact paths/hashes, final diff hash and cleanup checks. Nothing is staged or committed. Generated next-env/AGENTS/CLAUDE and ignored evidence are not candidates. Further investigation, integration and live verification belong to the next assignment.
