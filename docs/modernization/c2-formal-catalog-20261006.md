# Formal Alembic observed catalog guard

## Source and boundary

Dedicated worktree: `/Users/mmorinag/Sawa/2025.12/worktrees/modernization-20261004/hospital-c2-menu-migration`.
Base HEAD: `cef54d65b83d694a4135f5599e4ca224e50e24f2`; results below include uncommitted source, not just HEAD.
No stage, commit, push, deployment, existing database adoption, runtime repair, or cloud access.

Observed inputs: `/private/tmp/c2-catalog56-2ff014ea5c90475887d70befb0fa630b`, 56 standard Alembic reachable catalogs (0001 through 0028, SQLite and PostgreSQL). Original JSON total: 7,981,696 bytes. Deduplicated contract: 176 shapes, 796,963 bytes. Each input SHA256 is retained in the contract's `sources` map. No latest-model inference or guessed historical manifests.

Compared attributes: inspector table/column type, nullable, default, generated attributes, primary key, unique constraints, foreign keys, checks, indexes and views; SQLite native table declarations, suffixes, index/trigger tokens; PostgreSQL native index definition, uniqueness, validity, readiness, key options, predicate and collected constraint owner/type. Metadata collection ordering is normalized; quoted SQL literal content is preserved.

This is not complete PostgreSQL catalog equivalence. All dependency relationships, collations and operator classes were not independently collected and validated. PostgreSQL contracts were collected on **16.14 / public**. `pg_get_indexdef` embeds schema names and can vary across PostgreSQL versions. Before deployment, verify the actual runtime major/version, schema and search path against these observed conditions; other versions/schemas remain unverified and can reject otherwise valid databases. Do not infer acceptance from an index name.

## SQLite native preservation review

`_without_identity` no longer excludes the entire native table. Outer declaration ordering is normalized, but column declarations (including COLLATE) and table suffixes (STRICT / WITHOUT ROWID) remain compared. Only the exact recognized raw six-column UNIQUE declaration is removed; a same-name constraint carrying COLLATE, DESC or ON CONFLICT is retained as an unknown difference. Literals containing spaces, tabs, newlines, mixed case or doubled quotes remain distinct.

Existing identity preflight checks raw constraint column names, strict canonical/legacy index expressions, duplicate identities and batch preservation of known indexes, triggers, unique/check constraints, outgoing and incoming FKs. It does **not** itself check column COLLATE or table STRICT/WITHOUT ROWID. The formal catalog guard supplies that comparison rather than claiming preflight covers it.

Real SQLite tables verify COLLATE/STRICT/WITHOUT ROWID survive identity exclusion; these three tests exercise structured comparison, not three full migration attempts. The full-chain unknown-COLLATE case additionally verifies migration stops before DDL and catalog/rows/version snapshots are unchanged.

Observed legacy, canonical and legacy+canonical variants pass on both dialects (6 cases). Raw/raw+legacy/raw+canonical/raw+both pass through PostgreSQL migration (4 cases). The corresponding fresh-chain SQLite raw combinations explicitly stop without mutation (4 cases): fresh 0015 adds `menu_master_id` without its FK, whereas existing raw-constraint batch preflight requires the two known ORM FKs. These are not claimed as accepted historical ORM catalogs. Additional historical full-database shapes, including arbitrary 0015 runtime repairs, remain unverified; no name-only adoption was added.

## Historical execution before adjacent boundary additions

Command (executed from the dedicated worktree):

```sh
env TZ=UTC PYTHONPATH=backend C2_FORMAL_PG_URI='postgresql+psycopg2://mmorinag@/postgres?host=/private/tmp/sawa-c2-formal-alembic-20261006/socket&port=55464' /Users/mmorinag/Sawa/2025.12/workspace/backend/.venv/bin/python -m pytest backend/tests/integration/test_formal_alembic_fresh_chain.py -q > /private/tmp/c2-catalog-review-final.log 2>&1
```

Exit **0**, **137 passed**, no skip, 308 warnings, 41.84 seconds. Includes all 56 observed revision resumes; column/type/index mismatch stops; FK/check/index mismatch stops; unknown SQLite attributes and literal preservation; versionless/view-only/unknown/duplicate version stops; URI missing; actual SQLite DDL/version rollback; 0027 readonly rerun; 0028 value preservation and fault rollback.

