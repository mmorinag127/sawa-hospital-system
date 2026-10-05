# C0 Runtime Schema Verification

Base commit: `9d98a473079aa5086fb21c82c3a73ebc3e213af7`.

## Fixed invariant

Runtime imports, startup, and base-menu reads perform no DDL.  Missing or partial
base-menu and facility-template-version schemas now stop with a migration-required
error; neither is repaired or defaulted at runtime.  Portal user-system-access DDL
remains available only through the explicit bootstrap gate and migration `0026`.

## Current Integrated Status

Current `hospital-main` and `develop` source is
`08f1c2ad04e0ab5d5a9d16f1bd542dbe954b63cd`. Parent integrated evidence is
`tmp/c0-runtime-schema/parent-final-integrated-20261005/results.xml`, SHA256
`83bf890f5323e53a975b63b87bf035ca6447aa051a73eb01e9f4f15039ebbefa`:
81 passed with zero failures, errors, and skips. Parent bootstrap/menu regression
evidence is `tmp/c0-runtime-schema/parent-final-bootstrap-menu-regression-20261005/results.xml`,
SHA256 `4b73e8c7e5166be96292f7caf5b3b6b88a903851122dc19afe7dbe3ad41428c0`:
356 passed with zero failures, errors, and skips.

The initially introduced `650ec9a` collection error is retained below as history;
the compatibility correction is included in `08f`. The earlier related 24-pass
result remains attributed to `650ec9a`, not relabeled as `08f`.

Manual STG Actions run `37310451571` for exact `08f` completed successfully.
CI run `37310394150` also completed successfully; automatic run `37310394157`
was cancelled as the owned duplicate. Runtime schema preflight completed
successfully. Parent PostgreSQL is stopped and its private sockets are absent.
This record does not claim production deployment, full 391-failure clearance,
C1 human approval, or modernization completion.

The owned download `tmp/stg-08f1c2a/live-menu-master-stg-37310451571-1` has
`result.json`, `browser-result.json`, and a manifest whose 13 artifact hashes
match. It records all nine fields through POST, PUT, fresh reload, null/zero,
and Japanese labels; a real stale-editor 409 retaining the draft; and an injected
network abort that is not a real 503. Owned `MNUf951bc48` was deleted and verified
absent by API; owned processes stopped. `owned-edit-360.png`, `owned-edit-1280.png`,
and `owned-row-1280.png` were personally viewed, but are cropped rather than a
full-page review and are not human approval. The serving revisions were
`web-stg-00356-jn8` / `frontend@sha256:a2384cdc98d4052e9158dcd2d4d41f08837c745998b6771bbcdb6d3f41de9d68`
and `worker-stg-00776-rs6` / `backend@sha256:b52f9985e0ee341cc1722195c0f6fb3b1bceb75dbc7ba6f336e3afd6a95abfcb`,
each built from exact `08f` and serving 100% traffic.

The parent current-168 run at `08f` and pristine `9d` baseline both have 168
nodes: 7 passed, 161 failed, zero errors, and zero skips. Baseline raw XML is
`tmp/c0-runtime-schema/parent-other168-before-9d98a47-20261005/results.xml`
(SHA256 `f7473fc014d56b548439e52879a26d1c45d82495d5b9fef0726d4e2b9d6f62fa`);
current raw XML is
`tmp/c0-runtime-schema/parent-other168-current-08f1c2a-20261005/results.xml`
(SHA256 `50c360327316694aa217b20f1fe32aa5036f803c16f1a0accb449d6f21f5229b`).
The exact testcase status map difference is `[]`.

Local test-only fixture changes recorded below postdate deployed `08f`; they are
not deployed or part of the STG result.

## Evidence

- Pristine baseline source: `hospital-main` at `9d98a473079aa5086fb21c82c3a73ebc3e213af7`.
  Import proof: `tmp/c0-runtime-schema/before-pristine-pg-final-20261005/import-proof.log`.
  Result: 37 passed, 1 expected runtime-schema-guard failure.
