import copy
import json
import sys

import pytest

from test_stg_ocr_resume_upload import inputs, mod


@pytest.fixture
def existing(inputs, monkeypatch):
    case, selection, state, order, workflow, raw = inputs
    case.update(verification_mode='existing_order_hakodate', pdf_uri=mod.BUCKET + 'fixture.pdf', expected_menu_rows=4)
    selection['version']['facility']['fax_template'] = {'template_id': 'canonical-template'}
    order['template_version_id'] = mod.canonical_projection_id(case, selection)
    state.update(status='failed', lease_expires_at=None, lease_token='SECRET_TOKEN', lease_owner='SECRET_OWNER')
    workflow.update(blockers=['template_unresolved'], revision=2)
    workflow.update(dict.fromkeys(mod.UNSELECTED_RESULT_FIELDS))
    calls = []
    job = {'ocr_job_id': 'job', 'workflow_attempt_id': 'attempt', 'status': 'done',
           'order_id': order['id'], 'order_document_id': order['document_id']}
    def request(path, body=None, content_type=None, **kwargs):
        calls.append((path, body, kwargs))
        if path.endswith('/context'):
            assert kwargs == {'revision': 2}
            assert json.loads(body) == {'facility_id': case['facility_id'], 'week_start': '2026-10-25',
                                       'week_end': '2026-10-31', 'template_id': 'canonical-template'}
            workflow.update(facility_id=case['facility_id'], week_start='2026-10-25', week_end='2026-10-31',
                            template_version_id=order['template_version_id'], blockers=[], state='context_confirmed', revision=3)
            return copy.deepcopy(workflow)
        if path.endswith('/ocr-runs'):
            assert json.loads(body)['mode'] == 'hakodate'
            assert kwargs == {'revision': 3}
            workflow.update(revision=4, ocr_job=copy.deepcopy(job))
            return {'workflow': copy.deepcopy(workflow)}
        if path == '/orders/' + order['id'] + '/evidence':
            assert workflow.get('ocr_job') is None
            return {'id': 'previous-evidence'}
        if path == '/orders/' + order['id'] + '/workflow-v2/ocr-results':
            assert workflow['ocr_job'] == job
            return {'results': [{'ocr_result_id': 'evidence', 'execution_lineage': {
                'job_id': job['ocr_job_id'], 'workflow_attempt_id': job['workflow_attempt_id']}}]}
        if path == '/orders/' + order['id'] + '/workflow-v2/ocr-results/evidence':
            assert workflow['ocr_job'] == job
            return {'id': 'evidence', 'order_id': order['id'], 'status': 'done',
                'producer_version': 'hakodate_best_method_pipeline',
                'payload_json': {'_ocr_execution': {
                    'job_id': job['ocr_job_id'], 'workflow_attempt_id': job['workflow_attempt_id'],
                    'evidence_run_id': 'evidence', 'selected_pdf_receipt': {
                        'order_id': order['id'], 'document_id': order['document_id']}}, 'hakodate_overlay': {
                'uri': mod.BUCKET + 'overlay.png', 'sheet_review_base_uri': mod.BUCKET + 'corrected.png'},
                'hakodate_canonical_pipeline': {'metrics': {'row_axis_source': 'fax_intersections',
                    'row_axis_draft_body_row_count': 4, 'row_axis': []}}}}
        if '/resolved?' in path:
            return copy.deepcopy(selection)
        if path.endswith('/workflow-v2'):
            return copy.deepcopy(workflow)
        if path == '/orders/' + order['id']:
            return copy.deepcopy(order)
        assert path == '/ingest/uploads/' + state['id']
        return copy.deepcopy(state)
    monkeypatch.setattr(mod, 'request', request)
    monkeypatch.setattr(mod, 'download', lambda uri, path: path.write_bytes(raw))
    monkeypatch.setattr(mod.time, 'sleep', lambda _: pytest.fail('Unexpected polling delay'))
    return inputs, calls


def test_context_post_then_get_and_already_matching_no_post(existing, tmp_path):
    (case, selection, state, *_), calls = existing
    result = mod.prepare_existing_hakodate(case, selection, tmp_path)
    assert result['revision'] == 3
    context_index = next(i for i, call in enumerate(calls) if call[0].endswith('/context'))
    assert any(c[0].endswith('/workflow-v2') for c in calls[context_index + 1:])
    calls.clear()
    mod.prepare_existing_hakodate(case, selection, tmp_path)
    assert not any(c[1] is not None for c in calls)
    assert state['status'] == 'failed'


@pytest.mark.parametrize('field', mod.UNSELECTED_RESULT_FIELDS)
@pytest.mark.parametrize('missing', [True, False])
def test_selected_or_missing_results_stop(existing, tmp_path, field, missing):
    (case, selection, _, _, workflow, _), calls = existing
    if missing:
        del workflow[field]
    else:
        workflow[field] = 'selected'
    with pytest.raises(ValueError):
        mod.prepare_existing_hakodate(case, selection, tmp_path)
    assert not any(c[1] is not None for c in calls)


