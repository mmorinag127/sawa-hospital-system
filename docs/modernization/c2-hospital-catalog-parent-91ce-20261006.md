# Parent catalog verification at 91ce0c9

Source: `91ce0c92ac350b0ba05a36942dada1a3c7889f98`, FF integrated into
hospital-main after both monitors approved the frozen staged source. Tests
below imported this main worktree, not a sibling product source. No deployment
or full-regression success is claimed.

## Runs

Python: `/Users/mmorinag/Sawa/2025.12/workspace/backend/.venv/bin/python`.
Cwd: `/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-main`.
Selected files: `backend/tests/integration/test_formal_alembic_fresh_chain.py`,
`backend/tests/unit/test_explicit_database_target.py`, and
`backend/tests/unit/test_runtime_schema_mutation_guard.py`.

Each command used `env -i`, explicit PATH, HOME/TMPDIR `/private/tmp`, UTC,
PYTHONPATH `backend`, PYTHONDONTWRITEBYTECODE=1, a distinct SQLite DB_URI,
and tracked facility/ingest template paths. Pytest used distinct owned cache,
basetemp and JUnit paths with `-q`. No live service credentials or database
targets were used.

| Run | Additional selection/target | Result | JUnit SHA256 |
| --- | --- | --- | --- |
| SQLite | `-k sqlite`, no PG opt-in | 98 passed, 16 skipped, 148 deselected; exit 0, 18.30s | `b86aa9cfb6151270e35af22cb5970e033915e3496b9c78fd484782f5204cedfe` |
| SQLite/PG16 | Explicit C2_FORMAL_PG_URI | 245 passed, 17 skipped; exit 0, 86.77s | `cf1b72a553ceb235171e1b40bf0cf4348c91464b70ba3d0d3ffda1485ffa6580` |
| PG16 supplement | Only `test_postgres_missing_facility_effective_date_indexes_blocks_without_mutation`, explicit TEST_BOOTSTRAP_DB_URI | 1 passed, zero skips; exit 0, 0.06s | `62fe298004fec71595896cbbfb2d30b4b69bced7aa9da3212011b1c99ce40282` |

Artifacts: `/private/tmp/sawa-hospital-parent-91ce-results.xml`,
`/private/tmp/sawa-hospital-parent-91ce-pg-results.xml`, and
`/private/tmp/sawa-hospital-parent-91ce-supp-results.xml` respectively.
The 16 SQLite variants explicitly skip PostgreSQL-native cases. The 17th skip
in the second run was the absent TEST_BOOTSTRAP_DB_URI; the supplement executes
that exact case. These are separate runs, not an all-green aggregate rewrite.

## Ownership And Cleanup

PG16 target was checked physically before connection: role mmorinag,
data `/private/tmp/sawa-c2-hospital-catalog-20261006/pg16/pgdata`, socket
`/private/tmp/sawa-c2-hospital-catalog-20261006/pg16/socket`, port 55470,
listen_addresses empty, version 16.14. Both explicit PG URLs used database
postgres on that socket. The former worker had stopped the cluster before
the parent took ownership; the active menu worker uses a separate target.

After each run only postgres remained among non-template databases, with zero
other client sessions. The supplement left zero `runtime_schema_guard_%`
schemas. Both parent starts were followed by successful `pg_ctl -m fast -w
stop`. No lock files, other servers, or shared state were removed.

## Open Work

Worker full regression remains 400 failures, 2572 passes and 42 skips. This
parent component run does not close that suite, prove PG15 parent execution,
verify container startup, or establish STG/prod correctness. The menu GET
side-effect root repair proceeds separately from this catalog verification.
