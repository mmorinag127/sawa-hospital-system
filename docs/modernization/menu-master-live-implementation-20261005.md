# C1 STG Live Verification: Review Handoff

- WT: `hospital-c1-live`; branch `codex/modernization-c1-live-20261005`; original base `91f7291ad66019485da448e6a077f2935867b43f`; current HEAD `0455a69b37b7b729be808cf89b8c6615c8fd3af9` after parent's two-document FF.
- No worker stage/commit/merge/push/deploy or live data writes. Prior candidate files and evidence are preserved.
- This is an implementation candidate, not source/STG/C1 completion. Parent requested write freeze and review handoff.

## Changes

- `deploy-stg.yml`: `verify_menu_master_ui` boolean dispatch input defaults false. Only opted-in steps after successful web deploy prepare dependencies/WebKit, freshly mint the existing WIF/deploy-SA ID token, run verification, and upload sanitized artifacts with `always()`. Existing deploy gates are unchanged. Frontend image gets the same full `sawa.git_sha=${GITHUB_SHA}` label as backend.
- Python runner checks context, immutable manifest/config digests and full image SHA, existing SQL configuration/identity/schema/privileges, real auth/portal APIs and filtered absence before browser writes. It calls only config/proxy helpers, not migration/bootstrap entry points.
- Browser uses actual `/hospital/menu-masters`, canonical `昼食`/`夕食`, nine-field POST/PUT, fresh document reload, null/0/Japanese labels, second-editor real stale409, explicit reload, and labeled pre-backend network-abort injection. No success-response mocks, token arguments/logs/trace/HAR/video/storageState. Checked successful receipts persist before secondary display assertions.
- Cleanup uses bounded real SQL locks, exact acknowledged ID/name/normalized name/revision/nine fields, FK and explicit name/non-FK checks, and `DELETE ... RETURNING id`. Ambiguous POST/pending writes, changes, references, unknown schema/trigger/policy or insufficient privileges preserve the ledger and stop. No ORM/cascade/reference deletion.

## Prior Local Results (91f7291)

All paths below are relative to this WT; exact commands, exits and SHA256 are in `tmp/live-tests/handoff-manifest.json`.

| Check | Result | Evidence |
| --- | --- | --- |
| Fresh npm ci (Node 20.17.0, existing lock) | exit 0 | `tmp/live-tests/install-commands.json` |
| Focused live safety + existing STG migration | 170 passed, 0 failed/skipped; PG 16.14 | `tmp/live-tests/pg-1791171931/focused-pg.xml` |
| Frontend unit/config | 122 passed, 0 failed/skipped | `tmp/live-tests/checks-1791172191448/config.log` |
| Typecheck, full lint, new ESM-script lint | all exit 0 | same directory, `frontend-commands.json` |
| Full/prod npm audit | both exit 0, vulnerabilities 0 | same directory, separate valid JSON files |
| Actual Next build + backend + owned WebKit | build/browser exit 0; no page/hydration/unexpected-write errors | `tmp/live-tests/browser-2026-10-05T03-48-07.976Z/` |
| Existing registry read-only probe | manifest/blob HTTP200, digest checked, frontend SHA label absent | `tmp/live-tests/registry-probe.json` |

The browser used actual `src.main.app` and Next with isolated SQLite; only external Google verification was replaced locally. Its ledger holds canonical dayparts and revisions 1/2/3. Screenshots include `owned-created-1280.png`, `owned-created-right-360.png`, `owned-row-1280.png`, `owned-edit-360.png`. It is not a real Google login, service-principal authorization or deployed STG proof.

Earlier failures are retained: restricted-network npm acquisition, ESM lint parsing before the scoped parser setting, and the extra create-row display assertion using comma instead of the existing Japanese delimiter. The latter failed after real successful POST, with the checked receipt already persisted (`browser-2026-10-05T03-42-05.861Z/ledger.json`). Retests passed without changing product behavior. Initial PG162 and intermediate PG166 evidence is also retained.

## DB Target Gap Follow-Up (0455a69)

- The identified gap is closed in the shared `scripts/staging_db_target.py`, used by both `run_stg_menu_master_migration.py` and `verify_stg_menu_master_ui.py`. Production bootstrap and `backend/src/db.py` are untouched.
- Registry manifest/config digest checks are reused to read actual immutable image `Env`; revision env overrides baked defaults. Only the measured explicit Cloud SQL component profile is supported: exact instance/socket, `orders`, `orders_app`, driver `postgresql+psycopg2`, absent/empty/5432 port. Nonempty `DB_URI` (including otherwise valid URIs) and routing-secret/ambiguous shapes stop; URI support is not claimed. Password references are resolved privately. Product URI precedence is not changed or silently bypassed.
- Both currently serving and latest observed/ready intended revisions are validated, including intended template parity. Template metadata.name is not required. Split/unresolved/stale configuration blocks. The live guard binds to the revision previously source-checked; web's effective `API_PROXY_TARGET` must equal the exact worker-stg URL. Migration also checks real connected database/role before schema writes; cleanup retains its real read-only DB/role gate.
- Final focused suite: **222 passed / 0 failed / 0 skipped**, PG16.14, `tmp/live-tests/pg-1791173516/focused-pg.xml`. Includes both callers rejecting URI/host/baked-env/proxy/traffic/target mismatches before opening the DB and a real SQL connected-target rejection before migration. Frontend unit/config: **122 passed / 0 failed / 0 skipped**, `db-target-frontend.log`.
- Actual read-only guard success: `tmp/live-tests/verified-db-target.json`. Serving revisions `web-stg-00351-z92` and `worker-stg-00771-t8s`, both DB targets `orders-stg/orders/orders_app`; immutable image configs contained no relevant baked DB/proxy keys. Passwords resolved without logging. This is configuration evidence, not DB privilege or human-login proof.
- Initial follow-up run retained at `pg-1791173373`: 213 passed / 6 failed (test parameter/module name collision during HTTP helper extraction). Fixed and rerun, no assertions skipped. No product frontend/browser code changed; prior UI evidence remains prior evidence, not a new UI run.
- Final candidate/diff/evidence hashes and exact commands: `tmp/live-tests/db-target-handoff-manifest.json`. All owned processes stopped; no dependency/lock/generated-next-env change.

## Required Review / Unverified

- DB target/proxy parity now has the shared fail-closed guard above; no known remainder of that assigned implementation gap. Unsupported URI configurations intentionally block rather than selecting another database.
- Actual opted-in Actions execution, both newly labeled images, existing deploy-SA hospital grant, live SQL DELETE/lock/schema/reference conditions and final API404/absence remain unrun. These are runtime gates, not assumed authorization. Parent dispatches only after review/integration; no auth/grant fallback.
- STG is PG15; local SQL tests used available PG16.14, not PG15. Registry blob redirects were not observed (200); simulated allowed HTTPS GCS redirects are tested without forwarding Authorization, and unknown targets are rejected.
- All owned backend/Next/WebKit/PG processes stopped; command/cleanup JSON retained. Generated `next-env.d.ts` restored exactly; package/lock/product source unchanged. Audit0 is not a general security guarantee.
