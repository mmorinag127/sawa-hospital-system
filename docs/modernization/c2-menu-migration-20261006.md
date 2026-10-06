# C2-3 Monthly Menu Identity Migration

Source: `83c2ba234d74c81108568e2dc389b8cbba6a71d6`.

## Failure Class And Invariant

`0023` attempted `DROP INDEX uq_monthly_menu_item_scope` against the PostgreSQL backing index of the ORM's identically named six-column `UNIQUE` constraint. The owned PostgreSQL repro recorded `DependentObjectsStillExist`; the pre-fix constraint and its rows remained after the failed transaction.

The canonical invariant is exactly one unowned, valid, ready, non-partial btree identity index named `uq_monthly_menu_items_scope_identity`, with six ASC/default-null-order keys: `monthly_menu_id`, `name`, and NULL-coalesced `daypart`, `category`, `diet_type`, and `facility_override`. Recognized sources are the named six-column raw `UNIQUE` constraint, the historical three-key scope expression index, or an empty bootstrap table. Colliding names, owned target/legacy indexes, non-default key options, and NULL-coalescing or downgrade three-key collisions stop before DDL.

## Change

- `backend/src/services/monthly_menu_identity_schema.py` is the shared PostgreSQL/SQLite preflight and readonly canonical guard.
- `0023` drops a recognized PostgreSQL constraint through `ALTER TABLE ... DROP CONSTRAINT`; SQLite uses batch recreation only when the recognized raw constraint actually exists.
- Runtime `menu_service` and explicit `0027` migration preflight use the same readonly guard. The existing operator message retains `run migration 0023 before boot` and the existing 0027 six-key diagnostic remains present.
- New `backend/tests/integration/test_monthly_menu_identity_migration.py` covers recognized ORM constraint, historical index, empty bootstrap, rerun, upgrade/downgrade row parity, unknown same-name stop, NULL collision rollback, three-key downgrade collision rollback, raw SQLite route, and PostgreSQL DESC key-option spoof rejection.

## Evidence

The entries through the 28-pass run below are historical evidence retained from intermediate sources; they do not describe the frozen current source.

Owned PostgreSQL root: `/private/tmp/sawa-c2-menu-migration-20261006/pgdata`; socket `/private/tmp/sawa-c2-menu-migration-20261006/socket`; port `55463`. No cloud or existing database was used.

| Command/result | Log SHA256 |
| --- | --- |
| pre-fix ORM constraint repro: expected PostgreSQL `DependentObjectsStillExist`; constraint retained | `33a47bfe76271949a3b1a36412960e9aa6c2c10733c7ed2d8f048c29ac439831` |
| post-fix same repro: 0023 succeeds; raw constraint removed | `d924ba59fcb49bf1c09bd29e992b7a2cddbd8947b48b904846be0634d1a78689` |
| new SQLite + owned PostgreSQL 0023 suite: 15 passed, 1 PostgreSQL-only skip, 2 fresh-chain tests deselected | `dd2fd503666973d938afee9185d772384816a0010baec1c86e60ccd2e7055a70` |
| existing `test_menu_master_revision.py`: 255 passed | `a2768821696e231261b8817be859a2e95106d5e518d35004972d7da145080f3e` |
| existing `test_stg_menu_master_migration.py`: 109 passed, 1 failed | `e6878e78de6f95e7a1b0def64543674292c8e6f48c4e0ff218f79ca9b928567c` |
| SQLite + owned PostgreSQL raw-constraint/canonical-index coexistence, batch-recreate preservation guard including unknown FK stop, and all existing 0023 cases: 21 passed, 1 PostgreSQL-only skip, 2 fresh-chain cases deselected | `567eb257cbcd09a5288a20802b541dbfb5d3a8f03220575e6ee0b9e8ee873347` |
| SQLite + owned PostgreSQL raw+legacy, raw+canonical, raw+both recognized coexistence, plus SQLite postcondition-error physical rollback: 26 passed, 1 PostgreSQL-only skip, 2 fresh-chain cases deselected | `f6e57ef40aef499fa12e028d4396e8ce5db5b78a7e85130480780ad84a1d9823` |
| SQLite + owned PostgreSQL recognized coexistence and rollback, SQLite unknown autoindex-backed UNIQUE/incoming CASCADE FK stop with row/index parity: 28 passed, 1 PostgreSQL-only skip, 2 fresh-chain cases deselected | `5025a4888a66ea03d2595ae02353b4d004799d91675fb59904321370a0163f22` |

Source SHA256: `0023_monthly_menu_item_identity_indexes.py` `b0f7df272c4f37f8b100fc07223f7a0598f494b4381a96c8c7e8aa743a653e8d`; `monthly_menu_identity_schema.py` `6cdccc8e27030b084b3fa04b6a6e30ef1372f4ceee66d352d0c35a5fb99ee989`; `menu_service.py` `200ae4ecf3aa7c9d34e7597b35432fd4a34b3100badbe2fb3a427a9848a1d4a5`; `apply_menu_master_revision_migration.py` `d650b116ff5ffc9b1cb422e3dd13beb42370a622c519d4e966960c23a909c0e9`; migration regression test `96e5b0e91290f8185c901fb9a56da3ca8aa6d0f668d51c6bac0a5da4e7671969`.

Historical source SHA256 after SQLite batch preservation correction: `0023_monthly_menu_item_identity_indexes.py` `1915fa58a70e5e9e886b70e717c5433c4bb786ec8ca91d7d499b97ccb58667a5`; `monthly_menu_identity_schema.py` `9d0e2626d16c33fe46d9cd89512c97e81833636bced1e73a171490e20b0dc7a5`; migration regression test `7feafe089fdfc58275edf200a27b5939aab1b681e4bce2a26b3b1b9d38f8c7f3`.

