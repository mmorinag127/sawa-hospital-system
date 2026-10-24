import json
from datetime import datetime

import pytest
from loguru import logger
from sqlalchemy import event

from src.db import session_scope
from src.models.order_ocr_evidence_run import OrderOcrEvidenceRun
from src.services import order_workflow_v2_service as service
from test_order_workflow_v2_service import (
    _create_order_with_evidence,
    _standard_active_template_for_workflow_tests,
    authored_april_context,
)


@pytest.fixture
def timing_logs():
    messages = []
    sink = logger.add(lambda message: messages.append(message.record['message']),
                      filter=lambda record: record['message'].startswith('inspection_timing '))
    try:
        yield messages
    finally:
        logger.remove(sink)


def metrics(messages):
    assert len(messages) == 1
    result = json.loads(messages[0].removeprefix('inspection_timing '))
    assert set(result) <= {
        'session', 'order', 'workflow_template', 'history_query', 'saved_sheet',
        'artifacts', 'response_serialization', 'workflow_serialize', 'ocr_row_serialize',
        'sign_url', 'selectability', 'candidate_projection', 'session_exit',
    }
    for value in result.values():
        assert set(value) == {'seconds', 'count'}
        assert isinstance(value['seconds'], (int, float)) and value['seconds'] >= 0
        assert isinstance(value['count'], int) and value['count'] >= 0
    assert 'SECRET' not in messages[0]
    assert 'gs://' not in messages[0] and 'https://' not in messages[0]
    return result


@pytest.mark.parametrize('include', [False, True])
def test_inspection_timing_keeps_response_queries_signing_validation_and_storage(
    include, monkeypatch, timing_logs, isolated_formal_schema, authored_april_context,
):
    order_id, first, second = _create_order_with_evidence()
    monkeypatch.setattr(service, '_now', lambda: datetime(2026, 4, 26, 12))
    with session_scope() as session:
        row = session.get(OrderOcrEvidenceRun, first)
        row.payload_json = {**row.payload_json, 'private': 'SECRET_PAYLOAD', 'hakodate_overlay': {
            'uri': 'gs://SECRET/overlay', 'sheet_review_base_uri': 'gs://SECRET/base'}}
        before = [(r.id, r.payload_json) for r in session.query(OrderOcrEvidenceRun).all()]
    signs, validations, queries = [], [], []
    def sign(uri):
        signs.append(uri)
        return 'https://SECRET/signature'
    original = service.ocr_evidence_service.evidence_run_is_selectable
    def validate(row, **kwargs):
        validations.append(row.id)
        return original(row, **kwargs)
    monkeypatch.setattr(service._get_order_service_module(), '_signed_url_from_uri', sign)
    monkeypatch.setattr(service.ocr_evidence_service, 'evidence_run_is_selectable', validate)
    def track(conn, cursor, statement, parameters, context, executemany):
        queries.append(statement)
    event.listen(isolated_formal_schema, 'before_cursor_execute', track)
    try:
        expected = service.get_inspection.__wrapped__(order_id, include_ocr_results=include)
        baseline = (list(signs), list(validations), list(queries))
        signs.clear(); validations.clear(); queries.clear()
        actual = service.get_inspection(order_id, include_ocr_results=include)
        assert actual == expected
        assert (signs, validations, queries) == baseline
    finally:
        event.remove(isolated_formal_schema, 'before_cursor_execute', track)
    assert len(signs) == 2
    assert sorted(validations) == sorted([first, second])
    recorded = metrics(timing_logs)
    assert recorded['history_query']['count'] == 2
    assert recorded['ocr_row_serialize']['count'] == 2
    assert recorded['sign_url']['count'] == 2
    assert recorded['selectability']['count'] == 2
    assert recorded['workflow_serialize']['count'] == 1
    assert ('candidate_projection' in recorded) == include
    assert service._inspection_timing.get() is None
    with session_scope() as session:
        assert [(r.id, r.payload_json) for r in session.query(OrderOcrEvidenceRun).all()] == before


@pytest.mark.parametrize('boundary', [
    '_get_order_or_error', '_get_workflow_readonly', '_serialize_uninitialized_workflow',
    '_serialize_ocr_result', '_project_ocr_candidates', 'sign_url', 'selectability',
])
def test_failure_is_same_exception_and_call_count_without_secret_logging(boundary, monkeypatch, timing_logs, authored_april_context):
    order_id, first, _ = _create_order_with_evidence()
    monkeypatch.setattr(service._get_order_service_module(), '_signed_url_from_uri', lambda uri: 'https://SECRET/fixed')
    with session_scope() as session:
        row = session.get(OrderOcrEvidenceRun, first)
        row.payload_json = {'hakodate_overlay': {'uri': 'gs://SECRET/overlay'}}
    error = RuntimeError('SECRET_EXCEPTION https://SECRET/token')
    calls = []
    def fail(*args, **kwargs):
        calls.append(1)
        raise error
    if boundary == 'sign_url':
        monkeypatch.setattr(service._get_order_service_module(), '_signed_url_from_uri', fail)
    elif boundary == 'selectability':
        monkeypatch.setattr(service.ocr_evidence_service, 'evidence_run_is_selectable', fail)
    else:
        monkeypatch.setattr(service, boundary, fail)
    for function in [service.get_inspection.__wrapped__, service.get_inspection]:
        with pytest.raises(RuntimeError) as raised:
            function(order_id, include_ocr_results=True)
        assert raised.value is error
    assert calls == [1, 1]
    metrics(timing_logs)
    assert service._inspection_timing.get() is None


def test_error_return_and_logging_failure_do_not_change_contract(monkeypatch, timing_logs):
    monkeypatch.setattr(service, '_get_order_or_error', lambda *args: (None, 'SECRET_ERROR'))
    assert service.get_inspection('SECRET_ORDER') == (None, 'SECRET_ERROR')
    metrics(timing_logs)
    def fail_log(*args):
        raise RuntimeError('logger unavailable')
    monkeypatch.setattr(service.logger, 'info', fail_log)
    assert service.get_inspection('SECRET_ORDER') == (None, 'SECRET_ERROR')
    assert service._inspection_timing.get() is None
