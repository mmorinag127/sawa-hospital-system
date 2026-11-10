"""Owned PostgreSQL proof for the no-maintenance 0039 release."""
import os
from pathlib import Path
import sys
from uuid import uuid4

from alembic import command
from alembic.config import Config
import pytest
import sqlalchemy as sa

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'scripts'))
import online_projection_guard as guard
from prepare_online_recovery_source import bridge_formal_source
from src.maintenance.formal_migration_schema import require_revision_catalog


@pytest.fixture
def database(monkeypatch):
    uri = os.environ.get('ONLINE_TEST_PG_URI')
    assert uri, 'explicit owned local PostgreSQL required; no skip'
    url = sa.engine.make_url(uri)
    assert not url.host and url.query.get('port') == '55489'
    socket = Path(url.query['host']).resolve()
    assert socket == (ROOT / 'tmp/online-release-pg/socket').resolve()
    admin = sa.create_engine(url, isolation_level='AUTOCOMMIT')
    name = 'online_' + uuid4().hex
    with admin.connect() as c:
        assert Path(c.exec_driver_sql('SHOW data_directory').scalar_one()).resolve() == socket.parent / 'pgdata'
        assert c.exec_driver_sql('SHOW listen_addresses').scalar_one() == ''
        assert int(c.exec_driver_sql('SHOW server_version_num').scalar_one()) // 10000 == 16
        c.exec_driver_sql('CREATE DATABASE ' + name)
    engine = sa.create_engine(url.set(database=name))
    for key in ('DB_URI','DATABASE_URL','DB_HOST','DB_NAME','DB_USER','DB_PASSWORD','DB_PORT','DB_DRIVER'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv('ALEMBIC_DB_URI', engine.url.render_as_string(hide_password=False))
    config = Config(str(ROOT / 'backend/alembic.ini'))
    try:
        command.upgrade(config, '0038')
        with engine.begin() as c:
            c.exec_driver_sql("""INSERT INTO ocr_jobs
                (id,status,input_reference,metrics,progress_metrics,created_at,updated_at)
                VALUES ('preserved','completed','original.pdf',
                '{"quantity":"operator text", "rows":[1,2,3]}',
                '{"stage":"completed"}',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""")
        yield engine, config
    finally:
        engine.dispose()
        with admin.connect() as c:
            c.exec_driver_sql('DROP DATABASE ' + name)
        admin.dispose()


def recovery_check(connection):
    source = ROOT / 'backend/src/maintenance/formal_migration_schema.py'
    scope = {'__file__': str(source)}
    exec(compile(bridge_formal_source(source.read_text()), str(source), 'exec'), scope)
    return scope['require_formal_head'](connection)


def test_real_upgrade_preserves_values_and_cold_recovery(database):
    engine, config = database
    with engine.connect() as c:
        before = guard.projection_snapshot(c)
        recovery_check(c)
    config.attributes['online_projection_only'] = True
    command.upgrade(config, 'head')
    assert config.attributes['online_projection_receipt'] == before
    with engine.connect() as c:
        assert guard.projection_snapshot(c) == before
        require_revision_catalog(c, '0039')
        recovery_check(c)
    with engine.begin() as c:
        c.exec_driver_sql("UPDATE ocr_jobs SET metrics = '{\"quantity\":\"kept manual value\"}'")
    with engine.connect() as c:
        assert 'kept manual value' in c.exec_driver_sql('SELECT metrics::text FROM ocr_jobs').scalar_one()


@pytest.mark.parametrize('failure', ['active', 'mismatch', 'lock'])
def test_refusal_rolls_back_schema_and_data(database, monkeypatch, failure):
    engine, config = database
    if failure == 'active':
        with engine.begin() as c:
            c.exec_driver_sql("UPDATE ocr_jobs SET status='running'")
    with engine.connect() as c:
        before = guard.projection_snapshot(c)
    if failure == 'mismatch':
        def reject(connection, snapshot):
            assert connection.exec_driver_sql('SELECT version_num FROM alembic_version').scalar_one() == '0039'
            raise RuntimeError('injected parity mismatch')
        monkeypatch.setattr(guard, 'finish_projection_upgrade', reject)
    holder = engine.connect() if failure == 'lock' else None
    try:
        if holder is not None:
            holder.exec_driver_sql('SELECT * FROM ocr_jobs')
        config.attributes['online_projection_only'] = True
        with pytest.raises((RuntimeError, sa.exc.DBAPIError)):
            command.upgrade(config, 'head')
    finally:
        if holder is not None:
            holder.rollback()
            holder.close()
    with engine.connect() as c:
        assert c.exec_driver_sql('SELECT version_num FROM alembic_version').scalar_one() == '0038'
        assert guard.projection_snapshot(c) == before
        require_revision_catalog(c, '0038')
        recovery_check(c)


def test_recovery_rejects_unknown_and_partial_catalog(database):
    engine, _ = database
    with engine.begin() as c:
        c.exec_driver_sql("UPDATE alembic_version SET version_num='unknown'")
        with pytest.raises(RuntimeError):
            recovery_check(c)
        c.exec_driver_sql("UPDATE alembic_version SET version_num='0038'")
        c.exec_driver_sql('ALTER TABLE ocr_jobs DROP COLUMN display_read_json')
        with pytest.raises(RuntimeError):
            recovery_check(c)
