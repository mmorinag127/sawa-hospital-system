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
    assert len(cells) >= 14, "The authored canonical plan must exercise ten issues plus four good values"
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
        "hakodate_canonical_pipeline": {"status": "blocked", "version": order_service.HAKODATE_CANONICAL_PIPELINE_VERSION,
            "expected_sheet_cells": cells, "requested_order_id": f["order_id"], "outputs": outputs, "artifact_packet": packet},
        "hakodate_overlay": {"uri": outputs["overlay"], "sheet_review_base_uri": outputs["sheet_review_base"]},
        "hakodate_preprocessing": {"target_cell_map": targets},
        "hakodate_ocr_evidence": {"records": records}, "hakodate_ocr_evidence_records": deepcopy(records),
        "hakodate_recognition_review": evidence.recognition_review(records)}
    assert payload["hakodate_recognition_review"]["count"] == 10
    completed, error = order_service._persist_ocr_rerun_completion(f["order_id"], f["job_id"], payload,
        error="authored_recognition_review", producer_version="hakodate_best_method_pipeline")
    assert error == "authored_recognition_review"
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


def get(f, mode=recovery.DIAGNOSTIC_SUBSET):
    result, error = recovery.get_review(f["order_id"], ocr_result_id=f["evidence_id"], mode=mode)
    assert error is None, error
    return result


def save(f, result, values):
    receipt = result["source_receipt"]
    return recovery.save_review(f["order_id"], source_receipt=receipt, values=values,
        edited_by="component-operator", expected_revision=receipt["workflow_revision"])


def test_real_native_subset_get_save_resume_preserves_good_records(diagnostic, monkeypatch):
    f = diagnostic
    before = snapshot(f)
    result = get(f)
    required = [row["sheet_cell"] for row in f["raw"] if row["resolution_state"] == "review_required"]
    assert [cell["cell"] for cell in result["cells"]] == required
    assert len(required) == 10 and result["retained_cell_count"] == len(f["raw"]) - 10
    assert result["mode"] == recovery.DIAGNOSTIC_SUBSET
    assert snapshot(f) == before
    values = {cell: str(index + 20) for index, cell in enumerate(required)}
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
                assert new["original_recognizer_output"] == old
                assert old["recognizer_raw_text"] in {"-", "--"}
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
        assert [row["recognizer_raw_text"] for row in rows[:4]] == ["1", "26", "0", ""]
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
    values = {cell["cell"]: "7" for cell in result["cells"]}
    if damage == "missing":
        values.pop(next(iter(values)))
    elif damage == "extra":
        values[f["raw"][0]["sheet_cell"]] = "0"
    elif damage == "non_numeric":
        values[next(iter(values))] = "-"
    elif damage == "mode":
        result["source_receipt"]["recognition_mode"] = recovery.FULL_MANUAL
    else:
        result["source_receipt"]["recognition_payload_digest"] = "0" * 64
    before = snapshot(f)
    saved, error = save(f, result, values)
    assert saved is None and error
    assert snapshot(f) == before


@pytest.mark.parametrize("damage", ["bytes", "duplicate", "missing", "execution", "generation", "document", "terminal", "foreign_job", "receipt", "checkpoint"])
def test_damaged_source_stops_get_save_and_resume(diagnostic, damage):
    f = diagnostic
    result = get(f)
    values = {cell["cell"]: "7" for cell in result["cells"]}
    saved, error = save(f, result, values)
    assert error is None
    with f["factory"]() as session:
        override = deepcopy(workflow._workflow_meta(session.get(OrderWorkflowState, f["order_id"]))["recognition_override"])
        current = guard.capture_reparse_read_input(session, session.get(Order, f["order_id"]))
        item = recovery.current_item(override["checkpoint"], current, result["source_receipt"], f["order_id"])
        job = session.get(OcrJob, f["job_id"])
        metrics = deepcopy(job.metrics)
        if damage in {"bytes", "duplicate", "missing"}:
            path = Path(result["source_receipt"]["recognition_records_receipt"]["uri"])
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
        elif damage == "foreign_job":
            evidence_row = session.get(OrderOcrEvidenceRun, f["evidence_id"])
            payload = deepcopy(evidence_row.payload_json)
            payload["_ocr_execution"]["job_id"] = "foreign"
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
    assert recovery.get_review(f["order_id"], ocr_result_id=f["evidence_id"], mode=recovery.DIAGNOSTIC_SUBSET)[1]
    fresh_result = deepcopy(result)
    with f["factory"]() as session:
        fresh_result["source_receipt"]["workflow_revision"] = session.get(OrderWorkflowState, f["order_id"]).revision
    assert save(f, fresh_result, values)[1]
    with pytest.raises(ValueError):
        recovery.resume_diagnostic_source(override, item=item, captured=current)
    assert snapshot(f) == before