- Historical candidate source: this worktree. Import proof:
  `tmp/c0-runtime-schema/after-runtime-schema-monitor-final-20261005/import-proof.log`.
  Result: 55 passed.
- Both PostgreSQL runs used a fresh PostgreSQL 16 cluster, fresh role/database,
  `listen_addresses=''`, a mode-0700 private Unix socket, and `postgresql+psycopg2`.
  Each status log records `pg_ctl_stop=0`, `pg_ctl_status_after_stop=3`, and
  `socket_present_after_stop=0`.
- The earlier `psycopg` attempt is retained at
  `tmp/c0-runtime-schema/pg-before-20261005/logs/before-tests.log`; its 17 setup
  errors were `ModuleNotFoundError: psycopg`, not product failures.
- Candidate evidence is this worktree's final 55-test run. Integrated evidence is
  `hospital-main@650ec9a0f501036151b21cb044a997fdd194f542` at
  `tmp/c0-runtime-schema/parent-integrated-650ec9a-20261005/results.xml`, SHA256
  `0cbe7857feab801bcc6155b31bd9f100cf1dc248586484a31da73726c7d00505`.
  Its JUnit suite records 55 tests with zero failures, errors, and skips. The paired
  import proof is `tmp/c0-runtime-schema/parent-integrated-650ec9a-20261005/import-proof.log`,
  SHA256 `f8812d6b651128e7106e6fbc8c2efdc9c67593e163be79345b07077d32a34413`;
  it records `hospital-main` as source root and the expected `main.py` SHA256.
- Integrated `hospital-main@650ec9a0f501036151b21cb044a997fdd194f542`
  runtime source hashes are: `main.py`
  `407b80709d3a0d1a7945edd8ec69bd2d758c83e090602b58b696930ca07bc62e`,
  `base_menu_service.py`
  `fb7a132cbaf68d8b7feb3332754aca7266ee58af2bcd30235f717d61f430ef8b`,
  `facility_template_version_service.py`
  `13563bc00b03523335af670767abed207bdd5ca9ba6ae43a994352382cd6b9ca`, and
  `portal_access_bootstrap_schema.py`
  `556ab3c3867aced92099f2d9b15fccf8dd2785fa55b0f0e593dd8a7016caedc9`.

## Commands

The final guarded runner command was:

```sh
PYTHONDONTWRITEBYTECODE=1 /Users/mmorinag/Sawa/2025.12/workspace/backend/.venv/bin/python -B scripts/run_c0_runtime_schema_isolated.py after-runtime-schema-monitor-final-20261005 --postgres-uri 'postgresql+psycopg2://c0runtimetest@/c0_runtime_schema_monitor_final?host=/private/tmp/sawa-c0-runtime-pg.9z8iIn&port=55488' --postgres-socket-dir /private/tmp/sawa-c0-runtime-pg.9z8iIn
```

It clears inherited environment values, creates unique SQLite/HOME/TMP/cache/artifact
paths, rejects socket/subprocess audit events in pytest, and verifies the actual
imported `main.py` source root and SHA256. The final import proof records Python
`/opt/homebrew/Cellar/python@3.11/3.11.15_4/Frameworks/Python.framework/Versions/3.11/bin/python3.11`,
source root `hospital-c0-runtime-schema`, and `main.py` SHA256
`407b80709d3a0d1a7945edd8ec69bd2d758c83e090602b58b696930ca07bc62e`.

The final raw results are
`tmp/c0-runtime-schema/after-runtime-schema-monitor-final-20261005/results.xml`
(SHA256 `7403217f7ee257c84be40be7e8e4de847133f5ff12978a6228bce7bc5edc520f`) and
`tmp/c0-runtime-schema/pg-after-monitor-final-20261005/logs/tests.log`
(SHA256 `ebf26eab45b3f6a2d0c783604680fd4fe5b1052cb4dbc673795577deaa7cf01f`).
The runtime read measurement records inspection/read statements and zero
`CREATE`/`ALTER`/`DROP` statements; it is not a performance claim.

