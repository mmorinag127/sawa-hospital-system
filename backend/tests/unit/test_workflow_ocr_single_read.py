from datetime import datetime

import pytest
from sqlalchemy import event

from src.db import session_scope
from src.models.order_ocr_evidence_run import OrderOcrEvidenceRun
from src.models.order_workflow_state import OrderWorkflowState
from src.models.order_sheet_draft import OrderSheetDraft
from src.services import order_workflow_v2_service as service
from test_order_workflow_v2_service import (
    _create_order_with_evidence,
    _standard_active_template_for_workflow_tests,
    authored_april_context,
    test_inspection_is_read_only_projection_of_current_lineage as seed_saved_lineage,
)


@pytest.mark.parametrize('context', ['none', 'no_version', 'current', 'mixed', 'invalid'])
def test_combined_exact_parity_and_one_history_read(context, authored_april_context, isolated_formal_schema, monkeypatch):
    monkeypatch.setattr(service, '_now', lambda: datetime(2026, 4, 26, 12))
    signed = []
    def sign(uri):
        signed.append(uri)
        return 'https://signed.example.invalid/fixed/' + uri.rsplit('/', 1)[-1]
    monkeypatch.setattr(service._get_order_service_module(), '_signed_url_from_uri', sign)
    order_id, first, second = _create_order_with_evidence()
    if context != 'none':
        workflow, error = service.confirm_context(
            order_id=order_id, expected_revision=0, facility_id='FAC00001',
            week_start='2026-04-26', week_end='2026-04-30', template_id='template-fac00001',
        )
        assert error is None
        with session_scope() as session:
            for evidence_id in [first, second]:
                session.get(OrderOcrEvidenceRun, evidence_id).template_version_id = workflow['template_version_id']
            if context == 'mixed':
                session.get(OrderOcrEvidenceRun, second).template_version_id = None
            if context in {'no_version', 'invalid'}:
                state = session.get(OrderWorkflowState, order_id)
                from tests.helpers.invalid_template_projection import add_invalid_projection
                state.template_version_id = (None if context == 'no_version' else
                    add_invalid_projection(session, workflow['template_version_id']))
                state.secondary_actions_json = {}
                if context == 'no_version':
                    state.state = 'uploaded'
    with session_scope() as session:
        session.get(OrderOcrEvidenceRun, first).payload_json = {'hakodate_overlay': {
            'uri': 'gs://synthetic/overlay.png', 'sheet_review_base_uri': 'gs://synthetic/base.png'}}
        row = session.get(OrderOcrEvidenceRun, second)
        row.status = 'diagnostic_only'
        row.payload_json = {'canonical_adoption_allowed': False,
                            'hakodate_recognition_review': {'count': 1, 'cells': [
                                {'sheet_cell': 'F11', 'raw_text': '1', 'reason': 'hakodate_blank_input_nonempty_prediction'}]}}
    expected_inspection, expected_error = service.get_inspection(order_id)
    expected_candidates, candidate_error = service.list_ocr_results(order_id)
    assert candidate_error is None
    assert expected_candidates['hidden_template_mismatch_result_count'] == {
        'none': 0, 'no_version': 2, 'current': 0, 'mixed': 1, 'invalid': 2,
    }[context]
    if context == 'invalid':
        assert expected_error == 'template_version_mismatch'
    signed.clear()
    queries, serialized = [], []
    original = service._serialize_ocr_result

    def serialize(row, **kwargs):
        serialized.append(row.id)
        return original(row, **kwargs)

    def track(conn, cursor, statement, parameters, context, executemany):
        if 'FROM order_ocr_evidence_runs' in statement and 'order_ocr_evidence_runs.payload_json' in statement:
            queries.append(statement)

    monkeypatch.setattr(service, '_serialize_ocr_result', serialize)
    event.listen(isolated_formal_schema, 'before_cursor_execute', track)
    try:
        combined, error = service.get_inspection(order_id, include_ocr_results=True)
    finally:
        event.remove(isolated_formal_schema, 'before_cursor_execute', track)
    assert error == expected_error
    if error:
        assert combined is None
        assert serialized == []
    else:
        assert combined.pop('ocr_candidates') == expected_candidates
        assert combined == expected_inspection
        assert len(queries) == 1
        assert sorted(serialized) == sorted([first, second])
        assert signed == ['gs://synthetic/overlay.png', 'gs://synthetic/base.png']
        assert 'ocr_candidates' not in expected_inspection
        assert combined['ocr_results'][0]['selectable'] is False


def test_missing_order_error_is_unchanged():
    assert service.get_inspection('missing-order') == (None, 'order_not_found')
    assert service.get_inspection('missing-order', include_ocr_results=True) == (None, 'order_not_found')


def test_inspection_api_sidecar_is_opt_in(monkeypatch):
    from fastapi.testclient import TestClient
    from src.main import app
    from tests.contract.auth_support import registered_hospital_operator
    calls = []

    def inspection(order_id, *, include_ocr_results=False):
        calls.append((order_id, include_ocr_results))
        return {'order_id': order_id}, None

    monkeypatch.setattr(service, 'get_inspection', inspection)
    client = TestClient(app)
    with registered_hospital_operator(monkeypatch) as operator:
        for suffix, expected in [('', False), ('?include_ocr_results=false', False), ('?include_ocr_results=true', True)]:
            response = client.get('/orders/ORDtest/workflow-v2/inspection' + suffix, headers=operator.headers)
            assert response.status_code == 200
            assert calls[-1] == ('ORDtest', expected)
        assert client.get('/orders/ORDtest/workflow-v2/inspection?include_ocr_results=invalid', headers=operator.headers).status_code == 422
        before = len(calls)
        assert client.get('/orders/ORDtest/workflow-v2/inspection?include_ocr_results=true').status_code == 401
        assert len(calls) == before
    with registered_hospital_operator(monkeypatch, systems=()) as denied:
        before = len(calls)
        assert client.get('/orders/ORDtest/workflow-v2/inspection?include_ocr_results=true', headers=denied.headers).status_code == 403
        assert len(calls) == before


@pytest.mark.parametrize('foreign', [False, True])
def test_saved_old_evidence_and_foreign_draft_linkage_remain_exact(authored_april_context, monkeypatch, foreign):
    monkeypatch.setattr(service, '_now', lambda: datetime(2026, 4, 26, 12))
    seed_saved_lineage(authored_april_context)
    other, foreign_evidence, _ = _create_order_with_evidence()
    with session_scope() as session:
        workflow = session.query(OrderWorkflowState).filter(OrderWorkflowState.draft_id.isnot(None)).one()
        order_id = workflow.order_id
        evidence = session.get(OrderOcrEvidenceRun, workflow.evidence_run_id)
        evidence.template_version_id = None
        draft = session.get(OrderSheetDraft, workflow.draft_id)
        if foreign:
            draft.base_evidence_run_id = foreign_evidence
    inspection, error = service.get_inspection(order_id)
    candidates, candidate_error = service.list_ocr_results(order_id)
    combined, combined_error = service.get_inspection(order_id, include_ocr_results=True)
    assert combined_error == error
    assert candidate_error is None
    if error:
        assert combined is None
    else:
        assert combined.pop('ocr_candidates') == candidates
        assert combined == inspection
        assert all(row['order_id'] == order_id for row in combined['ocr_results'])
        assert not any(row['ocr_result_id'] == foreign_evidence for row in combined['ocr_results'])
