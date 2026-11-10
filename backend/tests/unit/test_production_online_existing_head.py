"""Already migrated online releases verify the catalog without locking OCR jobs."""
from contextlib import nullcontext
from pathlib import Path
import sys
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'scripts'))
import run_prod_schema_adoption as subject


@pytest.mark.parametrize('versions', [['0038'], ['0039'], [], ['0038', '0039'], ['0040']])
def test_only_exact_verified_head_skips_migration(monkeypatch, versions):
    connection = Mock()
    connection.begin.return_value = nullcontext()
    connection.exec_driver_sql.return_value.scalars.return_value = versions
    monkeypatch.setattr(subject, 'formal_head_revision', lambda: '0039')
    verify = Mock()
    monkeypatch.setattr(subject, 'require_formal_head', verify)
    assert subject.verified_online_head(connection) is (versions == ['0039'])
    assert str(connection.execute.call_args.args[0]) == 'SET TRANSACTION READ ONLY'
    connection.exec_driver_sql.assert_called_once_with('SELECT version_num FROM alembic_version')
    if versions == ['0039']:
        verify.assert_called_once_with(connection)
    else:
        verify.assert_not_called()


def test_catalog_failure_is_not_treated_as_pending_migration(monkeypatch):
    connection = Mock()
    connection.begin.return_value = nullcontext()
    connection.exec_driver_sql.return_value.scalars.return_value = ['0039']
    monkeypatch.setattr(subject, 'formal_head_revision', lambda: '0039')
    monkeypatch.setattr(subject, 'require_formal_head', Mock(side_effect=RuntimeError('catalog mismatch')))
    with pytest.raises(RuntimeError, match='catalog mismatch'):
        subject.verified_online_head(connection)
