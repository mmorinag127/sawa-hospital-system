"""Offline ownership/dispatch mechanics; not real OCR or live-flow proof."""
from copy import deepcopy
from contextlib import contextmanager
import hashlib
import json
import sys
from types import SimpleNamespace as Row

import pytest

from src.services import ingest_job_service as ingest
from src.services import order_workflow_v2_service as workflow
from src.services import order_service
from src.workers import ingest_worker as worker
from test_stg_ocr_resume_upload import mod as verifier


def native_terminal(status='done'):
    pdf = {'order_id': 'order', 'document_id': 'document', 'sha256': 'a' * 64, 'storage_uri': 'owned-source'}
    capture = {'job_id': 'job', 'execution_id': 'execution', 'order_id': 'order', 'ingest_ocr_mode': 'hakodate',
               'intake_protocol': 'native_ingest_v2', 'ingest_lease': {'payload': {'intake_protocol': 'native_ingest_v2'}},
               'selected_pdf_receipt': pdf, 'orders': {'order': {'order': {'template_version_id': 'CFVexplicit'}}}}
    terminal = {'job_id': 'job', 'execution_id': 'execution', 'order_id': 'order', 'status': status,
                'evidence_run_id': 'evidence', 'workflow_attempt_id': None, 'error': None if status == 'done' else 'review'}
    evidence = Row(id='evidence', order_id='order', template_version_id='CFVexplicit',
        producer_version='hakodate_best_method_pipeline', status='done' if status == 'done' else 'diagnostic_only',
        payload_json={'_ocr_execution': {'job_id': 'job', 'nonworkflow_execution_id': 'execution',
            'evidence_run_id': 'evidence', 'selected_pdf_receipt': deepcopy(pdf)}})
    job = Row(id='job', status=status, error_message=terminal['error'], metrics={
        'nonworkflow_execution': capture, 'rerun_terminal_receipt': terminal,
        'result_state': 'evidence_ready' if status == 'done' else 'review_required', 'processing_stage': 'evidence_ready'})
    session = Row(get=lambda model, identity: evidence if identity == 'evidence' else None)
    return session, job, capture, evidence


@pytest.mark.parametrize('change', [None, 'missing_evidence', 'missing_value', 'conflict', 'unassigned', 'blocker'])
def test_resolved_blank_gate_requires_actual_evidence_for_every_target(change):
    from src.services.hakodate_ocr_evidence_service import has_resolved_blank_targets
    result = {'assignments': [{'assignment_state': 'blank_evidence', 'evidence_ids': ['authored-record']}],
              'blockers': [], 'unassigned_evidence': []}
    if change == 'missing_evidence': result['assignments'][0]['evidence_ids'] = []
    elif change == 'missing_value': result['assignments'][0]['assignment_state'] = 'blank'
    elif change == 'conflict': result['assignments'][0]['assignment_state'] = 'conflict'
    elif change == 'unassigned': result['unassigned_evidence'] = [{'evidence_id': 'foreign'}]
    elif change == 'blocker': result['blockers'] = ['unresolved target']
    assert has_resolved_blank_targets(result) is (change is None)


@pytest.mark.parametrize('status', ['done', 'failed'])
def test_native_selector_uses_real_rerun_receipt_not_first_pass_alias(status):
    session, job, capture, _ = native_terminal(status)
    assert ingest.ingest_terminal_receipt(session, job, capture) is job.metrics['rerun_terminal_receipt']
    assert 'first_pass_terminal_receipt' not in job.metrics


@pytest.mark.parametrize('change', ['mode', 'job', 'execution', 'order', 'template', 'source', 'producer',
                                 'foreign_evidence', 'ambiguous', 'unknown_state', 'missing_evidence'])
