"""Offline real SQL lineage/checkpoint recovery, not recognition accuracy proof."""
from copy import deepcopy
from datetime import date, datetime, timedelta

import numpy as np
import pytest
from sqlalchemy import update

from test_native_ingest_sql_component import (
    native_sql, store, initialize_and_capture, authored_native_payload, snapshot,
    db, delivery, ingest, guard, order_service, workflow, Order, OcrJob,
    IngestJob, IngestDelivery, OrderOcrEvidenceRun, OrderWorkflowState, OrderLine, ocr_job_service,
)
from src.services import ocr_recognition_recovery_service as recovery
from src.services import hakodate_ocr_checkpoint_service as checkpoints


@pytest.fixture
def native_review(native_sql, request):
    f = native_sql
    with delivery.claim_context(f["claim"]), ingest.ingest_execution_context(f["claim"]["lease"]):
        boundary = initialize_and_capture(f)
        with guard.nonworkflow_execution_context(boundary):
            captured = guard.processing_start_read_input()
            seed = {"resume_item": {"quad_px": [[0, 0], [99, 0], [99, 99], [0, 99]],
                                    "quad_source": "authored-component"}}
            item = recovery.current_item(seed, captured,
                {"selected_pdf_receipt": boundary["selected_pdf_receipt"]}, f["order_id"])
            regions = [{**deepcopy(region), "bbox": [1, 1, 8, 8]}
                       for region in item["original_target_semantics"]]
            checkpoint = order_service._persist_hakodate_recognition_checkpoint(
                item=item, raw_rectified_bgr=np.full((100, 100, 3), 255, dtype=np.uint8),
                regions=regions, expected_sheet_cells=item["original_expected_sheet_cells"],
                metadata={"axis_evidence": {"header_intersection_x_match": {}}, "rectified_quad_points": seed["resume_item"]["quad_px"],
                          "snap_debug": {"applied": True}})
            if getattr(request, "param", None) == "source_failure":
                from src.services.storage_service import load_selected_pdf_bytes
                f["source"].write_bytes(b"authored source changed after real checkpoint")
                with pytest.raises(ValueError, match="selected_pdf_bytes_changed") as raised:
                    load_selected_pdf_bytes(str(f["source"]), boundary["selected_pdf_receipt"])
                terminal = ocr_job_service.complete_own_selected_pdf_failure(f["job_id"], str(raised.value))
                assert terminal["evidence_run_id"] is None
                result = {"terminal_receipt": terminal}
            else:
                runtime_failure = getattr(request, "param", None) == "runtime_failure"
                payload = None if runtime_failure else authored_native_payload(f, boundary, "x")
                failure = "authored_runtime_failure" if runtime_failure else "authored_recognition_review"
                result, error = order_service._persist_ocr_rerun_completion(f["order_id"], f["job_id"], payload,
                    error=failure, producer_version="hakodate_best_method_pipeline")
                assert error == failure
            delivery.persist_runner_receipt(f["claim"]["lease"], result["terminal_receipt"])
            order_service.finalize_first_pass_side_effects(job_id=f["job_id"], order_id=f["order_id"],
                message_id=f["claim"]["lease"]["id"])
            assert delivery.acknowledge_claim(f["claim"]) == "business_failed"
    return {**f, "evidence_id": result.get("id"), "checkpoint": checkpoint}


def review(f):
    return recovery.get_review(f["order_id"], ocr_result_id=f["evidence_id"])


