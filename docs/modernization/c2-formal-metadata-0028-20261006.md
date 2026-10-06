# Formal Alembic 0028 verification

Base: cef54d65b83d694a4135f5599e4ca224e50e24f2. Uncommitted worker source.

0028 adds order_ocr_revisions.metadata_json as nullable JSON without a default.
Recognized existing columns are a value-preserving no-op; incompatible type,
nullability, default, identity or computed definitions stop before DDL.
0015 is unchanged by this addition. No adoption or stamp is performed.

Owned SQLite and PostgreSQL (55464) tests verify fresh head 0028, all ORM
tables and columns present, canonical menu identity, shipping schema and 0014
FKs. The 0027-to-0028 transition preserves existing rows and JSON, leaves new
metadata NULL, preserves non-NULL metadata on explicit migration rerun, rejects
an existing TEXT metadata column without changing rows/catalog, and rolls back
the new column and version after an injected failure immediately after ADD COLUMN.
The old explicit 0027 rerun snapshot test remains.

Command prefix:
`C2_FORMAL_PG_URI='postgresql+psycopg2://mmorinag@/postgres?host=/private/tmp/sawa-c2-formal-alembic-20261006/socket&port=55464' PYTHONPATH=backend /Users/mmorinag/Sawa/2025.12/workspace/backend/.venv/bin/python -m pytest`

Targeted arguments: the explicit node IDs in
backend/tests/integration/test_formal_alembic_fresh_chain.py:
test_owned_postgres_fresh_all_28_revisions,
test_fresh_sqlite_upgrade_uses_all_numbered_revisions,
test_sqlite_0027_to_0028_preserves_rows_json,
test_sqlite_0027_rerun_preserves_snapshot, followed by `-q`.

Targeted result: 4 passed, exit 0.
Log: /private/tmp/sawa-c2-formal-alembic-20261006/metadata28-nodes.log
SHA256: f0f633869e065b420e93396b38f4a3a9844a2f6e236a1caebc5b39fa211cc07f
UTC mtime (TZ=UTC): 2026-10-06T04:52:01Z. Start time not recorded.

Full file arguments: backend/tests/integration/test_formal_alembic_fresh_chain.py -q --tb=short.
Full result: 11 passed, 2 failed, exit 1.
Log: /private/tmp/sawa-c2-formal-alembic-20261006/metadata28-final.log
SHA256: ef4a03bb420ea4d0676236b84c87fc0f7e37fdeb9852a3c6a2160c3dd8f737f8
UTC mtime: 2026-10-06T04:51:18Z. Start time not recorded.
Known unresolved failures remain lower-partial revision catalog validation and
view-only versionless catalog acceptance. These are not passed/skipped.

Source SHA256:
- 0028_order_ocr_revision_metadata_json.py: 7fe25630bbd2cd7dbd62a1fda2b0dfd7970cf4019348599fa568a368ba421d3e
- backend/alembic/env.py: bb164719b32d44cf97bbc0505350b5fd59ccbeab7df5020283d0133faf0a058f
- test_formal_alembic_fresh_chain.py: fff1ad7e67f3fa388d3c91540a207bc508f3ab923464e9d0fc548e5f310a65a0

Temporary test databases are dropped in finally. Owned cluster stopped after
the final targeted run. Earlier cleanup log records zero temporary databases,
pg_ctl no server running and pg_isready no response:
/private/tmp/sawa-c2-formal-alembic-20261006/metadata28-cleanup.log.
No stage, commit, push, cloud or production operations.
