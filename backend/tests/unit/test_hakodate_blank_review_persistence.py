"""Real producer/serializer/fenced persistence with external recognition injected."""

from copy import deepcopy
from contextlib import nullcontext
from uuid import uuid4

from PIL import Image
import pytest

from test_h2_operator_repair_revision import connected_physical_pipeline, _capture_reparse_job
from src.db import session_scope
from src.models.order_workflow_state import OrderWorkflowState
from src.models.order_ocr_evidence_run import OrderOcrEvidenceRun
from src.models.ocr_job import OcrJob
from src.models.order import Order
from src.models.document import OrderDocument
from src.services import order_service as orders, ocr_evidence_service as evidence
from src.services import canonical_ocr_artifact_service as artifacts, storage_service
from src.services import workflow_attempt_guard_service as guard
from src.services.hakodate_recognizer_input_evidence import contact_crop_evidence
from src.hakodate_best_method_runtime import render_best_method_overlay_all_facilities as runtime


@pytest.mark.parametrize('full_rerun', [False, True])
def test_real_producer_retains_review_and_fenced_writer_saves_nonselectable_result(connected_physical_pipeline, monkeypatch, full_rerun):
    order_id, _receipt, _start, item, _output = connected_physical_pipeline

    def recognize(*, raw_rectified_bgr, regions, truth):
        image = Image.new('RGB', (80, 80), 'white')
        observed = contact_crop_evidence(image, [[0, 0], [10, 0], [10, 10], [0, 10]])
        return [{**deepcopy(region), 'truth': truth[region['sheet_cell']],
                 'expected_digits': truth[region['sheet_cell']]['expected_digits'],
                 'pred_digits': '1', 'raw_text': '1', 'recognizer_raw_text': '1',
                 'score': .08, 'recognizer_score': .08, 'ocr_contact_crop_box': [0, 0, 10, 10],
                 'recognizer_input_evidence': deepcopy(observed),
                 'quantity_source_issue': 'hakodate_blank_input_nonempty_prediction'} for region in regions], {}, image

    monkeypatch.setattr(runtime, '_run_text_recognizer_records', recognize)
    with session_scope() as session:
        order = session.get(Order, order_id)
        orders._record_current_order_version(session, order, session.get(OrderDocument, order.current_document_id))
        revision = session.get(OrderWorkflowState, order_id).revision
        job_id, attempt = str(uuid4()), str(uuid4())
        start, pdf, template_id = _capture_reparse_job(session, order_id, job_id, attempt)
    item['week_code'] = start['order']['week_code']
    item.update(selected_page=1, quad_px=[[20, 20], [270, 20], [270, 290], [20, 290]],
                quad_source='explicit-fixture', header_axis_override=None, row_axis_override=None)
    if full_rerun:
        with session_scope() as session:
            session.get(OcrJob, job_id).metrics = {}
    variables = [(var, var.set(value)) for var, value in (
        (orders._workflow_ocr_job_id, None if full_rerun else job_id),
        (orders._workflow_ocr_order_id, None if full_rerun else order_id),
        (orders._workflow_ocr_attempt_id, None if full_rerun else attempt),
        (orders._workflow_ocr_revision, None if full_rerun else revision))]
    try:
        with (nullcontext() if full_rerun else guard.workflow_execution_context(job_id, order_id, attempt, revision)):
            if full_rerun:
                saved, error = orders.rerun_ocr_evidence_only(order_id, job_id=job_id,
                    project_sheet=False, refresh_workflow=False, selected_document_id=pdf['document_id'])
                assert error == 'evidence_unusable', (error, saved)
                assert saved['terminal_receipt']['status'] == 'failed'
                payload = saved['payload_json']
            else:
                payload = orders._hakodate_canonical_payload_from_manifest_item(order_id=order_id, facility_id='fixture', item={})
            review = payload['hakodate_recognition_review']
            assert review['count'] == len(review['cells']) == len(item['original_expected_sheet_cells'])
            assert [cell['sheet_cell'] for cell in review['cells']] == item['original_expected_sheet_cells']
            assert all(cell['raw_text'] == '1' for cell in review['cells'])
            assert payload['hakodate_canonical_pipeline']['status'] == 'blocked'
            assert orders._classify_owned_evidence_payload(payload)['persistable'] is False
            if not full_rerun:
                saved = evidence.persist_evidence_run(order_id, payload, template_version_id=template_id,
                    status='diagnostic_only', source='own-recognizer', producer_version='fixture/v1',
                    workflow_job_id=job_id, workflow_attempt_id=attempt, expected_workflow_revision=revision)
            assert saved['status'] == 'diagnostic_only'
            assert saved['payload_json']['hakodate_recognition_review'] == review
            assert all(row['raw_text'] == '1' for row in saved['payload_json']['hakodate_ocr_evidence_records'])
    finally:
        for variable, token in reversed(variables):
            variable.reset(token)
    with session_scope() as session:
        row = session.get(OrderOcrEvidenceRun, saved['id'])
        assert not evidence.evidence_run_is_selectable(row)
        assert evidence._serialize_evidence_run(row, include_payload=True)['payload_json']['hakodate_recognition_review'] == review
        if full_rerun:
            job = session.get(OcrJob, job_id)
            assert job.status == 'failed'
            assert job.metrics['recovery_action'] == 'review_recognition_cells'
            assert job.metrics['recognition_checkpoint']
    records = payload['hakodate_ocr_evidence_records']
    verified_inputs = artifacts.validate_payload(payload)
    orders._validate_hakodate_recognition_review(payload, records, verified_inputs)
    forged_ready = deepcopy(payload)
    forged_ready['hakodate_canonical_pipeline']['status'] = 'ready'
    forged_ready['hakodate_canonical_pipeline']['blockers'] = []
    with pytest.raises(ValueError, match='hakodate_ocr_quantity_unresolved'):
        orders._validate_hakodate_owned_artifacts(forged_ready, payload['hakodate_repair_source_receipt'])
    for mutation in ('issue', 'hash', 'contract', 'summary', 'missing_input'):
        changed = deepcopy(payload)
        source = changed['hakodate_ocr_evidence_records'][0]['engine_metadata']['recognizer_output']
        if mutation == 'issue':
            source.pop('quantity_source_issue')
        elif mutation == 'hash':
            source['recognizer_input_evidence']['rgb_sha256'] = '0' * 64
        elif mutation == 'contract':
            source['recognizer_input_evidence']['contract'] = 'unknown'
        elif mutation == 'summary':
            changed['hakodate_recognition_review']['count'] = 0
        else:
            source.pop('recognizer_input_evidence')
        with pytest.raises(ValueError, match='hakodate_recogn'):
            orders._validate_hakodate_recognition_review(changed, changed['hakodate_ocr_evidence_records'], verified_inputs)
    reads = []
    original_load = storage_service.load_bytes_from_uri
    def observed_load(uri, **kwargs):
        reads.append(uri)
        return original_load(uri, **kwargs)
    monkeypatch.setattr(storage_service, 'load_bytes_from_uri', observed_load)
    for _ in range(2):
        orders._validate_hakodate_owned_artifacts(payload, payload['hakodate_repair_source_receipt'], allow_review=True)
    outputs = payload['hakodate_canonical_pipeline']['outputs']
    assert reads.count(outputs['records']) == 2
    assert reads.count(outputs['contact_sheet']) == 2
    from pathlib import Path
    path = Path(outputs['records'])
    original_bytes = path.read_bytes()
    try:
        path.write_bytes(original_bytes + b' ')
        with pytest.raises(ValueError, match='canonical_artifact_bytes_changed'):
            orders._validate_hakodate_owned_artifacts(payload, payload['hakodate_repair_source_receipt'], allow_review=True)
    finally:
        path.write_bytes(original_bytes)