def test_native_selector_rejects_foreign_ambiguous_and_unknown_contracts(change):
    session, job, capture, evidence = native_terminal()
    if change == 'mode': capture['ingest_ocr_mode'] = 'unknown'
    elif change in ('job', 'execution'):
        job.metrics['rerun_terminal_receipt'][change + '_id'] = 'foreign'
    elif change == 'order': evidence.order_id = 'foreign'
    elif change == 'template': evidence.template_version_id = 'foreign'
    elif change == 'source': evidence.payload_json['_ocr_execution']['selected_pdf_receipt']['sha256'] = 'b' * 64
    elif change == 'producer': evidence.producer_version = 'legacy'
    elif change == 'foreign_evidence': evidence.payload_json['_ocr_execution']['evidence_run_id'] = 'foreign'
    elif change == 'ambiguous': job.metrics['first_pass_terminal_receipt'] = deepcopy(job.metrics['rerun_terminal_receipt'])
    elif change == 'unknown_state': job.metrics['result_state'] = 'unknown'
    else: job.metrics['rerun_terminal_receipt']['evidence_run_id'] = None
    with pytest.raises(ValueError):
        ingest.ingest_terminal_receipt(session, job, capture)


def test_hard_failure_is_not_review_and_cannot_be_success_without_evidence():
    session, job, capture, _ = native_terminal('failed')
    job.metrics['rerun_terminal_receipt']['evidence_run_id'] = None
    job.error_message = job.metrics['rerun_terminal_receipt']['error'] = 'geometry_unresolved'
    job.metrics['result_state'] = 'hard_failed'
    assert ingest.ingest_terminal_receipt(session, job, capture)['status'] == 'failed'
    job.status = job.metrics['rerun_terminal_receipt']['status'] = 'done'
    with pytest.raises(ValueError, match='evidence_missing'):
        ingest.ingest_terminal_receipt(session, job, capture)


def test_saved_legacy_and_explicit_registration_receipts_keep_original_meaning():
    session, job, capture, _ = native_terminal()
    for mode in (None, 'registration_only'):
        capture['ingest_ocr_mode'] = mode
        capture['ingest_lease']['payload'] = {'intake_protocol': 'native_ingest_v2'} if mode else {}
        capture['orders']['order']['document'] = {'id': 'document'}
        job.metrics = {'result_state': 'skipped', 'non_ocr_terminal_receipt': {
            'job_id': job.id, 'execution_id': capture['execution_id'], 'document_id': 'document',
            'status': 'done', 'result_state': 'skipped'}}
        assert ingest.ingest_terminal_receipt(session, job, capture)['result_state'] == 'skipped'


@pytest.mark.parametrize('kind', ['first_pass_terminal_receipt', 'rerun_terminal_receipt'])
def test_registration_only_rejects_sole_wrong_receipt_kind(kind):
    session, job, capture, _ = native_terminal()
    capture['ingest_ocr_mode'] = 'registration_only'
    receipt = deepcopy(job.metrics['rerun_terminal_receipt'])
    job.metrics = {kind: receipt}
    with pytest.raises(ValueError, match='ingest_registration_'):
        ingest.ingest_terminal_receipt(session, job, capture)


def registration_source_failure():
    session, job, capture, _ = native_terminal('failed')
    capture['ingest_ocr_mode'] = 'registration_only'
    receipt = deepcopy(job.metrics['rerun_terminal_receipt'])
    receipt.update(error='selected_pdf_bytes_changed', evidence_run_id=None, adopted=False)
    job.error_message = receipt['error']
    job.metrics = {key: deepcopy(receipt) for key in
        ('source_failure_receipt', 'non_ocr_terminal_receipt', 'rerun_terminal_receipt')}
    job.metrics.update(result_state='hard_failed', processing_stage='source_verification_failed')
    return session, job, capture, receipt


def test_registration_only_accepts_exact_failure_only_source_alias_contract():
    session, job, capture, receipt = registration_source_failure()
    assert ingest.ingest_terminal_receipt(session, job, capture) == receipt


