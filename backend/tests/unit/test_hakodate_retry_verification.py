import json
import sys
import urllib.error

import pytest

from test_stg_ocr_resume_upload import inputs, mod
from test_stg_hakodate_verification import existing


@pytest.fixture
def retry_case(existing):
    data, calls = existing
    case, selection, state, order, workflow, raw = data
    workflow.update(facility_id=case['facility_id'], week_start='2026-10-25',
                    week_end='2026-10-31', template_version_id=order['template_version_id'],
                    blockers=[], state='ocr_running', revision=3)
    return data, calls


@pytest.mark.parametrize('status', ['failed', 'running', 'pending', 'awaiting_output', 'recovering', 'done'])
@pytest.mark.parametrize('rejected', [False, True])
def test_existing_job_reaches_nonforcing_api_and_preserves_rejection(retry_case, monkeypatch, tmp_path, status, rejected):
    (case, _, state, order, workflow, raw), calls = retry_case
    workflow['ocr_job'] = {'ocr_job_id': 'OCR-' + order['id'], 'order_id': order['id'],
                           'status': status, 'updated_at': '2026-10-15T04:26:46',
                           'template_version_id': order['template_version_id']}
    original_request = mod.request
    error = urllib.error.HTTPError('https://reserved.invalid', 409, 'ocr_rerun_in_progress', {}, None)
    posts = []
    def request(path, body=None, content_type=None, **kwargs):
        if path.endswith('/ocr-runs'):
            posts.append(json.loads(body))
            assert posts[-1] == {'mode': 'hakodate', 'document_id': case['expected_document_id'],
                                 'stale_action': 'retry', 'force': False}
            assert kwargs == {'revision': 3}
            if rejected:
                raise error
        return original_request(path, body, content_type, **kwargs)
    monkeypatch.setattr(mod, 'request', request)
    def download(uri, path):
        if uri == 'manifest':
            path.write_text(json.dumps({'cases': [case]}))
        else:
            path.write_bytes(raw)
    monkeypatch.setattr(mod, 'download', download)
    monkeypatch.setattr(mod, 'completed_evidence', lambda *args: True)
    monkeypatch.setattr(sys, 'argv', ['verify', '--manifest', 'manifest', '--output-dir', str(tmp_path)])
    if rejected:
        with pytest.raises(urllib.error.HTTPError) as caught:
            mod.main()
        assert caught.value is error
    else:
        mod.main()
    assert len(posts) == 1
    assert not any(call[0].endswith(('/context', '/retry')) for call in calls)
    assert state['status'] == 'failed'


@pytest.mark.parametrize('key', mod.UNSELECTED_RESULT_FIELDS)
def test_missing_result_fields_reject_before_api(retry_case, tmp_path, key):
    (case, selection, _, _, workflow, _), calls = retry_case
    del workflow[key]
    with pytest.raises(ValueError):
        mod.prepare_existing_hakodate(case, selection, tmp_path)
    assert not any(call[1] is not None for call in calls)


@pytest.mark.parametrize('key', [key for key in mod.UNSELECTED_RESULT_FIELDS if key != 'ocr_job'])
def test_selected_result_blocks_despite_failed_job(retry_case, tmp_path, key):
    (case, selection, _, _, workflow, _), calls = retry_case
    workflow['ocr_job'] = {'status': 'failed'}
    workflow[key] = 'selected'
    with pytest.raises(ValueError):
        mod.prepare_existing_hakodate(case, selection, tmp_path)
    assert not any(call[1] is not None for call in calls)


def test_unconfirmed_job_cannot_trigger_context_replacement(existing, tmp_path):
    (case, selection, _, _, workflow, _), calls = existing
    workflow['ocr_job'] = {'status': 'failed'}
    with pytest.raises(ValueError):
        mod.prepare_existing_hakodate(case, selection, tmp_path)
    assert not any(call[1] is not None for call in calls)