def test_native_terminal_review_get_save_and_checkpoint_resume(native_review, monkeypatch):
    f = native_review
    before = snapshot(f)
    result, error = review(f)
    assert error is None
    assert result["cells"] and all(cell["image_png_base64"].startswith("iVBOR") for cell in result["cells"])
    assert snapshot(f) == before
    receipt = result["source_receipt"]
    values = {cell["cell"]: "7" for cell in result["cells"]}
    saved, error = recovery.save_review(f["order_id"], source_receipt=receipt, values=values,
        edited_by="component-operator", expected_revision=receipt["workflow_revision"])
    assert error is None and saved["state"] == "context_confirmed"
    after = snapshot(f)
    for table in before:
        if table != "order_workflow_states":
            assert before[table] == after[table]
    with f["factory"]() as session:
        row = session.get(OrderWorkflowState, f["order_id"])
        override = workflow._workflow_meta(row)["recognition_override"]
        assert override["values"] == values and override["source_receipt"] == receipt
        assert (row.evidence_run_id, row.draft_id, row.confirmed_snapshot_id) == (None, None, None)
        current = guard.capture_reparse_read_input(session, session.get(Order, f["order_id"]))
        item = recovery.current_item(override["checkpoint"], current, receipt, f["order_id"])
    assert checkpoints.resume_recognizer_from_checkpoint(checkpoint=override["checkpoint"], item=item,
        recognize=lambda **state: state["expected_sheet_cells"]) == list(values)
    # The owner remains readable after the legitimate review metadata revision.
    assert review(f)[1] is None
    rejected, error = recovery.save_review(f["order_id"], source_receipt=receipt, values={cell: "8" for cell in values},
        edited_by="component-operator", expected_revision=receipt["workflow_revision"])
    assert error is not None
    assert snapshot(f) == after
    second, error = review(f)
    assert error is None
    second_receipt = second["source_receipt"]
    second_values = {cell: "8" for cell in values}
    saved, error = recovery.save_review(f["order_id"], source_receipt=second_receipt, values=second_values,
        edited_by="component-operator", expected_revision=second_receipt["workflow_revision"])
    assert error is None and saved["revision"] == second_receipt["workflow_revision"] + 1
    assert second_receipt["native_recognition_owner"] == receipt["native_recognition_owner"]
    assert review(f)[1] is None
    # Queue a real new workflow attempt. It carries the authored values/checkpoint,
    # not the completed ingest lease, and never fabricates an old attempt ID.
    from src.services import workflow_ocr_delivery_dispatch_service as dispatch
    from src.services import ingest_delivery_dispatch_service as ingest_dispatch
    for key, value in ingest_dispatch.required_config().items():
        monkeypatch.setenv(dispatch.PREFIX + key, value)
    queued, error = workflow.mark_ocr_run_queued(f["order_id"], "authored-recovery-job",
        input_reference=str(f["source"]), request_mode="ocr_rerun", expected_revision=saved["revision"])
    assert error is None
    with f["factory"]() as session:
        job = session.get(OcrJob, "authored-recovery-job")
        assert job.metrics["workflow_attempt_id"]
        assert "nonworkflow_execution" not in job.metrics
        captured = job.metrics["reparse_read_input"]
        attempt = job.metrics["workflow_attempt_id"]
        revision = job.metrics["workflow_completion_revision"]
        override = captured["workflow"]["secondary_actions_json"]["workflow_v2"]["recognition_override"]
        assert override["values"] == second_values
        item = recovery.current_item(override["checkpoint"], captured,
            {"selected_pdf_receipt": job.metrics["selected_pdf_receipt"]}, f["order_id"])
    assert checkpoints.load_recognizer_checkpoint(checkpoint=override["checkpoint"], item=item)["expected_sheet_cells"] == list(values)
    from src.hakodate_best_method_runtime import render_best_method_overlay_all_facilities as runtime
    from src.services import workflow_ocr_delivery_service as workflow_delivery
    monkeypatch.setattr(runtime, "_build_preprocess_for_ocr", lambda **kw: pytest.fail("saved checkpoint reprocessed"))
    monkeypatch.setattr(runtime, "_run_text_recognizer_records", lambda **kw: pytest.fail("operator values recognized again"))
    claim = workflow_delivery.claim_delivery(queued["delivery"]["id"], queued["delivery"]["generation"],
        lease_expires_at=datetime.utcnow() + timedelta(minutes=5))
    assert claim["job_id"] == "authored-recovery-job"
    with workflow_delivery.claim_context(claim), order_service.workflow_ocr_write_context(
            attempt, revision, "authored-recovery-job", f["order_id"]):
        payload = order_service._hakodate_canonical_payload_from_manifest_item(
            order_id=f["order_id"], facility_id=captured["order"]["facility_code"], item={})
        completed, error = order_service._persist_ocr_rerun_completion(f["order_id"], "authored-recovery-job", payload,
            producer_version="hakodate_best_method_pipeline")
        assert error is None
        assert completed["terminal_receipt"]["workflow_attempt_id"] == attempt
        assert completed["terminal_receipt"]["execution_id"] is None
        assert workflow_delivery.acknowledge_claim(claim["delivery_id"], claim["generation"], claim["claim_token"])
    records = payload["hakodate_ocr_evidence_records"]
    assert len(records) == len(second_values)
    assert all(record["raw_text"] == "8" and record["engine"] == "operator" for record in records)
    assert all(record["engine_metadata"]["edited_by"] == "component-operator" for record in records)
    with f["factory"]() as session:
        state = session.get(OrderWorkflowState, f["order_id"])
        assert (state.evidence_run_id, state.draft_id, state.confirmed_snapshot_id) == (None, None, None)
        assert session.get(OcrJob, f["job_id"]).metrics["recognition_checkpoint"] == f["checkpoint"]
    assert review(f)[1] == "recognition_review_result_changed"


