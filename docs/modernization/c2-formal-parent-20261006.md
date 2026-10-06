# Integrated formal migration verification

Parent independently verified source `918e39e9024dc689a182c2e0d8181ecf7b7afd18`.
Its parents are `d961e73e072234df5e74ac44a648b6d10d186f3b` and
`8eb2655473d92b60ee9065a7435d2f314eabb43c`. Both source and merge commits had
compliance and prompt-lock approval before commit. The seed default-path
finding was corrected and re-reviewed before either commit.

## Formal runner

From hospital-main, using read-only dependency Python at
`/Users/mmorinag/Sawa/2025.12/workspace/backend/.venv/bin/python`:

```sh
env TZ=UTC PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=backend C2_FORMAL_PG_URI='postgresql+psycopg2://mmorinag@/postgres?host=/private/tmp/sawa-c2-formal-pg15-20261006/socket&port=55465' /Users/mmorinag/Sawa/2025.12/workspace/backend/.venv/bin/python -m pytest backend/tests/integration/test_formal_alembic_fresh_chain.py backend/tests/integration/test_seed_facilities_explicit.py -q
```

The PG16 execution replaces the socket root with
`/private/tmp/sawa-c2-formal-alembic-20261006/socket` and port with `55464`.
Each command was logged with `set -o pipefail` and `tee`.
Each ran **187 passed, three SQLite-only materialized-view skips, exit 0**.
PG15.19/public took 46.91 seconds and ended 2026-10-06T05:37:00Z.
PG16.14/public took 48.14 seconds and ended 2026-10-06T05:38:18Z.
Start timestamps were not independently recorded.

| Artifact | SHA256 |
| --- | --- |
| `/private/tmp/sawa-c2-parent-hospital-formal-pg15.log` | `0aaa67748e528c92206ad5409c699a5a3edc5c4feed7f12abec2a570cbb64ee1` |
| `/private/tmp/sawa-c2-parent-hospital-formal-pg16.log` | `a7ce2b2e2330231b3206a3826d0013bd87ad05a4f7b5917d2821cc8a0de5fda9` |

## Adjacent regression

The existing isolated `tmp/run-parent-tests.sh` at the same source ran the CI
US1-4 target list: **432 passed, 17 PG-only skipped, exit 0**. The 17 cases were
then run separately against the owned PG15 cluster: **17 passed, exit 0**.
This does not rewrite the first result as a single 449-case execution.

The adjacent list was menu-master CRUD/OpenAPI, monthly-menu identity migration,
STG menu-master migration and runtime-schema preflight. Initial SQLite-only
execution passed 126 with 60 PG skips. Repeating the full list with
`C2_MENU_PG_URI` at owned port 55463 and `C1_STG_PG_URI` at owned port 55447
passed **185, one SQLite-specific skip, exit 0**, 9.24 seconds.

| Artifact | SHA256 |
| --- | --- |
| `hospital-main/tmp/c2-formal-918-ci/results.xml` | `5e9252319144092dcb3c367d138e3a23cec72df0d66045d4cce4a212d0fe2d73` |
| `hospital-main/tmp/c2-formal-918-regression/results.xml` | `4a9f2a20dbd57b978b719adddb70190e8a845059c6605182806b2548478e2615` |
| `/private/tmp/sawa-c2-parent-hospital-918-regression-pg.xml` | `17ba9244c19c5604d302d698265b06a9387e864280258005dd24b812f3f3a52b` |
| `/private/tmp/sawa-c2-parent-hospital-918-regression-pg.log` | `9c8b05aa87556843f3697fa056e642cffd4a37437df461592e60d20a19192b61` |
| `/private/tmp/sawa-c2-parent-hospital-918-bootstrap-pg.xml` | `1574cea4d78ab200a2351b3731830f2b3104a7f8bfd30b53fdc7458f717e8f3f` |
| `/private/tmp/sawa-c2-parent-hospital-918-bootstrap-pg.log` | `560b52fba8c9351e1a8f870f90e825e410d62f6e5ef44befae85d2d051ec3011` |

The relative artifact prefix `hospital-main` above means absolute directory
`/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-main`.
Final PG regression XML mtime was 2026-10-06T05:40:01Z; bootstrap XML was
2026-10-06T05:40:53Z. Owned test database/schema queries returned no rows after
each execution; all four owned clusters were stopped normally.

Cleanup was rechecked in a separate post-test pass to preserve file evidence;
the earlier tool-only observations are not represented as archived logs.
Each recheck used the owned socket/port/role listed above, queried the actual
`data_directory` and counted owned test databases/schemas, then stopped that
cluster again with `pg_ctl -m fast -w stop`. All counts were zero, all commands
exited 0. No product operation or test was rerun in this cleanup-only pass.

| Artifact under `/private/tmp/` | SHA256 |
| --- | --- |
| `sawa-c2-parent-918-pg15-cleanup.log` | `84398edd9041d6a23dc0910e9a9809392babe101dc7a3043fc88ad6151416740` |
| `sawa-c2-parent-918-pg16-cleanup.log` | `14863bc4113dc4dbc75564bdeb33376d4a758c4c9c4a12cc50920eef51a4fa98` |
| `sawa-c2-parent-918-identity-cleanup.log` | `dd1f3dc06afdb0dddb021ee70e5364d12e6dcfe0176fdb6cc2e56cdffc78f479` |
| `sawa-c2-parent-918-stgmigration-cleanup.log` | `cdaf56f4a2c3f144185b5b6b3c7624a9cf54f0e9f34fb8795d56be982acba78a` |

The four `sawa-c2-parent-918-{pg15,pg16,identity,stgmigration}-stop.log` files
under `/private/tmp/` each have SHA256
`ca19178a35ab4153b75b494963b66ce8243e1b94c173107c87b44d09db23652d`.
Their respective UTC mtimes are 2026-10-06T05:44:05Z, 05:44:26Z, 05:44:44Z
and 05:45:02Z. This is later cleanup evidence, not test completion time.

## Unverified boundaries

These are local exact-source tests, not STG, production, human usability,
all PostgreSQL catalog equivalence or adoption of actual historical databases.
Production minor, search path, locale and uncaptured dependencies remain open.
No deployment target/revision applies. The formal runner has not been wired
into the cloud adoption path, and broader C2/H1/H2/S1/L1/J1 work continues.
