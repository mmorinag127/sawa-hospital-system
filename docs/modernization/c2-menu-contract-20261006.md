# C2 Menu-Master Contract

The offline source is `frontend/src/generated/menu-master-openapi.json`, exported from
a dedicated `FastAPI` instance that mounts only the menu-master router and has no
startup or database side effect. It documents all fields returned by
`menu_service.serialize_menu_master`: `id`, `revision`, `name`, `normalized_name`,
`unit_type`, `qty_per_serving`, `bag_max_qty`, `bag_max_unit`, `temp_type`, `daypart`,
`category`, and `condiments`.

Run `npm run generate:menu-master-api` in `frontend` to regenerate the local schema and
types. Run `npm run check:menu-master-api` to fail on a stale schema or generated type.
The frontend aliases its menu-master response types to the generated OpenAPI components.

Input schemas remain the existing permissive `Any` fields. API routes use schema-only
`responses` declarations with `response_model=None`, so runtime output is not filtered,
coerced, or stripped. `condiments` is consequently documented as a JSON list. The
frontend keeps its string-list draft type and narrows this response only after runtime
validation.

## Correction Record

An earlier local attempt used `response_model` and exported `src.main.app.openapi()`.
It was corrected before verification because those choices could transform runtime
responses and export unrelated API schemas.

## Verification Evidence

All artifacts below were generated from the current uncommitted worktree diff on top
of `a156a07243d3fa80ebfe6efe8c55053b2d79f7f3`. That SHA is the base SHA, not a
generation commit. No generated artifact in this record is evidence of a commit.

Isolation used `HOME=/private/tmp/hospital-c2-menu-contract-home`,
`TMPDIR=/private/tmp/hospital-c2-menu-contract-tmp`, and
`PYTHONPYCACHEPREFIX=/private/tmp/hospital-c2-menu-contract-pycache`. Backend
commands used the read-only interpreter
`/Users/mmorinag/Sawa/2025.12/workspace/backend/.venv/bin/python`; frontend schema
commands prepended that interpreter directory to `PATH`. npm used the isolated cache
`/private/tmp/hospital-c2-menu-contract-npm-cache`.

| Check | cwd and complete command | Result and evidence |
| --- | --- | --- |
| Backend focused suite | cwd `/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c2-menu-contract`; `HOME=/private/tmp/hospital-c2-menu-contract-home TMPDIR=/private/tmp/hospital-c2-menu-contract-tmp PYTHONPYCACHEPREFIX=/private/tmp/hospital-c2-menu-contract-pycache /Users/mmorinag/Sawa/2025.12/workspace/backend/.venv/bin/python -m pytest backend/tests/contract/test_menu_master_openapi_contract.py backend/tests/integration/test_menu_master_revision.py backend/tests/unit/test_menu_master_crud_baseline.py backend/tests/contract/test_user2_permissions_api.py -q -rs` | exit 0; `148 passed, 127 skipped`. Log: `/private/tmp/hospital-c2-menu-contract-logs/backend-focused.log`; SHA256 `498febde9a59a315812baddd6ce052bd967d5cbd9dbe190ea81832037bd65f8f`. |
| Frontend menu-master unit | cwd `/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c2-menu-contract/frontend`; `HOME=/private/tmp/hospital-c2-menu-contract-home TMPDIR=/private/tmp/hospital-c2-menu-contract-tmp node --test tests/menu-masters-contract.test.cjs` | exit 0; `33 pass, 0 fail`. Log: `/private/tmp/hospital-c2-menu-contract-logs/frontend-menu-master-unit.log`; SHA256 `9a95b3f99cc926650e12a8382dd0a800431649dcf46ffc82c62bdf4d157fef06`. |
| Current generated-artifact check | cwd `/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c2-menu-contract/frontend`; `HOME=/private/tmp/hospital-c2-menu-contract-home TMPDIR=/private/tmp/hospital-c2-menu-contract-tmp PYTHONPYCACHEPREFIX=/private/tmp/hospital-c2-menu-contract-pycache PATH=/Users/mmorinag/Sawa/2025.12/workspace/backend/.venv/bin:$PATH npm run check:menu-master-api` | exit 0. Log: `/private/tmp/hospital-c2-menu-contract-logs/menu-master-stale-check.log`; SHA256 `ff1d738c6cf641fb2b2fffe4573520c422e1c789f55ae04fd548445a98e348e3`. |
| Deliberately stale schema | cwd `/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c2-menu-contract/frontend`; `cp src/generated/menu-master-openapi.json /private/tmp/hospital-c2-menu-contract-stale-openapi.json; python3 -c 'from pathlib import Path; p=Path("/private/tmp/hospital-c2-menu-contract-stale-openapi.json"); p.write_text(p.read_text(encoding="utf-8").replace("Menu Master API Contract", "stale contract", 1), encoding="utf-8")'; HOME=/private/tmp/hospital-c2-menu-contract-home TMPDIR=/private/tmp/hospital-c2-menu-contract-tmp PYTHONPYCACHEPREFIX=/private/tmp/hospital-c2-menu-contract-pycache PATH=/Users/mmorinag/Sawa/2025.12/workspace/backend/.venv/bin:$PATH python3 ../scripts/export_menu_master_openapi.py --check --output /private/tmp/hospital-c2-menu-contract-stale-openapi.json` | expected exit 1 and actual exit 1. Log: `/private/tmp/hospital-c2-menu-contract-logs/menu-master-stale-mismatch.log`; SHA256 `faac0a68c34b05f90c0c5ae24db36eb405a404d3289b0c1c20305bd741228500`. |
| Type regeneration equality | cwd `/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c2-menu-contract/frontend`; `./node_modules/.bin/openapi-typescript src/generated/menu-master-openapi.json -o /private/tmp/hospital-c2-menu-contract-menu-master-api.ts` | exit 0; tracked and regenerated SHA256 both `96889e99a9d1c481e6fdda175781534771ddaa7dd78f4f83180fce6413d384de`. Log: `/private/tmp/hospital-c2-menu-contract-logs/menu-master-types-regenerate.log`; SHA256 `1c8fcaeff5294452fda993954119659eebd699a6b925963dd19efa011f918e01`. |
| Frontend build | cwd `/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c2-menu-contract/frontend`; `HOME=/private/tmp/hospital-c2-menu-contract-home TMPDIR=/private/tmp/hospital-c2-menu-contract-tmp npm run build` | exit 0. Log: `/private/tmp/hospital-c2-menu-contract-logs/frontend-build.log`; SHA256 `6712b34704a9f46322a3b2e57532c24af5a3c6e5b613cb9186c2c95cad35ebe0`. |
| Dependency audit | cwd `/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c2-menu-contract/frontend`; `HOME=/private/tmp/hospital-c2-menu-contract-home npm audit --audit-level=low --cache /private/tmp/hospital-c2-menu-contract-npm-cache` | exit 0; `found 0 vulnerabilities`. Log: `/private/tmp/hospital-c2-menu-contract-logs/npm-audit.log`; SHA256 `00ca9fec62c40d8f19b79bbce73250c177e5f7e01c7bc8870e0191b67250866a`. |