Final log: `/private/tmp/c2-catalog-review-final.log`.
SHA256: `f4210925d30d0633c0f509bea5a83b8e011d00085232f6989f87b30c335c9980`.
UTC end mtime (`TZ=UTC stat`): **2026-10-06T05:06:15Z**. Start timestamp was not separately saved; no inferred interval.

Historical intermediate executions retained, not final-source proof:

- `/private/tmp/c2-catalog-review-attempt1.log`: exit 0, 120 passed; SHA256 `d2e09d315437df9102afd3c66b589af0b83a424a6ef2a513b9f6aca635276915`.
- `/private/tmp/c2-catalog-review-attempt2.log`: exit 0, 134 passed; SHA256 `827a397a235c67095a41b8860c58405a104dc86a46356bbd0450499823a8e0ab`.
- Earlier task failure logs remain untouched. These successful runs do not erase prior failure history.

## Historical source SHA256 before adjacent boundary additions

Paths below are relative to the dedicated worktree above.

| File | SHA256 |
| --- | --- |
| backend/src/maintenance/formal_migration_schema.py | 34c5754fa765fc375a5b6bae70ae8780633a1a1adcf6cabbb21999aeef8a45fb |
| backend/alembic/env.py | 44141948306ad684ff295729efc342f832bb9222b188cb7abf072406baa23a3c |
| backend/alembic/catalog_contracts.json | 34306c7a59e8a4d2002c2f23dc708993a97e17cd885746fcc3e5618a56ff476e |
| backend/scripts/generate_alembic_catalog_contracts.py | f05bc94415c9849664c9055e4a0b51fc9117cc377e455e2a9f13099984225047 |
| backend/tests/integration/test_formal_alembic_fresh_chain.py | ec1ed5b9a225057bcbe30e78b4e8de7c0085632ad8fadcc38f99b1ad77b29412 |

## Fixture cleanup and freeze

Fixture owns socket/data/port/listen validation and CREATE DATABASE within its try/finally. `created` controls DROP; engine and admin disposal also cover intermediate errors. Batch tests create one Operations instance, without boolean expression or duplicate context.

Owned PG data: `/private/tmp/sawa-c2-formal-alembic-20261006/pgdata`, socket same root `/socket`, port 55464, listen 127.0.0.1. No other cluster used.

- Final ownership/cleanup query: `/private/tmp/c2-catalog-review-cleanup-final.log`; zero `c2_formal_test_%` databases; PG16.14/public confirmed. UTC mtime 2026-10-06T05:06:26Z; SHA256 `f0beb74051eac9967d294685ad13ecc71f6ad078390ef585fb32760085a1e164`.
- `pg_ctl -D /private/tmp/sawa-c2-formal-alembic-20261006/pgdata -m fast -w stop`: exit 0. Log `/private/tmp/c2-catalog-review-pg-stop.log`; UTC mtime 2026-10-06T05:06:30Z; SHA256 `ca19178a35ab4153b75b494963b66ce8243e1b94c173107c87b44d09db23652d`.
- Subsequent `pg_ctl status`: exit 3, no server running; `pg_isready` on owned socket/55464: exit 2, no response.

Diff frozen after this evidence update. Broader C2 acceptance, deployment runtime compatibility and unknown historical catalog adoption are not declared complete.

## Adjacent boundary verification before seed validation

PostgreSQL versionless preflight now checks standard inspector materialized view names. `read_catalog` also rejects any materialized view as an unsupported catalog object, including databases with a known revision. No current numbered migration creates a materialized view: source search for `CREATE MATERIALIZED VIEW`/`materialized_view` finds none, and all 28 owned PostgreSQL reachable states assert an empty materialized-view name list. Future materialized view introduction requires an explicit formal contract update. This does not expand the claimed coverage to all PostgreSQL dependencies.

Actual PostgreSQL materialized-view-only database, revision 0001 plus unknown materialized view, and revision 0028 plus unknown materialized view all stop before migration DDL. Inspector table/column/constraint/index attributes, table rows (including version), materialized-view definition/content and version-table presence are unchanged. SQLite skips these three PostgreSQL-only tests explicitly; no PostgreSQL proof case is skipped.

