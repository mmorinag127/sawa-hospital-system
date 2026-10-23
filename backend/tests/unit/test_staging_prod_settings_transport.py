"""Reject settings that do not belong to the exact exported database snapshot."""
import base64
from copy import deepcopy
import hashlib
from types import SimpleNamespace

import pytest
import requests
from google.oauth2 import id_token
from src.services import staging_prod_settings_transport as transport
from src.services.staging_copy_refresh_service import RefreshError


def document():
    raw = b'{"raw":"unchanged"}'
    return {'content_base64': base64.b64encode(raw).decode(), 'sha256': hashlib.sha256(raw).hexdigest(),
            'raw_file_sha256': 'b'*64, 'path': '/app/src/data/fixed.json'}


@pytest.fixture
def exported(monkeypatch):
    value = {'schema': 1, 'revision': 'worker-prod-candidate', 'database_snapshot': '00000001-00000002-1',
             'settings': {'FACILITY_MASTER_URI': document(), 'INGEST_POLICY_URI': document(),
                          'OCR_PIPELINE_STATE_URI': 'gs://sawahospitalsystem-prod-raw/ocr/pipeline_state.json'}}
    monkeypatch.setattr(id_token, 'fetch_id_token', lambda request, audience: 'fixture-id-token')
    monkeypatch.setattr(requests, 'get', lambda *a, **k: SimpleNamespace(status_code=200, json=lambda: deepcopy(value)))
    return value


def test_verified_bytes_and_snapshot_are_returned_unchanged(exported):
    assert transport.export_runtime_settings('projects/example/revisions/worker-prod-candidate',
        '00000001-00000002-1') == exported


@pytest.mark.parametrize('field,value', [('revision','worker-prod-other'), ('database_snapshot','00000001-00000003-1')])
def test_wrong_revision_or_snapshot_is_blocked(exported, field, value):
    exported[field] = value
    with pytest.raises(RefreshError, match='mismatch'):
        transport.export_runtime_settings('worker-prod-candidate', '00000001-00000002-1')


def test_corrupted_export_bytes_are_blocked(exported):
    exported['settings']['FACILITY_MASTER_URI']['content_base64'] = base64.b64encode(b'{"changed":true}').decode()
    with pytest.raises(RefreshError, match='mismatch'):
        transport.export_runtime_settings('worker-prod-candidate', '00000001-00000002-1')


def test_untrusted_export_origin_cannot_receive_identity_token(monkeypatch):
    monkeypatch.setattr(id_token, 'fetch_id_token', lambda *a: pytest.fail('token requested for invalid origin'))
    with pytest.raises(RefreshError, match='origin_invalid'):
        transport.export_runtime_settings('worker-prod-candidate', '00000001-00000002-1', endpoint='https://example.org')