@pytest.mark.parametrize('change', ['non_ocr', 'rerun', 'source', 'firstpass', 'missing_non_ocr',
    'missing_source', 'missing_rerun', 'job', 'execution', 'order', 'success', 'adopted', 'evidence',
    'unknown_error', 'attempt', 'state', 'stage'])
def test_registration_only_rejects_conflicting_or_non_failure_source_aliases(change):
    session, job, capture, receipt = registration_source_failure()
    if change in ('non_ocr', 'rerun', 'source'):
        key = {'non_ocr': 'non_ocr_terminal_receipt', 'rerun': 'rerun_terminal_receipt', 'source': 'source_failure_receipt'}[change]
        job.metrics[key]['error'] = 'foreign'
    elif change.startswith('missing_'):
        key = {'missing_non_ocr': 'non_ocr_terminal_receipt', 'missing_source': 'source_failure_receipt',
               'missing_rerun': 'rerun_terminal_receipt'}[change]
        del job.metrics[key]
    elif change == 'firstpass':
        job.metrics['first_pass_terminal_receipt'] = deepcopy(receipt)
    elif change in ('state', 'stage'):
        job.metrics['result_state' if change == 'state' else 'processing_stage'] = 'unknown'
    else:
        field, value = {'job': ('job_id', 'foreign'), 'execution': ('execution_id', 'foreign'),
            'order': ('order_id', 'foreign'), 'success': ('status', 'done'), 'adopted': ('adopted', True),
            'evidence': ('evidence_run_id', 'foreign'), 'unknown_error': ('error', 'runtime_error'),
            'attempt': ('workflow_attempt_id', 'foreign')}[change]
        for key in ('source_failure_receipt', 'non_ocr_terminal_receipt', 'rerun_terminal_receipt'):
            job.metrics[key][field] = value
    with pytest.raises(ValueError, match='ingest_registration_'):
        ingest.ingest_terminal_receipt(session, job, capture)


def test_v1_none_keeps_its_existing_first_pass_receipt_semantics():
    session, job, capture, _ = native_terminal()
    capture['ingest_ocr_mode'] = None
    capture['ingest_lease']['payload'] = {}
    receipt = deepcopy(job.metrics['rerun_terminal_receipt'])
    job.metrics = {'first_pass_terminal_receipt': receipt}
    assert ingest.ingest_terminal_receipt(session, job, capture) is receipt


def context_fixture(monkeypatch, active=True):
    from src.models.ocr_job import OcrJob
    from src.models.document import OrderDocument
    from src.services import ingest_facility_selection_service as selection
    lease = {'id': 'message', 'generation': 'new-generation', 'claim_id': 'current-claim',
             'payload': {'intake_protocol': 'native_ingest_v2'}}
    order = Row(id='order', facility_code='FAC00004', week_code='2026-10@2026-10-18~2026-10-24',
                current_document_id='document', template_version_id='CFVexplicit')
    job = Row(id='job', order_id=order.id, order_document_id='document', input_reference='owned-source', status='running',
              metrics={'ingest_lease': deepcopy(lease), 'ingest_ocr_mode': 'hakodate'})
    document = Row(id='document', source_email_id='message', storage_uri='owned-source')
    projection = Row(id='CFVexplicit', template_id='authored-template', template_digest='digest')
    suggestion = {'facility_id': 'FAC00004', 'week_start': '2026-10-18', 'week_end': '2026-10-24'}
    row = Row(order_id=order.id, revision=2, state='uploaded', template_version_id=None,
        evidence_run_id=None, draft_id=None, confirmed_snapshot_id=None,
        secondary_actions_json={'unrelated_metadata': 'kept', 'workflow_v2': {
            'context_suggestion': suggestion, 'expanded_cell_copy_mode': 'auto'}},
        blockers_json=[], warnings_json=[])
    def get(model, identity, **kwargs):
        if model is OcrJob: return job
        if model is OrderDocument: return document
        if model is workflow.OrderWorkflowState: return row
        return projection
    session = Row(get=get)
    calls = []
    monkeypatch.setattr(ingest, 'current_ingest_lease', lambda: deepcopy(lease))
    monkeypatch.setattr(ingest, 'lock_ingest_lease', lambda *args, **kwargs: active)
    monkeypatch.setattr(selection, 'require_order_selection', lambda *args: calls.append('selection'))
    monkeypatch.setattr(workflow.facility_template_version_service, 'template_version_applies_on', lambda *args: True)
    monkeypatch.setattr(workflow, 'lock_order_execution_input', lambda *args: calls.append('order-lock') or order)
    monkeypatch.setattr(workflow, '_get_or_create_workflow', lambda *args: row)
    def finalize(session, target):
        assert workflow._expected_workflow_revision.get() == 2
        calls.append('context-write')
        target.revision += 1
    monkeypatch.setattr(workflow, '_finalize_workflow_mutation', finalize)
    return session, order, job, row, calls