0028 downgrade now rejects any `metadata_json IS NOT NULL` row before DROP COLUMN. SQL NULL permits formal downgrade; JSON `null` is non-SQL-NULL and is rejected, as is a nonempty JSON object. Six SQLite/PostgreSQL cases verify rejection preserves catalog/rows/version and successful all-SQL-NULL downgrade preserves every other column and row, reaches 0027 and passes its observed catalog guard.

Current full-file command:

```sh
env TZ=UTC PYTHONPATH=backend C2_FORMAL_PG_URI='postgresql+psycopg2://mmorinag@/postgres?host=/private/tmp/sawa-c2-formal-alembic-20261006/socket&port=55464' /Users/mmorinag/Sawa/2025.12/workspace/backend/.venv/bin/python -m pytest backend/tests/integration/test_formal_alembic_fresh_chain.py -q > /private/tmp/c2-catalog-matview-known-final.log 2>&1
```

Exit 0: **146 passed, 3 explicitly SQLite-only skips**, 326 warnings, 43.81 seconds. UTC end mtime: **2026-10-06T05:10:22Z**; start not independently recorded. Log SHA256: `7251b1c775c7ccede5076407083a5af43306ffa0e8f958b44f8387d83c0e7023`.

Intermediate versionless-only boundary run remains at `/private/tmp/c2-catalog-boundary-final.log`: exit 0, 144 passed / 1 SQLite-only skip, SHA256 `0fb208fe54f88ae929715a98d67bf909195fac9903f79f0fd18dd998ce7793ae`. It is historical, not current source evidence.

Current changed source SHA256 (same worktree root):

| File | SHA256 |
| --- | --- |
| backend/alembic/env.py | cd4e57a1222cf518d7f358feff7409e5c0901adb771bd2ebfe2df9f97bc65644 |
| backend/migrations/0028_order_ocr_revision_metadata_json.py | 3ce6aa786926205f362a7b27046d005ab121c937efc17594820ad46faad2b506 |
| backend/src/maintenance/formal_migration_schema.py | 7b86d8177cae05d2ff011a18668299a70d26986e768429f806f70a7efacda105 |
| backend/tests/integration/test_formal_alembic_fresh_chain.py | ca332eb541f679d1781b34697f3ff369a32c3c621afb471d70a4c5ac349e036f |

Contract/generator hashes are unchanged from the historical table. No contract was guessed to accommodate materialized views.

Cleanup: `/private/tmp/c2-catalog-matview-cleanup.log` has zero owned test databases; UTC mtime 2026-10-06T05:10:44Z, SHA256 `b2baa47a46358b2a8d38a7d190642072c724c6b59a1046b4c7c24e7b6406be70`. Owned `pg_ctl -m fast -w stop` exit 0; `/private/tmp/c2-catalog-matview-stop.log` UTC mtime 2026-10-06T05:10:48Z, SHA256 `ca19178a35ab4153b75b494963b66ce8243e1b94c173107c87b44d09db23652d`.

Historical seed clarification pause: current canonical template has 17 facilities with empty `areas`; FacilityArea has composite primary key `(id, facility_id)`, not a global `id` key. No facility/area IDs or names were generated, and no real DB was seeded. Parent subsequently confirmed empty areas are permitted and duplicate area IDs are prohibited only within one facility.

## Historical seed validation and PG16 related regression

`backend/scripts/seed_facilities.py` now validates the complete JSON input before entering any database session: object root, nonempty facilities array, facility objects, required nonblank string facility IDs/names, present areas arrays, area objects with required nonblank string IDs/names, unique facility IDs, and unique area IDs within each facility. Explicit `areas: []` is allowed. Cross-facility identical area IDs retain the composite-PK meaning and are allowed. IDs and names are never generated, trimmed or replaced. Missing areas remain an error, not an inferred empty list. Config extraction and existing replacement semantics are unchanged. Import does not connect, create tables or seed.

`specs/001-order-spec/tasks.md` updates the sole old seed path reference to `backend/scripts/seed_facilities.py`, outside revision discovery. No schema/model/JSON master was changed for this seed fix.

