from copy import deepcopy

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
