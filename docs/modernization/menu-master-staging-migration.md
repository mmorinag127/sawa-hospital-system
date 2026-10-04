# Staging Menu Master Migration Gate

Date: 2026-10-05 JST. Preparation-only worker result; not a staging deployment
or integrated C1 acceptance.

- WT: `/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c1-stg-migration`
- Branch: `codex/modernization-c1-stg-migration-20261005`
- Base/integrated input: `6d6dd4180ad90038e1a2370929c048314b04f210`.
- This unit changes staging workflow, explicit migration helper, one staging
  runner, focused tests and this document only, plus owned tmp evidence.
- No API/model/service, frontend, lock/dependency, production workflow, 0023 or
  0027 migration-source changes. No old cleanup incorporation. No stage,
  commit, merge, push, live credential read, live DB connection or deploy.

## Source And Deployment Dependencies

The new `menu-master-migration` job in `deploy-stg.yml` runs only when
`backend_changed=true`, after successful `source-gate` and `automation-bootstrap`.
It uses existing staging OIDC secrets, not production credentials or a key.
The source gate still requires a clean develop checkout equal to origin/develop
and preserves the staging OAuth checks. Both shift and school-lunch automation
bootstrap commands and their script remain unchanged.

```text
source-gate -> automation-bootstrap -> menu-master-migration -> build-backend
source-gate -----------------------------------------------> build-frontend
build-backend + menu-master-migration ----------------------> deploy-backend
build-frontend + source-gate -------------------------------> deploy-frontend
  when backend_changed=true, deploy-frontend ALSO requires:
    automation-bootstrap + menu-master-migration + deploy-backend success
```

The actual worker-stg rollout is `deploy-backend`; the actual web-stg rollout
is `deploy-frontend` using the frontend image. There is no separate web-backend
job to invent. Both deploy surfaces are migration-gated when backend changes.
The existing backend live checks must finish successfully before frontend
rollout. Failure/cancellation/skipping of the relevant migration/backend jobs
blocks that rollout. For a legitimate frontend-only change, the successful
source gate and frontend build still allow rollout while backend/migration
jobs are skipped. Frontend image building may occur earlier; deployment may not.

The workflow calls:

```sh
uv run --project backend --extra dev --frozen python scripts/run_stg_menu_master_migration.py
```

The migration job has `timeout-minutes: 10`. There is no continue-on-error,
repair fallback, change of source origin or production workflow modification.
Workflow dependency checks here are local YAML/expression tests, not execution
of GitHub Actions. `actionlint` was not installed on this host.

## Runner And Target Guards

Before any Cloud Run/config/proxy/DB call the runner requires all of:

| Environment key | Required value |
| --- | --- |
| GITHUB_ACTIONS | `true` |
| GITHUB_RUN_ID | Nonzero decimal run id |
| GITHUB_REF | `refs/heads/develop` |
| GITHUB_REF_NAME | `develop` |
| PROJECT_ID | `sawahospitalsystem` |
| REGION | `asia-northeast2` |
| WEB_SERVICE | `web-stg` |
| WORKER_SERVICE | `worker-stg` |

Missing/wrong values are rejected; none are replaced by deployment defaults.
The existing `_load_service_db_config` reads both services and resolves secrets
in memory through its existing Cloud Run/Secret Manager path. The runner does
not invoke that module's production main/bootstrap operation.

The two configs must agree on instance and database. In addition, the only
allowed destination is **`sawahospitalsystem:asia-northeast2:orders-stg`, DB
`orders`**. Parent's 2026-10-05 read-only metadata inspection supplied these
verified values; this worker made no live metadata requests. Parent also
reported DB_HOST `/cloudsql/sawahospitalsystem:asia-northeast2:orders-stg`.
The runner does not use DB_HOST as an alternate route: its transport is the
proxy for the exact allowed instance. Production also has a DB named `orders`,
so database-name equality alone is deliberately insufficient. Matching configs
that both point to orders-prod or another instance are rejected after config
read and before proxy or DB connection. The target pin is a validation, not a
fallback that replaces an incorrect service configuration.

Cloud SQL proxy uses the existing bootstrap pattern's Linux amd64 version
2.24.1 and SHA256
`fae2766aac9d614a2bdef2f2a7778f3d054f3acd5ff07a81a9e300bd471512eb`.
It refuses an existing listener on 127.0.0.1:5432, verifies the downloaded
binary, checks child liveness/readiness, and terminates/waits for its own child
on success or failure. A temporary binary is cleaned up. Its output is not
relayed. No shared bootstrap extraction was made: the established shell
bootstrap paths remain unchanged; the Python runner owns its own bounded
transport lifecycle.