@pytest.mark.parametrize('field', ['facility_id', 'week_start', 'week_end', 'template_version_id'])
def test_mixed_context_stops(existing, tmp_path, field):
    (case, selection, _, _, workflow, _), calls = existing
    workflow[field] = 'conflict'
    with pytest.raises(ValueError):
        mod.prepare_existing_hakodate(case, selection, tmp_path)
    assert not any(c[1] is not None for c in calls)


@pytest.mark.parametrize('field,value', [('lease_expires_at', '2099-01-01T00:00:00'),
    ('lease_expires_at', 'invalid'), ('status', 'processing'), ('current_order_id', 'other'),
    ('current_document_id', 'other'), ('content_sha256', 'other'), ('skip_ocr', True)])
def test_upload_guards_before_context(existing, tmp_path, field, value):
    (case, selection, state, *_), calls = existing
    state[field] = value
    with pytest.raises(ValueError):
        mod.prepare_existing_hakodate(case, selection, tmp_path)
    assert not any(c[1] is not None for c in calls)


def test_failed_upload_explicit_main_runs_hakodate_without_retry(existing, monkeypatch, tmp_path):
    (case, _, state, *_), calls = existing
    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps({'cases': [case]}))
    real_download = mod.download
    def download(uri, path):
        if uri == 'manifest':
            path.write_text(manifest.read_text())
        else:
            real_download(uri, path)
    monkeypatch.setattr(mod, 'download', download)
    monkeypatch.setattr(sys, 'argv', ['verify', '--manifest', 'manifest', '--output-dir', str(tmp_path / 'out')])
    mod.main()
    assert state['status'] == 'failed'
    writes = [c[0] for c in calls if c[1] is not None]
    assert writes == ['/orders/ORD581f55d6/workflow-v2/context', '/orders/ORD581f55d6/workflow-v2/ocr-runs']
    assert any(c[0] == '/orders/ORD581f55d6/workflow-v2/ocr-results' for c in calls)
    assert any(c[0] == '/orders/ORD581f55d6/workflow-v2/ocr-results/evidence' for c in calls)
    folder = tmp_path / 'out' / case['source_order_id']
    scope = json.loads((folder / 'verification-scope.json').read_text())
    assert scope['upload_to_ocr_success'] is False
    assert not any(c[0].endswith('/workflow-v2/sheet') for c in calls)
    assert not (folder / 'step2-sheet.json').exists()
    for artifact in folder.glob('*.json'):
        assert 'SECRET_TOKEN' not in artifact.read_text()
        assert 'SECRET_OWNER' not in artifact.read_text()


def test_redaction_recursive(tmp_path):
    mod.save_json(tmp_path, 'safe.json', {'nested': [{'lease_token': 'secret', 'lease_owner': 'secret', 'status': 'failed'}]})
    assert json.loads((tmp_path / 'safe.json').read_text()) == {'nested': [{'status': 'failed'}]}


def test_stored_pdf_hash_rechecked(existing, monkeypatch, tmp_path):
    (case, selection, *_), calls = existing
    monkeypatch.setattr(mod, 'download', lambda uri, path: path.write_bytes(b'changed'))
    with pytest.raises(ValueError, match='hash mismatch'):
        mod.prepare_existing_hakodate(case, selection, tmp_path)
    assert not any(c[1] is not None for c in calls)


def test_canonical_change_stops(existing, monkeypatch, tmp_path):
    (case, selection, *_), calls = existing
    monkeypatch.setattr(mod, 'resolve_selection', lambda case: {**selection, 'revision': 'changed'})
    with pytest.raises(ValueError, match='selection changed'):
        mod.prepare_existing_hakodate(case, selection, tmp_path)
    assert not any(c[1] is not None for c in calls)


@pytest.mark.parametrize('mutation', ['context', 'selected_result'])
def test_confirmation_reread_conflicts_stop(existing, monkeypatch, tmp_path, mutation):
    (case, selection, _, _, workflow, _), _ = existing
    original = mod.request
    def request(path, *args, **kwargs):
        result = original(path, *args, **kwargs)
        if path.endswith('/context'):
            if mutation == 'context':
                workflow['facility_id'] = 'different'
            else:
                workflow['saved_sheet_id'] = 'new-draft'
        return result
    monkeypatch.setattr(mod, 'request', request)
    with pytest.raises(ValueError):
        mod.prepare_existing_hakodate(case, selection, tmp_path)


def test_post_start_null_context_rejected(existing, tmp_path):
    (case, selection, *_), _ = existing
    with pytest.raises(ValueError, match='Selected workflow context required'):
        mod.verify_hakodate_existing(case, selection, tmp_path, require_context=True)


def test_unknown_unselected_marker_rejected(existing, tmp_path):
    (case, selection, _, _, workflow, _), calls = existing
    workflow['blockers'] = []
    with pytest.raises(ValueError):
        mod.prepare_existing_hakodate(case, selection, tmp_path)
    assert not any(c[1] is not None for c in calls)


def test_missing_lease_state_rejected(existing, tmp_path):
    (case, selection, state, *_), _ = existing
    del state['lease_expires_at']
    with pytest.raises(ValueError, match='lease state missing'):
        mod.prepare_existing_hakodate(case, selection, tmp_path)
