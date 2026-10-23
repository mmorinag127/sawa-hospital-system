"""Release proof must use the coordinator identity and a live read-only snapshot."""
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException
from src.api import staging_source_probe as probe


def test_only_release_identity_on_worker_stg_can_probe(monkeypatch):
    monkeypatch.setenv('K_SERVICE', 'worker-stg')
    claims = {'email': probe.RELEASE_IDENTITY, 'email_verified': True}
    monkeypatch.setattr(probe.id_token, 'verify_oauth2_token', lambda *args: claims)
    probe._authorize('Bearer token')
    for invalid in ({'email': 'other', 'email_verified': True},
                    {'email': probe.RELEASE_IDENTITY, 'email_verified': False}):
        claims.update(invalid)
        with pytest.raises(HTTPException) as error:
            probe._authorize('Bearer token')
        assert error.value.status_code == 403
    monkeypatch.setenv('K_SERVICE', 'worker-prod')
    with pytest.raises(HTTPException):
        probe._authorize('Bearer token')


@pytest.fixture
def candidate(monkeypatch):
    row = {'tag': 'staging-source-api', 'percent': 0, 'revision': 'worker-prod-candidate',
           'uri': 'https://staging-source-api---worker-prod-avlnzjjrca-dt.a.run.app'}
    monkeypatch.setattr(probe, '_cloud_session', lambda: SimpleNamespace(get=lambda *a, **k:
        SimpleNamespace(status_code=200, json=lambda: {'trafficStatuses': [row]})))
    monkeypatch.setattr(probe, 'runtime_source', lambda service: ('secret-uri', {}, 'serving'))
    monkeypatch.setattr(probe, '_database', lambda uri, env: {'database': 'orders'})
    @contextmanager
    def proxy(config):
        yield 12345
    monkeypatch.setattr(probe, '_proxy', proxy)
    cursor = Mock()
    cursor.fetchone.side_effect = [('on',), ('00000001-00000002-1',)]
    connection = Mock()
    connection.cursor.return_value.__enter__ = Mock(return_value=cursor)
    connection.cursor.return_value.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(probe, '_connect', lambda config, port: connection)
    exporter = Mock(return_value={'revision': row['revision'], 'settings': {
        'FACILITY_MASTER_URI': {'sha256': 'a' * 64, 'raw_file_sha256': 'b' * 64},
        'INGEST_POLICY_URI': 'gs://prod/config.json', 'OCR_PIPELINE_STATE_URI': 'gs://prod/state.json'}})
    monkeypatch.setattr(probe, 'export_runtime_settings', exporter)
    return row, connection, cursor, exporter


def test_snapshot_export_call_is_readonly_and_only_hashes_returned(candidate):
    row, connection, cursor, exporter = candidate
    result = probe.probe_candidate()
    assert result['verified'] is True
    assert result['coordinator'].startswith('worker-exec-stg@')
    exporter.assert_called_once_with(probe.SERVICE + '/revisions/' + row['revision'],
        '00000001-00000002-1', endpoint=row['uri'])
    assert [call.args[0] for call in cursor.execute.call_args_list] == [
        'SHOW transaction_read_only', 'SELECT pg_export_snapshot()']
    connection.rollback.assert_called_once()
    connection.close.assert_called_once()
    assert 'secret-uri' not in str(result)


@pytest.mark.parametrize('change', [{'percent': 1}, {'uri': 'https://example.org'},
                                   {'revision': 'projects/other/revisions/worker'}])
def test_candidate_with_traffic_or_untrusted_origin_is_blocked(candidate, change):
    row, connection, cursor, exporter = candidate
    row.update(change)
    with pytest.raises(ValueError):
        probe.probe_candidate()
    exporter.assert_not_called()


def test_readwrite_snapshot_is_blocked(candidate):
    row, connection, cursor, exporter = candidate
    cursor.fetchone.side_effect = [('off',)]
    with pytest.raises(ValueError, match='read-only'):
        probe.probe_candidate()
    exporter.assert_not_called()
    connection.close.assert_called_once()