def test_stale_lease_between_registration_and_context_cannot_mutate(monkeypatch):
    session, order, job, row, calls = context_fixture(monkeypatch, active=False)
    before = deepcopy((vars(order), vars(job), vars(row)))
    with pytest.raises(ValueError, match='ingest_attempt_changed'):
        workflow.initialize_ingest_context(session, order, {'effective_date': '2026-10-18'}, 'job')
    assert (vars(order), vars(job), vars(row)) == before
    assert calls == []


def test_context_accepts_ordinary_suggestion_shape_but_does_not_adopt(monkeypatch):
    session, order, _, row, calls = context_fixture(monkeypatch)
    suggestion = deepcopy(row.secondary_actions_json['workflow_v2']['context_suggestion'])
    workflow.initialize_ingest_context(session, order, {'effective_date': '2026-10-18'}, 'job')
    assert calls == ['order-lock', 'selection', 'context-write']
    assert row.state == 'context_confirmed'
    assert row.template_version_id == order.template_version_id == 'CFVexplicit'
    assert row.secondary_actions_json['workflow_v2']['ocr_job_id'] == 'job'
    assert 'ocr_job_attempt_id' not in row.secondary_actions_json['workflow_v2']
    assert row.secondary_actions_json['workflow_v2']['context_suggestion'] == suggestion
    assert row.secondary_actions_json['unrelated_metadata'] == 'kept'
    assert row.evidence_run_id is row.draft_id is row.confirmed_snapshot_id is None


@pytest.mark.parametrize('change', ['foreign_job', 'newer_edit', 'selected_evidence', 'saved_draft', 'wrong_claim'])
def test_context_refuses_foreign_or_operator_owned_state(monkeypatch, change):
    session, order, job, row, calls = context_fixture(monkeypatch)
    if change == 'foreign_job': job.order_id = 'foreign'
    elif change == 'newer_edit': row.secondary_actions_json['workflow_v2']['operator_change'] = True
    elif change == 'selected_evidence': row.evidence_run_id = 'selected'
    elif change == 'saved_draft': row.draft_id = 'saved'
    else: job.metrics['ingest_lease']['claim_id'] = 'foreign'
    before = deepcopy(vars(row))
    with pytest.raises(ValueError):
        workflow.initialize_ingest_context(session, order, {'effective_date': '2026-10-18'}, 'job')
    assert vars(row) == before
    assert 'context-write' not in calls


def test_native_settlement_errors_are_not_warning_only(monkeypatch):
    from src.services.workflow_attempt_guard_service import nonworkflow_execution_context
    execution = {'job_id': 'job', 'order_id': 'order', 'ingest_ocr_mode': 'hakodate', 'ingest_lease': {'id': 'message'}}
    def refused(value):
        raise ValueError('settlement-refused')
    monkeypatch.setattr(ingest, 'settle_ingest_ocr_terminal', refused)
    with nonworkflow_execution_context(execution), pytest.raises(ValueError, match='settlement-refused'):
        order_service.finalize_first_pass_side_effects(job_id='job', order_id='order', message_id='message')