def test_native_review_actual_http_get(native_review):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from src.api import orders
    from src.api import auth
    f = native_review
    app = FastAPI()
    app.include_router(orders.router, prefix="/orders")
    # Auth is outside this offline mechanics proof; provide an explicit actor,
    # rather than pretending AUTH_DISABLED's anonymous user is an operator edit.
    app.dependency_overrides[auth.get_current_operator] = lambda: auth.UserContext(role="operator", account="component-operator")
    before = snapshot(f)
    with TestClient(app) as client:
        response = client.get(f"/orders/{f['order_id']}/workflow-v2/recognition-review",
            params={"ocr_result_id": f["evidence_id"]})
        assert response.status_code == 200, response.text
        assert response.json()["source_receipt"]["recognition_evidence_run_id"] == f["evidence_id"]
        assert response.json()["cells"]
        assert snapshot(f) == before
        receipt = response.json()["source_receipt"]
        saved = client.put(f"/orders/{f['order_id']}/workflow-v2/recognition-review",
            headers={"If-Match": str(receipt["workflow_revision"])},
            json={"source_receipt": receipt, "values": {cell["cell"]: "7" for cell in response.json()["cells"]}})
        assert saved.status_code == 200, saved.text
        assert saved.json()["state"] == "context_confirmed"
    after = snapshot(f)
    for table in before:
        if table != "order_workflow_states":
            assert before[table] == after[table]


@pytest.mark.parametrize("damage", [
    "capture", "mode", "attempt", "execution", "terminal", "delivery", "generation", "payload",
    "document", "evidence", "source", "checkpoint", "lease", "latest_result", "capture_snapshot",
    "capture_job", "start_digest", "missing_start", "selected_pdf", "operator_line", "selected_result",
    "null_start", "null_processing_start", "null_terminal", "missing_mode",
])
def test_native_review_rejects_tampered_owner_without_mutation(native_review, damage):
    f = native_review
    initial, error = review(f)
    assert error is None
    with f["factory"]() as session:
        job = session.get(OcrJob, f["job_id"])
        metrics = deepcopy(job.metrics)
        if damage == "capture":
            metrics.pop("nonworkflow_execution")
        elif damage == "mode":
            metrics["nonworkflow_execution"]["ingest_ocr_mode"] = "registration_only"
        elif damage == "attempt":
            metrics["workflow_attempt_id"] = "foreign-attempt"
        elif damage == "execution":
            metrics["nonworkflow_execution"]["execution_id"] = "foreign-execution"
        elif damage == "capture_job":
            metrics["nonworkflow_execution"]["job_id"] = "foreign-job"
        elif damage == "start_digest":
            metrics["nonworkflow_execution"]["start_orders"][f["order_id"]]["input_sha256"] = "0" * 64
        elif damage == "missing_start":
            metrics["nonworkflow_execution"].pop("start_orders")
        elif damage == "null_start":
            metrics["nonworkflow_execution"]["start_orders"] = None
        elif damage == "null_processing_start":
            metrics["nonworkflow_execution"]["start_orders"][f["order_id"]]["processing_start_input"] = None
        elif damage == "null_terminal":
            metrics["rerun_terminal_receipt"] = None
        elif damage == "missing_mode":
            metrics.pop("ingest_ocr_mode")
            metrics["nonworkflow_execution"].pop("ingest_ocr_mode")
        elif damage == "selected_pdf":
            metrics["nonworkflow_execution"]["selected_pdf_receipt"]["document_id"] = "foreign-document"
        elif damage == "operator_line":
            session.add(OrderLine(id="authored-operator-line", order_id=f["order_id"], date=date(2026, 10, 7),
                daypart="昼食", menu_name="Operator authored", diet_type="regular", quantity_original=3,
                quantity_corrected=7, change_note="Operator correction"))
        elif damage == "selected_result":
            session.get(OrderWorkflowState, f["order_id"]).evidence_run_id = f["evidence_id"]
        elif damage == "terminal":
            metrics.pop("rerun_terminal_receipt")
        elif damage == "lease":
            metrics["nonworkflow_execution"]["ingest_lease"]["generation"] = "foreign-generation"
        elif damage == "capture_snapshot":
            metrics["nonworkflow_execution"]["orders"][f["order_id"]]["document"]["storage_uri"] = "foreign.pdf"
        elif damage == "latest_result":
            state = session.get(OrderWorkflowState, f["order_id"])
            meta = workflow._workflow_meta(state)
            meta["latest_ocr_result_id"] = "foreign-result"
            workflow._write_workflow_meta(state, meta)
        elif damage == "checkpoint":
            metrics["recognition_checkpoint"]["image"]["sha256"] = "0" * 64
        elif damage == "delivery":
            session.execute(update(IngestDelivery).where(IngestDelivery.id == f["claim"]["delivery_id"])
                .values(terminal_receipt=None))
        elif damage in {"generation", "payload"}:
            original = session.get(IngestJob, f["claim"]["lease"]["id"])
            if damage == "generation":
                original.generation = "foreign-generation"
            else:
                original.payload = {**original.payload, "week_hint": "changed"}
        elif damage == "document":
            session.get(Order, f["order_id"]).current_document_id = None
        elif damage == "evidence":
            row = session.get(OrderOcrEvidenceRun, f["evidence_id"])
            payload = deepcopy(row.payload_json)
            payload["_ocr_execution"]["nonworkflow_execution_id"] = "foreign-execution"
            row.payload_json = payload
        elif damage == "source":
            f["source"].write_bytes(b"changed authored source")
        session.execute(update(OcrJob).where(OcrJob.id == job.id).values(metrics=metrics))
        session.commit()
    before = snapshot(f)
    result, error = review(f)
    assert result is None and error
    assert snapshot(f) == before
    receipt = initial["source_receipt"]
    result, error = recovery.save_review(f["order_id"], source_receipt=receipt,
        values={cell["cell"]: "7" for cell in initial["cells"]}, edited_by="component-operator",
        expected_revision=receipt["workflow_revision"])
    if damage in {"latest_result", "selected_result"}:
        assert error == workflow.WORKFLOW_REVISION_CONFLICT
    else:
        assert result is None and error
    assert snapshot(f) == before