Startup proof uses the actual decorated `main._initialize_menu_schema()` function:
missing and partial owned SQLite facility schemas fail without DDL or schema mutation,
while the migrated owned schema succeeds without DDL. The unrelated menu validation,
facility-name sync, and recovery-loop side effects are isolated only in those startup
tests; the facility schema validator is not mocked. The owned PostgreSQL test also
proves missing effective-date indexes block without a runtime mutation.

## Explicit Related-Test Targets

`--pytest-target` accepts one or more existing `.py` paths, optionally followed by a
pytest node id. Each resolved path must be inside `<source-root>/backend/tests`; paths
outside that directory, directories, missing files, and non-Python files fail before
pytest is invoked. The exact integrated-main command for related regression is:

```sh
PYTHONDONTWRITEBYTECODE=1 /Users/mmorinag/Sawa/2025.12/workspace/backend/.venv/bin/python -B scripts/run_c0_runtime_schema_isolated.py related-menu-api-auth-650ec9a --source-root /Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-main --pytest-target backend/tests/integration/test_menu_scope_normalization.py --pytest-target backend/tests/contract/test_user2_permissions_api.py --pytest-target backend/tests/contract/test_auth_guardrails.py
```

The runner writes output only below this worker's `tmp/c0-runtime-schema/` and records
the resolved imported source root and `main.py` hash in `import-proof.log`.

The parent related menu/API/auth regression on
`hospital-main@650ec9a0f501036151b21cb044a997fdd194f542` is
`tmp/c0-runtime-schema/parent-related-menu-api-auth-650ec9a-20261005/results.xml`,
SHA256 `5566635b54936833c84059816d8029f75e97ad30a490e9296d42e0bab22b0037`.
Its JUnit suite records 24 tests with zero failures, errors, and skips.

## Read-Only Staging Schema Preflight

Do not run this procedure from a local deploy path. After the integrated commit is on
`develop`, the `menu-master-migration` GitHub Actions job runs
`scripts/run_stg_runtime_schema_preflight.py` after 0027 and before `build-backend`.
It reuses `scripts/run_stg_menu_master_migration.py` for `require_staging_context`,
the temporary owned loopback Cloud SQL proxy, and the staging target constants. It
resolves `web-stg` and `worker-stg` Cloud Run database configuration, requires the
same instance/database/user, verifies the connected PostgreSQL database/role with
`verify_connection_target`, and disposes the engine before the owned proxy exits.
Output contains only fixed blocker text; connection details and credentials are
suppressed.

Against that proxy, open a PostgreSQL transaction with `SET TRANSACTION READ ONLY` and
execute only catalog reads. Verify `base_menu_cycle_items` has `id`, `cycle_day`,
`daypart`, `category`, `name`, `diet_type`, and `slot_index`; verify
`facility_template_versions` has every ORM column (`id`, `facility_id`, `version`,
`status`, `template_id`, `source`, `config_json`, `columns_json`, `cells_json`,
`template_digest`, `validation_json`, `valid_from`, `valid_to`, `created_by`,
`created_at`, `activated_at`, `archived_at`); and verify
`ix_facility_template_versions_valid_from` and
`ix_facility_template_versions_valid_to` in `pg_indexes`. Record only query results,
service revision/image identity, source commit, proxy cleanup status, and output hashes.
No DDL, DML, migration, service update, deployment, or fallback is permitted.

Release impact: absence of any listed table, column, or index is a deployment blocker.
The integrated runtime now fails closed at startup/read rather than repairing schema.

The preflight opens a transaction, executes `SET TRANSACTION READ ONLY`, applies a
5-second lock timeout and 15-second statement timeout, then issues only
`information_schema.columns` and `pg_indexes` catalog reads. It always rolls that
transaction back. It contains no DDL, DML, migration, or service-write path.