def test_native_dispatch_does_not_enter_old_pipeline_or_auto_adopt(monkeypatch):
    capture = {'ingest_ocr_mode': 'hakodate', 'intake_protocol': 'native_ingest_v2',
        'ingest_lease': {'payload': {'intake_protocol': 'native_ingest_v2'}}, 'orders': {'order': {
        'order': {'week_code': '2026-10@2026-10-18~2026-10-24'}, 'document': {'id': 'document'}}}}
    payload = Row(skip_ocr=False, week_hint='2026-10@2026-10-18~2026-10-24', order_id='order',
                  order_document_id='document', message_id='message', intake_protocol='native_ingest_v2')
    calls = []
    monkeypatch.setattr(order_service, 'rerun_ocr_evidence_only', lambda *args, **kwargs:
        calls.append(kwargs) or ({'terminal_receipt': {'status': 'done'}}, None))
    monkeypatch.setattr(order_service, 'finalize_first_pass_side_effects', lambda **kwargs: calls.append('settle'))
    monkeypatch.setattr(worker, 'run_ocr_pipeline', lambda **kwargs: pytest.fail('legacy pipeline invoked'))
    assert worker._process_ingest_captured(payload, 'job', capture)['terminal_receipt']['status'] == 'done'
    assert calls[0]['project_sheet'] is False
    assert calls[0]['selected_document_id'] == 'document'
    assert calls[1] == 'settle'


def test_native_successor_is_explicitly_forbidden():
    with pytest.raises(ValueError, match='llm_successor_forbidden'):
        worker._run_auto_llm_reparse('order', job_id='job', execution_input={'ingest_ocr_mode': 'hakodate'},
            own_payload={}, original_request={})


def test_native_recovery_does_not_parse_overlay_png_as_legacy_json(monkeypatch):
    from src.services.workflow_attempt_guard_service import nonworkflow_execution_context
    capture = {'job_id': 'job', 'order_id': 'order', 'ingest_ocr_mode': 'hakodate', 'ingest_lease': {'id': 'message'}}
    @contextmanager
    def session_scope():
        yield Row(get=lambda *args: Row(id='job'))
    monkeypatch.setattr(order_service, 'session_scope', session_scope)
    monkeypatch.setattr(ingest, 'ingest_terminal_receipt', lambda *args: {'status': 'done'})
    settled = []
    monkeypatch.setattr(order_service, 'finalize_first_pass_side_effects', lambda **kwargs: settled.append(kwargs))
    monkeypatch.setattr(worker, '_load_completed_ocr_output', lambda *args: pytest.fail('overlay parsed as JSON'))
    with nonworkflow_execution_context(capture):
        assert worker._recover_captured_ocr_job({'id': 'job', 'output_reference': 'overlay.png'})
    assert settled[0]['job_id'] == 'job'


def test_completed_legacy_upload_cannot_fall_back_to_second_ocr(tmp_path, monkeypatch):
    raw = b'authored-source'
    case = {'case_id': 'source', 'expected_execution_mode': 'native_ingest',
        'pdf_uri': 'source-pdf', 'sha256': hashlib.sha256(raw).hexdigest(), 'identity_verified': True}
    def download(uri, target):
        target.write_text(json.dumps({'cases': [case]})) if uri == 'manifest' else target.write_bytes(raw)
    monkeypatch.setattr(verifier, 'download', download)
    monkeypatch.setattr(verifier, 'resolve_selection', lambda *args: {})
    monkeypatch.setattr(verifier, 'upload_new', lambda *args: {'uploaded_pdf_id': 'upload'})
    monkeypatch.setattr(verifier, 'verify_owner', lambda *args: 'document')
    calls = []
    def request(path, *args, **kwargs):
        calls.append((path, args))
        if args:
            pytest.fail('second POST forbidden')
        if path == '/ingest/uploads/upload':
            return {'status': 'completed', 'current_order_id': 'order'}
        if path == '/orders/order':
            return {'id': 'order'}
        if path.endswith('/workflow-v2'):
            return {'order_id': 'order', 'ocr_job': {'status': 'done'}}
        pytest.fail(path)
    monkeypatch.setattr(verifier, 'request', request)
    monkeypatch.setattr(sys, 'argv', ['verify', '--manifest', 'manifest', '--output-dir', str(tmp_path)])
    with pytest.raises(RuntimeError, match='native lineage missing'):
        verifier.main()
    assert all(not args for _, args in calls)