Dedicated disposable SQLite seed fixtures verify 22 invalid/missing/empty/duplicate/late-invalid input cases stop before session entry with existing DB rows unchanged. Three further cases verify the exact current 17 facilities and empty areas, existing unrelated quantity config preservation, scoped area IDs and repeat execution with unchanged names, and import without connection/DDL/seed side effects. No real database was seeded.

Final related command from the same dedicated worktree:

```sh
env TZ=UTC PYTHONPATH=backend C2_FORMAL_PG_URI='postgresql+psycopg2://mmorinag@/postgres?host=/private/tmp/sawa-c2-formal-alembic-20261006/socket&port=55464' /Users/mmorinag/Sawa/2025.12/workspace/backend/.venv/bin/python -m pytest backend/tests/integration/test_formal_alembic_fresh_chain.py backend/tests/integration/test_seed_facilities_explicit.py -q > /private/tmp/c2-formal-seed-final.log 2>&1
```

Exit **0**, **171 passed, 3 SQLite-only materialized-view skips**, 326 warnings, 45.44 seconds. All actual PostgreSQL cases executed. UTC end mtime **2026-10-06T05:13:35Z**; start not independently recorded. Final log SHA256 `a77d871aba23404868ec2e08d7db6fbde601953b8d5d573f87b3030e2d16379f`.

Initial seed-only command used the same Python/PYTHONPATH/TZ and `-m pytest backend/tests/integration/test_seed_facilities_explicit.py -q`; exit 0, 25 passed. Preserved log `/private/tmp/c2-seed-validation-attempt1.log`, SHA256 `c15b71be04f4376528c0ffb06a7288697e528de596c4cc548b1bdce763d39bd5`.

Additional current source SHA256:

| File | SHA256 |
| --- | --- |
| backend/scripts/seed_facilities.py | eb76b0a4f70e3f44969506bc66382b707942ea58eb5fbc612fa34f06870b071d |
| backend/tests/integration/test_seed_facilities_explicit.py | bbff72b9bca14d58ba574963d7cdbb7122942504f61152f15e77a63bdac7f5ac |
| specs/001-order-spec/tasks.md | d77fd77fc311d9c0e5df531f6faaf5dbf9f1c7af80a638272dea01c4f1148fba |

All four adjacent-boundary source hashes above matched that execution. PG version/schema/uncaptured dependency limitations remain unchanged; no full-catalog equivalence claim or automatic historical adoption is made.

Final cleanup `/private/tmp/c2-formal-seed-cleanup.log`: zero owned test databases, UTC mtime 2026-10-06T05:13:47Z, SHA256 `b2baa47a46358b2a8d38a7d190642072c724c6b59a1046b4c7c24e7b6406be70`. Owned PG55464 stop exit 0; `/private/tmp/c2-formal-seed-pg-stop.log`, UTC mtime 2026-10-06T05:13:50Z, SHA256 `ca19178a35ab4153b75b494963b66ce8243e1b94c173107c87b44d09db23652d`. No stage/commit/push/deploy. Source diff frozen after evidence update.

## Historical owned PostgreSQL 15 verification before transaction locking

Parent observed CloudSQL orders-stg/prod major 15; this worker did not connect to CloudSQL or seed any real database. Official source listing: https://www.postgresql.org/ftp/source/v15.19/ . Downloaded https://ftp.postgresql.org/pub/source/v15.19/postgresql-15.19.tar.gz and the same URL plus `.sha256` into `/private/tmp/sawa-c2-formal-pg15-20261006`. `shasum -a 256 -c postgresql-15.19.tar.gz.sha256` exit 0 / OK. Tar SHA256: `4bf5474f0ee4afc55de5e71fb5cc8cd133ee56cd1bf16092093897b7c93328fb`.

Dedicated source/prefix/HOME only; no global brew/package install or Docker/GUI/other daemon operation. Commands executed from that root (configure/build/install from `postgresql-15.19`):