## STG Preflight Candidate Evidence

`scripts/run_stg_runtime_schema_preflight.py` checks exactly seven
`base_menu_cycle_items` ORM columns, exactly seventeen
`facility_template_versions` ORM columns, and exactly two 0025 effective-date
indexes: `ix_facility_template_versions_valid_from` and
`ix_facility_template_versions_valid_to`.

The initial six-test preflight run below is retained as historical evidence only:

```sh
PYTHONDONTWRITEBYTECODE=1 /Users/mmorinag/Sawa/2025.12/workspace/backend/.venv/bin/python -B scripts/run_c0_runtime_schema_isolated.py stg-runtime-preflight-20261005 --source-root /Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c0-runtime-schema --pytest-target backend/tests/integration/test_stg_runtime_schema_preflight.py
```

It records six passing tests at
`tmp/c0-runtime-schema/stg-runtime-preflight-20261005/results.xml`.

The current preflight evidence is
`tmp/c0-runtime-schema/stg-runtime-preflight-final-20261005/results.xml`, SHA256
`b5410db49fe588ea26a4dde4aa0c0c0d4a21cdf53ce524220131ab1697bbde30`:
seven tests passed with zero failures, errors, and skips. It covers missing table,
missing column, missing index, successful catalog-only read path, staging-context
rejection, verified database-target rejection before proxy startup, and
proxy/engine/transaction cleanup.

The supplementary existing C0 command without a PostgreSQL URI was:

```sh
PYTHONDONTWRITEBYTECODE=1 /Users/mmorinag/Sawa/2025.12/workspace/backend/.venv/bin/python -B scripts/run_c0_runtime_schema_isolated.py stg-runtime-preflight-c0-regression-20261005 --source-root /Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c0-runtime-schema
```

It records 37 passes and one expected PostgreSQL-contract skip at
`tmp/c0-runtime-schema/stg-runtime-preflight-c0-regression-20261005/results.xml`;
it is not PostgreSQL proof. The separate parent integrated 55-test evidence above
remains the complete owned PostgreSQL C0 proof.

## Compatibility Regression And Frozen Candidate Evidence

The parent broad regression collection failure is retained at
`tmp/c0-runtime-schema/parent-bootstrap-menu-regression-650ec9a-20261005/results.xml`,
SHA256 `a27d8f922f32287a3e01adfb156a687c6d055fbfaa6000957bb86d4c2876acd7`.
It records the `ImportError` caused by the missing
`_assert_canonical_user_system_access_schema` compatibility import.

The compatibility wrapper delegates to the maintenance schema assertion using the
unchanged `PortalAccessBootstrapError` identity and has no DDL SQL in the runtime
portal service. The compatibility-focused owned PostgreSQL run records portal 8,
price contract 12, and PostgreSQL contract 17: 37 passed at
`tmp/c0-runtime-schema/bootstrap-compat-pg-escalated-final-20261005/results.xml`.
All `src` package imports then succeeded for 150 modules at
`tmp/c0-runtime-schema/bootstrap-compat-pg-20261005/logs/all-source-imports.log`.

The frozen latest candidate evidence is
`tmp/c0-runtime-schema/candidate-freeze-combined-final-20261005/results.xml`, SHA256
`b8851677ea46c304478f741c34f739cf067e9dd648e5afaa32728c8eb2fb1243`:
68 passed, zero failures, errors, and skips. Its source proof is
`tmp/c0-runtime-schema/candidate-freeze-combined-final-20261005/import-proof.log`,
SHA256 `6a7c34345ce6c91a704927ff4345ceb1b0c093e7acd1257341b411723d32706c`.
It combines runtime mutation guards, portal tests, price contracts, all seven
preflight tests, all five runner tests, and the 17 real PostgreSQL contracts.
The owned PostgreSQL cleanup status is retained at
`tmp/c0-runtime-schema/bootstrap-compat-pg-20261005/logs/status.log`: final
`combined_pg_ctl_status_after_stop=3` and `combined_socket_present_after_stop=0`.