@pytest.mark.parametrize('mode', ['unknown', '', None])
def test_unknown_explicit_manifest_mode_is_rejected_before_upload(tmp_path, monkeypatch, mode):
    raw = b'authored-source'
    case = {'case_id': 'source', 'expected_execution_mode': mode, 'pdf_uri': 'source-pdf',
            'sha256': hashlib.sha256(raw).hexdigest(), 'identity_verified': True}
    def download(uri, target):
        target.write_text(json.dumps({'cases': [case]})) if uri == 'manifest' else target.write_bytes(raw)
    monkeypatch.setattr(verifier, 'download', download)
    monkeypatch.setattr(verifier, 'resolve_selection', lambda *args: {})
    monkeypatch.setattr(verifier, 'upload_new', lambda *args: pytest.fail('Unknown mode cannot upload'))
    monkeypatch.setattr(sys, 'argv', ['verify', '--manifest', 'manifest', '--output-dir', str(tmp_path)])
    with pytest.raises(ValueError, match='Unknown expected execution mode'):
        verifier.main()


def test_v1_manifest_cannot_adopt_native_marker_or_count_as_native_proof(tmp_path, monkeypatch):
    raw = b'authored-source'
    case = {'source_order_id': 'legacy', 'pdf_uri': 'source-pdf',
            'sha256': hashlib.sha256(raw).hexdigest(), 'identity_verified': True}
    def download(uri, target):
        target.write_text(json.dumps({'cases': [case]})) if uri == 'manifest' else target.write_bytes(raw)
    monkeypatch.setattr(verifier, 'download', download)
    monkeypatch.setattr(verifier, 'resolve_selection', lambda *args: {})
    monkeypatch.setattr(verifier, 'upload_new', lambda *args: {'uploaded_pdf_id': 'upload'})
    monkeypatch.setattr(verifier, 'verify_owner', lambda *args: 'document')
    def request(path, *args, **kwargs):
        assert not args, 'V1 must not POST another OCR after seeing a native marker'
        if path == '/ingest/uploads/upload':
            return {'status': 'completed', 'current_order_id': 'order'}
        if path == '/orders/order':
            return {'id': 'order'}
        if path.endswith('/workflow-v2'):
            return {'order_id': 'order', 'ocr_job': {'native_ingest': {'mode': 'hakodate'}}}
        pytest.fail(path)
    monkeypatch.setattr(verifier, 'request', request)
    monkeypatch.setattr(sys, 'argv', ['verify', '--manifest', 'manifest', '--output-dir', str(tmp_path)])
    with pytest.raises(RuntimeError, match='V1 workflow executor cannot adopt'):
        verifier.main()
    assert json.loads((tmp_path / 'legacy' / 'verification-scope.json').read_text()) == {
        'verification_mode': 'v1_workflow_owned', 'normal_native_ingest_verified': False}


def test_source_failure_alias_is_only_the_validated_failure_contract():
    session, job, capture, _ = native_terminal('failed')
    receipt = job.metrics['rerun_terminal_receipt']
    receipt.update(evidence_run_id=None, error='selected_pdf_bytes_changed', adopted=False)
    job.error_message = receipt['error']
    job.metrics.update(source_failure_receipt=deepcopy(receipt), non_ocr_terminal_receipt=deepcopy(receipt), result_state='hard_failed')
    assert ingest.ingest_terminal_receipt(session, job, capture) == receipt
    job.metrics['non_ocr_terminal_receipt']['error'] = 'other'
    with pytest.raises(ValueError, match='ambiguous'):
        ingest.ingest_terminal_receipt(session, job, capture)