```sh
tar -xzf postgresql-15.19.tar.gz
env HOME=/private/tmp/sawa-c2-formal-pg15-20261006/home ./configure --prefix=/private/tmp/sawa-c2-formal-pg15-20261006/install --without-readline --without-zlib
env HOME=/private/tmp/sawa-c2-formal-pg15-20261006/home make -j4
env HOME=/private/tmp/sawa-c2-formal-pg15-20261006/home make install
env HOME=/private/tmp/sawa-c2-formal-pg15-20261006/home TZ=UTC /private/tmp/sawa-c2-formal-pg15-20261006/install/bin/initdb -D /private/tmp/sawa-c2-formal-pg15-20261006/pgdata --locale=C --encoding=UTF8 --auth=trust
env HOME=/private/tmp/sawa-c2-formal-pg15-20261006/home TZ=UTC /private/tmp/sawa-c2-formal-pg15-20261006/install/bin/pg_ctl -D /private/tmp/sawa-c2-formal-pg15-20261006/pgdata -l /private/tmp/sawa-c2-formal-pg15-20261006/server.log -o '-p 55465 -k /private/tmp/sawa-c2-formal-pg15-20261006/socket -h 127.0.0.1' -w start
```

Extract/configure/build/install exit 0. Initial sandbox initdb exit 1 (`shmget: Operation not permitted`), removed its own incomplete pgdata. Initial pg_ctl exit 1 because pgdata did not exist. Both failure logs remain, not suppressed. Repeated initdb with regular require_escalated succeeded (exit 0), followed by escalated pg_ctl start exit 0 and pg_isready accepting connections. Initial sandbox run preceded the explicit TZ=UTC initialization above; its original JST error timestamp is not represented as UTC.

Fixture now permits exactly two opt-in targets: PG16 root `/private/tmp/sawa-c2-formal-alembic-20261006`, socket `/socket`, port 55464; PG15 root `/private/tmp/sawa-c2-formal-pg15-20261006`, socket `/socket`, port 55465. Driver, administrative database, socket-root pairing, data_directory, port, loopback listen, socket setting and server major are asserted before CREATE DATABASE. Five added negative tests reject mismatched root/port, port 5432, TCP URI and different administrative database before connection. No arbitrary root/port permission was added.

Full test command from dedicated WT:

```sh
env HOME=/private/tmp/sawa-c2-formal-pg15-20261006/home TZ=UTC PYTHONPATH=backend C2_FORMAL_PG_URI='postgresql+psycopg2://mmorinag@/postgres?host=/private/tmp/sawa-c2-formal-pg15-20261006/socket&port=55465' /Users/mmorinag/Sawa/2025.12/workspace/backend/.venv/bin/python -m pytest backend/tests/integration/test_formal_alembic_fresh_chain.py backend/tests/integration/test_seed_facilities_explicit.py -q > /private/tmp/sawa-c2-formal-pg15-20261006/formal-tests.log 2>&1
```

Exit **0**, **176 passed, 3 SQLite-only materialized-view skips**, 326 warnings, 43.30 seconds. All PostgreSQL cases ran on **15.19 / public**. UTC end mtime **2026-10-06T05:17:45Z**; start not independently recorded. Test log SHA256 `eecdcd3ce3122bd9716965b3e2cf8cddca788031ca05aece6ca086025551ab92`.

The existing PG16 contract is unchanged (SHA256 `34306c7a59e8a4d2002c2f23dc708993a97e17cd885746fcc3e5618a56ff476e`). All 28 PostgreSQL revision states compare through `require_revision_catalog` before resuming to head. **Zero mismatches detected within the enumerated comparison attributes** on local PG15.19/public; no literal normalization, expectation weakening, contract replacement or inferred adoption was used. This is not all-PG-catalog equivalence. Production PG minor, schema/search path/locale, uncaptured dependencies and actual-data adoption remain unverified deployment prerequisites.

Only tracked code change in this follow-up is `backend/tests/integration/test_formal_alembic_fresh_chain.py`: current SHA256 `5509f88e6e39e0bff859a0c0cfa6589cfd4d53131b25ad67656671d6e4e221d2`. Helper/env/0028/seed/generator/contract/spec hashes recorded above are unchanged. No migrations or production guard semantics were altered to pass PG15.

Evidence below is under absolute root `/private/tmp/sawa-c2-formal-pg15-20261006`:

