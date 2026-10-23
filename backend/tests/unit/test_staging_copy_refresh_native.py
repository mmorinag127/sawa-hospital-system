"""Real pg_dump/restore proof on a run-owned native PostgreSQL cluster only."""
from contextlib import contextmanager
import os
from pathlib import Path

import psycopg2
import pytest
from psycopg2 import sql

from src.services import staging_copy_refresh_engine as engine
from src.services.staging_copy_refresh_service import RefreshError


@pytest.fixture
def native(monkeypatch):
    uri = os.environ.get("STAGING_REFRESH_NATIVE_URI")
    if not uri:
        pytest.skip("run scripts/test_staging_copy_refresh_native.py for owned native PostgreSQL")
    parsed = psycopg2.extensions.parse_dsn(uri)
    if parsed.get("host") != "127.0.0.1" or parsed.get("user") != "refresh_native_test":
        raise AssertionError("owned_native_test_database_required")
    connection = psycopg2.connect(uri)
    connection.autocommit = True
    with connection.cursor() as cursor:
        for name in ("refresh_source", "refresh_test", "refresh_current_copy"):
            cursor.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(name)))
    connection.close()
    for database in ("refresh_source", "refresh_test", "refresh_current_copy"):
        with psycopg2.connect(**{**parsed, "dbname": database}) as conn, conn.cursor() as cursor:
            cursor.execute("CREATE TABLE alembic_version(version_num text PRIMARY KEY)")
            cursor.execute("INSERT INTO alembic_version VALUES('refresh_fixture_head')")
            cursor.execute("CREATE TABLE quantities(id bigserial PRIMARY KEY,value text NOT NULL,details jsonb)")
            cursor.execute("INSERT INTO quantities(value,details) VALUES(%s,%s),(%s,%s)", ("--", '{"uri":"gs://fixture-prod/original.pdf?generation=3"}', "", '{"raw":"Ⅲ"}'))
            cursor.execute("CREATE SCHEMA auxiliary")
            cursor.execute("CREATE TABLE auxiliary.preserved(value bytea)")
            cursor.execute("INSERT INTO auxiliary.preserved VALUES(decode('00ff10','hex'))")
            cursor.execute("CREATE VIEW quantity_view AS SELECT value FROM quantities")
    @contextmanager
    def proxy(config):
        yield int(parsed["port"])
    monkeypatch.setattr(engine, "_proxy", proxy)
    common = {"user": parsed["user"], "password": "owned-test-only"}
    return ({**common, "instance": "fixture:local:source-prod", "database": "refresh_source"},
            {**common, "instance": "fixture:local:destination-stg", "database": "refresh_test"}, parsed)


def test_real_snapshot_preserves_raw_values_extra_schema_sequence_and_test_and_previous_copy(native, tmp_path):
    source, destination, parsed = native
    before = {}
    for database in ("refresh_test", "refresh_current_copy"):
        with psycopg2.connect(**{**parsed, "dbname": database}) as conn:
            conn.set_session(readonly=True)
            before[database] = engine.fingerprints(conn, tmp_path)[0]
    tables, refs, local_receipts = engine.clone_database(source, destination, "sawa_replay_abc123", tmp_path, ["refresh_fixture_head"])
    assert "auxiliary.preserved" in tables and "public.quantities" in tables
    assert refs == {"gs://fixture-prod/original.pdf?generation=3"}
    with psycopg2.connect(**{**parsed, "dbname": "sawa_replay_abc123"}) as conn, conn.cursor() as cursor:
        cursor.execute("SELECT value FROM quantity_view ORDER BY value COLLATE \"C\"")
        assert cursor.fetchall() == [("",), ("--",)]
        cursor.execute("INSERT INTO quantities(value) VALUES('operator-edit') RETURNING id")
        assert cursor.fetchone() == (3,)
    for database in before:
        with psycopg2.connect(**{**parsed, "dbname": database}) as conn:
            conn.set_session(readonly=True)
            assert engine.fingerprints(conn, tmp_path)[0] == before[database]
    # Existing copy target and the Test database can never be overwritten.
    with pytest.raises(RefreshError, match="already_exists"):
        engine.clone_database(source, destination, "sawa_replay_abc123", tmp_path, ["refresh_fixture_head"])
    with pytest.raises(RefreshError, match="not_isolated"):
        engine.clone_database(source, destination, "refresh_test", tmp_path, ["refresh_fixture_head"])