@pytest.mark.parametrize('error', [None, 'review', 'geometry_unresolved'])
def test_native_workflow_publishes_candidate_surface_without_selecting(monkeypatch, error):
    from src.services.workflow_attempt_guard_service import nonworkflow_execution_context
    session, order, _, row, _ = context_fixture(monkeypatch)
    workflow.initialize_ingest_context(session, order, {'effective_date': '2026-10-18'}, 'job')
    execution = {'ingest_ocr_mode': 'hakodate', 'job_id': 'job', 'order_id': 'order', 'orders': {
        'order': {'workflow_revision': row.revision, 'order': {'template_version_id': order.template_version_id}}}}
    expected_revision = row.revision
    monkeypatch.setattr(workflow, 'lock_actual_write_ownership', lambda *args, **kwargs: True)
    def finalize(session, row):
        assert workflow._expected_workflow_revision.get() == expected_revision
        row.revision += 1
    monkeypatch.setattr(workflow, '_finalize_workflow_mutation', finalize)
    with nonworkflow_execution_context(execution):
        workflow.complete_native_ingest_context(session, order.id, 'job', None if error == 'geometry_unresolved' else 'candidate', error)
    assert row.state == ('ocr_failed' if error else 'ocr_completed')
    assert row.evidence_run_id is row.draft_id is row.confirmed_snapshot_id is None
    assert row.secondary_actions_json['workflow_v2']['ocr_job_id'] == 'job'
    assert 'ocr_job_attempt_id' not in row.secondary_actions_json['workflow_v2']


@pytest.mark.parametrize('hard', [False, True])
def test_native_terminal_metrics_keep_actual_review_distinct_from_hard_failure(monkeypatch, hard):
    from src.services.workflow_attempt_guard_service import nonworkflow_execution_context
    job = Row(id='job', metrics={}, status='running', error_message=None)
    session = Row(get=lambda *args: job, flush=lambda: None)
    monkeypatch.setattr(order_service, '_workflow_attempt_write_fence', lambda *args, **kwargs: True)
    execution = {'job_id': 'job', 'order_id': 'order', 'execution_id': 'execution', 'ingest_ocr_mode': 'hakodate'}
    with nonworkflow_execution_context(execution):
        receipt = order_service._complete_ocr_rerun_in_session(session, 'order', 'job',
            None if hard else {'id': 'diagnostic-evidence'}, error='geometry_unresolved' if hard else 'evidence_unusable',
            metrics_patch={'result_state': 'hard_failed', 'processing_stage': 'native_ingest_failed'} if hard else {
                'recovery_action': 'review_recognition_cells',
                'recognition_review': {'count': 1, 'cells': [
                    {'sheet_cell': 'H17', 'raw_text': '-', 'reason': 'hakodate_ocr_quantity_non_numeric'}]},
            },
            failure_metrics_override=hard)
    assert receipt['status'] == 'failed'
    assert job.metrics['result_state'] == ('hard_failed' if hard else 'review_required')
    assert receipt['evidence_run_id'] == (None if hard else 'diagnostic-evidence')


def test_native_terminal_without_review_details_is_a_hard_failure(monkeypatch):
    from src.services.workflow_attempt_guard_service import nonworkflow_execution_context
    job = Row(id='job', metrics={}, status='running', error_message=None)
    session = Row(get=lambda *args: job, flush=lambda: None)
    monkeypatch.setattr(order_service, '_workflow_attempt_write_fence', lambda *args, **kwargs: True)
    execution = {'job_id': 'job', 'order_id': 'order', 'execution_id': 'execution', 'ingest_ocr_mode': 'hakodate'}
    with nonworkflow_execution_context(execution):
        receipt = order_service._complete_ocr_rerun_in_session(session, 'order', 'job',
            {'id': 'diagnostic-evidence'}, error='review', metrics_patch={})
    assert receipt['status'] == 'failed'
    assert job.metrics['result_state'] == 'hard_failed'
    assert job.metrics['recognition_review'] is None
    assert job.metrics['recovery_action'] is None


