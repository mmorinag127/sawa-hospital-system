from copy import deepcopy
from types import SimpleNamespace

import pytest

from src.models.ocr_job import OcrJob
from src.services.ocr_failure_diagnostics import recognition_failure_details
from src.services.order_workflow_v2_service import _serialize_ocr_job


@pytest.mark.parametrize("reason,raw", [
    ("hakodate_ocr_quantity_non_numeric", "--"),
    ("hakodate_ocr_quantity_low_confidence", "12"),
    ("hakodate_blank_input_nonempty_prediction", "1"),
])
def test_rejection_explains_exact_cells_without_altering_recognition(reason, raw):
    payload = {"hakodate_recognition_review": {"count": 1, "cells": [
        {"sheet_cell": "H17", "raw_text": raw, "reason": reason},
    ]}}
    before = deepcopy(payload)
    details = recognition_failure_details(payload, evidence_run_id="own-evidence")
    assert details["recognition_review"] == payload["hakodate_recognition_review"]
    assert details["evidence_run_id"] == "own-evidence"
    assert "1セル" in details["error_user_message"]
    assert details["recovery_action"] == "review_recognition_cells"
    assert payload == before


@pytest.mark.parametrize("review", [None, {}, {"cells": [], "count": 0},
    {"cells": [{"sheet_cell": "H17", "raw_text": "-", "reason": "non_numeric"}], "count": 2}])
def test_absent_or_malformed_review_cannot_be_reported_as_recognition_completed(review):
    assert recognition_failure_details({"hakodate_recognition_review": review}, evidence_run_id="own") == {}


@pytest.mark.parametrize("mismatch", [None, "job", "order", "evidence", "disposition"])
def test_existing_failure_read_uses_only_its_own_rejected_evidence(mismatch):
    job = OcrJob(id="job", order_id="order", status="failed", error_message="evidence_unusable",
                 metrics={"evidence_run_id": "evidence", "result_state": "review_required", "recovery_action": "review_recognition_cells"})
    evidence = SimpleNamespace(id="evidence", order_id="order", payload_json={
        "_ocr_execution": {"job_id": "job", "evidence_run_id": "evidence", "disposition": "rejected"},
        "hakodate_recognition_review": {"count": 1, "cells": [
            {"sheet_cell": "H17", "raw_text": "-", "reason": "hakodate_ocr_quantity_non_numeric"},
        ]},
    })
    if mismatch == "order":
        evidence.order_id = "different-order"
    elif mismatch is not None:
        key = {"job": "job_id", "evidence": "evidence_run_id", "disposition": "disposition"}[mismatch]
        evidence.payload_json["_ocr_execution"][key] = "different"
    before = deepcopy(job.metrics)
    serialized = _serialize_ocr_job(job, session=SimpleNamespace(get=lambda model, key: evidence))
    assert bool(serialized["recognition_review"]) is (mismatch is None)
    assert serialized["status"] == "failed"
    assert serialized["error_message"] == "evidence_unusable"
    assert job.metrics == before


@pytest.mark.parametrize("error,recovery,result", [("database_write_failed", "review_recognition_cells", "review_required"),
    ("evidence_unusable", "review_facility_template", "review_required"),
    ("evidence_unusable", "review_recognition_cells", "hard_failed")])
def test_real_failure_cannot_be_replaced_by_review_diagnostics(error, recovery, result):
    job = OcrJob(id="job", order_id="order", status="failed", error_message=error,
                 metrics={"evidence_run_id": "evidence", "result_state": result, "recovery_action": recovery,
                          "error_user_message": "保存処理に失敗しました"})
    def unexpected_lookup(*args):
        pytest.fail("hard failure must not consult review evidence")
    serialized = _serialize_ocr_job(job, session=SimpleNamespace(get=unexpected_lookup))
    assert serialized["error_user_message"] == (None if recovery == "review_recognition_cells" else "保存処理に失敗しました")
    assert serialized["recognition_review"] is None


def test_existing_stale_review_is_not_exposed_for_a_real_failure():
    job = OcrJob(id="job", order_id="order", status="failed", error_message="database_write_failed",
                 metrics={"result_state": "review_required", "recovery_action": "review_recognition_cells",
                          "recognition_review": {"count": 1}, "error_user_message": "文字認識は完了しました"})
    serialized = _serialize_ocr_job(job)
    assert serialized["recovery_action"] is None
    assert serialized["error_user_message"] is None
    assert serialized["recognition_review"] is None


def test_real_recognition_checkpoint_still_offers_its_explicit_recovery():
    job = OcrJob(id="job", order_id="order", status="failed", error_message="hakodate_recognizer_failed_after_checkpoint",
                 metrics={"result_state": "hard_failed", "recovery_action": "review_recognition_cells",
                          "recognition_checkpoint": {"uri": "gs://synthetic/checkpoint"},
                          "error_user_message": "文字認識で停止しました"})
    serialized = _serialize_ocr_job(job)
    assert serialized["recovery_action"] == "review_recognition_cells"
    assert serialized["error_user_message"] == "文字認識で停止しました"
    assert serialized["recognition_review"] is None