def test_native_review_save_rejects_missing_or_forged_get_owner(native_review):
    f = native_review
    result, error = review(f)
    assert error is None
    before = snapshot(f)
    for proof in (None, "forged"):
        receipt = deepcopy(result["source_receipt"])
        if proof is None:
            receipt.pop("native_recognition_owner")
        else:
            receipt["native_recognition_owner"] = proof
        saved, error = recovery.save_review(f["order_id"], source_receipt=receipt,
            values={cell["cell"]: "7" for cell in result["cells"]}, edited_by="component-operator",
            expected_revision=receipt["workflow_revision"])
        assert saved is None and error == "recognition_native_owner_changed"
        assert snapshot(f) == before


@pytest.mark.parametrize("native_review", ["runtime_failure"], indirect=True)
def test_native_runtime_checkpoint_without_evidence_get_save(native_review):
    f = native_review
    assert f["evidence_id"] is None
    result, error = review(f)
    assert error is None
    assert "recognition_evidence_run_id" not in result["source_receipt"]
    before = snapshot(f)
    saved, error = recovery.save_review(f["order_id"], source_receipt=result["source_receipt"],
        values={cell["cell"]: "7" for cell in result["cells"]}, edited_by="component-operator",
        expected_revision=result["source_receipt"]["workflow_revision"])
    assert error is None and saved["state"] == "context_confirmed"
    after = snapshot(f)
    for table in before:
        if table != "order_workflow_states":
            assert before[table] == after[table]


@pytest.mark.parametrize("native_review", ["source_failure"], indirect=True)
@pytest.mark.parametrize("missing_rerun", [False, True])
def test_actual_native_source_failure_is_typed_blocker_not_alternative_result(native_review, missing_rerun):
    f = native_review
    if missing_rerun:
        with f["factory"]() as session:
            metrics = deepcopy(session.get(OcrJob, f["job_id"]).metrics)
            metrics.pop("rerun_terminal_receipt")
            session.execute(update(OcrJob).where(OcrJob.id == f["job_id"]).values(metrics=metrics))
            session.commit()
    before = snapshot(f)
    result, error = review(f)
    assert result is None and error
    assert snapshot(f) == before
