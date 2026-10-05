# C0 Runtime Schema Verification

Base commit: `9d98a473079aa5086fb21c82c3a73ebc3e213af7`.

## Fixed invariant

Runtime imports, startup, and base-menu reads perform no DDL.  Missing or partial
base-menu and facility-template-version schemas now stop with a migration-required
error; neither is repaired or defaulted at runtime.  Portal user-system-access DDL
remains available only through the explicit bootstrap gate and migration `0026`.

## Evidence

- Pristine baseline source: `hospital-main` at `9d98a473079aa5086fb21c82c3a73ebc3e213af7`.
  Import proof: `tmp/c0-runtime-schema/before-pristine-pg-final-20261005/import-proof.log`.
  Result: 37 passed, 1 expected runtime-schema-guard failure.
- Final source: this worktree. Import proof:
  `tmp/c0-runtime-schema/after-runtime-schema-monitor-final-20261005/import-proof.log`.
  Result: 55 passed.
- Both PostgreSQL runs used a fresh PostgreSQL 16 cluster, fresh role/database,
  `listen_addresses=''`, a mode-0700 private Unix socket, and `postgresql+psycopg2`.
  Each status log records `pg_ctl_stop=0`, `pg_ctl_status_after_stop=3`, and
  `socket_present_after_stop=0`.
- The earlier `psycopg` attempt is retained at
  `tmp/c0-runtime-schema/pg-before-20261005/logs/before-tests.log`; its 17 setup
  errors were `ModuleNotFoundError: psycopg`, not product failures.

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

## Final source hashes

- `base_menu_service.py`: `fb7a132cbaf68d8b7feb3332754aca7266ee58af2bcd30235f717d61f430ef8b`
- `facility_template_version_service.py`: `13563bc00b03523335af670767abed207bdd5ca9ba6ae43a994352382cd6b9ca`
- `portal_access_bootstrap_service.py`: `0b7a45579f6474f827cacb6d1f9eed0cdae304e332f993d58588fedeeb49e47d`
- `portal_access_bootstrap_schema.py`: `556ab3c3867aced92099f2d9b15fccf8dd2785fa55b0f0e593dd8a7016caedc9`
- `test_runtime_schema_mutation_guard.py`: `25638423e73210c30723d857389f2342805464b636f8a4f329fd54ae5d18d41a`
- `run_c0_runtime_schema_isolated.py`: `92094e20ca24faa2cbe3a07bacdc96be209ee7bfaac807614ceef8722287a326`
