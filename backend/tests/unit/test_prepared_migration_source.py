import json
from pathlib import Path

import pytest

from src.maintenance import prepared_migration_source as prepared


@pytest.fixture
def source(tmp_path, monkeypatch):
    (tmp_path / 'migrations').mkdir()
    (tmp_path / 'migrations' / '0001.py').write_text('revision = "0001"\n')
    (tmp_path / 'alembic.ini').write_text('[alembic]\n')
    monkeypatch.setattr(prepared, 'ROOT', tmp_path)
    monkeypatch.setattr(prepared, 'MANIFEST', tmp_path / 'prepared.json')
    monkeypatch.delenv('SAWA_PREPARED_RUNTIME', raising=False)
    return tmp_path


def test_prepared_head_is_bound_to_exact_source(source):
    prepared.write('0001')
    assert prepared.read_head() == '0001'
    (source / 'migrations' / '0001.py').write_text('revision = "changed"\n')
    with pytest.raises(RuntimeError, match='does not match'):
        prepared.read_head()


def test_production_missing_manifest_never_falls_back(source, monkeypatch):
    assert prepared.read_head() is None
    monkeypatch.setenv('SAWA_PREPARED_RUNTIME', '1')
    with pytest.raises(RuntimeError, match='missing'):
        prepared.read_head()


@pytest.mark.parametrize('head', [None, '', 1, []])
def test_invalid_prepared_head_is_rejected(source, head):
    prepared.MANIFEST.write_text(json.dumps({'head': head, 'source_sha256': prepared.fingerprint()}))
    with pytest.raises(RuntimeError):
        prepared.read_head()


def test_adding_migration_invalidates_preparation(source):
    prepared.write('0001')
    (source / 'migrations' / '0002.py').write_text('revision = "0002"\n')
    with pytest.raises(RuntimeError):
        prepared.read_head()