The SQLAlchemy URL is constructed in memory with username/password/database
from the reader and loopback proxy host/port. No password or URI is placed in
argv. Raw Cloud Run env, config objects, secrets and driver/proxy tracebacks
are never printed. Known prerequisite errors have fixed diagnostics; other
errors print `0027 failed: staging migration error; cloud/DB details suppressed`
and return exit 1. No failure permits deployment.

Immediately after `engine.begin()`, the runner executes:

```sql
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';
```

These are transaction-local settings, not schema repair. They bound lock/SQL
waits and roll back with the transaction. The real lock-failure test uses no
test-side timeout override: it observes `5s`/`1min` inside the runner's actual
ALTER attempt, waits for its lock timeout, verifies exit 1 and no revision
column/data change. The 10-minute job timeout also bounds setup/transport work.

## Prerequisites And DDL Ownership

`upgrade_staging` inspects required prior menu/users/audit/access tables and
columns before calling the canonical 0027 `upgrade`. It checks relevant string,
numeric, JSON/timestamp and menu identity constraints, including the
normalized-name unique index. Missing/incompatible prerequisites fail closed.

The monthly identity index is checked in PostgreSQL catalogs: the intended
monthly_menu_items relation, uniqueness, validity/readiness, no predicate,
six keys without included extras, btree/default key options, and each complete
PostgreSQL-deparsed key:

```text
monthly_menu_id
name
COALESCE(daypart, ''::character varying)
COALESCE(category, ''::character varying)
COALESCE(diet_type, ''::character varying)
COALESCE(facility_override, ''::character varying)
```

An index with only the right name is insufficient. Missing/wrong definition
reports the 0023/C2 prerequisite and stops; no index repair is performed.

Access-table validation is intentionally proportional to hospital grant reads:
required user_id/system_key string and enabled boolean columns, required-column
NOT NULL, and the user_id/system_key primary key. Additional compatible
columns/checks and equivalent check-expression rendering are allowed. This
gate does not re-canonicalize the whole auth schema or modify auth/runtime.
The existing automation bootstrap has its own separate validation, unchanged.

0027 remains the only revision DDL source. Revision absent plus valid prior
schema adds INTEGER NOT NULL DEFAULT 1, preserving id, all nine editable
fields, normalized name/timestamp, users, grants and audit rows. Existing valid
revision is a read-only no-op, including values already advanced past 1.
Invalid revision schema/data stops without overwrite. The runner uses a single
transaction and does not apply prior migrations or stamp a migration version.

The existing stdin-only helper path remains available for authorized isolated
tests and does not become an Actions-only command:

```sh
PYTHONDONTWRITEBYTECODE=1 "$PYTHON" scripts/apply_menu_master_revision_migration.py \
  --db-uri-stdin < "$DB_URI_FILE"
```

The restricted file supplies the URI without shell tracing/argv/log exposure.
Do not use this as a local staging bypass.

## Dedicated Local Reproduction

Tests used PostgreSQL 16.14 Homebrew with no TCP listener, data only under this
WT, socket `/private/tmp/sawa-c1stg-pg.xkE5HF`, role `c1stgtest`, port identifier
55447 and database `c1_stg_migration`. The long WT name cannot use the earlier
C1 socket path within macOS's Unix path limit. This unique short directory was
created with `mktemp -d /private/tmp/sawa-c1stg-pg.XXXXXX`; its OWNER file contains
this resolved WT path. Tests verify OWNER, actual PG data_directory and TCP
disabled before creating per-test schemas. Earlier committed C1 tests were not
modified for this socket.

```sh
WT=/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c1-stg-migration
SOCKET=/private/tmp/sawa-c1stg-pg.xkE5HF
PYTHON=/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c0/backend/.venv/bin/python
PG=/opt/homebrew/opt/postgresql@16/bin
cd "$WT"
lsof -nP -iTCP:55447 -sTCP:LISTEN
# Initial creation only; retained cluster must not be reinitialized.
"$PG/initdb" -D tmp/stg-migration/pgdata -U c1stgtest \
  --auth-local=trust --auth-host=reject --no-locale -E UTF8
"$PG/pg_ctl" -D tmp/stg-migration/pgdata -l tmp/stg-migration/logs/postgresql.log \
  -o "-c listen_addresses='' -k $SOCKET -p 55447" -w start
# Once only on a fresh cluster:
"$PG/createdb" -h "$SOCKET" -p 55447 -U c1stgtest c1_stg_migration
```