| File | SHA256 |
| --- | --- |
| configure.log | e67e8976713295716695c9aeac21ab438273e986e4f065a18a6a758dd9f49dba |
| build.log | a4008f616c43958926c5eed55f2c78d4699d3d99521f7500ea5c0778d27ba0d3 |
| install.log | 1cedc9d296709ed743746186a09d1ad13fa021094ec133a2f6459dc120c1cd29 |
| install/bin/postgres | 2fd63f25ea10aa66f504fca5d92bad71b6d1615663e6b30e3c5a169d63481abf |
| initdb.log (sandbox failure) | 2d7da303e45ef9572e82444a4ce24dc0f9a1224238a312b14781cc625a5a9fe5 |
| start.log (missing pgdata failure) | cb887d009793a484df8fc78a3489d7f2c8809cde09d06c38da5c6c9727142195 |
| initdb-escalated.log | e4b4a41524c8c39ae7f7f1b77639ac0a5c5c29902c6d50f0d9f357f731fb0de9 |
| ownership-tests.log (5 pass) | 978731705dfafa7d66e212f1f25bd4bbceca36e245c1584d2c1115a8e94e0c80 |
| ownership.log | fbf9d0c2276c95bacd9fb62f9ff5933d024e40d02139d7d1a9f8f12bf07539b3 |
| cleanup.log | b2baa47a46358b2a8d38a7d190642072c724c6b59a1046b4c7c24e7b6406be70 |
| stop.log | ca19178a35ab4153b75b494963b66ce8243e1b94c173107c87b44d09db23652d |

`ownership.log` confirms actual 15.19/public/root/55465/loopback/socket. `cleanup.log` shows zero `c2_formal_test_%` databases; UTC mtime 2026-10-06T05:18:14Z. Owned pg_ctl fast/wait stop exit 0; `stop.log` UTC mtime 2026-10-06T05:18:18Z. Subsequent pg_ctl status exit 3 (no server running), pg_isready exit 2 (no response). PG16 cluster and other existing clusters were not started or operated in this follow-up. Source diff frozen, no stage/commit/push/deploy; broader C2 is not declared complete.

## Historical exclusion-boundary verification before mandatory seed input

Failure class: preflight committed before the DDL transaction, allowing another formal migration to change the checked state. `backend/alembic/env.py` now holds one connection transaction from lock acquisition through preflight, all revision DDL and version updates. Success commits once; any exception rolls back; the connection context closes and engine disposal is in finally. The post-preflight commit is removed.

PostgreSQL takes nonblocking `pg_try_advisory_xact_lock(1396791105, 1296648018)` (stable SAWA/MIGR namespace shared across every formal revision in the same database). False returns an explicit migration-lock-held error without retry/queue. This is cooperative exclusion for formal migration runners, not a claim to prevent arbitrary outside SQL bypassing the lock. SQLite takes physical `BEGIN IMMEDIATE` before inspection, with connection timeout 0, preserving the lock until successful commit or failure rollback. Lock contention retains its `database is locked` exception.

The only versioned catalog validation entry is `require_revision_catalog`. Redundant env 0027/0028 canonical validation and dynamic 0028 import are removed; the shared guard already compares those catalog attributes and canonical identity. Versionless/view/materialized-view and unknown/duplicate revision boundaries remain. No repair, inferred stamp/adoption or runtime DDL was added.

Three additional cases per dialect cover a second connection holding the lock (migration stops without catalog/rows/version mutation), successful upgrade and injected actual post-DDL failure. The tests probe a competing connection during preflight and after each DDL, assert the same transaction object remains active, verify rollback snapshots after the injected failure, then prove lock release, ordinary UPDATE and a subsequent full upgrade. Existing fresh/unknown-schema/NULL JSON/downgrade/materialized-view/DDL rollback/seed assertions are retained.

Full commands, executed from the same dedicated WT at HEAD `cef54d65b83d694a4135f5599e4ca224e50e24f2` plus current dirty source:

```sh
env TZ=UTC PYTHONPATH=backend C2_FORMAL_PG_URI='postgresql+psycopg2://mmorinag@/postgres?host=/private/tmp/sawa-c2-formal-pg15-20261006/socket&port=55465' /Users/mmorinag/Sawa/2025.12/workspace/backend/.venv/bin/python -m pytest backend/tests/integration/test_formal_alembic_fresh_chain.py backend/tests/integration/test_seed_facilities_explicit.py -q > /private/tmp/c2-lock-pg15-final.log 2>&1
env TZ=UTC PYTHONPATH=backend C2_FORMAL_PG_URI='postgresql+psycopg2://mmorinag@/postgres?host=/private/tmp/sawa-c2-formal-alembic-20261006/socket&port=55464' /Users/mmorinag/Sawa/2025.12/workspace/backend/.venv/bin/python -m pytest backend/tests/integration/test_formal_alembic_fresh_chain.py backend/tests/integration/test_seed_facilities_explicit.py -q > /private/tmp/c2-lock-pg16-final.log 2>&1
```

