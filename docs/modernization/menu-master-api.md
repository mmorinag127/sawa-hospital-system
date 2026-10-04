# C1 Menu Master Backend Contract And Evidence

Date: 2026-10-05 JST. Backend worker evidence, not acceptance of all C1.

- Worktree: `/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c1-api`
- Branch: `codex/modernization-c1-api-20261005`
- Base: `5ca6c130309413aedcfdc6f95f0b69739a795b90`; changes are uncommitted.
- Source decision: the old cleanup's 83 tracked changes and 19 new files are
  excluded, per `platform-main/docs/modernization-legacy-cleanup-review.md`.
  No files were copied from that cleanup. The underlying C0 baseline remains
  `7ae61ee1033776e2c413a621a2013c4c93002df4`.
- No frontend, OCR, facility, order/output algorithms, workflow or lock changes.
  No staging, commit, merge, push, live connection or deployment was performed.

## Contract

All four endpoints retain `require_role("operator")`: operator and admin with
hospital access through the current Bearer/local or Portal path. Basic is not
accepted. Authorization precedes the menu schema dependency.

| Method | Request | Successful response |
| --- | --- | --- |
| GET `/menu-masters` | `q`, `limit`, `offset`, `sort`, `order` | `{items, total, offset, limit}` |
| GET `/menu-masters/{id}` | Existing id | `{item}` |
| POST `/menu-masters` | Existing editable fields | `{item}`; initial revision 1 |
| PUT `/menu-masters/{id}` | Editable patch plus the revision read by the caller | `{updated: true, item}`; flushed saved record |

Items retain `id`, `normalized_name`, and all nine editable fields: `name`,
`unit_type`, `qty_per_serving`, `bag_max_qty`, `bag_max_unit`, `temp_type`,
`daypart`, `category`, `condiments`. They additionally include `revision`.

Existing coercion remains in the service: name trim/normalization, unit aliases
and `g`/`cut`/`count`, daypart normalization, null quantities and numeric zero.
No new enum restrictions apply to business input. Absent patch fields do not
change saved values. Existing condiments behavior is retained (null becomes an
empty list; update strips/removes blank entries). Readonly `id`,
`normalized_name`, and other extra input keys do not populate business fields.
POST ignores a supplied revision and returns an existing normalized-name match
unchanged, including its current revision. Duplicate rename/blank name remains
400. Unknown GET/PUT id is 404 for otherwise valid authorized requests.

PUT revision is a strict positive integer: omission, null, bool, numeric string,
float, zero and negative values return 422 without update. An old revision or
an ORM flush conflict returns 409, without retry or unconditional overwrite.
The API uses `save_menu_master`; the existing boolean `update_menu_master`
service wrapper uses that same save implementation. SQLAlchemy `version_id_col`
covers ordinary ORM writes, including monthly-menu master updates. A changed
record increments its revision; a patch with no effective changes need not
increment it. Direct SQL/bulk ORM updates do not participate in SQLAlchemy's
per-instance version check and must not be introduced as an alternate writer.

List `q` remains a trimmed, case-insensitive name substring (`ILIKE`, retaining
existing SQL wildcard semantics). `total` counts matching rows before paging.
Default ordering is name ascending, then id ascending. Explicit sort allowlist:
`id`, `name`, `unit_type`, `qty_per_serving`, `bag_max_qty`, `bag_max_unit`,
`temp_type`, `daypart`, `category`, `revision`. `order` is `asc` or `desc`;
id ascending breaks ties in either direction. Other sorts/orders and negative
offset return 422. Offset defaults to 0. Existing limit coercion is retained:
default/zero -> 1000, negative -> 1, values above 5000 -> 5000. In particular,
existing `limit=10000` clients are not rejected.

## Schema And Explicit Migration

`backend/migrations/0027_menu_master_revision.py` is the only DDL source for
revision. It follows 0026 and adds `INTEGER NOT NULL DEFAULT 1` without updating
id, the nine editable fields, normalized_name or updated_at. Existing records
receive revision 1. Re-executing the explicit upgrade checks INTEGER (not
BIGINT/text), NOT NULL, a constant default 1, no identity/computed generation,
and no non-positive saved revision. Valid existing schema is a read-only no-op;
different schema or a missing table produces a fixed diagnostic and stops.
Already advanced positive revisions are retained. It never repairs an invalid
existing revision column.

