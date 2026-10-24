from copy import deepcopy
from datetime import datetime
import json
import os
from pathlib import Path

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.dialects import postgresql, sqlite

from src.db import session_scope
from src.models.order import Order
from src.models.order_ocr_evidence_run import OrderOcrEvidenceRun
from src.models.order_workflow_state import OrderWorkflowState
from src.services import ocr_evidence_service, order_workflow_v2_service as service
from src.services.ocr_history_read import history_statement, read_history
from test_order_workflow_v2_service import (
    _create_order_with_evidence, _standard_active_template_for_workflow_tests,
    authored_april_context, test_inspection_is_read_only_projection_of_current_lineage as seed_saved,
)


def full_history(session, order_id):
    return session.query(OrderOcrEvidenceRun).filter_by(order_id=order_id).order_by(
        OrderOcrEvidenceRun.created_at.desc(), OrderOcrEvidenceRun.id.desc()).all()


def outcome(call):
    try:
        return ('value', call())
    except Exception as exc:
        return ('error', type(exc).__name__, str(exc))


@pytest.mark.parametrize('payload', [
    {'diagnostic_pipeline_output': {'large': ['x'] * 1000}, 'raw': ['1', '26', '0'],
     'nested': {'diagnostic_pipeline_output': 'retain'}, 'unknown': None},
    {'raw': 'unchanged'}, {}, None, [], [{'diagnostic_pipeline_output': 'retain'}],
    {'diagnostic_pipeline_output': {}},
    {'diagnostic_pipeline_output': {}, 'unicode': '\u0000', 'unknown': {'text': '日本語'}},
    'diagnostic_pipeline_output', 123, 0, True, False,
])
def test_sqlite_only_removes_top_level_archive_without_orm_or_storage_mutation(payload):
    order, first, _ = _create_order_with_evidence()
    with session_scope() as session:
        session.get(OrderOcrEvidenceRun, first).payload_json = deepcopy(payload)
    with session_scope() as session:
        before = session.get(OrderOcrEvidenceRun, first)
        projected = next(row for row in read_history(session, order) if row.id == first)
        expected = {k: v for k, v in payload.items() if k != 'diagnostic_pipeline_output'} if isinstance(payload, dict) else payload
        assert projected.payload_json == expected
        assert inspect(projected, raiseerr=False) is None
        assert session.get(OrderOcrEvidenceRun, first) is before
        assert before.payload_json == payload
        assert outcome(lambda: service._serialize_ocr_result(before, selected=True)) == outcome(
            lambda: service._serialize_ocr_result(projected, selected=True))
        assert outcome(lambda: ocr_evidence_service.evidence_run_selectability_error(before)) == outcome(
            lambda: ocr_evidence_service.evidence_run_selectability_error(projected))
        assert not session.dirty
    with session_scope() as session:
        # Projection-first must not poison the identity map on a later exact-ID read.
        read_history(session, order)
        assert not any(isinstance(row, OrderOcrEvidenceRun) for row in session.identity_map.values())
        assert session.get(OrderOcrEvidenceRun, first).payload_json == payload


def test_postgresql_and_sqlite_query_contract():
    for dialect in [postgresql.dialect(), sqlite.dialect()]:
        statement = history_statement('O')
        text = str(statement.compile(dialect=dialect, compile_kwargs={'literal_binds': True}))
        assert 'json_' not in text and 'CAST(' not in text
        assert "order_ocr_evidence_runs.order_id = 'O'" in text
        assert 'ORDER BY order_ocr_evidence_runs.created_at DESC, order_ocr_evidence_runs.id DESC' in text
        assert ' LIMIT ' not in text and ' OFFSET ' not in text
        assert set(statement.selected_columns.keys()) == set(OrderOcrEvidenceRun.__table__.c.keys())
        assert statement.get_execution_options()['yield_per'] == 4


@pytest.mark.parametrize('raw', [
    r'{"diagnostic_pipeline_output":{},"unknown":"\u0000"}',
    r'{"diagnostic_pipeline_output":{},"unknown":1e1000000}',
    r'{"diagnostic_pipeline_output":{},"unknown":1e-1000000}',
    r'{"diagnostic_pipeline_output":{},"unknown":{"diagnostic_pipeline_output":"keep"}}',
    r'{"diagnostic_pipeline_output":{}}',
    r'"\u0000"', r'1e1000000', r'null',
])
def test_sqlite_raw_json_parser_boundary(raw):
    order_id, evidence_id, _ = _create_order_with_evidence()
    with session_scope() as session:
        session.execute(text('UPDATE order_ocr_evidence_runs SET payload_json=:payload WHERE id=:id'),
                        {'payload': raw, 'id': evidence_id})
    original = json.loads(raw)
    expected = {k: v for k, v in original.items() if k != 'diagnostic_pipeline_output'} if isinstance(original, dict) else original
    with session_scope() as session:
        stored_before = session.execute(text('SELECT payload_json FROM order_ocr_evidence_runs WHERE id=:id'),
                                        {'id': evidence_id}).scalar_one()
        projected = next(row for row in read_history(session, order_id) if row.id == evidence_id)
        assert projected.payload_json == expected
        assert session.execute(text('SELECT payload_json FROM order_ocr_evidence_runs WHERE id=:id'),
                               {'id': evidence_id}).scalar_one() == stored_before


