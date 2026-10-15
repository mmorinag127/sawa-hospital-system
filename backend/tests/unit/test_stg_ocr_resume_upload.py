import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import urllib.error

import pytest

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('resume_upload', ROOT / 'scripts/verify_stg_ocr_upload.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


@pytest.fixture
def inputs():
    # Matches the observed API shape, including explicitly uninitialized workflow.
    raw = b'unit-test-pdf'
    case = {'existing_uploaded_pdf_id': 'UPL2f6c937d', 'expected_order_id': 'ORD581f55d6',
            'expected_document_id': 'DOC0d4cb340', 'facility_id': 'FAC00015',
            'week_id': '2026-10@2026-10-25~2026-10-31', 'effective_date': '2026-10-25',
            'sha256': hashlib.sha256(raw).hexdigest(), 'identity_verified': True, 'source_order_id': 'ORD3d234696'}
    selection = {'source': 'canonical_json', 'facility_id': 'FAC00015', 'effective_date': '2026-10-25',
                 'revision': 'unit-revision', 'version': {'version_id': 'unit-version', 'valid_from': None,
                 'valid_to': None, 'facility': {'facility_id': 'FAC00015'}}}
    uri = mod.BUCKET + 'manual-uploads/unit.pdf?generation=1'
    state = {'id': case['existing_uploaded_pdf_id'], 'content_sha256': case['sha256'],
             'facility_hint': case['facility_id'], 'week_hint': case['week_id'], 'skip_ocr': False,
             'status': 'processing', 'current_order_id': case['expected_order_id'],
             'current_document_id': case['expected_document_id'], 'message_id': 'upload:unit', 'storage_uri': uri}
    version = {'document_id': case['expected_document_id'], 'order_id': case['expected_order_id'],
               'facility_code': case['facility_id'], 'week_code': case['week_id'], 'is_current': True,
               'message_id': state['message_id'], 'storage_uri': uri}
    version['selected_version_input'] = copy.deepcopy(version)
    order = {'id': case['expected_order_id'], 'facility': case['facility_id'], 'week_value': case['week_id'],
             'document_id': case['expected_document_id'], 'template_version_id': mod.canonical_projection_id(case, selection),
             'message_id': state['message_id'], 'document': uri, 'current_version': version}
    workflow = {'order_id': order['id'], 'revision': 1, 'state': 'uploaded',
                'blockers': ['workflow_not_initialized'], 'facility_id': None, 'week_start': None,
                'week_end': None, 'template_version_id': None, 'ocr_job': None}
    return case, selection, state, order, workflow, raw


def install_api(monkeypatch, inputs, *, deny=False):
    case, selection, state, order, workflow, raw = inputs
    posts = []
    def request(path, *args, **kwargs):
        if path.endswith('/retry'):
            posts.append(path)
            assert kwargs == {'method': 'POST'}
            if deny:
                raise urllib.error.HTTPError(path, 409, 'restart blocked', {}, None)
            return {'accepted': True, 'item': {'id': state['id']}}
        if path.endswith('/resolved?effective_date=2026-10-25'):
            return copy.deepcopy(selection)
        if path.endswith('/workflow-v2'):
            return copy.deepcopy(workflow)
        if path.startswith('/orders/'):
            return copy.deepcopy(order)
        assert path == '/ingest/uploads/' + case['existing_uploaded_pdf_id']
        return copy.deepcopy(state)
    monkeypatch.setattr(mod, 'request', request)
    monkeypatch.setattr(mod, 'download', lambda uri, path: path.write_bytes(raw))
    return posts


def test_explicit_retry_and_completed_skip(monkeypatch, inputs, tmp_path):
    posts = install_api(monkeypatch, inputs)
    case, selection, state, *_ = inputs
    assert mod.resume_upload(case, selection, tmp_path)['uploaded_pdf_id'] == state['id']
    assert posts == ['/ingest/uploads/UPL2f6c937d/retry']
    posts.clear()
    state['status'] = 'completed'
    mod.resume_upload(case, selection, tmp_path)
    assert not posts


@pytest.mark.parametrize('owner,key', [
    ('case', 'existing_uploaded_pdf_id'), ('case', 'expected_order_id'), ('case', 'expected_document_id'),
    ('case', 'identity_verified'), ('state', 'id'), ('state', 'content_sha256'),
    ('state', 'current_order_id'), ('state', 'current_document_id'), ('state', 'facility_hint'),
    ('state', 'week_hint'), ('state', 'skip_ocr'), ('state', 'message_id'), ('state', 'storage_uri'),
    ('order', 'id'), ('order', 'facility'), ('order', 'week_value'), ('order', 'document_id'),
    ('order', 'template_version_id'), ('order', 'message_id'), ('order', 'document'),
    ('version', 'document_id'), ('version', 'order_id'), ('version', 'facility_code'),
    ('version', 'week_code'), ('version', 'is_current'), ('version', 'storage_uri'), ('version', 'message_id'),
    ('workflow', 'order_id'), ('workflow', 'revision')])
def test_every_identity_mismatch_stops_before_retry(monkeypatch, inputs, tmp_path, owner, key):
    posts = install_api(monkeypatch, inputs)
    case, selection, state, order, workflow, _ = inputs
    owners = {'case': case, 'state': state, 'order': order, 'version': order['current_version'], 'workflow': workflow}
    owners[owner][key] = None
    with pytest.raises((ValueError, KeyError)):
        mod.resume_upload(case, selection, tmp_path)
    assert not posts


@pytest.mark.parametrize('key', ['existing_uploaded_pdf_id', 'expected_order_id', 'expected_document_id'])
def test_partial_manifest_rejected(monkeypatch, inputs, tmp_path, key):
    posts = install_api(monkeypatch, inputs)
    case, selection, *_ = inputs
    del case[key]
    with pytest.raises(ValueError):
        mod.resume_upload(case, selection, tmp_path)
    assert not posts


@pytest.mark.parametrize('change', ['no-marker', 'wrong-state', 'mixed', 'missing', 'initialized-null', 'marker-initialized'])
def test_workflow_null_marker_contract(inputs, change):
    case, selection, state, order, workflow, _ = inputs
    if change == 'no-marker':
        workflow['blockers'] = []
    elif change == 'wrong-state':
        workflow['state'] = 'context_ready'
    elif change == 'mixed':
        workflow['facility_id'] = case['facility_id']
    elif change == 'missing':
        del workflow['week_end']
    else:
        workflow.update(facility_id=case['facility_id'], week_start='2026-10-25', week_end='2026-10-31',
                        template_version_id=order['template_version_id'], state='context_ready')
        if change == 'initialized-null':
            workflow['blockers'] = []
            workflow['week_start'] = None
    with pytest.raises(ValueError):
        mod.verify_resume_identity(case, selection, state, order, workflow)


def test_initialized_context_matches_all_fields(inputs):
    case, selection, state, order, workflow, _ = inputs
    workflow.update(blockers=[], state='context_ready', facility_id=case['facility_id'],
                    week_start='2026-10-25', week_end='2026-10-31', template_version_id=order['template_version_id'])
    mod.verify_resume_identity(case, selection, state, order, workflow)
    for key in ('facility_id', 'week_start', 'week_end', 'template_version_id'):
        changed = copy.deepcopy(workflow)
        changed[key] = 'wrong'
        with pytest.raises(ValueError):
            mod.verify_resume_identity(case, selection, state, order, changed)


@pytest.mark.parametrize('key', ['document_id', 'order_id', 'facility_code', 'week_code', 'is_current', 'message_id', 'storage_uri'])
def test_selected_input_identity_mismatch_stops_retry(monkeypatch, inputs, tmp_path, key):
    posts = install_api(monkeypatch, inputs)
    inputs[3]['current_version']['selected_version_input'][key] = None
    with pytest.raises(ValueError):
        mod.resume_upload(inputs[0], inputs[1], tmp_path)
    assert not posts


def test_restart_guard_propagates_without_fallback(monkeypatch, inputs, tmp_path):
    posts = install_api(monkeypatch, inputs, deny=True)
    with pytest.raises(urllib.error.HTTPError):
        mod.resume_upload(inputs[0], inputs[1], tmp_path)
    assert posts == ['/ingest/uploads/UPL2f6c937d/retry']


def test_current_pdf_bytes_must_match(monkeypatch, inputs, tmp_path):
    posts = install_api(monkeypatch, inputs)
    monkeypatch.setattr(mod, 'download', lambda uri, path: path.write_bytes(b'wrong'))
    with pytest.raises(ValueError, match='hash mismatch'):
        mod.resume_upload(inputs[0], inputs[1], tmp_path)
    assert not posts


def test_new_upload_still_uses_original_endpoint(monkeypatch, inputs):
    calls = []
    monkeypatch.setattr(mod, 'request', lambda *args, **kwargs: calls.append(args) or {'uploaded_pdf_id': 'new'})
    assert mod.upload_new(inputs[0], inputs[1], inputs[-1]) == {'uploaded_pdf_id': 'new'}
    assert len(calls) == 1 and calls[0][0] == '/ingest/upload'
    assert b'name="skip_ocr"' not in calls[0][1]


@pytest.mark.parametrize('field', ['source', 'facility_id', 'effective_date', 'revision', 'version'])
def test_changed_canonical_selection_stops_retry(monkeypatch, inputs, tmp_path, field):
    posts = install_api(monkeypatch, inputs)
    case, selection, *_ = inputs
    original = mod.request
    def changed(path, *args, **kwargs):
        result = original(path, *args, **kwargs)
        if '/resolved?' in path:
            result[field] = {'version_id': 'changed'} if field == 'version' else 'changed'
        return result
    monkeypatch.setattr(mod, 'request', changed)
    with pytest.raises((ValueError, KeyError)):
        mod.resume_upload(case, selection, tmp_path)
    assert not posts


def test_completed_resume_continues_existing_hakodate_path(monkeypatch, inputs, tmp_path):
    case, selection, state, order, workflow, raw = inputs
    case.update(pdf_uri=mod.BUCKET + 'verified.pdf', expected_menu_rows=1)
    state['status'] = 'completed'
    calls = []
    started = False
    job = {'ocr_job_id': 'job', 'workflow_attempt_id': 'attempt', 'order_id': order['id'],
           'order_document_id': order['document_id'], 'status': 'done'}
    def request(path, *args, **kwargs):
        nonlocal started
        calls.append(path)
        if '/resolved?' in path:
            return copy.deepcopy(selection)
        if path == '/ingest/uploads/' + state['id']:
            return copy.deepcopy(state)
        if path == '/orders/' + order['id']:
            return copy.deepcopy(order)
        if path.endswith('/workflow-v2/ocr-runs'):
            assert json.loads(args[0])['mode'] == 'hakodate'
            assert kwargs['revision'] == 1
            started = True
            return {'workflow': {**workflow, 'ocr_job': job}}
        if path.endswith('/workflow-v2'):
            return {**workflow, 'ocr_job': job if started else None}
        if path.endswith('/evidence'):
            if not started:
                raise urllib.error.HTTPError(path, 404, 'none', {}, None)
            return {'id': 'new-evidence', 'status': 'done', 'producer_version': 'hakodate_best_method_pipeline',
                    'payload_json': {'_ocr_execution': {'job_id': 'job', 'workflow_attempt_id': 'attempt',
                    'evidence_run_id': 'new-evidence', 'selected_pdf_receipt': {'order_id': order['id'], 'document_id': order['document_id']}},
                    'hakodate_overlay': {'uri': mod.BUCKET + 'overlay', 'sheet_review_base_uri': mod.BUCKET + 'corrected'},
                    'hakodate_canonical_pipeline': {'metrics': {'row_axis_source': 'fax_intersections',
                        'row_axis_draft_body_row_count': 1, 'row_axis': []}}}}
        pytest.fail('Unexpected API operation: ' + path)
    manifest = mod.BUCKET + 'manifest.json'
    def download(uri, path):
        if uri == manifest:
            path.write_text(json.dumps({'cases': [case]}))
        else:
            path.write_bytes(raw)
    monkeypatch.setattr(mod, 'request', request)
    monkeypatch.setattr(mod, 'download', download)
    monkeypatch.setattr('sys.argv', ['verify', '--manifest', manifest, '--output-dir', str(tmp_path)])
    monkeypatch.setattr(mod.time, 'sleep', lambda _: pytest.fail('Unexpected polling delay'))
    mod.main()
    assert started
    assert not any(path == '/ingest/upload' or path.endswith('/retry') for path in calls)


@pytest.mark.skipif(not os.environ.get('STG_OCR_RECOVERY_ARTIFACT_DIR'), reason='explicit read-only real artifact check')
def test_real_recovery_artifact_shape_and_canonical_identity():
    folder = Path(os.environ['STG_OCR_RECOVERY_ARTIFACT_DIR'])
    order = json.loads((folder / 'order.json').read_text())
    workflow = json.loads((folder / 'workflow.json').read_text())
    selection = json.loads((folder / 'facility-selection.json').read_text())
    raw_state = json.loads((folder / 'upload-state.json').read_text())
    state = {key: value for key, value in raw_state.items() if not key.startswith('lease_')}
    case = {'existing_uploaded_pdf_id': 'UPL2f6c937d', 'expected_order_id': 'ORD581f55d6',
            'expected_document_id': 'DOC0d4cb340', 'sha256': 'acafa4eacc0fe0d82aff29c078b2ad8bf56dd3700c19a13bf26f69a484678a31',
            'facility_id': 'FAC00015', 'week_id': '2026-10@2026-10-25~2026-10-31'}
    assert workflow['state'] == 'uploaded'
    assert 'workflow_not_initialized' in workflow['blockers']
    assert all(workflow[key] is None for key in ('facility_id', 'week_start', 'week_end', 'template_version_id'))
    assert mod.canonical_projection_id(case, selection) == 'CFV571556a9848db8406264cb50d45eeabb36b4fbd92da28ba2df3c9a05618cebf8'
    mod.verify_resume_identity(case, selection, state, order, workflow)


@pytest.mark.parametrize('uri,expected', [
    (mod.BUCKET + 'original.pdf', mod.BUCKET + 'original.pdf'),
    (mod.BUCKET + 'original.pdf?generation=1792028285165557', mod.BUCKET + 'original.pdf#1792028285165557'),
    (mod.BUCKET + 'a%20b.pdf?generation=123', mod.BUCKET + 'a b.pdf#123'),
])
def test_download_preserves_exact_generation_for_gcloud(monkeypatch, tmp_path, uri, expected):
    calls = []
    monkeypatch.setattr(mod.subprocess, 'run', lambda *args, **kwargs: calls.append((args, kwargs)))
    target = tmp_path / 'original.pdf'
    mod.download(uri, target)
    assert calls == [((['gcloud', 'storage', 'cp', expected, str(target)],), {'check': True})]


@pytest.mark.parametrize('uri', [
    'gs://sawahospitalsystem-prod-raw/original.pdf',
    'gs://sawahospitalsystem-stg-raw.other/original.pdf',
    'https://sawahospitalsystem-stg-raw/original.pdf',
    mod.BUCKET + 'original.pdf?unknown=1',
    mod.BUCKET + 'original.pdf?generation=1&unknown=2',
    mod.BUCKET + 'original.pdf?generation=1&generation=2',
    mod.BUCKET + 'original.pdf?generation=',
    mod.BUCKET + 'original.pdf?generation=0',
    mod.BUCKET + 'original.pdf?generation=-1',
    mod.BUCKET + 'original.pdf?generation=01',
    mod.BUCKET + 'original.pdf?generation=1.0',
    mod.BUCKET + 'original.pdf?generation=%31',
    mod.BUCKET + 'original.pdf#123',
    mod.BUCKET + 'original.pdf#',
    mod.BUCKET + '*.pdf',
    mod.BUCKET + '%2A.pdf',
    mod.BUCKET + '%3F.pdf',
    mod.BUCKET + '%5Ba%5D.pdf',
    mod.BUCKET + '%23.pdf',
    mod.BUCKET + '%00.pdf',
    mod.BUCKET + '%ZZ.pdf',
])
def test_download_rejects_ambiguous_identity_before_cli(monkeypatch, tmp_path, uri):
    monkeypatch.setattr(mod.subprocess, 'run', lambda *args, **kwargs: pytest.fail('Unsafe URI reached CLI'))
    with pytest.raises(ValueError):
        mod.download(uri, tmp_path / 'original.pdf')


@pytest.mark.skipif(not os.environ.get('STG_OCR_RECOVERY_ARTIFACT_DIR'), reason='explicit read-only real artifact check')
def test_real_registered_uri_converts_to_exact_cli_generation(monkeypatch, tmp_path):
    folder = Path(os.environ['STG_OCR_RECOVERY_ARTIFACT_DIR'])
    uri = json.loads((folder / 'upload-state.json').read_text())['storage_uri']
    calls = []
    monkeypatch.setattr(mod.subprocess, 'run', lambda args, **kwargs: calls.append(args))
    mod.download(uri, tmp_path / 'original.pdf')
    assert calls[0][3] == (mod.BUCKET + 'manual-uploads/2026/10/15/'
                          'acafa4eacc0fe0d82aff29c0-ORD3d234696.pdf#1792028285165557')
