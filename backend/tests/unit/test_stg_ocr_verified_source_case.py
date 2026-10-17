"""Offline verification mechanics; not real-source OCR or live UI proof."""
from copy import deepcopy
import json
import hashlib
from pathlib import Path
import sys

import pytest

from test_stg_ocr_resume_upload import mod
from test_stg_recognition_review_verification import case as review_case
from test_stg_ocr_native_empty_resolution import content, staging, canonical, runtime, observation, cv2


RAW = {'date': '2026-10-24', 'daypart': 'lunch', 'menu_name': 'authored fixture',
       'field': 'qty.no_meat_x', 'expected_raw': '1'}


def union_case():
    return {'case_id': 'verified-source', 'expected_outcome': 'done_or_review_required',
            'required_raw_cells': [deepcopy(RAW)]}


@pytest.mark.parametrize('value', [None, '', 7, '../escape', '/absolute', 'a/b', '.', '..', 'a\\b'])
@pytest.mark.parametrize('key', ['case_id', 'source_order_id'])
def test_identity_rejects_non_string_empty_and_path_escape(key, value):
    with pytest.raises(ValueError):
        mod.verification_case_id({key: value})


def test_identity_one_of_and_symlink(tmp_path):
    for case in ({}, {'case_id': 'new', 'source_order_id': 'old'}):
        with pytest.raises(ValueError):
            mod.verification_case_id(case)
    assert mod.verification_case_id({'source_order_id': 'ORDlegacy'}) == 'ORDlegacy'
    assert mod.verification_case_id({'case_id': 'source-case'}) == 'source-case'
    (tmp_path / 'link').symlink_to(tmp_path.parent, target_is_directory=True)
    with pytest.raises(ValueError):
        mod.verification_case_folder(tmp_path, {'case_id': 'link'})


def test_collision_stops_before_source_download(tmp_path, monkeypatch):
    manifest = {'cases': [{'case_id': 'same'}, {'source_order_id': 'same'}]}
    calls = []
    def download(uri, target):
        calls.append(uri)
        target.write_text(json.dumps(manifest))
    monkeypatch.setattr(mod, 'download', download)
    monkeypatch.setattr(sys, 'argv', ['verify', '--manifest', 'manifest', '--output-dir', str(tmp_path)])
    with pytest.raises(ValueError, match='collision'):
        mod.main()
    assert calls == ['manifest']


@pytest.mark.parametrize('change', ['legacy', 'native', 'review', 'blank', 'missing_raw', 'empty_raw'])
def test_union_cannot_relax_old_cases(change):
    case = union_case()
    if change == 'legacy':
        case['source_order_id'] = case.pop('case_id')
    elif change in ('native', 'review', 'blank'):
        case[{'native': 'native_empty_resolution', 'review': 'review_resolution', 'blank': 'blank_resolution'}[change]] = {}
    elif change == 'missing_raw':
        del case['required_raw_cells']
    else:
        case['required_raw_cells'] = []
    with pytest.raises(ValueError):
        mod.case_expectation(case)


def outcome_values(outcome):
    values = list(review_case())
    evidence, workflow = values[:2]
    source = evidence['payload_json']['hakodate_ocr_evidence_records'][0]['engine_metadata']['recognizer_output']
    source.update(resolution_state='review_required', quantity_source_issue='hakodate_blank_input_nonempty_prediction')
    if outcome == 'done':
        workflow['ocr_job']['status'] = 'done'
        workflow['ocr_job'].update(result_state='evidence_ready', processing_stage='evidence_ready',
                                   error=None, error_message=None, error_detail=None, error_user_message=None)
        evidence['status'] = 'done'
        evidence['payload_json'].pop('hakodate_recognition_review')
        source['resolution_state'] = 'resolved'
    return values


@pytest.mark.parametrize('outcome', ['done', 'recognition_review_required'])
def test_union_checks_both_actual_contracts(outcome):
    policy, verify = mod.case_expectation(union_case())
    values = outcome_values(outcome)
    assert policy == 'done_or_review_required'
    assert verify(*values)
    assert mod.actual_case_outcome(*values) == outcome


@pytest.mark.parametrize('field', ['result_state', 'processing_stage'])
@pytest.mark.parametrize('state', [None, 'absent', 'error', 'hard_failed', 'processing',
                                 'persist_pending', 'unknown', 'draft_blocked', 'review_required', ''])