@pytest.mark.parametrize('state', ['no_workflow', 'mixed_version', 'missing_version', 'invalid_version', 'saved'])
def test_complete_api_projection_parity(state, authored_april_context, monkeypatch):
    monkeypatch.setattr(service, '_now', lambda: datetime(2026, 4, 26, 12))
    monkeypatch.setattr(service._get_order_service_module(), '_signed_url_from_uri', lambda uri: 'https://fixed.invalid/image')
    if state == 'saved':
        seed_saved(authored_april_context)
        with session_scope() as session:
            order_id = session.query(OrderWorkflowState).filter(OrderWorkflowState.draft_id.isnot(None)).one().order_id
    else:
        order_id, first, second = _create_order_with_evidence()
        if state != 'no_workflow':
            workflow, error = service.confirm_context(order_id=order_id, expected_revision=0,
                facility_id='FAC00001', week_start='2026-04-26', week_end='2026-04-30', template_id='template-fac00001')
            assert error is None
            with session_scope() as session:
                session.get(OrderOcrEvidenceRun, first).template_version_id = workflow['template_version_id']
                row = session.get(OrderWorkflowState, order_id)
                if state == 'missing_version':
                    row.template_version_id = None
                    row.secondary_actions_json = {}
                    row.state = 'uploaded'
                elif state == 'invalid_version':
                    from tests.helpers.invalid_template_projection import add_invalid_projection
                    row.template_version_id = add_invalid_projection(session, workflow['template_version_id'])
    with session_scope() as session:
        for row in full_history(session, order_id):
            row.payload_json = {**row.payload_json, 'diagnostic_pipeline_output': {'opaque': ['1', '26'] * 1000}}
        before = [(row.id, deepcopy(row.payload_json)) for row in full_history(session, order_id)]
    def reads():
        return [service.list_ocr_results(order_id), service.get_inspection(order_id),
                service.get_inspection(order_id, include_ocr_results=True)]
    with monkeypatch.context() as patch:
        patch.setattr(service, 'read_history', full_history)
        expected = reads()
        exact = [service.get_ocr_result(order_id, row_id) for row_id, _ in before]
    assert reads() == expected
    assert [service.get_ocr_result(order_id, row_id) for row_id, _ in before] == exact
    with session_scope() as session:
        assert [(row.id, row.payload_json) for row in full_history(session, order_id)] == before


def test_real_payload_projection_equivalence(monkeypatch, record_property):
    path = os.environ.get('OCR_HISTORY_REAL_PAYLOAD')
    if not path:
        pytest.skip('optional real artifact: set OCR_HISTORY_REAL_PAYLOAD; synthetic parity always runs')
    payload = json.loads(Path(path).read_text())
    assert 'diagnostic_pipeline_output' in payload
    execution = payload['_ocr_execution']
    order_id, evidence_id = execution['selected_pdf_receipt']['order_id'], execution['evidence_run_id']
    monkeypatch.setattr(service._get_order_service_module(), '_signed_url_from_uri', lambda uri: 'https://fixed.invalid/image')
    with session_scope() as session:
        session.add(Order(id=order_id, facility_code='FAC00015', week_code='2026-10-25', status='test',
                          document_uri='file:///local-parity-only.pdf', message_id=order_id, received_at=datetime(2026, 10, 15)))
        session.add(OrderOcrEvidenceRun(id=evidence_id, order_id=order_id, schema_version='real-parity',
            producer_version='hakodate_best_method_pipeline', source='hakodate', status='diagnostic_only',
            payload_json=payload, artifact_manifest_json=payload['evidence_manifest'],
            artifact_digest=payload['evidence_digest'], created_at=datetime(2026, 10, 15)))
    with session_scope() as session:
        full = session.get(OrderOcrEvidenceRun, evidence_id)
        projected = read_history(session, order_id)[0]
        assert projected.payload_json == {k: v for k, v in payload.items() if k != 'diagnostic_pipeline_output'}
        assert service._serialize_ocr_result(full, selected=False) == service._serialize_ocr_result(projected, selected=False)
        assert ocr_evidence_service.evidence_run_selectability_error(full) == ocr_evidence_service.evidence_run_selectability_error(projected)
        assert full.payload_json == payload
        full_bytes = len(json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode())
        projected_bytes = len(json.dumps(projected.payload_json, ensure_ascii=False, separators=(',', ':')).encode())
        assert projected_bytes < full_bytes
        record_property('full_bytes', full_bytes)
        record_property('projected_bytes', projected_bytes)
        print(f'real payload compact JSON bytes: full={full_bytes}, projected={projected_bytes}')