The current cluster is stopped and retained. Before restart inspect its status
and stop on port/process collision; never reuse another WT's server. For parent
reproduction in another WT, use that WT's own data directory and a newly owned
mktemp socket directory; create OWNER with apply_patch containing only the new
resolved WT path and newline. Do not modify this worker's ownership marker.
Keep the documented DB/role/port and use the new socket in C1_STG_PG_URI. Each
test's temporary schema is dropped; no test schemas remained after final run.

Dependencies were read only from the interpreter above. No dependency install
or lock change occurred. PYTHONDONTWRITEBYTECODE and own-WT PYTHONPATH/caches
were set. `evidence.log` proves src/db/config-reader imports are in this WT.

Exact final suite command (exit 0):

```sh
cd "$WT/backend"
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PWD" \
  DB_URI="sqlite:///$WT/tmp/stg-migration/global.sqlite3" \
  C1_STG_PG_URI="postgresql+psycopg2://c1stgtest@/c1_stg_migration?host=$SOCKET&port=55447" \
  "$PYTHON" -m pytest tests/integration/test_stg_menu_master_migration.py \
  tests/contract/test_automation_bootstrap.py tests/contract/test_price_automation_bootstrap.py \
  -vv -o cache_dir=../tmp/stg-migration/cache --basetemp=../tmp/stg-migration/final-bounded \
  --junitxml=../tmp/stg-migration/logs/final-bounded.xml > ../tmp/stg-migration/logs/final-bounded.log 2>&1
```

Use new output/basetemp suffixes when rerunning to preserve these artifacts.
Existing stdin helper regression command (exit 0):

```sh
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PWD" \
  DB_URI="sqlite:///$WT/tmp/stg-migration/global.sqlite3" "$PYTHON" -m pytest \
  'tests/integration/test_menu_master_revision.py::test_explicit_migration_first_second_run_preserves_data[sqlite]' \
  'tests/integration/test_menu_master_revision.py::test_explicit_migration_wrong_schema_stops_without_repair[sqlite-text]' \
  'tests/integration/test_menu_master_revision.py::test_explicit_migration_wrong_schema_stops_without_repair[sqlite-bigint]' \
  'tests/integration/test_menu_master_revision.py::test_explicit_migration_wrong_schema_stops_without_repair[sqlite-nullable]' \
  'tests/integration/test_menu_master_revision.py::test_explicit_migration_wrong_schema_stops_without_repair[sqlite-default2]' \
  'tests/integration/test_menu_master_revision.py::test_explicit_migration_wrong_schema_stops_without_repair[sqlite-no-default]' \
  'tests/integration/test_menu_master_revision.py::test_explicit_migration_missing_table_and_invalid_revision_stop[sqlite]' \
  tests/integration/test_menu_master_revision.py::test_helper_never_logs_secret_uri \
  -vv -o cache_dir=../tmp/stg-migration/stdin-cache --basetemp=../tmp/stg-migration/stdin-tests \
  --junitxml=../tmp/stg-migration/logs/stdin-helper.xml > ../tmp/stg-migration/logs/stdin-helper.log 2>&1
```

Global conftest SQLite remains under this WT. The earlier C1 PostgreSQL tests
were not redirected or changed. Additional exact commands (evidence reader
must run before stopping the dedicated PG; local guard exit 1 is expected):

```sh
cd "$WT"
env PYTHONDONTWRITEBYTECODE=1 "$PYTHON" -m ruff check \
  scripts/apply_menu_master_revision_migration.py scripts/run_stg_menu_master_migration.py \
  backend/tests/integration/test_stg_menu_master_migration.py --no-cache \
  > tmp/stg-migration/logs/ruff-final.log 2>&1
env -u GITHUB_ACTIONS PYTHONDONTWRITEBYTECODE=1 "$PYTHON" \
  scripts/run_stg_menu_master_migration.py > tmp/stg-migration/logs/local-guard.log 2>&1
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$WT/backend" \
  DB_URI="sqlite:///$WT/tmp/stg-migration/global.sqlite3" \
  C1_STG_PG_URI="postgresql+psycopg2://c1stgtest@/c1_stg_migration?host=$SOCKET&port=55447" \
  "$PYTHON" tmp/stg-migration/evidence.py > tmp/stg-migration/logs/evidence.log 2>&1
git diff --check
```