@pytest.mark.parametrize('outcome', ['done', 'recognition_review_required'])
def test_native_python_verifier_matches_exact_receipt_without_attempt(outcome):
    from test_stg_recognition_review_verification import case
    evidence, current, *_ = case()
    job = current['ocr_job']
    job.update(error='review', error_message='review')
    job['workflow_attempt_id'] = None
    execution = evidence['payload_json']['_ocr_execution']
    execution['workflow_attempt_id'] = None
    execution['nonworkflow_execution_id'] = 'execution'
    execution['selected_pdf_receipt']['sha256'] = 'a' * 64
    source = evidence['payload_json']['hakodate_ocr_evidence_records'][0]['engine_metadata']['recognizer_output']
    source.update(resolution_state='review_required', quantity_source_issue='hakodate_blank_input_nonempty_prediction')
    if outcome == 'done':
        job.update(status='done', result_state='evidence_ready', processing_stage='evidence_ready',
            error=None, error_message=None, error_detail=None, error_user_message=None)
        evidence['status'] = 'done'
        evidence['payload_json'].pop('hakodate_recognition_review')
        source['resolution_state'] = 'resolved'
    job['native_ingest'] = {'mode': 'hakodate', 'intake_protocol': 'native_ingest_v2', 'execution_id': 'execution', 'terminal_receipt': {
        'job_id': 'job', 'execution_id': 'execution', 'order_id': 'order', 'status': job['status'],
        'evidence_run_id': evidence['id'], 'workflow_attempt_id': None, 'error': None if outcome == 'done' else 'review'}}
    expected = {'sha256': 'a' * 64, 'expected_execution_mode': 'native_ingest', 'required_raw_cells': []}
    assert verifier.native_ingest_outcome(evidence, current, expected) == outcome
    evidence['payload_json']['_ocr_execution']['nonworkflow_execution_id'] = 'foreign'
    assert verifier.native_ingest_outcome(evidence, current, expected) is None


def test_new_generation_does_not_reinitialize_a_saved_legacy_capture(monkeypatch):
    previous = Row(id='old-job', metrics={'nonworkflow_execution': {'execution_id': 'old-execution',
        'ingest_lease': {'id': 'message', 'generation': 'old-generation'}}})
    before = deepcopy(vars(previous))
    @contextmanager
    def session_scope():
        yield Row(execute=lambda *args: None, get=lambda *args, **kwargs: previous)
    monkeypatch.setattr(ingest, 'session_scope', session_scope)
    monkeypatch.setattr(ingest, 'lock_ingest_lease', lambda *args: True)
    with pytest.raises(ValueError, match='original_job_reuse_forbidden'):
        ingest.prepare_ingest_ocr_job({'id': 'message', 'generation': 'new-generation', 'payload': {}}, 'old-job', Row(intake_protocol=None))
    assert vars(previous) == before


def test_native_new_generations_get_distinct_jobs_instead_of_overwriting_failed_job(monkeypatch):
    payload = Row(week_hint='2026-10@2026-10-18~2026-10-24', skip_ocr=False, ocr_job_id=None, message_id='message')
    monkeypatch.setattr(worker, 'parse_ingest_payload', lambda value: payload)
    jobs = []
    monkeypatch.setattr(ingest, 'prepare_ingest_ocr_job', lambda lease, identity, payload: jobs.append(identity) or {'metrics': {}})
    monkeypatch.setattr(worker, '_capture_and_process_ingest', lambda *args, **kwargs: {'id': 'order'})
    for generation in ('generation-1', 'generation-2'):
        monkeypatch.setattr(ingest, 'current_ingest_lease', lambda: {'id': 'message', 'generation': generation})
        worker._process_ingest_inline()
    assert jobs == ['OCR-message-generation-1', 'OCR-message-generation-2']
