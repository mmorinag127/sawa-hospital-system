import ast
from copy import deepcopy
from pathlib import Path

import pytest

from test_stg_ocr_resume_upload import mod


def case():
    job = {"ocr_job_id": "job", "workflow_attempt_id": "attempt", "order_id": "order",
           "order_document_id": "pdf", "status": "failed", "result_state": "review_required",
           "recovery_action": "review_recognition_cells"}
    workflow = {"order_id": "order", "ocr_job": job}
    cells = [{"sheet_cell": "F11", "raw_text": "1", "reason": "hakodate_blank_input_nonempty_prediction"}]
    evidence = {"id": "result", "status": "diagnostic_only", "selectable": False,
                "producer_version": "hakodate_best_method_pipeline", "payload_json": {
        "_ocr_execution": {"job_id": "job", "workflow_attempt_id": "attempt", "evidence_run_id": "result",
                           "selected_pdf_receipt": {"order_id": "order", "document_id": "pdf"}},
        "hakodate_recognition_review": {"cells": cells, "count": 1},
        "hakodate_ocr_evidence_records": [{"raw_text": "1", "engine_metadata": {"sheet_cell": "F11",
            "recognizer_output": {"recognizer_raw_text": "1", "recognizer_input_evidence": {"all_white": True}}}}],
    }}
    return evidence, workflow, deepcopy(workflow), "order", "pdf", "old-result"


def test_review_outcome_is_not_normal_ocr_success():
    args = case()
    assert mod.review_required_evidence(*args)
    assert not mod.completed_evidence(*args)


def test_review_verifier_requests_only_the_exact_own_evidence_id(monkeypatch):
    evidence, workflow, started, *_ = case()
    evidence['order_id'] = 'order'
    calls = []
    def request(path):
        calls.append(path)
        if path.endswith('/ocr-results'):
            return {'results': [{'ocr_result_id': evidence['id'], 'execution_lineage': evidence['payload_json']['_ocr_execution']}]}
        return evidence
    monkeypatch.setattr(mod, 'request', request)
    assert mod.fetch_own_evidence('order', workflow, started) == evidence
    assert calls == ['/orders/order/workflow-v2/ocr-results', '/orders/order/workflow-v2/ocr-results/result']


def test_review_verifier_does_not_fetch_a_candidate_without_own_evidence_id(monkeypatch):
    evidence, workflow, started, *_ = case()
    called = []
    monkeypatch.setattr(mod, 'request', lambda path: called.append(path) or evidence)
    del started['ocr_job']['ocr_job_id']
    assert mod.fetch_own_evidence('order', workflow, started) is None
    assert called == []


def test_review_verifier_rejects_multiple_candidates_for_own_execution(monkeypatch):
    evidence, workflow, started, *_ = case()
    monkeypatch.setattr(mod, 'request', lambda path: {'results': [
        {'ocr_result_id': 'one', 'execution_lineage': evidence['payload_json']['_ocr_execution']},
        {'ocr_result_id': 'two', 'execution_lineage': evidence['payload_json']['_ocr_execution']},
    ]})
    with pytest.raises(ValueError, match='Multiple OCR evidence candidates'):
        mod.fetch_own_evidence('order', workflow, started)


def test_exact_evidence_retrieval_is_order_and_template_scoped_readonly():
    root = Path(__file__).resolve().parents[3]
    service = ast.parse((root / 'backend/src/services/order_workflow_v2_service.py').read_text())
    function = next(node for node in service.body if isinstance(node, ast.FunctionDef) and node.name == 'get_ocr_result')
    source = ast.unparse(function)
    assert 'OrderOcrEvidenceRun.id == normalized_result_id' in source
    assert 'OrderOcrEvidenceRun.order_id == order.id' in source
    assert 'OrderOcrEvidenceRun.template_version_id == workflow_template_version_id' in source
    assert '_serialize_evidence_run(row, include_payload=True)' in source
    api = (root / 'backend/src/api/orders.py').read_text()
    assert '@router.get("/{order_id}/workflow-v2/ocr-results/{ocr_result_id}"' in api


@pytest.mark.parametrize("changed", ["job", "attempt", "pdf", "status", "selectable", "count", "cells", "white", "recovery", "state"])
def test_other_failure_or_stale_evidence_never_passes_review_verification(changed):
    args = case()
    evidence, workflow, *_ = args
    if changed in {"job", "attempt", "pdf"}:
        execution = evidence["payload_json"]["_ocr_execution"]
        if changed == "pdf":
            execution["selected_pdf_receipt"]["document_id"] = "wrong"
        else:
            execution[{"job": "job_id", "attempt": "workflow_attempt_id"}[changed]] = "wrong"
    elif changed == "status":
        evidence["status"] = "done"
    elif changed == "selectable":
        evidence["selectable"] = True
    elif changed == "count":
        evidence["payload_json"]["hakodate_recognition_review"]["count"] = 0
    elif changed == "cells":
        evidence["payload_json"]["hakodate_recognition_review"]["cells"][0]["sheet_cell"] = "E11"
    elif changed == "white":
        evidence["payload_json"]["hakodate_ocr_evidence_records"][0]["engine_metadata"]["recognizer_output"]["recognizer_input_evidence"]["all_white"] = False
    elif changed == "recovery":
        workflow["ocr_job"]["recovery_action"] = "other_failure"
    elif changed == "state":
        workflow["ocr_job"]["result_state"] = "error"
    assert not mod.review_required_evidence(*args)