Both exit **0**: each **182 passed, 3 SQLite-only materialized-view skips**, 344 warnings. All actual PostgreSQL tests execute, not opt-in skips. PG15.19/public: 48.83 seconds, UTC end mtime **2026-10-06T05:24:12Z**; PG16.14/public: 49.02 seconds, UTC end mtime **2026-10-06T05:25:16Z**. Start timestamps not independently recorded. Initial SQLite lock-only run at `/private/tmp/c2-lock-sqlite-attempt1.log` exited 0 (3 pass / 3 explicit PostgreSQL-opt-in skips / 154 deselected), then every PG case ran in the two full executions above.

Current changed source SHA256:

| File | SHA256 |
| --- | --- |
| backend/alembic/env.py | 538742bdb41c5e3cdf79accc217e9e9c944f6b5597108a68492417d1df6ed672 |
| backend/tests/integration/test_formal_alembic_fresh_chain.py | cf226cd721409b186fb35b6a7873d91e952a9109ded205dd46a1b0da21c276f7 |

All other source/contract/seed hashes above are unchanged; earlier env/test hashes describe historical executions only. Readonly comparison coverage, uncaptured PG dependencies and production minor/schema/actual data adoption limitations remain open.

Evidence SHA256 (absolute paths):

| File | SHA256 |
| --- | --- |
| /private/tmp/c2-lock-pg15-final.log | a0383a3cd9ad8d4075eb257a6f203a1893be07ed0ad8468ca60d35f5e9f85f55 |
| /private/tmp/c2-lock-pg16-final.log | 3387292a2cb6a4690414443f3d2b3b1c71574cc88b01d01815cdb83364feccb7 |
| /private/tmp/c2-lock-sqlite-attempt1.log | 59c305ab1ed6dff80a51d5f743b250d7dd5f3defe2b25d1adcc46c9f459b0e9c |
| /private/tmp/c2-lock-pg15-cleanup.log | 4bfbd914b782dc3f41580d6c403292047f94705b3e770cf890a34c0789a4fd32 |
| /private/tmp/c2-lock-pg16-cleanup.log | 9c6934239895a918453f45b9bfcfccc268a87fbf731885cbbbe17cf457737068 |
| /private/tmp/c2-lock-pg15-stop.log | ca19178a35ab4153b75b494963b66ce8243e1b94c173107c87b44d09db23652d |
| /private/tmp/c2-lock-pg16-stop.log | ca19178a35ab4153b75b494963b66ce8243e1b94c173107c87b44d09db23652d |

Cleanup queries confirm actual versions/public schema and zero owned test databases on both roots. Owned pg_ctl fast/wait stop exit 0 for both; PG15 stop-log UTC mtime **2026-10-06T05:24:33Z**, PG16 **2026-10-06T05:25:45Z**. Both owned clusters stopped. No other DB, stage, commit, push or deploy. Final diff frozen after evidence verification; broader C2 completion is not claimed.

## Current mandatory seed input verification

Review found the explicit seed command still defaulted an absent FACILITY_MASTER_PATH to the template. That default is removed. `main()` requires the environment input to be present and nonblank before reading JSON or entering a DB session. It passes the supplied path unchanged to the existing fully validating seed function; nonexistent explicit files raise instead of selecting another source. `__main__` calls this entry. Direct `seed_from_master(path)` remains explicit-path-only, with no default argument or inferred source. Import still performs no seed/DDL/connection.