def test_union_done_requires_exact_evidence_ready_state(field, state):
    values = outcome_values('done')
    job = values[1]['ocr_job']
    if state == 'absent':
        del job[field]
    else:
        job[field] = state
    assert mod.actual_case_outcome(*values) is None
    assert not mod.case_expectation(union_case())[1](*values)
    # The existing scalar contract is deliberately unchanged.
    assert mod.completed_evidence(*values)


@pytest.mark.parametrize('field', ['error', 'error_message', 'error_detail', 'error_user_message'])
@pytest.mark.parametrize('value', ['absent', 'failed', '', False, 0])
def test_union_done_requires_serialized_null_error_fields(field, value):
    values = outcome_values('done')
    if value == 'absent':
        del values[1]['ocr_job'][field]
    else:
        values[1]['ocr_job'][field] = value
    assert mod.actual_case_outcome(*values) is None
    assert mod.completed_evidence(*values)


@pytest.mark.parametrize('change', ['job', 'attempt', 'status', 'review_cells', 'done_with_review', 'hard_failure'])
def test_union_rejects_inconsistent_or_unknown_contract(change):
    values = outcome_values('done' if change == 'done_with_review' else 'recognition_review_required')
    evidence, workflow = values[:2]
    if change in ('job', 'attempt'):
        evidence['payload_json']['_ocr_execution'][change + '_id' if change == 'job' else 'workflow_attempt_id'] = 'foreign'
    elif change == 'status':
        evidence['status'] = 'done'
    elif change == 'review_cells':
        evidence['payload_json']['hakodate_recognition_review']['cells'][0]['raw_text'] = '0'
    elif change == 'done_with_review':
        evidence['payload_json']['hakodate_recognition_review'] = {'cells': [{'raw_text': '-'}], 'count': 1}
    else:
        workflow['ocr_job']['result_state'] = 'error'
    assert mod.actual_case_outcome(*values) is None
    assert not mod.case_expectation(union_case())[1](*values)


def test_publication_delay_is_not_mismatched_terminal():
    workflow = {'ocr_job': {'status': 'done'}}
    for policy in ('done', 'recognition_review_required', 'done_or_review_required'):
        assert mod.reject_unmatched_terminal(workflow, None, policy) is None
    with pytest.raises(RuntimeError, match='Unexpected terminal'):
        mod.reject_unmatched_terminal(workflow, {'id': 'own'}, 'recognition_review_required')
    with pytest.raises(RuntimeError, match='Unexpected terminal'):
        mod.reject_unmatched_terminal(workflow, {'id': 'own'}, 'done_or_review_required')
    assert mod.reject_unmatched_terminal(workflow, {'id': 'own'}, 'done') is None


@pytest.mark.parametrize('published', [False, True])
def test_main_polls_done_until_own_evidence_then_stops_mismatch(tmp_path, monkeypatch, published):
    raw = b'authored-source'
    case = {**union_case(), 'pdf_uri': 'fake-pdf', 'sha256': hashlib.sha256(raw).hexdigest(),
            'identity_verified': True}
    values = outcome_values('recognition_review_required')
    evidence, workflow = values[:2]
    workflow['revision'] = 1
    workflow['ocr_job']['status'] = 'done'  # Explicitly incompatible review evidence.
    manifest = {'cases': [case]}
    def download(uri, target):
        target.write_text(json.dumps(manifest)) if uri == 'manifest' else target.write_bytes(raw)
    monkeypatch.setattr(mod, 'download', download)
    monkeypatch.setattr(mod, 'resolve_selection', lambda case: {})
    monkeypatch.setattr(mod, 'upload_new', lambda *args: {'uploaded_pdf_id': 'upload'})
    monkeypatch.setattr(mod, 'verify_owner', lambda *args: 'pdf')
    monkeypatch.setattr(mod, 'apply_verified_quad', lambda *args: None)
    monkeypatch.setattr(mod, 'fetch_own_evidence', lambda *args: evidence if published else None)
    def request(path, *args, **kwargs):
        if path == '/ingest/uploads/upload':
            return {'status': 'completed', 'current_order_id': 'order'}
        if path == '/orders/order':
            return {'id': 'order'}
        if path.endswith('/workflow-v2'):
            return workflow
        if path.endswith('/ocr-runs'):
            return {'workflow': workflow}
        if path.endswith('/evidence'):
            return None
        raise AssertionError(path)
    monkeypatch.setattr(mod, 'request', request)
    sleeps = []
    class PollObserved(Exception):
        pass
    def sleep(seconds):
        sleeps.append(seconds)
        raise PollObserved()
    monkeypatch.setattr(mod.time, 'sleep', sleep)
    monkeypatch.setattr(sys, 'argv', ['verify', '--manifest', 'manifest', '--output-dir', str(tmp_path)])
    if published:
        with pytest.raises(RuntimeError, match='Unexpected terminal'):
            mod.main()
        assert sleeps == []
        assert json.loads((tmp_path / case['case_id'] / 'evidence.json').read_text()) == evidence
    else:
        with pytest.raises(PollObserved):
            mod.main()
        assert sleeps == [15]
    assert json.loads((tmp_path / case['case_id'] / 'workflow.json').read_text()) == workflow


