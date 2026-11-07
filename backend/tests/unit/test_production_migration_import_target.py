import os
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'scripts'))
from run_prod_schema_adoption import migration_import_target
from src.maintenance.database_target import explicit_database_uri


@pytest.mark.parametrize('failure', [False, True])
def test_import_target_uses_verified_connection_and_restores_environment(monkeypatch, failure):
    monkeypatch.setenv('DB_HOST', 'previous-host')
    monkeypatch.setenv('DATABASE_URL', 'previous-uri')
    before = dict(os.environ)
    engine = create_engine('sqlite:///:memory:')
    try:
        with engine.connect() as connection:
            try:
                with migration_import_target(connection):
                    assert explicit_database_uri() == 'sqlite:///:memory:'
                    assert explicit_database_uri(migration=True) == 'sqlite:///:memory:'
                    assert 'DB_HOST' not in os.environ
                    if failure:
                        raise RuntimeError('injected migration failure')
            except RuntimeError:
                assert failure
        assert dict(os.environ) == before
    finally:
        engine.dispose()