Four new cases cover unset, empty, space-only and tab/newline-only environment input: explicit ValueError and existing DB snapshot unchanged, with zero session entries. A fifth case proves explicit JSON success/idempotence and nonexistent explicit path rejection without DB mutation. All prior 25 seed assertions, including current 17-facility empty areas and duplicate/missing input stops, remain. Seed-only execution: `env TZ=UTC PYTHONPATH=backend /Users/mmorinag/Sawa/2025.12/workspace/backend/.venv/bin/python -m pytest backend/tests/integration/test_seed_facilities_explicit.py -q`; exit 0, 30 passed; log `/private/tmp/c2-seed-required-input.log`, SHA256 `38ad026a445520b4a0b74c2e9c8b3946766c8d19a25177af8e9527e385f88f5f`.

Full current-source commands from the dedicated WT:

```sh
env TZ=UTC PYTHONPATH=backend C2_FORMAL_PG_URI='postgresql+psycopg2://mmorinag@/postgres?host=/private/tmp/sawa-c2-formal-pg15-20261006/socket&port=55465' /Users/mmorinag/Sawa/2025.12/workspace/backend/.venv/bin/python -m pytest backend/tests/integration/test_formal_alembic_fresh_chain.py backend/tests/integration/test_seed_facilities_explicit.py -q > /private/tmp/c2-input-pg15-final.log 2>&1
env TZ=UTC PYTHONPATH=backend C2_FORMAL_PG_URI='postgresql+psycopg2://mmorinag@/postgres?host=/private/tmp/sawa-c2-formal-alembic-20261006/socket&port=55464' /Users/mmorinag/Sawa/2025.12/workspace/backend/.venv/bin/python -m pytest backend/tests/integration/test_formal_alembic_fresh_chain.py backend/tests/integration/test_seed_facilities_explicit.py -q > /private/tmp/c2-input-pg16-final.log 2>&1
```

Both exit **0**, each **187 passed, 3 SQLite-only materialized-view skips**, 344 warnings. PG15.19/public duration 46.89 seconds, UTC end mtime **2026-10-06T05:32:22Z**. PG16.14/public duration 47.93 seconds, UTC end mtime **2026-10-06T05:33:33Z**. Start timestamps not separately recorded. No actual PG cases were skipped.

Current changed source SHA256:

| File | SHA256 |
| --- | --- |
| backend/scripts/seed_facilities.py | 76199e04d6afae5d896ce1092270cb05bf20c62c1208f1ecb302d7e6ba702e00 |
| backend/tests/integration/test_seed_facilities_explicit.py | 9e77ff3c0f40e7aa830e0669dd6dd6332e52c103496337d6862a6fbabc6f5c55 |

Env/formal-test SHA256 remain `538742bdb41c5e3cdf79accc217e9e9c944f6b5597108a68492417d1df6ed672` / `cf226cd721409b186fb35b6a7873d91e952a9109ded205dd46a1b0da21c276f7`. Other source/contracts are untouched in this follow-up. Previous seed hashes/results above are historical.

| Evidence absolute path | SHA256 |
| --- | --- |
| /private/tmp/c2-input-pg15-final.log | 027c92974afdad33f9d527ef1e06066be36175bf97bccc38774891ca83aba0f6 |
| /private/tmp/c2-input-pg16-final.log | 47227eb995abdebeaa6b34021731d19b08f7173c7cd732ed9257f3b05bd13683 |
| /private/tmp/c2-input-pg15-cleanup.log | 4bfbd914b782dc3f41580d6c403292047f94705b3e770cf890a34c0789a4fd32 |
| /private/tmp/c2-input-pg16-cleanup.log | 9c6934239895a918453f45b9bfcfccc268a87fbf731885cbbbe17cf457737068 |
| /private/tmp/c2-input-pg15-stop.log | ca19178a35ab4153b75b494963b66ce8243e1b94c173107c87b44d09db23652d |
| /private/tmp/c2-input-pg16-stop.log | ca19178a35ab4153b75b494963b66ce8243e1b94c173107c87b44d09db23652d |

Both cleanup logs confirm actual version/public schema and zero owned test databases. Owned cluster stops exit 0; PG15 stop UTC mtime **2026-10-06T05:32:51Z**, PG16 **2026-10-06T05:34:01Z**. Existing staged content was not restaged or modified in the index. Only seed/test/doc working-tree changes were made; no stage/commit/push/deploy. Diff frozen. Production minor/schema/data adoption and broader C2 acceptance remain open, not substituted by these tests.
