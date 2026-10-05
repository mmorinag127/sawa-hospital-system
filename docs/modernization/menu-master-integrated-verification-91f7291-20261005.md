# Hospital C1 Integrated Verification: 91f7291

Date: 2026-10-05 UTC. Dedicated WT (`W`): `/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c1-verify`.
Branch: `codex/modernization-c1-hospital-verify-20261005`.
Generating commit: **`91f7291ad66019485da448e6a077f2935867b43f`**, equal to read-only hospital-main; both trees `9fa5cc439b1aa75bc1b7b6d662b6d34b768ca6bd`.

All results below are fresh postcommit executions, not relabeled candidate evidence. This is local integrated-product verification, **not live/stg/C1 acceptance or full-backend-suite clearance**.

## Results

| Execution | Result | Exit |
| --- | --- | --- |
| Fresh npm ci, Node20.17.0 / npm10.8.2 | Fixed committed lock installed, unchanged | 0 |
| Frontend unit/config | 120 pass, 0 fail/cancel/skip | 0 |
| TypeScript / lint | Both pass | 0 / 0 |
| Production Next16.3.8 build | Pass | 0 |
| Tracked-config WebKit | 27 pass, 0 unexpected/flaky/skip; one full run, retry0 | 0 |
| StrictMode history subset | 6 pass, 0 unexpected/flaky/skip | 0 |
| Same 17-file backend set, AUTH_DISABLED=false | 750 pass, 0 fail/error/skip | 0 |
| Vendor800 installed parity | 12 regular files match tarball; lock integrity matches | 0 |

The repaired system-admin tests now pass 8/8; auth-support passes 47/47. Menu revision passes 255/255 and staging-migration contracts 109/109. Exact file groups and JUnit are retained. Frontend tests exercise shared Japanese unit/temperature labels, null/zero/unknown preservation, both entry URLs, all nine saved fields, new-document readback, revision409, pending inputs, dirty/history, auth/cache disposal and IME events. The previously failing invalid-query and guest navigation tests pass with their original strict pageerror/hydration checks and committed finite-request drain; no retry, suppression or source repair occurred here.

## Reproduction And Evidence

`N=/Users/mmorinag/.anyenv/envs/nodenv/versions/20.17.0/bin/node`; `P=/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c0/backend/.venv/bin/python` (read-only interpreter). New ignored harnesses adapt previous source only for this HEAD, owned output paths, and fresh database isolation; no old result is reused.

```sh
# cwd W
"$N" tmp/integrated-91f7291-r1/source-proof.mjs before
# cwd W/frontend
"$N" tmp/integrated-91f7291-r1/fresh-install.mjs
"$N" tmp/integrated-91f7291-r1/checks.mjs units
MENU_TRACE=on "$N" tmp/integrated-91f7291-r1/run.mjs --tracked-config
MENU_GREP='unknown.*history|URL search/page|dirty link and history|document reload' "$N" tmp/integrated-91f7291-r1/run.mjs --dev
"$N" tmp/integrated-91f7291-r1/extract-evidence.mjs 2026-10-05T03-30-38-794Z
# cwd W
"$P" tmp/integrated-91f7291-r1/run_backend.py
"$P" tmp/integrated-91f7291-r1/verify_artifact.py
"$N" tmp/integrated-91f7291-r1/source-proof.mjs after
"$P" tmp/integrated-91f7291-r1/finalize.py
```

- [Final manifest](../../tmp/integrated-91f7291-r1/final-manifest.json): exact expanded argv/cwd/status, generating SHA, source-before/after hashes, tracked test/vendor hashes, per-file evidence inventory and process checks.
- [Backend manifest](../../tmp/integrated-91f7291-r1/backend/manifest.json), [750-case summary](../../tmp/integrated-91f7291-r1/backend/results-summary.json), [groups](../../tmp/integrated-91f7291-r1/backend-groups.json). Pytest ran all 17 files with `-vv --tb=short`, owned `--basetemp`, cache and JUnit; no selection/assertion changes or skips.
- [Frontend120/types/lint](../../frontend/tmp/integrated-91f7291-r1/units-2026-10-05T03-30-15-424Z/manifest.json), [production27](../../frontend/tmp/integrated-91f7291-r1/runs/2026-10-05T03-30-38-794Z/manifest.json), [Strict6](../../frontend/tmp/integrated-91f7291-r1/runs/2026-10-05T03-31-45-424Z/manifest.json).
- Visually inspected fresh [360px](../../frontend/tmp/integrated-91f7291-r1/runs/2026-10-05T03-30-38-794Z/readable-attachments/prefixed-actual-next-360.png), [1280px](../../frontend/tmp/integrated-91f7291-r1/runs/2026-10-05T03-30-38-794Z/readable-attachments/prefixed-actual-next-1280.png), [mobile 1500/0/Japanese units](../../frontend/tmp/integrated-91f7291-r1/runs/2026-10-05T03-30-38-794Z/readable-attachments/7-actual-quantity-table-360.png). Images are byte-extracted Playwright attachments, not reconstructed screenshots; their hashes are in the manifest.

## Isolation, Preservation And Limits

PostgreSQL is **16.14 (Homebrew)**, not staging15 and not proof of exact production-version parity. Two new clusters used Unix sockets only (`listen_addresses=''`), owned SQLite and isolated HOME/temp/cache. AUTH_DISABLED remained false. Python3.11.15 imports resolve to this WT's backend; synthetic auth/Google fixtures and external-network rejection remain. No existing DB was connected. The fixture-mandated old PG data directory was checked stopped, moved aside, restored after the new cluster stopped, and all 1272 old files rehashed unchanged.

All 1304 tracked files (including product/tests/lock/vendor) match the pre-run hashes; source diff is empty. Lock SHA256 `e17f31cebdec7ed1d7ba77096495e3fc729a5eddd12bb808e3bb0c8422d5c590`. Vendor source `8004321e0364f54d1294df4bb3143dc091fc5a86`, tarball SHA256 `eb85dd9a368cbac702840289ff4c1e964de09707a07831b7043755130d82dfcb`; [installed comparison](../../tmp/integrated-91f7291-r1/installed-artifact.json). No repacking occurred.

Old untracked [053 report](menu-master-integrated-verification-053c30e-20261005.md) and 236 indexed prior evidence files remain unchanged, including initial failures. This new execution had no test/startup failures. npm deprecation warnings and Playwright's NO_COLOR/FORCE_COLOR warning remain in logs. No fresh full/prod audit beyond npm ci's normal audit is claimed by this bounded assignment.

All owned Next/mock/WebKit processes and both PG clusters stopped; the four reserved HTTP ports have no listener, Unix sockets are absent, and generated next-env/AGENTS/CLAUDE artifacts are restored. Only this new report is a stage candidate; ignored tmp harnesses/results are evidence, not product changes. No stage/commit/push/merge/deploy, live auth/grants, Chrome or user browser was used. Other WTs and staged documents remain read-only. Backend coverage is the selected set, not closure of older full-suite failures; browser API/Bearer are local fixtures and IME events are not OS-native Japanese IME proof. Live acceptance remains for its separately authorized worker.
