"""Offline registry byte identity and rejected-target checks."""
import hashlib
import importlib
import io
from pathlib import Path
import sys
from unittest.mock import Mock
import urllib.error
import urllib.request

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT))
r = importlib.import_module('registry_image_digest')
transport = importlib.import_module('staging_db_target')
RAW = b'{"schemaVersion":2, "config":{}}\n'
DIGEST = 'sha256:' + hashlib.sha256(RAW).hexdigest()


@pytest.mark.parametrize('component', ['frontend', 'backend', 'ocr-pipeline'])
@pytest.mark.parametrize('reference', [':stg-build_1.2-abc', '@' + DIGEST])
def test_resolve_exact_manifest_bytes(monkeypatch, component, reference):
    read = Mock(return_value=(200, RAW))
    monkeypatch.setattr(r, 'read_http', read)
    monkeypatch.setattr(r, '_access_token', lambda: 'test-token')
    assert r.resolve_image_digest(r.PREFIX + component + reference) == r.PREFIX + component + '@' + DIGEST
    read.assert_called_once_with(
        f'https://{r.REGISTRY}/v2/sawahospitalsystem/backend/{component}/manifests/{reference[1:]}',
        'test-token', accept=r.ACCEPT, blob_redirect=False)


@pytest.mark.parametrize('reference', [
    '', 'frontend:tag', r.PREFIX + 'frontend', r.PREFIX + 'worker:tag',
    r.PREFIX.replace('sawahospitalsystem', 'other') + 'frontend:tag',
    r.PREFIX.replace('/backend/', '/other/') + 'frontend:tag',
    r.PREFIX + 'frontend:-tag', r.PREFIX + 'frontend:.tag',
    r.PREFIX + 'frontend:' + 'x' * 129, r.PREFIX + 'frontend:tag/other',
    r.PREFIX + 'frontend:tag?redirect=x', r.PREFIX + 'frontend:tag#fragment',
    r.PREFIX + 'frontend:tag\n', r.PREFIX + 'frontend:tag@' + DIGEST,
    r.PREFIX + 'frontend@sha256:' + 'A' * 64,
    r.PREFIX + 'frontend@sha256:abc',
    'https://' + r.PREFIX + 'frontend:tag',
])
def test_invalid_reference_stops_before_credentials_or_http(monkeypatch, reference):
    token, read = Mock(), Mock()
    monkeypatch.setattr(r, '_access_token', token)
    monkeypatch.setattr(r, 'read_http', read)
    with pytest.raises(r.Blocked, match='reference_rejected'):
        r.resolve_image_digest(reference)
    token.assert_not_called()
    read.assert_not_called()


@pytest.mark.parametrize('status', [201, 301, 302, 303, 307, 308, 401, 403, 404, 500])
def test_only_http_200_is_accepted(monkeypatch, status):
    monkeypatch.setattr(r, '_access_token', lambda: 'test-token')
    monkeypatch.setattr(r, 'read_http', Mock(return_value=(status, RAW)))
    with pytest.raises(r.Blocked, match='http_failure'):
        r.resolve_image_digest(r.PREFIX + 'frontend:stg-tag')


def test_immutable_mismatch_and_empty_body_stop(monkeypatch):
    monkeypatch.setattr(r, '_access_token', lambda: 'test-token')
    monkeypatch.setattr(r, 'read_http', Mock(return_value=(200, RAW + b' ')))
    with pytest.raises(r.Blocked, match='digest_mismatch'):
        r.resolve_image_digest(r.PREFIX + 'frontend@' + DIGEST)
    monkeypatch.setattr(r, 'read_http', Mock(return_value=(200, b'')))
    with pytest.raises(r.Blocked, match='bytes_required'):
        r.resolve_image_digest(r.PREFIX + 'frontend:stg-tag')


def test_actual_transport_rejects_manifest_redirect_without_following(monkeypatch):
    monkeypatch.setattr(r, '_access_token', lambda: 'secret-token')
    opener = Mock()
    opener.open.side_effect = urllib.error.HTTPError('https://registry.invalid', 302, 'redirect',
        {'Location': 'https://storage.googleapis.com/target'}, io.BytesIO(b''))
    build = Mock(return_value=opener)
    monkeypatch.setattr(transport.urllib.request, 'build_opener', build)
    with pytest.raises(r.Blocked, match='http_failure'):
        r.resolve_image_digest(r.PREFIX + 'frontend:stg-tag')
    opener.open.assert_called_once()
    handler = build.call_args.args[0]
    assert isinstance(handler, transport.NoRedirect)
    assert handler.redirect_request(urllib.request.Request('https://registry.invalid'), None,
                                    302, 'redirect', {}, 'https://other.invalid') is None


def test_cli_outputs_only_complete_digest_on_success(monkeypatch, capsys):
    monkeypatch.setattr(sys, 'argv', ['resolve', '--image', r.PREFIX + 'frontend:stg-tag'])
    monkeypatch.setattr(r, '_access_token', lambda: 'secret-token')
    monkeypatch.setattr(r, 'read_http', Mock(return_value=(200, RAW)))
    assert r.main() == 0
    output = capsys.readouterr()
    assert output.out == r.PREFIX + 'frontend@' + DIGEST + '\n'
    assert output.err == ''


def test_cli_failure_never_exposes_credentials_or_emits_a_digest(monkeypatch, capsys):
    monkeypatch.setattr(sys, 'argv', ['resolve', '--image', r.PREFIX + 'frontend:stg-tag'])
    monkeypatch.setattr(r, '_access_token', Mock(side_effect=RuntimeError('secret-token')))
    assert r.main() == 1
    output = capsys.readouterr()
    assert output.out == ''
    assert output.err == 'registry_digest_resolution_failed\n'