Historical source SHA256 after autoindex/incoming-FK preservation correction: `0023_monthly_menu_item_identity_indexes.py` `8416275ebb47ba8bc7f1f0661f926549cf41c213aaa4977f080d88b29c92de13`; `monthly_menu_identity_schema.py` `b08a28c064d6eea2d282492d902d41473763e9dbcef5ea11f8b79cab13d28eed`; migration regression test `0530fee440ee86d95c62e5972f6eb99c598514424184feed856c48172ff76845`.

## Current Frozen Source Evidence

The current six-key DDL authority is `identity_index_ddl()` and `legacy_index_ddl()` in `backend/src/services/monthly_menu_identity_schema.py`; `0023` imports those functions. The workflow-order assertion is in `backend/tests/integration/test_stg_menu_master_migration.py` and requires the explicit migration command before the readonly preflight command.

| Surface | Exact command | UTC interval | Absolute log / SHA256 | Result |
| --- | --- | --- | --- | --- |
| 0023 owned PostgreSQL | `C2_MENU_PG_URI="postgresql+psycopg2://mmorinag@/postgres?host=/private/tmp/sawa-c2-menu-migration-20261006/socket&port=55463" PYTHONPATH=backend /Users/mmorinag/Sawa/2025.12/workspace/backend/.venv/bin/python -m pytest -q backend/tests/integration/test_monthly_menu_identity_migration.py -k 'not fresh_full_chain'` | `2026-10-06T03:47:40Z` to `2026-10-06T03:47:41Z` | `/private/tmp/sawa-c2-menu-migration-20261006/final-0023-pg.log` / `3a154c7ea9d1a923f4622f26eafda33cc6f694a88356d3f362607e91150adca1` | `41 passed, 1 skipped, 2 deselected` |
| Existing C1 menu regression | `C1_POSTGRES_URI="postgresql+psycopg2://mmorinag@/c1_menu_master?host=/private/tmp/c2m-owned-pgs&port=55437" PYTHONPATH=backend /Users/mmorinag/Sawa/2025.12/workspace/backend/.venv/bin/python -m pytest -q backend/tests/integration/test_menu_master_revision.py` | `2026-10-06T03:47:59Z` to `2026-10-06T03:48:11Z` | `/private/tmp/sawa-c2-final-c1/final-c1.log` / `b2dce73be7203f4ec3594ae1bfc67c1696fe5e04683d30d59d357af73e335deb` | `255 passed` |
| Stg workflow contract | `C1_STG_PG_URI="postgresql+psycopg2://mmorinag@/c1_stg_migration?host=/private/tmp/sawa-c1stg-pg.final2&port=55447" PYTHONPATH=backend /Users/mmorinag/Sawa/2025.12/workspace/backend/.venv/bin/python -m pytest -q backend/tests/integration/test_stg_menu_master_migration.py` | start UTC not recorded; log UTC mtime `2026-10-06T03:50:34.457Z`; duration `6.69s` | `/private/tmp/sawa-c2-stg-final2/final.log` / `7d74e68c326a3f941b16c851ad0bc1a6678e5901ed9896515832437674648c76` | `110 passed` |

Current source SHA256: `backend/migrations/0023_monthly_menu_item_identity_indexes.py` `ce6102770a827ba038c9db0706a5a87a34224941db15d605a5c3df429bc942f0`; `backend/src/services/monthly_menu_identity_schema.py` `4fa359659f7ff7abf99633ac6eb7d66c94a0da254538f37b44735989cc51ae44`; `backend/src/services/menu_service.py` `200ae4ecf3aa7c9d34e7597b35432fd4a34b3100badbe2fb3a427a9848a1d4a5`; `scripts/apply_menu_master_revision_migration.py` `d650b116ff5ffc9b1cb422e3dd13beb42370a622c519d4e966960c23a909c0e9`; `backend/tests/integration/test_monthly_menu_identity_migration.py` `787c7b221867ae5c0b9193e6bcd48aeeb661c0019ff04e15160b6906c2224a30`; `backend/tests/integration/test_stg_menu_master_migration.py` `c1387977afbf9f9c198bf134ed3064cf0c06649e9999dd7750828e5bc3568da3`; `.github/workflows/deploy-stg.yml` `c5b1d9dec2bfac1729c9d462f21e7d89914417e91615250dfa5686e0ec88ef7d`.

Fixture cleanup was verified after the three runs: the `55463` cluster reports `no server running`; the C1 short alias `/private/tmp/c2m-owned-pgs` and `ROOT/tmp/pgs` were removed; the stg owner socket `/private/tmp/sawa-c1stg-pg.final2` and `ROOT/tmp/stg-migration` were removed.

## Historical Failures

- The earlier stg workflow assertion run recorded `71 passed, 38 skipped, 1 failed` because it asserted the last step was the migration command although the checked-in workflow also has the required readonly preflight step. It predates the current workflow-contract assertion source and is retained in the evidence table above.
- The earlier 15-, 21-, 26-, and 28-pass 0023 runs and their source hashes are intermediate evidence, not current-source results.

## Open Failures

1. Full `0001` through `0023` chain fails before 0023 on both SQLite and owned PostgreSQL: `0007_shipping_tracking_ship_date.py` issues `ALTER TABLE shipping_tracking_logs` although the preceding chain has not created that table. This is outside the approved 0023 scope; no migration was changed.
2. `MonthlyMenuItem.__table_args__` still defines the old raw six-column `UniqueConstraint`; `Base.metadata.create_all()` can therefore regenerate the noncanonical schema after migration. ORM metadata and formal migration-entry changes remain outside this frozen 0023 scope.