The 127 backend skips are all PostgreSQL-only integration cases in
`backend/tests/integration/test_menu_master_revision.py`: 3 at line 142, 1 at 171,
10 at 190, 1 at 200, 1 at 209, 2 at 229, 1 at 252, 96 at 269, 4 at 311, 1 at 330,
1 at 377, 5 at 401, and 1 at 416. Every one reports
`C1_POSTGRES_URI required for dedicated local PostgreSQL proof`; no test was removed
or weakened.

`node --test tests/config/*.test.js tests/*.test.cjs` was compared against an isolated
`git archive` of base SHA `a156a07243d3fa80ebfe6efe8c55053b2d79f7f3`. Both base and
current uncommitted worktree exit 1 with `122 pass, 1 fail`: `tests/config/google-auth-deploy.test.js:51`
expects 3 and observes 4. Base log:
`/private/tmp/hospital-c2-menu-contract-logs/base-a156-test-config.log`, SHA256
`109bbe18ad02fc22ff1bc5e0ac7b0a5a665dad19c5384f549c150180d56c8efd`.
Current log: `/private/tmp/hospital-c2-menu-contract-logs/current-test-config.log`, SHA256
`9242b07173f94980aff8fb2460a0ea8f8b779295b2d6449f792be7d404382ebb`.

Not run: frontend E2E and live/browser/cloud verification. They are outside this
offline contract assignment and were not used as completion evidence.

## Post-Integration Verification: `ef21a14ca1d7123f63b492903b7aa25914ee1c2e`

Both `/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c2-menu-contract`
and `/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-main`
resolved to this commit before the following runs. Generated source artifacts are from
this committed tree: `/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c2-menu-contract/frontend/src/generated/menu-master-openapi.json`
has SHA256 `95fb719cbe277f970cb0f652b0aa81d385bed9ba827a1f23b65ce058c8e70096`, and
`/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c2-menu-contract/frontend/src/generated/menu-master-api.ts`
has SHA256 `96889e99a9d1c481e6fdda175781534771ddaa7dd78f4f83180fce6413d384de`.