## Final source hashes

- `base_menu_service.py`: `fb7a132cbaf68d8b7feb3332754aca7266ee58af2bcd30235f717d61f430ef8b`
- `facility_template_version_service.py`: `13563bc00b03523335af670767abed207bdd5ca9ba6ae43a994352382cd6b9ca`
- `portal_access_bootstrap_service.py`: `db2f4aac684095b02bffde3ee3c929624eb75deea7b4c00d3bc7e0dc20f2a1a4`
- `portal_access_bootstrap_schema.py`: `556ab3c3867aced92099f2d9b15fccf8dd2785fa55b0f0e593dd8a7016caedc9`
- `test_runtime_schema_mutation_guard.py`: `25638423e73210c30723d857389f2342805464b636f8a4f329fd54ae5d18d41a`
- `run_c0_runtime_schema_isolated.py`: `3ad4becab35be136943fe5f3de522e15fbee43e893ca1f7db4f8c4de7b4bb2b9`
- `run_stg_runtime_schema_preflight.py`: `bb70cc21e8fc52e8b085d9d6636539cd59950cd9d8985fcd9a507b3c85fddccf`
- `test_stg_runtime_schema_preflight.py`: `6760d3fa9f53057cdd5f15308d4f1ab283642daf4e896d1b8b8cd26ee0b505bc`
- `test_runtime_schema_runner.py`: `006b832a6d132ae8754f6d1f22ac8a8d488fe0bfded0fd8d403aa78e96380477`
- `test_portal_access_bootstrap_service.py`: `5f65cdb9262b7ee96926174715fa3d091578ced4a077149bd77b6f7211a60ca9`
- `deploy-stg.yml`: `2e274b871614d98f9f9eb172a04956796b43fe5481a37c745b4338af2702d0b2`
- `test_orders_archive_api.py` (local test-only): `d20a99a75d38f0b8929bc3f64a113ad925fc85427996bfdecce0ed2a3fd6e518`
- `test_order_workflow_v2_service.py` (local test-only): `141ebba8e798f86f10e785ce81984322c8bc3dccfc811c568e86a4ab7969646b`

## Test-Only Fixture Evidence

The archive fixture now supplies canonical `OcrJob.order_id` and retains an
unlinked same-prefix job, proving one purge rather than ID-prefix deletion. The
auto-edit fixture supplies row/column targets because real selector behavior now
applies: a non-empty cell with OCR presence is intentionally excluded. Its payload
case therefore uses blank-plus-presence to create a valid suspect and exercise OCR
value exclusion; the real-selector neighbor retains both `1` and `110` populated
first-cell cases and proves neither is selected. It records primary groups `2/2/1`
by sorted `target_chunk_index`, two sequential persistent one-cell retries for group
1, and two patches. Final focused evidence is
`tmp/c0-runtime-schema/c0-test-only-fixtures-focused-final-20261005/results.xml`
(SHA256 `d2063e5336b32beab262af62c372efe0d30ca2595a3eca7eedd132e5c424654d`):
7 passed. Its import proof SHA256 is
`6a7c34345ce6c91a704927ff4345ceb1b0c093e7acd1257341b411723d32706c`.
Final whole-file evidence is
`tmp/c0-runtime-schema/c0-test-only-fixtures-whole-files-final-20261005/results.xml`
(SHA256 `2095e72f5e6bcdb02bf167cab1911d9662cb4d8bc24a2f6e1eb21e310ab80124`):
76 passed, 1 failed. The retained failure is
`test_facility_template_columns_save_on_confirmed_order_clears_snapshot_reference`
with `legacy_facility_template_column_override_disabled`; it is outside these
fixtures. Earlier focused attempts are retained: first and second runs were each
5 passed / 1 failed because the newly-real selector correctly emitted an empty
target set for the old populated payload fixture.