`scripts/apply_menu_master_revision_migration.py` loads that exact migration's
`upgrade` under Alembic Operations in one transaction. It does not duplicate
DDL, run prior migrations, stamp an Alembic version table or run at startup.
The complete existing migration chain and operational runner remain parent
integration work, not demonstrated by this helper.

Supply the URI on stdin, not as an argv value. The helper has no `--db-uri`
option. Use a restricted secret file from the approved execution environment;
never echo its contents or enable shell tracing. For an explicitly authorized
execution (this worker used only dedicated local databases):

```sh
set +x
PYTHONDONTWRITEBYTECODE=1 "$PYTHON" "$WT/scripts/apply_menu_master_revision_migration.py" \
  --db-uri-stdin < "$DB_URI_FILE"
```

`DB_URI_FILE` denotes a pre-existing restricted file (for example mode 0600),
not a URI string. No credential belongs in the command line. Successful output
is `0027 complete: revision schema applied or already valid`. Invalid schemas
return exit 1 and a fixed `0027 blocked` reason. Other exceptions return exit 1
with driver/parser details and traceback suppressed. Tests cover secret
sentinels in both parser and PostgreSQL connection failures.

Runtime/import/read paths add no revision DDL. The existing schema check now
requires revision and raises `MenuSchemaNotMigrated`. Authorized menu-master
API requests encountering that check return JSON 503:

```json
{"detail":{"code":"menu_schema_not_migrated","message":"menu schema is not migrated; run database migrations before boot: menu_masters.revision"}}
```

The production `src/main.py` startup hook still calls the same check and fails
startup before serving requests when migration is missing. It was not changed
to continue on an invalid database. JSON 503 was tested on the actual router
in a minimal FastAPI harness; the full application's lifespan/OCR recovery
loop was not started. This is not a claim that a failed-startup deployment can
serve JSON. The schema exception names the missing column/index for the
deployment operator; generic 500 HTML is not the tested API contract.

The previous PostgreSQL read-time repair of monthly-menu identity indexes was
removed. Absence of `uq_monthly_menu_items_scope_identity` now stops existing
read/write service paths and produces the same JSON 503 at these API routes,
explicitly naming migration 0023. It does not auto-create/drop indexes.

### Deployment Prerequisites Still Open

- Parent must connect explicit migrations to the approved GitHub Actions route
  before live use. No stg/prod migration or deployment is complete here.
- Inspect stg's actual revision column, prior migrations/system-access table,
  and monthly-menu indexes/constraints before release. In particular verify
  the definition/uniqueness of `uq_monthly_menu_items_scope_identity`, not only
  its name. Runtime validation currently checks presence, not index-definition
  equivalence.
- If 0023 is absent, explicitly apply the approved migration before startup.
  Inspect whether `uq_monthly_menu_item_scope` is a constraint-owned index:
  existing 0023 drops the index, whereas the removed runtime repair also
  dropped that constraint. Constraint ownership can therefore block direct
  0023 application and requires a parent-reviewed explicit migration path;
  do not reinstate runtime repair or claim the current helper resolves it.
  This historical migration-chain gap is carried by the parent to C2. Migration
  0023 was not changed in this assignment.
- UI revision submission/conflict presentation, integration SHA regressions,
  real stg entry/save/reload/error flows and user usability confirmation remain
  unverified. The existing frontend PUT does not send revision and receives
  422 from this API. **Do not deploy this API alone before the C1 UI is integrated.**

## Isolated Verification

No existing PostgreSQL instance was used. Homebrew PostgreSQL 16.14 used data
directory `$WT/tmp/c1-pgdata`, socket `$WT/tmp/pgs`, port identifier 55437,
database `c1_menu_master`, local role `c1test`. `listen_addresses=''` disables
TCP entirely; host authentication rejects connections. Each PG test creates
and drops a unique schema inside this database. SQLite files, caches, logs and
pytest basetemp are under this WT's tmp. The existing root conftest's
`create_all` is confined to the explicitly supplied local SQLite file.