def test_real_workflow_diagnostic_uses_same_subset(diagnostic, monkeypatch):
    with next_workflow(diagnostic, monkeypatch) as (f, claim):
        f = produce_diagnostic(f, monkeypatch)
    result = get(f)
    assert result["mode"] == recovery.DIAGNOSTIC_SUBSET and len(result["cells"]) == 10
    assert "native_recognition_owner" not in result["source_receipt"]
    saved, error = save(f, result, {cell["cell"]: "7" for cell in result["cells"]})
    assert error is None and saved["state"] == "context_confirmed"
    with next_workflow(f, monkeypatch) as (next_f, claim):
        payload = order_service._hakodate_canonical_payload_from_manifest_item(
            order_id=f["order_id"], facility_id="SQL-COMPONENT", item={})
        rows = json.loads(artifacts.validate_payload(payload)["records"])
        required = {cell["cell"] for cell in result["cells"]}
        assert all(new == old for old, new in zip(f["raw"], rows) if old["sheet_cell"] not in required)
        assert all(row["operator_value"] == "7" for row in rows if row["sheet_cell"] in required)
        completed, error = order_service._persist_ocr_rerun_completion(f["order_id"], next_f["job_id"], payload,
            producer_version="hakodate_best_method_pipeline")
        assert error is None and completed["terminal_receipt"]["status"] == "done"


def test_subset_http_and_stale_revision(diagnostic):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from src.api import orders, auth
    f = diagnostic
    app = FastAPI()
    app.include_router(orders.router, prefix="/orders")
    # Only the external actor is supplied; source/CAS/receipt validators are real.
    app.dependency_overrides[auth.get_current_operator] = lambda: auth.UserContext(
        role="operator", account="component-operator")
    with TestClient(app) as client:
        response = client.get(f"/orders/{f['order_id']}/workflow-v2/recognition-review",
            params={"ocr_result_id": f["evidence_id"], "mode": recovery.DIAGNOSTIC_SUBSET})
        assert response.status_code == 200, response.text
        result = response.json()
        assert len(result["cells"]) == 10 and result["retained_cell_count"] == 5
        body = {"source_receipt": result["source_receipt"],
                "values": {cell["cell"]: "7" for cell in result["cells"]}}
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
    diagnostic_result = get(f)
    if damaged_artifact:
        Path(diagnostic_result["source_receipt"]["recognition_records_receipt"]["uri"]).write_bytes(b"invalid diagnostic artifact")
        before = snapshot(f)
        result, error = recovery.get_review(f["order_id"], ocr_result_id=f["evidence_id"], mode=recovery.DIAGNOSTIC_SUBSET)
        assert result is None and error
        assert snapshot(f) == before
    # Omitted mode is deliberately the original complete-manual API, not a
    # fallback selected after trying diagnostic validation.
    result, error = recovery.get_review(f["order_id"], ocr_result_id=f["evidence_id"])
    assert error is None
    assert result["mode"] == recovery.FULL_MANUAL and result["retained_cell_count"] == 0
    assert len(result["cells"]) == len(f["raw"])
    assert result["source_receipt"]["recognition_evidence_run_id"] == f["evidence_id"]
    values = {cell["cell"]: "7" for cell in result["cells"]}
    assert save(f, result, {cell["cell"]: "7" for cell in diagnostic_result["cells"]})[1] == "recognition_values_all_cells_required"
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