| Check | Result | Log and SHA256 |
| --- | --- | --- |
| Backend focused suite with the same isolated HOME/TMP/PYTHONPYCACHEPREFIX and read-only backend venv recorded above | exit 0; `148 passed, 127 skipped`; all skips remain `C1_POSTGRES_URI required for dedicated local PostgreSQL proof`. | `/private/tmp/hospital-c2-menu-contract-logs/ef21a14-backend-focused.log`; `db6cddedaf34e8632f77853e5e18626f8b93c2cb34c7f93b78bf79544cb7e287` |
| Frontend menu-master unit | exit 0; `33 pass, 0 fail`. | `/private/tmp/hospital-c2-menu-contract-logs/ef21a14-frontend-menu-master-unit.log`; `1522d6205fe70fa655d8873b6a171217b42b174e90586a606ff64410ed9c86eb` |
| `npm run check:menu-master-api` with isolated HOME/TMP/cache and backend venv on PATH | exit 0. | `/private/tmp/hospital-c2-menu-contract-logs/ef21a14-schema-check.log`; `06e4ddad60282331ed4b5e14f199df81bef1b6eeb469b1b39bcd148568ccb5f7` |
| `openapi-typescript src/generated/menu-master-openapi.json -o /private/tmp/hospital-c2-menu-contract-ef21a14-menu-master-api.ts` | exit 0; regenerated TS SHA256 exactly matches tracked TS SHA256 above. | `/private/tmp/hospital-c2-menu-contract-logs/ef21a14-types-regenerate.log`; `20278dc075ebc6a30fdff84c99788ee84d1c0803944594055f078a3304f2ac41` |
| `npm run build` with isolated HOME/TMP | exit 0. `next-env.d.ts` received a Next-generated line and that line alone was removed afterward. | `/private/tmp/hospital-c2-menu-contract-logs/ef21a14-frontend-build.log`; `5b9af8d7bfd2a3cbdaf11daf36ac71cddd7f299dea14587efb1b78aac94ad327` |

### PostgreSQL Proof Status

This WT used only `tmp/c1-pgdata`, `tmp/pgs`, `tmp/c1/cache`, and
`tmp/c1/logs`, with port 55437 confirmed unused before initialization. Initial
`initdb` in the sandbox failed with `shmget(...): Operation not permitted`; a
permitted retry initialized the dedicated cluster. PostgreSQL then rejected the
required fixture socket path
`/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c2-menu-contract/tmp/pgs/.s.PGSQL.55437`
because it exceeds its 103-byte Unix-socket maximum. The fixture requires exactly
`ROOT/tmp/pgs`, so a shorter socket path would not test the required contract and was
not substituted. No server started (`pg_ctl status` exit 3), no database was created,
and the 127 PostgreSQL cases were not run or treated as passing. PostgreSQL log:
`/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c2-menu-contract/tmp/c1/logs/postgresql.log`;
SHA256 `2b75fc036633e6b11a5d8b42445da65834fbbf6578c33b59c09208afde5ffadb`.

### PostgreSQL Proof Completion: Short Socket Alias

The fixed fixture invariant is `Path(url.query["host"]).resolve() == ROOT / "tmp/pgs"`.
To avoid the PostgreSQL 103-byte Unix-socket path limit without weakening that assertion,
the dedicated alias `/private/tmp/hospital-c2-pgs` was created as a symlink to this WT's
`tmp/pgs`. PostgreSQL `unix_socket_directories` and `C1_POSTGRES_URI` both used the
short alias. The preflight confirmed that the URI resolves to
`/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c2-menu-contract/tmp/pgs`,
uses local role `c1test`, database `c1_menu_master`, and port `55437`.

The command used `C1_POSTGRES_URI=postgresql+psycopg2://c1test@/c1_menu_master?host=/private/tmp/hospital-c2-pgs&port=55437`
with the existing isolated HOME/TMP/cache environment and ran
`backend/tests/integration/test_menu_master_revision.py`. It exited 0 with `255 passed`
in 11.43 seconds; the PostgreSQL parameterized cases were executed rather than skipped.
No assertion, production implementation, or existing test was changed. Test log:
`/private/tmp/hospital-c2-menu-contract-logs/ef21a14-menu-master-postgres.log`; SHA256
`d642ed2f5a161a15181ff7296d3c42b73d396e8f84d4ed767e0de4e976558304`.
JUnit artifact:
`/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c2-menu-contract/tmp/c1/logs/pytest-ef21a14-pg.xml`;
SHA256 `e817686f989a7072ae0c019729ca269536367e57c1b79f8a4e42759a81e32787`.

URI preflight log:
`/private/tmp/hospital-c2-menu-contract-logs/ef21a14-pg-uri-preflight.log`; SHA256
`340bcd05b724bcdce88c204a6c8787cfbf15f780e0a067413d676cdfe5bbd9cd`.
The lifecycle log records the symlink target, `c1_menu_master|c1test|t`, server stop
exit 0, and post-stop `pg_ctl status` exit 3:
`/private/tmp/hospital-c2-menu-contract-logs/ef21a14-pg-lifecycle.log`; SHA256
`95d17bb9bb99098cf9ba496a0c89ee5d4f0d93d45291e7e1ad94811e8d3c6fd2`.
Server log:
`/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c2-menu-contract/tmp/c1/logs/postgresql-short-socket.log`;
SHA256 `e4cc70dfdeceb9afe675e55e097bc568fb4620d6a5f754b446149f3858850ef4`.
The earlier long-path failure record remains above as historical evidence.
