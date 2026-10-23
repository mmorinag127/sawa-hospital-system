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
    def false_success(config, port, target, expected, source_heads, target_heads):
        with engine._connect(config, port, database=target, readonly=False) as changed, changed.cursor() as cursor:
            cursor.execute("UPDATE quantities SET value='corrupted'")
            cursor.execute("UPDATE alembic_version SET version_num='new_fixture_head'")
        return {'existing_values_preserved': True,
                'preserved_tables': {key: value for key, value in expected.items() if key != 'public.alembic_version'}}
    with pytest.raises(ValueError, match='existing_values_not_preserved'):
        engine.clone_database(source, destination, 'sawa_replay_bad456', tmp_path, ['new_fixture_head'],
            source_heads=['refresh_fixture_head'], upgrade=false_success)


def test_untracked_production_schema_is_adopted_only_in_fresh_copy(monkeypatch, tmp_path):
    import json
    import sqlalchemy as sa
    from src.services.staging_copy_migration import upgrade_hospital
    from src.maintenance import formal_migration_schema as formal
    uri = os.environ.get('STAGING_REFRESH_NATIVE_URI')
    if not uri:
        pytest.skip('owned native Actions cluster required')
    parsed = psycopg2.extensions.parse_dsn(uri)
    assert parsed['host'] == '127.0.0.1' and parsed['user'] == 'refresh_native_test'
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'integration'))
    from live_schema_adoption_support import restore_captured
    contract = json.loads(formal.CONTRACT.read_text())
    shapes = {table: contract['shapes'][key] for table, key in
              contract['adoption_baselines']['live-20261014-prod']['tables'].items()}
    admin = psycopg2.connect(uri); admin.autocommit = True
    with admin.cursor() as cursor:
        for name in ('untracked_source', 'retained_test'):
            cursor.execute(sql.SQL('CREATE DATABASE {} TEMPLATE template0').format(sql.Identifier(name)))
    admin.close()
    source_url = sa.URL.create('postgresql+psycopg2', username=parsed['user'], password='owned-test-only',
        host='127.0.0.1', port=int(parsed['port']), database='untracked_source')
    source_engine = sa.create_engine(source_url)
    with source_engine.begin() as connection:
        restore_captured(connection, shapes)
        connection.exec_driver_sql("INSERT INTO facilities(id,name) VALUES ('preserved','実データ保持')")
        connection.exec_driver_sql("INSERT INTO audit_logs(id,actor,action,target,created_at) VALUES ('kept','operator','saved','order',NULL)")
    source_engine.dispose()
    @contextmanager
    def proxy(config):
        yield int(parsed['port'])
    monkeypatch.setattr(engine, '_proxy', proxy)
    common = {'user': parsed['user'], 'password': 'owned-test-only'}
    source = {**common, 'instance': 'fixture:local:source-prod', 'database': 'untracked_source'}
    destination = {**common, 'instance': 'fixture:local:target-stg', 'database': 'retained_test'}
    before_dir = tmp_path / 'before'; before_dir.mkdir()
    with psycopg2.connect(**{**parsed, 'dbname': 'untracked_source'}) as original:
        source_before = engine.fingerprints(original, before_dir)[0]
    receipt = {}
    after, _, _ = engine.clone_database(source, destination, 'sawa_replay_ad0f7', tmp_path,
        ['0036'], source_heads=[], upgrade=upgrade_hospital, receipt=receipt)
    assert receipt['existing_values_preserved'] is True
    assert receipt['source_tables'] == source_before
    with psycopg2.connect(**{**parsed, 'dbname': 'untracked_source'}) as original:
        assert engine.fingerprints(original, before_dir)[0] == source_before
        assert engine._heads(original) == []
    with psycopg2.connect(**{**parsed, 'dbname': 'sawa_replay_ad0f7'}) as copied, copied.cursor() as cursor:
        assert engine._heads(copied) == ['0036']
        cursor.execute('SELECT name FROM facilities WHERE id=%s', ('preserved',))
        assert cursor.fetchone() == ('実データ保持',)
