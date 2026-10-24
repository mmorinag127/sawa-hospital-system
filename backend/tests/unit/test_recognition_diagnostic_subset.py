"""Real owned SQL/artifact recovery; model answers are synthetic, not FAX accuracy."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import date, datetime, timedelta
import json
from pathlib import Path
import sqlite3

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
import pytest
from sqlalchemy import event, update
from sqlalchemy.engine import Engine

from test_ingest_delivery_contract import store as original_store
from test_native_ingest_sql_component import (
    native_sql, initialize_and_capture, snapshot, db, delivery, ingest, guard,
    order_service, workflow, Order, OcrJob, OrderOcrEvidenceRun, OrderWorkflowState,
)
from src.services import ocr_recognition_recovery_service as recovery
from src.services import hakodate_ocr_checkpoint_service as checkpoints
from src.services import hakodate_ocr_evidence_service as evidence
from src.services import canonical_ocr_artifact_service as artifacts
from src.services.hakodate_source_receipt import source_reference
from src.hakodate_best_method_runtime import render_best_method_overlay_all_facilities as runtime
from src.hakodate_best_method_runtime import run_text_recognizer_corner_noise_trial as recognizer


@pytest.fixture
def store(tmp_path, monkeypatch):
    def enforce(connection, _record):
        assert isinstance(connection, sqlite3.Connection)
        connection.execute("PRAGMA foreign_keys=ON")
        assert connection.execute("PRAGMA foreign_keys").fetchone() == (1,)
        assert all(not row[2] or Path(row[2]).resolve().is_relative_to(tmp_path.resolve())
                   for row in connection.execute("PRAGMA database_list"))
    event.listen(Engine, "connect", enforce)
    try:
        yield from original_store.__wrapped__(tmp_path, monkeypatch)
    finally:
        event.remove(Engine, "connect", enforce)


def produce_diagnostic(f, monkeypatch):
    captured = guard.processing_start_read_input()
    pdf = guard.processing_start_pdf_receipt()
    quad = [[0, 0], [799, 0], [799, 1199], [0, 1199]]
    item = recovery.current_item({"resume_item": {"quad_px": quad, "quad_source": "authored-component"}},
                                 captured, {"selected_pdf_receipt": pdf}, f["order_id"])
    cells = item["original_expected_sheet_cells"]
    assert len(cells) >= 14, "The authored plan must retain all ten symbol values and four good values"
    values = ["1", "26", "0", ""] + ["-"] * 8 + ["--"] * 2 + [""] * (len(cells) - 14)
    image = Image.new("RGB", (800, 1200), "white")
    draw = ImageDraw.Draw(image)
    regions = []
    for index, (region, value) in enumerate(zip(item["original_target_semantics"], values)):
        x, y = (index % 8) * 100, (index // 8) * 70
        assert y + 60 <= image.height
        regions.append({**deepcopy(region), "bbox": [float(x), float(y), float(x + 90), float(y + 60)]})
        if value:
            draw.text((x + 25, y + 15), value, fill="black", font=ImageFont.load_default(size=25))
    checkpoint = order_service._persist_hakodate_recognition_checkpoint(item=item,
        raw_rectified_bgr=cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR), regions=regions,
        expected_sheet_cells=cells, metadata={"axis_evidence": {}, "rectified_quad_points": quad,
                                            "snap_debug": {"applied": True}})
    class Wrapper:
        def __init__(self, *args, **kwargs):
            pass
        def recognize(self, pixels, *, points):
            assert len(points) == len(values)
            return {"contents": values, "scores": [0.99] * len(values),
                    "candidates": [[{"text": value, "score": 0.99}] for value in values],
                    "directions": ["horizontal"] * len(values), "points": points}
    with monkeypatch.context() as model_boundary:
        model_boundary.setattr(runtime, "_get_text_recognizer", lambda: object())
        model_boundary.setattr(recognizer, "YomitokuTextRecognizerTopKWrapper", Wrapper)
        summary, _ = runtime.build_best_method_for_manifest_item(item=item, page_index=1,
            draft_sheet={"fields": ["qty.regular"], "rows": [[""]]},
            output_dir=f["source"].parent / ("producer-" + f["job_id"]), recognition_checkpoint=checkpoint)
    raw = json.loads(Path(summary["outputs"]["records"]).read_text())
    source = order_service._hakodate_owned_source(captured["configured_input"], pdf)
    packet = artifacts.persist_outputs(order_id=f["order_id"], outputs=summary["outputs"],
        source_receipt=source, expected_sheet_cells=cells,
        producer_version=order_service.HAKODATE_CANONICAL_PIPELINE_VERSION,
        workspace=f["source"].parent / ("producer-" + f["job_id"]))
    outputs = {key: value["uri"] for key, value in packet["outputs"].items()}
    targets = evidence.target_cells_from_regions(raw)
    for target in targets:
        target["source_receipt"] = source_reference(source)
    records = evidence.evidence_from_records([
        {"text": row["recognizer_raw_text"], "normalized_value": row["resolved_value"],
         "confidence": row["recognizer_score"], "bbox": row["bbox"],
         "evidence_id": "authored-" + row["sheet_cell"], "engine_metadata": {
             "sheet_cell": row["sheet_cell"], "source_receipt": source_reference(source),
             "recognizer_output": row}}
        for row in raw], run_id="authored-recognizer", engine=runtime.TEXT_RECOGNIZER_ENGINE,
        source_scope="hakodate_cell_crop_batch", raw_payload_ref=outputs["records"], retain_empty_records=True)
    payload = {"hakodate_repair_source_receipt": source,
        "hakodate_canonical_pipeline": {"status": "ready", "version": order_service.HAKODATE_CANONICAL_PIPELINE_VERSION,
            "expected_sheet_cells": cells, "requested_order_id": f["order_id"], "outputs": outputs, "artifact_packet": packet},
        "hakodate_overlay": {"uri": outputs["overlay"], "sheet_review_base_uri": outputs["sheet_review_base"]},
        "hakodate_preprocessing": {"target_cell_map": targets},
        "hakodate_ocr_evidence": {"records": records}, "hakodate_ocr_evidence_records": deepcopy(records),
        "hakodate_recognition_review": evidence.recognition_review(records)}
    assert payload["hakodate_recognition_review"] == {"cells": [], "count": 0}
    assert [row["recognizer_raw_text"] for row in raw] == values
    assert all(row["resolution_state"] == "resolved" for row in raw)
    # An independent execution failure exercises recovery, not a quantity gate.
    completed, error = order_service._persist_ocr_rerun_completion(f["order_id"], f["job_id"], payload,
        error="authored_runtime_failure", producer_version="hakodate_best_method_pipeline")
    assert error == "authored_runtime_failure"
    return {**f, "evidence_id": completed["id"], "checkpoint": checkpoint, "raw": raw, "terminal": completed["terminal_receipt"]}


@pytest.fixture
def diagnostic(native_sql, monkeypatch):
    f = native_sql
    from src.models.menu import MonthlyMenuEntry, MonthlyMenuItem, MenuMaster
    # Author additional canonical menu rows before the real processing capture.
    with db.session_scope() as session:
        for day in range(5, 12):
            for daypart in ("breakfast", "dinner"):
                identity = f"authored-{day}-{daypart}"
                session.add(MenuMaster(id=identity, name=identity, normalized_name=identity,
                    qty_per_serving=1, unit_type="g", category="主菜"))
                session.flush()
                session.add(MonthlyMenuEntry(id=identity, monthly_menu_id="2026-10", menu_date=date(2026, 10, day),
                    daypart=daypart, name=identity, diet_type="regular", slot_index=0,
                    category="主菜", facility_override="SQL-COMPONENT"))
                session.add(MonthlyMenuItem(id=identity, monthly_menu_id="2026-10", menu_master_id=identity,
                    name=identity, diet_type="regular", facility_override="SQL-COMPONENT", daypart=daypart,
                    category="主菜", qty_per_serving=1, unit_type="g", master_resolution_mode="manual"))
    with delivery.claim_context(f["claim"]), ingest.ingest_execution_context(f["claim"]["lease"]):
        capture = initialize_and_capture(f)
        with guard.nonworkflow_execution_context(capture):
            f = produce_diagnostic(f, monkeypatch)
            delivery.persist_runner_receipt(f["claim"]["lease"], f["terminal"])
            order_service.finalize_first_pass_side_effects(job_id=f["job_id"], order_id=f["order_id"],
                message_id=f["claim"]["lease"]["id"])
            assert delivery.acknowledge_claim(f["claim"]) == "business_failed"
    return f


@contextmanager
def next_workflow(f, monkeypatch):
    from src.services import workflow_ocr_delivery_service as workflow_delivery
    from src.services import workflow_ocr_delivery_dispatch_service as dispatch
    from src.services import ingest_delivery_dispatch_service as ingest_dispatch
    for key, value in ingest_dispatch.required_config().items():
        monkeypatch.setenv(dispatch.PREFIX + key, value)
    with f["factory"]() as session:
        revision = session.get(OrderWorkflowState, f["order_id"]).revision
    job_id = f"authored-next-review-job-{revision}"
    queued, error = workflow.mark_ocr_run_queued(f["order_id"], job_id,
        input_reference=str(f["source"]), request_mode="ocr_rerun", expected_revision=revision)
    assert error is None
    with f["factory"]() as session:
        job = session.get(OcrJob, job_id)
        attempt = job.metrics["workflow_attempt_id"]
        revision = job.metrics["workflow_completion_revision"]
    claim = workflow_delivery.claim_delivery(queued["delivery"]["id"], queued["delivery"]["generation"],
        lease_expires_at=datetime.utcnow() + timedelta(minutes=5))
    with workflow_delivery.claim_context(claim), order_service.workflow_ocr_write_context(
            attempt, revision, job_id, f["order_id"]):
        yield {**f, "job_id": job_id}, claim
        assert workflow_delivery.acknowledge_claim(claim["delivery_id"], claim["generation"], claim["claim_token"])


def get(f, mode=recovery.FULL_MANUAL):
    result, error = recovery.get_review(f["order_id"], ocr_result_id=f["evidence_id"], mode=mode)
    assert error is None, error
    return result


def reject_empty_subset(f):
    before = snapshot(f)
    assert recovery.get_review(f["order_id"], ocr_result_id=f["evidence_id"],
        mode=recovery.DIAGNOSTIC_SUBSET) == (None, "recognition_review_result_changed")
    assert snapshot(f) == before


def explicit_values(f):
    # Good values are explicitly entered too; FULL_MANUAL never reuses a subset.
    values = {row["sheet_cell"]: str(index + 20) for index, row in enumerate(f["raw"])}
    for row in f["raw"]:
        if row["recognizer_raw_text"] in {"1", "26", "0", ""}:
            values[row["sheet_cell"]] = row["recognizer_raw_text"]
    return values


def save(f, result, values):
    receipt = result["source_receipt"]
    saved, error = recovery.save_review(f["order_id"], source_receipt=receipt, values=values,
        edited_by="component-operator", expected_revision=receipt["workflow_revision"])
    if error is None:
        with f["factory"]() as session:
            override = workflow._workflow_meta(session.get(OrderWorkflowState, f["order_id"]))["recognition_override"]
            assert override["mode"] == recovery.FULL_MANUAL
            assert override["values"] == values
            assert override["source_receipt"] == receipt
            assert override["edited_by"] == "component-operator"
    return saved, error


def test_real_native_subset_get_save_resume_preserves_good_records(diagnostic, monkeypatch):
    f = diagnostic
    before = snapshot(f)
    reject_empty_subset(f)
    result = get(f)
    required = [row["sheet_cell"] for row in f["raw"]]
    assert [cell["cell"] for cell in result["cells"]] == required
    assert result["retained_cell_count"] == 0
    assert result["mode"] == recovery.FULL_MANUAL
    assert snapshot(f) == before
    values = explicit_values(f)
    saved, error = save(f, result, values)
    assert error is None
    after = snapshot(f)
    assert all(before[table] == after[table] for table in before if table != "order_workflow_states")
    with f["factory"]() as session:
        original_payload = deepcopy(session.get(OrderOcrEvidenceRun, f["evidence_id"]).payload_json)
    monkeypatch.setattr(runtime, "_build_preprocess_for_ocr", lambda **kw: pytest.fail("checkpoint was reprocessed"))
    monkeypatch.setattr(runtime, "_run_text_recognizer_records", lambda **kw: pytest.fail("good cells were recognized again"))
    with next_workflow(f, monkeypatch) as (next_f, claim):
        payload = order_service._hakodate_canonical_payload_from_manifest_item(
            order_id=f["order_id"], facility_id="SQL-COMPONENT", item={})
        rows = json.loads(artifacts.validate_payload(payload)["records"])
        for old, new in zip(f["raw"], rows):
            cell = old["sheet_cell"]
            if cell in values:
                assert new["operator_value"] == values[cell] and new["recognition_source"] == "operator"
                assert "original_recognizer_output" not in new
                assert "recognizer_raw_text" not in new
            else:
                assert new == old
        old_records = original_payload["hakodate_ocr_evidence_records"]
        for old, new in zip(old_records, payload["hakodate_ocr_evidence_records"]):
            cell = old["engine_metadata"]["sheet_cell"]
            if cell in values:
                assert new["raw_text"] == values[cell]
                assert new["engine_metadata"]["source"] == "operator"
            else:
                comparable = deepcopy(new)
                comparable["raw_payload_ref"] = old["raw_payload_ref"]
                comparable["engine_metadata"]["source_receipt"] = old["engine_metadata"]["source_receipt"]
                assert comparable == old
        assert [row["recognizer_raw_text"] for row in f["raw"][:4]] == ["1", "26", "0", ""]
        assert payload["hakodate_recognition_review"] == {"cells": [], "count": 0}
        completed, error = order_service._persist_ocr_rerun_completion(f["order_id"], next_f["job_id"], payload,
            producer_version="hakodate_best_method_pipeline")
        assert error is None and completed["terminal_receipt"]["status"] == "done"
    with f["factory"]() as session:
        assert session.get(OrderOcrEvidenceRun, f["evidence_id"]).payload_json == original_payload
        state = session.get(OrderWorkflowState, f["order_id"])
        assert (state.evidence_run_id, state.draft_id, state.confirmed_snapshot_id) == (None, None, None)


@pytest.mark.parametrize("damage", ["missing", "extra", "non_numeric", "mode", "proof"])
def test_invalid_subset_or_proof_is_rejected_without_mutation(diagnostic, damage):
    f = diagnostic
    result = get(f)
    values = explicit_values(f)
    if damage == "missing":
        values.pop(next(iter(values)))
    elif damage == "extra":
        values["not-a-declared-cell"] = "0"
    elif damage == "non_numeric":
        values[next(iter(values))] = "-"
    elif damage == "mode":
        result["source_receipt"]["recognition_mode"] = recovery.DIAGNOSTIC_SUBSET
    else:
        result["source_receipt"]["recognition_checkpoint_digest"] = "0" * 64
    before = snapshot(f)
    saved, error = save(f, result, values)
    assert saved is None and error
    assert snapshot(f) == before


@pytest.mark.parametrize("damage", ["bytes", "duplicate", "missing", "execution", "generation", "document", "terminal", "foreign_job", "receipt", "checkpoint", "evidence_execution", "evidence_pdf", "evidence_attempt"])
def test_damaged_source_stops_get_save_and_resume(diagnostic, monkeypatch, damage):
    f = diagnostic
    result = get(f)
    values = explicit_values(f)
    saved, error = save(f, result, values)
    assert error is None
    with f["factory"]() as session:
        override = deepcopy(workflow._workflow_meta(session.get(OrderWorkflowState, f["order_id"]))["recognition_override"])
        current = guard.capture_reparse_read_input(session, session.get(Order, f["order_id"]))
        item = recovery.current_item(override["checkpoint"], current, result["source_receipt"], f["order_id"])
        job = session.get(OcrJob, f["job_id"])
        metrics = deepcopy(job.metrics)
        if damage in {"bytes", "duplicate", "missing"}:
            original_payload = deepcopy(session.get(OrderOcrEvidenceRun, f["evidence_id"]).payload_json)
            path = Path(original_payload["hakodate_canonical_pipeline"]["artifact_packet"]["outputs"]["records"]["uri"])
            rows = json.loads(path.read_text())
            if damage == "duplicate":
                rows.append(deepcopy(rows[0]))
            elif damage == "missing":
                rows.pop()
            else:
                rows[0]["recognizer_raw_text"] = "0"
            path.write_text(json.dumps(rows))
        elif damage == "document":
            session.get(Order, f["order_id"]).current_document_id = None
        elif damage == "execution":
            metrics["nonworkflow_execution"]["execution_id"] = "foreign"
        elif damage == "generation":
            metrics["nonworkflow_execution"]["ingest_lease"]["generation"] = "foreign"
        elif damage in {"foreign_job", "evidence_execution", "evidence_pdf", "evidence_attempt"}:
            evidence_row = session.get(OrderOcrEvidenceRun, f["evidence_id"])
            payload = deepcopy(evidence_row.payload_json)
            field = {"foreign_job": "job_id", "evidence_execution": "nonworkflow_execution_id",
                     "evidence_pdf": "selected_pdf_receipt", "evidence_attempt": "workflow_attempt_id"}[damage]
            payload["_ocr_execution"][field] = "foreign"
            evidence_row.payload_json = payload
        elif damage == "receipt":
            metrics["rerun_terminal_receipt"]["evidence_run_id"] = "foreign"
        elif damage == "checkpoint":
            metrics["recognition_checkpoint"]["digest"] = "0" * 64
        else:
            metrics["rerun_terminal_receipt"] = None
        session.execute(update(OcrJob).where(OcrJob.id == job.id).values(metrics=metrics))
        session.commit()
    before = snapshot(f)
    if damage in {"bytes", "duplicate", "missing"}:
        # Complete manual recovery does not consume the old recognition artifact.
        with pytest.raises(ValueError):
            artifacts.validate_payload(original_payload)
        assert get(f)["retained_cell_count"] == 0
        assert save(f, get(f), values)[1] is None
        after_save = snapshot(f)
        assert all(before[table] == after_save[table] for table in before
                   if table != "order_workflow_states")
        with next_workflow(f, monkeypatch) as (next_f, claim):
            payload = order_service._hakodate_canonical_payload_from_manifest_item(
                order_id=f["order_id"], facility_id="SQL-COMPONENT", item={})
            rows = json.loads(artifacts.validate_payload(payload)["records"])
            assert {row["sheet_cell"]: row["operator_value"] for row in rows} == values
            assert all("original_recognizer_output" not in row for row in rows)
            completed, error = order_service._persist_ocr_rerun_completion(
                f["order_id"], next_f["job_id"], payload,
                producer_version="hakodate_best_method_pipeline")
            assert error is None and completed["terminal_receipt"]["status"] == "done"
        with f["factory"]() as session:
            assert session.get(OrderOcrEvidenceRun, f["evidence_id"]).payload_json == original_payload
        return
    assert recovery.get_review(f["order_id"], ocr_result_id=f["evidence_id"], mode=recovery.DIAGNOSTIC_SUBSET)[1]
    fresh_result = deepcopy(result)
    with f["factory"]() as session:
        fresh_result["source_receipt"]["workflow_revision"] = session.get(OrderWorkflowState, f["order_id"]).revision
    assert save(f, fresh_result, values)[1]
    assert recovery.get_review(f["order_id"], ocr_result_id=f["evidence_id"],
        mode=recovery.FULL_MANUAL)[1]
    with pytest.raises(ValueError):
        recovery.validate_manual_resume(override, item=item)
    assert snapshot(f) == before


def test_real_workflow_diagnostic_uses_same_subset(diagnostic, monkeypatch):
    with next_workflow(diagnostic, monkeypatch) as (f, claim):
        f = produce_diagnostic(f, monkeypatch)
    result = get(f)
    reject_empty_subset(f)
    assert result["mode"] == recovery.FULL_MANUAL and len(result["cells"]) == len(f["raw"])
    assert result["retained_cell_count"] == 0
    assert "native_recognition_owner" not in result["source_receipt"]
    values = explicit_values(f)
    with f["factory"]() as session:
        original_payload = deepcopy(session.get(OrderOcrEvidenceRun, f["evidence_id"]).payload_json)
    saved, error = save(f, result, values)
    assert error is None and saved["state"] == "context_confirmed"
    with next_workflow(f, monkeypatch) as (next_f, claim):
        payload = order_service._hakodate_canonical_payload_from_manifest_item(
            order_id=f["order_id"], facility_id="SQL-COMPONENT", item={})
        rows = json.loads(artifacts.validate_payload(payload)["records"])
        required = {cell["cell"] for cell in result["cells"]}
        assert required == {row["sheet_cell"] for row in f["raw"]}
        assert all(row["operator_value"] == values[row["sheet_cell"]] for row in rows)
        assert all("original_recognizer_output" not in row for row in rows)
        completed, error = order_service._persist_ocr_rerun_completion(f["order_id"], next_f["job_id"], payload,
            producer_version="hakodate_best_method_pipeline")
        assert error is None and completed["terminal_receipt"]["status"] == "done"
    with f["factory"]() as session:
        assert session.get(OrderOcrEvidenceRun, f["evidence_id"]).payload_json == original_payload


@pytest.mark.parametrize("field", ["workflow_attempt_id", "workflow_revision", "processing_start_input", "selected_pdf_receipt"])
def test_manual_workflow_resume_rejects_changed_execution_without_writes(diagnostic, monkeypatch, field):
    with next_workflow(diagnostic, monkeypatch) as (f, claim):
        f = produce_diagnostic(f, monkeypatch)
    result = get(f)
    assert save(f, result, explicit_values(f))[1] is None
    with f["factory"]() as session:
        state = session.get(OrderWorkflowState, f["order_id"])
        override = deepcopy(workflow._workflow_meta(state)["recognition_override"])
        captured = guard.capture_reparse_read_input(session, session.get(Order, f["order_id"]))
        item = recovery.current_item(override["checkpoint"], captured, result["source_receipt"], f["order_id"])
        evidence_row = session.get(OrderOcrEvidenceRun, f["evidence_id"])
        payload = deepcopy(evidence_row.payload_json)
        payload["_ocr_execution"][field] = "foreign"
        evidence_row.payload_json = payload
        session.commit()
    before = snapshot(f)
    with pytest.raises(ValueError):
        recovery.validate_manual_resume(override, item=item)
    assert snapshot(f) == before


def test_subset_http_and_stale_revision(diagnostic, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from src.api import orders
    f = diagnostic
    app = FastAPI()
    app.include_router(orders.router, prefix="/orders")
    # Registered actor and real authorization; only Google verification is mocked.
    from tests.contract.auth_support import registered_hospital_operator
    with registered_hospital_operator(monkeypatch) as actor, TestClient(app, headers=actor.headers) as client:
        before = snapshot(f)
        response = client.get(f"/orders/{f['order_id']}/workflow-v2/recognition-review",
            params={"ocr_result_id": f["evidence_id"], "mode": recovery.DIAGNOSTIC_SUBSET})
        assert response.status_code == 400, response.text
        assert response.json()["detail"] == "recognition_review_result_changed"
        assert snapshot(f) == before
        response = client.get(f"/orders/{f['order_id']}/workflow-v2/recognition-review",
            params={"ocr_result_id": f["evidence_id"], "mode": recovery.FULL_MANUAL})
        assert response.status_code == 200, response.text
        assert snapshot(f) == before
        result = response.json()
        assert len(result["cells"]) == len(f["raw"]) and result["retained_cell_count"] == 0
        body = {"source_receipt": result["source_receipt"],
                "values": explicit_values(f)}
        headers = {"If-Match": str(result["source_receipt"]["workflow_revision"])}
        response = client.put(f"/orders/{f['order_id']}/workflow-v2/recognition-review",
            headers=headers, json=body)
        assert response.status_code == 200, response.text
        before = snapshot(f)
        response = client.put(f"/orders/{f['order_id']}/workflow-v2/recognition-review",
            headers=headers, json=body)
        assert response.status_code == 409, response.text
        assert snapshot(f) == before
        response = client.get(f"/orders/{f['order_id']}/workflow-v2/recognition-review",
            params={"ocr_result_id": f["evidence_id"], "mode": "unknown"})
        assert response.status_code == 422
        assert snapshot(f) == before


@pytest.mark.parametrize("damaged_artifact", [False, True])
def test_explicit_full_manual_with_diagnostic_evidence_never_consumes_recognized_rows(diagnostic, monkeypatch, damaged_artifact):
    f = diagnostic
    reject_empty_subset(f)
    with f["factory"]() as session:
        original_payload = deepcopy(session.get(OrderOcrEvidenceRun, f["evidence_id"]).payload_json)
    if damaged_artifact:
        Path(original_payload["hakodate_canonical_pipeline"]["artifact_packet"]["outputs"]["records"]["uri"]).write_bytes(b"invalid diagnostic artifact")
        before = snapshot(f)
        result, error = recovery.get_review(f["order_id"], ocr_result_id=f["evidence_id"], mode=recovery.DIAGNOSTIC_SUBSET)
        assert result is None and error
        assert snapshot(f) == before
    # Full manual is explicit, never a fallback from a rejected subset request.
    result, error = recovery.get_review(f["order_id"], ocr_result_id=f["evidence_id"], mode=recovery.FULL_MANUAL)
    assert error is None
    assert result["mode"] == recovery.FULL_MANUAL and result["retained_cell_count"] == 0
    assert len(result["cells"]) == len(f["raw"])
    assert result["source_receipt"]["recognition_evidence_run_id"] == f["evidence_id"]
    values = explicit_values(f)
    partial = {row["sheet_cell"]: values[row["sheet_cell"]] for row in f["raw"]
               if row["recognizer_raw_text"] in {"-", "--"}}
    before = snapshot(f)
    assert save(f, result, partial)[1] == "recognition_values_all_cells_required"
    assert snapshot(f) == before
    assert save(f, result, values)[1] is None
    monkeypatch.setattr(recovery, "resume_diagnostic_source", lambda *a, **kw: pytest.fail("full manual consumed old recognizer rows"))
    monkeypatch.setattr(runtime, "_run_text_recognizer_records", lambda **kw: pytest.fail("manual values were recognized again"))
    with next_workflow(f, monkeypatch) as (next_f, claim):
        payload = order_service._hakodate_canonical_payload_from_manifest_item(
            order_id=f["order_id"], facility_id="SQL-COMPONENT", item={})
        rows = json.loads(artifacts.validate_payload(payload)["records"])
        assert {row["sheet_cell"]: row["operator_value"] for row in rows} == values
        assert all(row["recognition_source"] == "operator" and "original_recognizer_output" not in row for row in rows)
        completed, error = order_service._persist_ocr_rerun_completion(f["order_id"], next_f["job_id"], payload,
            producer_version="hakodate_best_method_pipeline")
        assert error is None and completed["terminal_receipt"]["status"] == "done"
    with f["factory"]() as session:
        assert session.get(OrderOcrEvidenceRun, f["evidence_id"]).payload_json == original_payload


def test_no_evidence_full_manual_is_not_inferred_from_missing_diagnostic_source(native_sql, monkeypatch):
    f = native_sql
    with delivery.claim_context(f["claim"]), ingest.ingest_execution_context(f["claim"]["lease"]):
        capture = initialize_and_capture(f)
        with guard.nonworkflow_execution_context(capture):
            captured = guard.processing_start_read_input()
            item = recovery.current_item({"resume_item": {"quad_px": [[0, 0], [99, 0], [99, 99], [0, 99]],
                "quad_source": "authored-component"}}, captured, {"selected_pdf_receipt": capture["selected_pdf_receipt"]}, f["order_id"])
            regions = [{**row, "bbox": [1, 1, 80, 60]} for row in item["original_target_semantics"]]
            order_service._persist_hakodate_recognition_checkpoint(item=item, raw_rectified_bgr=np.zeros((100, 100, 3), np.uint8),
                regions=regions, expected_sheet_cells=item["original_expected_sheet_cells"],
                metadata={"axis_evidence": {}, "rectified_quad_points": item["quad_px"], "snap_debug": {}})
            completed, error = order_service._persist_ocr_rerun_completion(f["order_id"], f["job_id"], None,
                error="authored_runtime_failure", producer_version="hakodate_best_method_pipeline")
            assert error == "authored_runtime_failure"
            delivery.persist_runner_receipt(f["claim"]["lease"], completed["terminal_receipt"])
            order_service.finalize_first_pass_side_effects(job_id=f["job_id"], order_id=f["order_id"], message_id=f["claim"]["lease"]["id"])
            assert delivery.acknowledge_claim(f["claim"]) == "business_failed"
    assert recovery.get_review(f["order_id"], mode=recovery.DIAGNOSTIC_SUBSET) == (
        None, "recognition_diagnostic_evidence_required_use_full_manual")
    result = get({**f, "evidence_id": None}, mode=recovery.FULL_MANUAL)
    assert result["mode"] == recovery.FULL_MANUAL and result["retained_cell_count"] == 0
    assert len(result["cells"]) == len(item["original_expected_sheet_cells"])
    values = {cell["cell"]: "0" for cell in result["cells"]}
    assert save(f, result, {})[1] == "recognition_values_all_cells_required"
    saved, error = save(f, result, values)
    assert error is None and saved["state"] == "context_confirmed"
    monkeypatch.setattr(runtime, "_build_preprocess_for_ocr", lambda **kw: pytest.fail("checkpoint was reprocessed"))
    monkeypatch.setattr(runtime, "_run_text_recognizer_records", lambda **kw: pytest.fail("manual values were recognized again"))
    with next_workflow(f, monkeypatch) as (next_f, claim):
        payload = order_service._hakodate_canonical_payload_from_manifest_item(
            order_id=f["order_id"], facility_id="SQL-COMPONENT", item={})
        rows = json.loads(artifacts.validate_payload(payload)["records"])
        assert {row["sheet_cell"]: row["operator_value"] for row in rows} == values
        assert all(row["recognition_source"] == "operator" for row in rows)
        assert all("original_recognizer_output" not in row for row in rows)
        completed, error = order_service._persist_ocr_rerun_completion(f["order_id"], next_f["job_id"], payload,
            producer_version="hakodate_best_method_pipeline")
        assert error is None and completed["terminal_receipt"]["status"] == "done"