Dependencies were read from
`/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c0/backend/.venv/bin/python`.
No install/write was done there. `PYTHONDONTWRITEBYTECODE=1`, own-WT PYTHONPATH
and own caches were used. `tmp/c1/logs/environment-final.log` proves
`src.__file__`, API/model/service imports all resolve into hospital-c1-api,
and records SQLAlchemy 2.0.46, Pydantic 2.12.5, FastAPI 0.136.1, pytest 9.0.2,
psycopg2-binary 2.9.12 and Alembic 1.18.4.

Initial cluster creation (already done; do not reinitialize the retained data):

For a parent rerun, start in the integrated backend repository's main WT and
set `WT=$(git rev-parse --show-toplevel)`. Use that WT's own tmp directories,
not the worker's PG cluster. This is necessary because the tests explicitly
validate `ROOT/tmp/pgs`, port 55437 and database `c1_menu_master`. `ROOT` is
derived from the test file in the WT being tested. Create the directories only
in that WT; a fresh parent cluster needs the initdb and createdb commands below:

```sh
WT=$(git rev-parse --show-toplevel)
mkdir -p "$WT/tmp/c1/logs" "$WT/tmp/c1/cache" "$WT/tmp/pgs"
```

```sh
/opt/homebrew/opt/postgresql@16/bin/initdb -D "$WT/tmp/c1-pgdata" \
  -U c1test --auth-local=trust --auth-host=reject --no-locale -E UTF8
```

Before starting, check existing own-cluster status and port 55437 ownership.
Stop on collision; do not reuse another instance or overlap an existing run.
The approved local invocation was:

```sh
cd "$WT"
/opt/homebrew/opt/postgresql@16/bin/pg_ctl -D "$WT/tmp/c1-pgdata" status
lsof -nP -iTCP:55437 -sTCP:LISTEN
/opt/homebrew/opt/postgresql@16/bin/pg_ctl -D "$WT/tmp/c1-pgdata" \
  -l "$WT/tmp/c1/logs/postgresql.log" \
  -o "-c listen_addresses='' -k $WT/tmp/pgs -p 55437" -w start
```

Create the database only once on a fresh cluster:

```sh
/opt/homebrew/opt/postgresql@16/bin/createdb -h "$WT/tmp/pgs" \
  -p 55437 -U c1test c1_menu_master
```

Exact final pytest invocation expressed with the same resolved paths:

```sh
WT=/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c1-api
PYTHON=/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c0/backend/.venv/bin/python
cd "$WT/backend"
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PWD" \
  DB_URI="sqlite:///$WT/tmp/c1/pytest-global.sqlite3" \
  C1_POSTGRES_URI="postgresql+psycopg2://c1test@/c1_menu_master?host=$WT/tmp/pgs&port=55437" \
  AUTH_DISABLED=false "$PYTHON" -m pytest \
  tests/unit/test_menu_master_crud_baseline.py \
  tests/unit/test_monthly_menu_master_source.py \
  tests/integration/test_menu_master_revision.py -vv \
  -o cache_dir=../tmp/c1/cache --basetemp=../tmp/c1/pytest-final \
  --junitxml=../tmp/c1/logs/pytest-final.xml > ../tmp/c1/logs/pytest-final.log 2>&1
```

For another run choose a new basetemp/log/JUnit suffix to retain this evidence.
The PG fixture refuses non-dedicated database/socket/port inputs; when no
`C1_POSTGRES_URI` is supplied it skips PG and does not constitute PG evidence.
This run supplied it and had **zero skips**. Long commands were launched with
`exec_command yield_time_ms=1000` and awaited via their `session_id` until exit.

The new auth tests force AUTH_DISABLED=false and use real require_role, user
rows and hospital grants. Only Google token signature/network verification is
fake. Portal HTTP transport is redirected in-process to the actual Portal
`/auth/me?system=hospital` route with real token/user/grant decisions; no fake
allow/deny Portal response is supplied. Google cryptography, real Portal HTTP,
production credentials and deployed configuration are not proven. No external
OCR or user browser was started. Existing 17 service comparisons are not
authorization evidence.