After all tests/evidence collection:

```sh
cd "$WT"
"$PG/pg_ctl" -D tmp/stg-migration/pgdata -m fast -w stop
"$PG/pg_ctl" -D tmp/stg-migration/pgdata status
```

Both were executed: stop exit 0; status exit 3 (`no server running`). No actual
Cloud SQL proxy/cloud/browser was started. All pytest exec sessions exited.

## Results And Evidence

Final focused suite: **168 passed, 0 failures/errors/skips, 6.66s**, comprising
109 new staging tests, 47 existing shift bootstrap tests and 12 existing
school-lunch bootstrap tests. Stdin helper subset: **9 passed, 2.67s**, zero
failures/errors/skips. Ruff on the two scripts and new test file passed;
`git diff --check` passed. Actual local runner invocation with GITHUB_ACTIONS
unset returned expected exit 1 before cloud/DB access (`local-guard.log`).

Cloud Run/Secret Manager/proxy transport is mocked. The config reader, SQLAlchemy
transaction, prerequisite catalog reads and canonical 0027 DDL are real. PG
tests cover old menu/operator/grant preservation, repeat read-only no-op,
missing prior schema, wrong index key/order/expression/uniqueness/predicate,
wrong access columns/type/key, compatible access extensions, invalid revision,
real lock failure/rollback, and runner error propagation. Guard/redaction tests
cover missing/wrong Actions context and wrong matching cloud targets including
orders-prod. YAML tests cover backend gating, unchanged bootstrap paths and
frontend failure/cancel/skip versus legitimate frontend-only deployment.

Evidence directory: `$WT/tmp/stg-migration/logs`.

| Artifact | SHA256 |
| --- | --- |
| final-bounded.xml | `1e8539b224b9b1e377152b762d420d4c7a13e05aede3aeac379126f28b034e68` |
| final-bounded.log | `e8bbfbb5de0322e6ba75e057a8143dbda8bc8e80312dfa6954a609009d530c7c` |
| stdin-helper.xml | `6998c3a21ff7164f4eb580dbb11fcc72a94791884fee5362bdc57f25c9522517` |
| stdin-helper.log | `b06f983750d08f9f8479e01edab85997b4a2c30cf9cec4126a4a0f13e4d613ad` |

`evidence.log` records imports, dependency versions, PG isolation, JUnit counts
and hashes; `tmp/stg-migration/evidence.py` is its reproducible read-only source.
`pg-init.log`, `pg-start.log`, `postgresql.log`, `pg-stop.log` record lifecycle.
`source-final.sha256` fixes the five changed source/doc files against this
uncommitted working diff. Evidence is not proof of an integrated/deployed SHA.

Earlier artifacts are retained: run1 was 137 pass / 1 failure because per-key
deparser output omitted DESC options; catalog key options now detect that
definition. run2 was 162 pass, final was 163 pass before destination hardening,
final-target was 168 pass before real runner timeout settings. These are not
the final acceptance evidence and are not added together.

## Retained Operational Blockers

- **API alone must not deploy before the revision-aware C1 UI is integrated.**
  Old frontend PUT lacks revision. UI choice remains pending; this preparation
  neither authorizes a workflow run nor resolves compatibility/rollout timing.
- 0023/full historical migration-chain remediation belongs to C2. Tests build
  an already-migrated prior shape, deliberately omitting create_all's old
  constraint-backed uq_monthly_menu_item_scope before applying fixture 0023.
  Canonical 0023 still uses DROP INDEX and cannot drop that constraint-owned
  index. This is **prior migrated schema +0027**, not fresh-chain completeness.
  Missing/invalid prior state stops deployment; no guessed repair is available.
- No live DB schema, database DDL privileges, Cloud SQL network reachability,
  actual proxy binary execution/OIDC/Secret Manager permissions or GitHub
  Actions run was verified by this worker. Parent target metadata is not live
  schema/migration proof. Those checks and authorized migration/deploy remain
  parent operations through the existing staging Actions path.
- C1 integrated API/UI/stg/usability acceptance and the wider C0 failure
  inventory remain open. Parent review/monitoring/staging/commit/integration
  have not been performed by this worker.
