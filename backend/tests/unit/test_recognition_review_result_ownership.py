from copy import deepcopy
from types import SimpleNamespace

import pytest

from src.services import ocr_recognition_recovery_service as recovery


def context(monkeypatch):
    job = SimpleNamespace(id="job", order_id="order", status="done",
                          metrics={"recognition_checkpoint": {"digest": "checkpoint"}, "workflow_attempt_id": "attempt"})
    row = SimpleNamespace(order_id="order", payload_json={
        "_ocr_execution": {"job_id": "job", "evidence_run_id": "result", "workflow_attempt_id": "attempt"},
        "hakodate_recognition_review": {"count": 1, "cells": [{"sheet_cell": "F11"}]},
    })
    meta = {"ocr_job_id": "job", "latest_ocr_result_id": "result"}
    monkeypatch.setattr(recovery.workflow, "_workflow_meta", lambda state: meta)
    session = SimpleNamespace(get=lambda model, key: {"job": job, "result": row}.get(key))
    return session, SimpleNamespace(id="order"), object(), job, row, meta


@pytest.mark.parametrize("status", ["done", "failed"])
def test_done_review_requires_explicit_current_result(monkeypatch, status):
    session, order, state, job, row, _ = context(monkeypatch)
    job.status = status
    before = deepcopy(row.payload_json)
    actual, checkpoint = recovery._failed_checkpoint(session, order, state, "result")
    assert actual is job
    assert checkpoint == job.metrics["recognition_checkpoint"]
    assert checkpoint is not job.metrics["recognition_checkpoint"]
    assert row.payload_json == before
    if status == "done":
        with pytest.raises(ValueError, match="recognition_failed_job_required"):
            recovery._failed_checkpoint(session, order, state)


@pytest.mark.parametrize("mutation", [
    "old_result", "other_order", "other_job", "other_execution", "no_review",
    "empty_review", "bad_count", "running_job", "missing_checkpoint", "other_attempt",
])
def test_review_rejects_wrong_result_or_job_without_mutation(monkeypatch, mutation):
    session, order, state, job, row, meta = context(monkeypatch)
    if mutation == "old_result":
        meta["latest_ocr_result_id"] = "new-result"
    elif mutation == "other_order":
        row.order_id = "other"
    elif mutation == "other_job":
        row.payload_json["_ocr_execution"]["job_id"] = "other"
    elif mutation == "other_execution":
        row.payload_json["_ocr_execution"]["evidence_run_id"] = "other"
    elif mutation == "other_attempt":
        row.payload_json["_ocr_execution"]["workflow_attempt_id"] = "old-attempt"
    elif mutation == "no_review":
        del row.payload_json["hakodate_recognition_review"]
    elif mutation == "empty_review":
        row.payload_json["hakodate_recognition_review"] = {"count": 0, "cells": []}
    elif mutation == "bad_count":
        row.payload_json["hakodate_recognition_review"]["count"] = 2
    elif mutation == "running_job":
        job.status = "running"
    elif mutation == "missing_checkpoint":
        job.metrics = {}
    before = deepcopy(row.payload_json)
    with pytest.raises(ValueError):
        recovery._failed_checkpoint(session, order, state, "result")
    assert row.payload_json == before


def test_failed_job_recovery_remains_available(monkeypatch):
    session, order, state, job, _, _ = context(monkeypatch)
    job.status = "failed"
    assert recovery._failed_checkpoint(session, order, state)[0] is job