PostgreSQL fixtures create a migrated-shape monthly table without the old
constraint, then execute the actual 0023 index and 0026 access upgrades. The
0027 preservation fixture builds a separate legacy menu_masters table and
runs the real helper in subprocesses via stdin. **This proves an already-migrated
schema shape plus 0027, NOT the full fresh-database migration chain.** In test
setup, create_all's `uq_monthly_menu_item_scope` constraint is removed from the
copied metadata before table creation. Canonical 0023 uses DROP INDEX only and
cannot drop a PostgreSQL constraint-backed index. The test setup must not be
mistaken for proof that canonical 0023 handles that prior state. No full
historical migration-chain test or production-clone migration was performed.

## Results And Evidence

All paths below are relative to the dedicated WT. Final **272 passed, 0 failed,
0 errors, 0 skipped in 10.91s**:

| Coverage | Passed |
| --- | ---: |
| Existing CRUD/monthly-source comparison | 17 |
| SQLite new contract/migration cases | 126 |
| PostgreSQL new contract/migration cases | 127 |
| Secret-URI parser/driver output checks | 2 |

Coverage includes all fields, unit codes, null/zero, normalization, duplicate
create/rename, readonly input, paging/search/tie-break/clamp, strict revisions,
stale/no-write and a real intervening-commit flush conflict. Two-session ORM
tests cover both shared CRUD and monthly master patches, with rollback of an
additional pending monthly record. Auth covers operator/admin allow and
missing/Basic/invalid/unverified/unregistered/inactive/wrong-role/no-hospital/
disabled-grant/shift-only rejection, through all four endpoints and both auth
providers on both databases. Migration tests cover first/second run, preservation
of id/all fields/normalized name/timestamp, revision 1 and preservation of later
revisions, DDL-free no-op, wrong schema, missing table, non-positive revisions,
unmigrated JSON 503/no writes/no repair, and missing 0023 index stop on PG.

- `tmp/c1/logs/pytest-final.log` and `pytest-final.xml`: full final results.
- Log SHA256: `2e9c628ff0dc0d0c61be9b45c20ddc5121c4a22e7f4e8a412d9ba710afbc2613`.
- JUnit SHA256: `daad8779f9efe0d4b79f2ab158998f183411439e23268f89b1b244e1244ac168`.
- `tmp/c1/logs/environment-final.log`: import/DB isolation, versions, structured
  JUnit counts and hashes. `tmp/c1/verify_evidence.py` is its reproducible reader.
- `tmp/c1/logs/ruff-final.log`: targeted API/schema/migration/helper/new-test
  checks, all passed. No whole-repository lint claim.
- `tmp/c1/logs/pg-initdb-approved.log`, `pg-resume.log`, `postgresql.log`:
  local cluster lifecycle. Initial sandbox initdb failure is retained separately.
- Earlier `pytest-run1.{log,xml}`: 143 passed, 125 connection setup errors from
  an unnormalized `backend/..` socket path exceeding macOS's 103-byte limit.
  No product assertion failed; corrected to the same socket's canonical path.
- Earlier `pytest-run2.{log,xml}`: 268 passed before final boundary additions.
  It is not the final test inventory.

The full backend suite was not rerun. C0's 421 failures remain a separate
baseline, not resolved by these 272 cases. The excluded cleanup's historical
1610/414 totals are not current results. Stg/prod data/index state, operational
migrations, integrated UI and live usability remain open.

After verification stop only this cluster and verify stopped status:

```sh
/opt/homebrew/opt/postgresql@16/bin/pg_ctl -D "$WT/tmp/c1-pgdata" -m fast -w stop
/opt/homebrew/opt/postgresql@16/bin/pg_ctl -D "$WT/tmp/c1-pgdata" status
```

Final process-stop evidence and source hashes are recorded in
`tmp/c1/logs/pg-final-stop.log` and `tmp/c1/logs/source-final.sha256`.
The dedicated cluster was stopped successfully; the final `pg_ctl status`
returned exit 3 and `no server running`. All pytest exec sessions exited.
Parent review, monitoring, commit and integration are still required.
