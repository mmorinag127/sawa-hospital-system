# STG Catalog Relation-Type Regression

Base: `b28833f2b7e104f787f6489a5b92f5ab62e6dce6`, WT `hospital-c1-live`. No stage/commit/deploy/live writes; this is a local fix candidate, not C1 or deployed verification completion.

## Cause And Fix

The read-only result from Actions `37263836807` failed at `read-only-database-preflight`, code `unknown-non-FK-reference-column`, before ledger/POST. The deployed source was b28833f, web `00353-5rz`, worker `00773-j78`.

`backend/migrations/0015_runtime_schema_repairs.py:68` and `:233` define the constraint/index `uq_menu_facility_override_scope` and `uq_menu_facility_overrides_master_facility` on `(menu_master_id, facility_id)`. The prior live-test fixture omitted both. Adding those definitions reproduced the exact error against the unchanged b28833f guard; a normal index on `monthly_menu_items(menu_master_id)` also reproduced it. The isolated catalog output contains their real index definitions and `relkind=i` attributes.

The shared `schema_gate` queried `pg_attribute` without distinguishing indexes. PostgreSQL documents [index attributes](https://www.postgresql.org/docs/16/catalog-pg-attribute.html) and [relation kinds](https://www.postgresql.org/docs/16/catalog-pg-class.html). The fix excludes only `i`/`I` (ordinary/partitioned indexes), not table names. All other relation kinds remain checked. Existing FK, reference, identity, privilege, row equality and lock checks are unchanged.

## Verification

Commands from this WT used `/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c0/backend/.venv/bin/python tmp/live-tests/run-pg-relkind-b28833f.py before` and the same command with `after`.

| Evidence under `tmp/live-tests/` | Result |
| --- | --- |
| `relkind-before-qgex9li1/` | Sandbox initdb shared-memory EPERM; exit 1 retained; no test executed |
| `relkind-before-pzx7uj5h/` | Approved isolated PG run: 2 failed / 0 skipped, exact reported code; exit 1 |
| `relkind-after-u4f7ya5y/` | 236 passed / 0 failed / 0 skipped, exit 0; PostgreSQL 16.14 |

The final suite preserves all prior 222 tests and adds 14: two successful index cases with exact owned cleanup, and twelve unknown-reference cases (both ID/name columns across ordinary, partitioned, foreign, materialized-view, view and composite kinds). Unknown ordinary/partitioned relations also have indexes. They stop the real runner at DB preflight with no API/browser call, ledger or record. Foreign-table tests use catalog-only FDW metadata, no handler or network target. Existing unsafe cleanup/ref/lock cases remain covered.

Every invocation has immutable source copies, source diff, commands/exits, XML, logs, cleanup and SHA256 manifest. Final code/test diff: `ac0cc66d2da3cb248abb39e6314c692ec4866afe6a1a03db29293118250de8af`. Final run manifest: `44c347dd7ed3f963975c8741d64e9b11c8d23ca60157d86312371b746c9ab487`. Consolidated paths/hashes: `tmp/live-tests/relkind-b28833f-handoff.json`.

All owned PG processes stopped; no TCP listener was enabled and dedicated sockets were removed. The pre-existing stopped cluster was preserved at `relkind-before-qgex9li1/prior-stopped-pgdata`; old harnesses/manifests/logs were not overwritten. Frontend/config and product UI/API did not change, so no frontend/UI rerun was made. Local PG16 results are not STG PG15 or live-success proof; parent must review/integrate and rerun the opted-in Actions path.
