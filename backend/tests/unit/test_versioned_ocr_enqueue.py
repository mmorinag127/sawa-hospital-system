"""Local isolated schema/API enqueue proof, not OCR execution or live proof."""

import json
import os
from pathlib import Path

import pytest
from fastapi import HTTPException

from test_h2_operator_repair_revision import repair_order
from src.api import orders
from src.db import session_scope
from src.models.order import Order
from src.models.ocr_job import OcrJob
from src.services import config_service
from src.services import order_workflow_v2_service as workflow


@pytest.fixture
def version_only(repair_order, monkeypatch):
    for key, value in {
        'PROJECT_ID': 'unit-versioned-enqueue', 'LOCATION': 'asia-northeast2',
        'QUEUE': 'unit-enqueue', 'TARGET_URL': 'https://reserved.invalid',
        'SERVICE_ACCOUNT_EMAIL': 'unit@unit-versioned-enqueue.iam.gserviceaccount.com',
        'AUDIENCE': 'https://reserved.invalid', 'DISPATCH_DEADLINE_SECONDS': '300',
    }.items():
        monkeypatch.setenv('HOSPITAL_WORKFLOW_OCR_DELIVERY_' + key, value)
    order_id, revision, _ = repair_order
    path = Path(os.environ['FACILITY_MASTER_PATH'])
    master = json.loads(path.read_text())
    root = master['facilities'][0]
    del root['fax_template']
    root.pop('fax_template_id', None)
    path.write_text(json.dumps(master))
    with pytest.raises(ValueError, match='authored_fax_template_missing'):
        config_service.get_facility_config('fixture')
    confirmed, error = workflow.confirm_context(
        order_id=order_id, facility_id='fixture', week_start='2026-10-05',
        week_end='2026-10-11', template_id='own', expected_revision=revision)
    assert error is None
    dispatched = []
    monkeypatch.setattr(orders, '_dispatch_workflow_ocr_delivery', dispatched.append)
    return order_id, confirmed['revision'], confirmed['template_version_id'], dispatched


def enqueue(kind, order_id, revision):
    if kind == 'hakodate':
        return orders.run_order_workflow_v2_ocr(
            order_id, orders.WorkflowV2OcrRunBody(mode='hakodate'), expected_revision=revision)
    if kind == 'rerun':
        return orders._enqueue_workflow_v2_evidence_rerun(order_id, expected_revision=revision)
    return orders._enqueue_order_reparse_job(
        order_id, request_mode='ocr_reparse', expected_revision=revision)


@pytest.mark.parametrize('kind', ['hakodate', 'rerun', 'reparse'])
def test_version_only_confirmed_master_enqueues(version_only, kind):
    order_id, revision, projection, dispatched = version_only
    result = enqueue(kind, order_id, revision)
    assert result['accepted'] is True
    assert result['workflow']['template_version_id'] == projection
    assert len(dispatched) == 1
    with session_scope() as session:
        job = session.get(OcrJob, result['ocr_job_id'])
        assert job.template_version_id == projection
        assert job.metrics['reparse_read_input']['order']['template_version_id'] == projection


@pytest.mark.parametrize('kind', ['hakodate', 'rerun', 'reparse'])
@pytest.mark.parametrize('fault,reason', [
    ('missing', 'template_version_required'), ('mismatch', 'template_version_mismatch'),
    ('authored_missing', 'template_version_mismatch'),
    ('ambiguous', 'template_version_mismatch'),
])
def test_version_only_invalid_selection_blocks(version_only, kind, fault, reason):
    order_id, revision, selected_version, dispatched = version_only
    if fault in {'authored_missing', 'ambiguous'}:
        path = Path(os.environ['FACILITY_MASTER_PATH'])
        master = json.loads(path.read_text())
        versions = master['facilities'][0]['versions']
        master['facilities'][0]['versions'] = [] if fault == 'authored_missing' else versions + versions
        path.write_text(json.dumps(master))
    else:
        with session_scope() as session:
            from tests.helpers.invalid_template_projection import add_invalid_projection
            session.get(Order, order_id).template_version_id = (None if fault == 'missing' else
                add_invalid_projection(session, selected_version))
    with pytest.raises(HTTPException) as error:
        enqueue(kind, order_id, revision)
    assert reason in str(error.value.detail)
    assert not dispatched
    with session_scope() as session:
        assert session.get(OcrJob, 'OCR-' + order_id) is None