def test_old_and_explicit_new_review_contracts_remain_distinct():
    values = review_case()
    assert mod.case_expectation({'source_order_id': 'old', 'expected_outcome': 'recognition_review_required'})[1](*values)
    assert not mod.case_expectation({**union_case(), 'expected_outcome': 'recognition_review_required'})[1](*values)
    assert mod.case_expectation({'native_empty_resolution': {}, 'expected_outcome': 'recognition_review_required'})[0] == 'recognition_review_required'


@pytest.mark.parametrize('change', ['none', 'missing', 'duplicate', 'raw_zero', 'api_zero', 'source_bytes'])
def test_required_raw_one_has_full_canonical_source_and_contact_guard(content, staging, tmp_path, change):
    _, records, payload, contact = content
    identity = {key: value for key, value in RAW.items() if key != 'expected_raw'}
    # This authored fixture cell is not a claim about any real worksheet address.
    records[2].update(identity)
    if change == 'missing':
        records[2]['menu_name'] = 'other'
    if change == 'duplicate':
        records[3].update(identity)
    if change == 'raw_zero':
        records[2].update(recognizer_raw_text='0', raw_text='0', resolved_value='0', pred_digits='0')
    image, regions, _, skipped = runtime.build_recognizer_contact_sheet(
        rectified_fax_bgr=cv2.imread(str(tmp_path / 'source.png')),
        regions=[observation.geometry(row) for row in records], line_mask=None, mode='corner_cc')
    assert not skipped
    image.save(contact)
    for record, region in zip(records, regions):
        record.update(region)
        record['recognizer_input_evidence'] = deepcopy(region['recognizer_preprocess_observation']['contact'])
    for index, record in enumerate(records):
        payload['hakodate_ocr_evidence_records'][index]['engine_metadata']['recognizer_output'] = deepcopy(record)
        payload['hakodate_ocr_evidence_records'][index]['raw_text'] = record['raw_text']
        payload['hakodate_ocr_evidence_records'][index]['normalized_value'] = record['resolved_value']
    canonical(content, tmp_path, staging)
    if change == 'api_zero':
        payload['hakodate_ocr_evidence_records'][2]['normalized_value'] = '0'
    if change == 'source_bytes':
        packet = payload['hakodate_canonical_pipeline']['artifact_packet']
        saved_observation = json.loads(staging.objects[packet['outputs']['preprocess_observation']['uri']])
        staging.objects[saved_observation['checkpoint']['image']['uri']] = b'changed'
    if change == 'none':
        assert mod.verify_required_raw_cells([RAW], records, payload, contact) == {'count': 1, 'cells': [RAW]}
    else:
        with pytest.raises(ValueError):
            mod.verify_required_raw_cells([RAW], records, payload, contact)


def test_upload_case_label_does_not_supply_business_owner(monkeypatch):
    case = {'case_id': 'label', 'facility_id': 'FAC00004', 'week_id': 'week', 'effective_date': 'date'}
    calls = []
    monkeypatch.setattr(mod, 'request', lambda *args: calls.append(args))
    mod.upload_new(case, {'revision': 'rev', 'version': {'version_id': 'version'}}, b'original')
    assert b'filename="label.pdf"' in calls[0][1]
    assert b'original' in calls[0][1]
    assert b'source_order_id' not in calls[0][1]


def test_ci_retains_native_module_and_adds_verified_source_module():
    text = (Path(__file__).resolve().parents[3] / '.github/workflows/ci.yml').read_text()
    assert text.count('tests/unit/test_stg_ocr_verified_source_case.py') == 1
    assert text.count('tests/unit/test_stg_ocr_native_empty_resolution.py') == 1
