"""Explicit synthetic component cases, not live OCR accuracy/acceptance proof."""
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path

import numpy as np
import pytest
from sqlalchemy import event, select

from src.db import session_scope
from src.models.order import Order
from src.models.ocr_job import OcrJob
from src.models.order_workflow_state import OrderWorkflowState
from src.models.order_ocr_evidence_run import OrderOcrEvidenceRun
from src.models.order_sheet_draft import OrderSheetDraft
from src.services import order_service, order_workflow_v2_service as workflow
from src.services import ocr_recognition_recovery_service as recovery
from src.services import recognition_acceptance_service as acceptance
from src.services import hakodate_ocr_evidence_service as evidence
from src.services import hakodate_ocr_checkpoint_service as checkpoints
from test_h2_operator_repair_revision import _make_repair_order, _owned_artifact_fixture
from test_native_ingest_sql_component import native_sql
from test_native_recognition_recovery import native_review

TEN = [(cell, "--" if cell in {"J38", "I50"} else "-") for cell in
       ("H17", "G20", "G21", "G29", "J38", "G45", "I49", "I50", "I60", "J60")]


def ten_payload():
    records = []
    for index, (cell, raw) in enumerate(TEN):
        source = {"role": "quantity", "field": "qty.regular", "raw_text": raw, "recognizer_raw_text": raw,
                  "resolution_state": "review_required", "quantity_source_issue": "hakodate_ocr_quantity_non_numeric"}
        records.append({"evidence_id": f"cell-{index}", "raw_text": raw, "normalized_value": None,
            "source_scope": "hakodate_cell_crop_batch", "engine_metadata": {"sheet_cell": cell, "recognizer_output": source}})
    return {"hakodate_ocr_evidence": {"records": records},
        "hakodate_recognition_review": evidence.recognition_review(records),
        "hakodate_repair_source_receipt": {"selected_pdf_receipt": {"sha256": "original-pdf"}},
        "_ocr_execution": {"evidence_run_id": "original-result"},
        "hakodate_canonical_pipeline": {"status": "blocked", "blockers": [
            f"hakodate_ocr_quantity_unresolved: cell-{index}" for index in range(10)]}}


def acknowledged(payload, values):
    return acceptance.acknowledge(payload, values=values, edited_by="explicit-operator",
        created_at=datetime.utcnow().isoformat(), source_receipt={"workflow_revision": 7,
            "selected_pdf_receipt": payload["hakodate_repair_source_receipt"]["selected_pdf_receipt"],
            "recognition_evidence_run_id": payload["_ocr_execution"]["evidence_run_id"]})


@pytest.mark.parametrize("choice", ["", "0", "12"])
def test_exact_ten_dash_patterns_require_explicit_interpretation_and_retain_raw(choice):
    original = ten_payload()
    before = deepcopy(original)
    result = acknowledged(original, dict.fromkeys(dict(TEN), choice))
    assert acceptance.accepted_values(result) == {f"cell-{index}": choice for index in range(10)}
    assert original == before
    assert {key: value for key, value in result.items() if key != acceptance.KEY} == before
    assert result[acceptance.KEY]["cells"] == original["hakodate_recognition_review"]["cells"]
    assert [row["raw_text"] for row in original["hakodate_ocr_evidence"]["records"]] == list(dict(TEN).values())


@pytest.mark.parametrize("values", [None, {}, dict(TEN), dict.fromkeys(dict(TEN), "?"),
    dict.fromkeys(dict(TEN), None), dict.fromkeys(dict(TEN), 0), dict.fromkeys(dict(TEN), "-1")])
def test_ten_unresolved_symbols_never_become_automatic_blank_or_zero(values):
    payload = ten_payload()
    before = deepcopy(payload)
    with pytest.raises(ValueError, match="explicit_values_required"):
        acknowledged(payload, values)
    assert payload == before


@pytest.mark.parametrize("change", ["raw", "reason", "pdf", "evidence", "missing_actor", "extra_blocker"])
def test_acknowledgement_cannot_hide_changed_source_or_extra_blockers(change):
    result = acknowledged(ten_payload(), dict.fromkeys(dict(TEN), ""))
    if change == "raw":
        result["hakodate_ocr_evidence"]["records"][0]["raw_text"] = "9"
    elif change == "reason":
        result["hakodate_recognition_review"]["cells"][0]["reason"] = "other"
    elif change == "pdf":
        result["hakodate_repair_source_receipt"]["selected_pdf_receipt"]["sha256"] = "new"
    elif change == "evidence":
        result["_ocr_execution"]["evidence_run_id"] = "other"
    elif change == "missing_actor":
        result[acceptance.KEY]["edited_by"] = ""
    else:
        result["hakodate_canonical_pipeline"]["blockers"].append("missing_geometry")
    with pytest.raises(ValueError):
        acceptance.accepted_values(result)


@pytest.fixture
def reviewed_order(isolated_formal_schema, monkeypatch, tmp_path, request):
    engine = isolated_formal_schema
    def fk_on(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")
    event.listen(engine, "connect", fk_on)
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
    try:
        identity, _, _ = _make_repair_order(monkeypatch, tmp_path)
        with session_scope() as session:
            order, state = session.get(Order, identity), session.get(OrderWorkflowState, identity)
            workflow._write_workflow_meta(state, {"facility_id": order.facility_code,
                "week_start": "2026-10-04", "week_end": "2026-10-10", "template_id": "own",
                "template_version_id": state.template_version_id})
        with session_scope() as session:
            order, state = session.get(Order, identity), session.get(OrderWorkflowState, identity)
            captured = workflow.capture_reparse_read_input(session, order)
            receipt, _ = workflow._axis_source_receipt(session, order, state)
            source = order_service._hakodate_owned_source(captured["configured_input"], receipt["selected_pdf_receipt"],
                {**captured, "input_versions": {"order": captured["order"]}, "workflow_meta": captured["workflow"]["secondary_actions_json"]})
            evidence_id = state.evidence_run_id
            source.update(job_id="review-job", workflow_attempt_id="review-attempt", workflow_revision=state.revision)
            mode = getattr(request, "param", None)
            raw = "" if mode == "empty" else "7" if mode in {"numeric", "normal"} else "-"
            targets, records = _owned_artifact_fixture(source, raw)
            for record in records:
                original = record["engine_metadata"]["recognizer_output"]
                original.update(recognizer_raw_text=raw, resolution_state="review_required",
                    resolved_value=None, ocr_text=None, ocr_normalized=None, pred_digits=None,
                    quantity_source_issue="hakodate_ocr_quantity_non_numeric")
                record.update(normalized_value=None, resolution_state="review_required")
                if mode == "numeric":
                    original.update(recognizer_digit_score_threshold=0.9, recognizer_score=0.2,
                                    quantity_source_issue="hakodate_ocr_quantity_low_confidence")
                if mode == "normal":
                    original.update(resolution_state="resolved", resolved_value=raw, ocr_text=raw, ocr_normalized=raw, pred_digits=raw)
                    record.update(normalized_value=raw, resolution_state="resolved")
            payload = {"hakodate_repair_source_receipt": source,
                "hakodate_canonical_pipeline": {"status": "ready" if mode == "normal" else "blocked", "version": order_service.HAKODATE_CANONICAL_PIPELINE_VERSION,
                    "expected_sheet_cells": source["expected_sheet_cells"], "blockers": [
                        f"hakodate_ocr_quantity_unresolved: {record['evidence_id']}" for record in records]},
                "hakodate_preprocessing": {"target_cell_map": targets}, "hakodate_ocr_evidence": {"records": records},
                "hakodate_recognition_review": evidence.recognition_review(records)}
            if mode == "normal":
                payload["hakodate_canonical_pipeline"]["blockers"] = []
            from PIL import Image
            from test_modernization_canonical_ocr_artifacts import authored_outputs
            from src.services import canonical_ocr_artifact_service as artifacts
            from src.services.hakodate_recognizer_input_evidence import contact_crop_evidence
            workspace = tmp_path / "authored-review-packet"
            outputs = authored_outputs(workspace)
            with Image.open(outputs["contact_sheet"]) as image:
                crop = contact_crop_evidence(image, [[0, 0], [24, 0], [24, 32], [0, 32]])
            originals = []
            for record in records:
                original = record["engine_metadata"]["recognizer_output"]
                original.update(recognizer_input_evidence=crop, ocr_contact_crop_box=crop["bbox"])
                originals.append(original)
            Path(outputs["records"]).write_text(json.dumps(originals))
            Path(outputs["ocr_regions"]).write_text(json.dumps(originals))
            packet = artifacts.persist_outputs(order_id=identity, outputs=outputs, source_receipt=source,
                expected_sheet_cells=source["expected_sheet_cells"], producer_version=order_service.HAKODATE_CANONICAL_PIPELINE_VERSION,
                workspace=workspace)
            outputs = {key: entry["uri"] for key, entry in packet["outputs"].items()}
            payload["hakodate_canonical_pipeline"].update(outputs=outputs, artifact_packet=packet, requested_order_id=identity)
            payload["hakodate_overlay"] = {"uri": outputs["overlay"], "sheet_review_base_uri": outputs["sheet_review_base"]}
            for record in records:
                record["raw_payload_ref"] = outputs["records"]
            payload["hakodate_ocr_evidence_records"] = deepcopy(records)
            payload["_ocr_execution"] = {"job_id": "review-job", "workflow_attempt_id": "review-attempt",
                "workflow_revision": state.revision, "evidence_run_id": evidence_id, "disposition": "pipeline_result" if mode == "normal" else "rejected",
                "selected_pdf_receipt": receipt["selected_pdf_receipt"], "processing_start_input": {
                    **captured, "evidence_run_id": source.get("evidence_run_id")}}
            order_service._validate_hakodate_owned_artifacts(payload, source, allow_review=True)
            row = session.get(OrderOcrEvidenceRun, evidence_id)
            row.payload_json = payload
            row.status = "ready" if mode == "normal" else "diagnostic_only"
            row.artifact_manifest_json = {"original_hakodate_source": source}
            row.artifact_digest = acceptance.payload_digest(payload)
            seed = {"resume_item": {"quad_px": [[0, 0], [99, 0], [99, 99], [0, 99]], "quad_source": "authored-component"}}
            item = recovery.current_item(seed, captured, receipt, identity)
            checkpoint = checkpoints.persist_recognizer_checkpoint(item=item,
                raw_rectified_bgr=np.full((100, 100, 3), 255, dtype=np.uint8),
                regions=[{**deepcopy(region), "bbox": [1, 1, 8, 8]} for region in source["expected_target_semantics"]],
                expected_sheet_cells=source["expected_sheet_cells"])
            session.add(OcrJob(id="review-job", order_id=identity, status="done" if mode == "normal" else "failed", error_message=None if mode == "normal" else "authored review required",
                input_reference=receipt["selected_pdf_receipt"]["storage_uri"],
                metrics={"workflow_attempt_id": "review-attempt", "recognition_checkpoint": checkpoint}))
            meta = workflow._workflow_meta(state)
            meta.update(ocr_job_id="review-job", latest_ocr_result_id=evidence_id)
            workflow._write_workflow_meta(state, meta)
            state.state = "ocr_completed" if mode == "normal" else "ocr_failed"
        review, error = recovery.get_review(identity, ocr_result_id=evidence_id)
        if mode == "normal":
            assert error == "recognition_review_result_changed"
        else:
            assert error is None
        yield identity, evidence_id, review, engine
    finally:
        event.remove(engine, "connect", fk_on)


def snapshot(engine):
    from src import models
    with engine.connect() as connection:
        return {table.name: [dict(row) for row in connection.execute(select(table)).mappings()]
                for table in models.Base.metadata.sorted_tables}


@pytest.mark.parametrize("reviewed_order", ["normal"], indirect=True)
def test_normal_warning_free_backend_selection_is_unchanged(reviewed_order):
    identity, evidence_id, _, engine = reviewed_order
    before = snapshot(engine)["order_ocr_evidence_runs"]
    with session_scope() as session:
        state = session.get(OrderWorkflowState, identity)
        revision = state.revision
    selected, error = workflow.select_ocr_result(identity, evidence_id, expected_revision=revision)
    assert error is None and selected["state"] == "ocr_selected"
    projected, error = workflow.build_sheet_from_selected_ocr(identity)
    assert error is None and not projected["blockers"]
    assert snapshot(engine)["order_ocr_evidence_runs"] == before


@pytest.mark.parametrize("reviewed_order", ["numeric"], indirect=True)
def test_low_confidence_number_can_be_confirmed_unchanged_without_score_rewrite(reviewed_order):
    identity, evidence_id, review, engine = reviewed_order
    original = deepcopy(snapshot(engine)["order_ocr_evidence_runs"][0]["payload_json"])
    assert review["review_cells"][0]["reason"] == "hakodate_ocr_quantity_low_confidence"
    saved, error = recovery.accept_review(identity, source_receipt=review["source_receipt"],
        values={cell["sheet_cell"]: cell["raw_text"] for cell in review["review_cells"]},
        edited_by="explicit-operator", expected_revision=review["source_receipt"]["workflow_revision"])
    assert error is None
    selected, error = workflow.select_ocr_result(identity, evidence_id, expected_revision=saved["revision"])
    assert error is None
    result, error = workflow.build_sheet_from_selected_ocr(identity)
    assert error is None and not result["blockers"]
    assert result["sheet"]["ocr_numeric_cell_summary"]["raw_ocr_numeric_count"] == 1
    assert result["sheet"]["ocr_numeric_cell_summary"]["operator_reviewed_numeric_count"] == 1
    assert snapshot(engine)["order_ocr_evidence_runs"][0]["payload_json"] == original


def test_actual_http_revision_and_actor_binding(reviewed_order, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from src.api import orders
    from tests.contract.auth_support import registered_hospital_operator
    identity, _, review, engine = reviewed_order
    app = FastAPI()
    app.include_router(orders.router, prefix="/orders")
    body = {"source_receipt": review["source_receipt"], "values": {cell["sheet_cell"]: "" for cell in review["review_cells"]}}
    endpoint = f"/orders/{identity}/workflow-v2/recognition-review/accept"
    revision = review["source_receipt"]["workflow_revision"]
    with registered_hospital_operator(monkeypatch) as actor, TestClient(app, headers=actor.headers) as client:
        before = snapshot(engine)
        assert client.put(endpoint, json=body).status_code == 428
        assert client.put(endpoint, json=body, headers={"If-Match": str(revision - 1)}).status_code == 409
        assert snapshot(engine) == before
        response = client.put(endpoint, json=body, headers={"If-Match": str(revision)})
        assert response.status_code == 200, response.text
        with session_scope() as session:
            audit = workflow._workflow_meta(session.get(OrderWorkflowState, identity))["recognition_acceptances"][-1]
            assert audit["edited_by"] == actor.account
            assert audit["values"] == body["values"]


@pytest.mark.parametrize("value", ["", "0", "12"])
@pytest.mark.parametrize("saved_blocked", [False, True])
def test_formal_accept_select_generate_save_refresh_without_recognition(reviewed_order, value, saved_blocked):
    identity, evidence_id, review, engine = reviewed_order
    before = snapshot(engine)
    values = {cell["sheet_cell"]: value for cell in review["review_cells"]}
    saved, error = recovery.accept_review(identity, source_receipt=review["source_receipt"], values=values,
        edited_by="explicit-operator", expected_revision=review["source_receipt"]["workflow_revision"])
    assert error is None, error
    with session_scope() as session:
        row = session.get(OrderOcrEvidenceRun, evidence_id)
        assert row.payload_json == before["order_ocr_evidence_runs"][0]["payload_json"]
        assert row.artifact_digest == before["order_ocr_evidence_runs"][0]["artifact_digest"]
        assert row.status == "diagnostic_only"
        assert workflow.ocr_evidence_service.evidence_run_is_selectable(row)
    selected, error = workflow.select_ocr_result(identity, evidence_id, expected_revision=saved["revision"])
    assert error is None, error
    assert selected["state"] == "ocr_selected"
    projected, error = workflow.build_sheet_from_selected_ocr(identity)
    assert error is None, error
    assert not projected["sheet"].get("blockers"), projected
    assert projected["sheet"]["selected_ocr_result_id"] == evidence_id
    assert value in [str(cell) for row in projected["sheet"]["rows"] for cell in row]
    assert projected["sheet"]["ocr_numeric_cell_summary"]["raw_ocr_numeric_count"] == 0
    assert projected["sheet"]["ocr_numeric_cell_summary"].get("operator_reviewed_numeric_count", 0) == (1 if value else 0)
    persisted, error = workflow.save_sheet(order_id=identity, sheet=projected["sheet"], edited_by="explicit-operator",
        expected_revision=selected["revision"])
    assert error is None, error
    assert workflow.get_saved_sheet(identity)[1] is None
    refreshed, error = workflow.get_workflow_readonly(identity)
    assert error is None and refreshed["state"] == "sheet_saved"
    assert "evidence_unusable" not in refreshed["blockers"]
    assert order_service.get_order_by_id(identity, allow_unresolved_menu_amount=True)["id"] == identity
    with session_scope() as session:
        state = session.get(OrderWorkflowState, identity)
        draft = session.get(OrderSheetDraft, state.draft_id)
        state.state = "ocr_failed"
        state.blockers_json = ["evidence_unusable"]
        state.warnings_json = ["preserved-operator-warning"]
        if saved_blocked:
            draft.draft_sheet_json = {**draft.draft_sheet_json, "blockers": ["menu_amount_unresolved"]}
        draft_before = deepcopy(draft.draft_sheet_json)
        session.flush()
        revision = state.revision
    reselected, error = workflow.select_ocr_result(identity, evidence_id, expected_revision=revision)
    assert error is None and reselected["revision"] > revision
    assert reselected["state"] != "ocr_failed"
    assert "evidence_unusable" not in reselected["blockers"]
    assert "preserved-operator-warning" in reselected["warnings"]
    if saved_blocked:
        assert "menu_amount_unresolved" in reselected["blockers"]
        assert reselected["primary_action"] == "edit_sheet"
    with session_scope() as session:
        state = session.get(OrderWorkflowState, identity)
        assert session.get(OrderSheetDraft, state.draft_id).draft_sheet_json == draft_before
    assert snapshot(engine)["ocr_jobs"] == before["ocr_jobs"]


@pytest.fixture
def store(isolated_formal_schema, monkeypatch):
    """Use the existing native producer on a formal, owned FK-on schema."""
    from sqlalchemy.orm import sessionmaker
    from src.services import workflow_ocr_delivery_execution_service as exclusion
    from src.services import ingest_delivery_dispatch_service as dispatch
    engine = isolated_formal_schema
    def fk_on(connection, _record): connection.execute("PRAGMA foreign_keys=ON")
    event.listen(engine, "connect", fk_on)
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
    monkeypatch.setattr(exclusion, "get_engine", lambda: engine)
    values = {"PROJECT_ID": "fixture-project", "LOCATION": "fixture-location", "QUEUE": "fixture-queue",
        "TARGET_URL": "https://fixture.invalid", "SERVICE_ACCOUNT_EMAIL": "fixture@fixture.iam.gserviceaccount.com",
        "AUDIENCE": "fixture-audience", "DISPATCH_DEADLINE_SECONDS": "15"}
    for key, value in values.items(): monkeypatch.setenv(dispatch.PREFIX + key, value)
    for role in ("WORKER_", "DRAIN_"):
        for key, value in {"ISSUER": "https://accounts.google.com", "AUDIENCE": "fixture-audience",
                "EMAIL": values["SERVICE_ACCOUNT_EMAIL"], "SUBJECT": "fixture-subject"}.items():
            monkeypatch.setenv(dispatch.PREFIX + role + key, value)
    try:
        yield sessionmaker(engine, autoflush=False, expire_on_commit=True)
    finally:
        event.remove(engine, "connect", fk_on)


@pytest.mark.parametrize("value", ["", "0", "7"])
def test_native_actual_terminal_owner_accept_select_generate_save_refresh(native_review, value):
    f = native_review
    review, error = recovery.get_review(f["order_id"], ocr_result_id=f["evidence_id"])
    assert error is None
    with f["factory"]() as session:
        original = deepcopy(session.get(OrderOcrEvidenceRun, f["evidence_id"]).payload_json)
        metrics = deepcopy(session.get(OcrJob, f["job_id"]).metrics)
        assert original["_ocr_execution"]["workflow_attempt_id"] is None
    accepted, error = recovery.accept_review(f["order_id"], source_receipt=review["source_receipt"],
        values={cell["sheet_cell"]: value for cell in review["review_cells"]}, edited_by="explicit-native-operator",
        expected_revision=review["source_receipt"]["workflow_revision"])
    assert error is None, error
    selected, error = workflow.select_ocr_result(f["order_id"], f["evidence_id"], expected_revision=accepted["revision"])
    assert error is None, error
    sheet, error = workflow.build_sheet_from_selected_ocr(f["order_id"])
    assert error is None, error
    assert not sheet["sheet"].get("blockers"), sheet
    saved, error = workflow.save_sheet(order_id=f["order_id"], sheet=sheet["sheet"], edited_by="explicit-native-operator",
        expected_revision=selected["revision"])
    assert error is None, error
    assert saved["workflow"]["state"] == "sheet_saved"
    assert workflow.get_saved_sheet(f["order_id"])[1] is None
    refreshed, error = workflow.get_workflow_readonly(f["order_id"])
    assert error is None and refreshed["state"] == "sheet_saved"
    assert "evidence_unusable" not in refreshed["blockers"]
    assert order_service.get_ocr_sheet(f["order_id"])[1] is None
    assert order_service.get_latest_sheet_draft(f["order_id"])["base_evidence_run_id"] == f["evidence_id"]
    assert order_service.get_order_by_id(f["order_id"], allow_unresolved_menu_amount=True)["id"] == f["order_id"]
    assert workflow.get_workflow_readonly(f["order_id"])[0]["state"] == "sheet_saved"
    with f["factory"]() as session:
        assert session.get(OrderOcrEvidenceRun, f["evidence_id"]).payload_json == original
        assert session.get(OcrJob, f["job_id"]).metrics == metrics


@pytest.mark.parametrize("fault", ["revision", "pdf", "evidence", "missing", "nonnumeric"])
def test_formal_stale_or_uninterpreted_review_is_no_write(reviewed_order, fault):
    identity, _, review, engine = reviewed_order
    receipt = deepcopy(review["source_receipt"])
    values = {cell["sheet_cell"]: "" for cell in review["review_cells"]}
    revision = receipt["workflow_revision"]
    if fault == "revision": revision -= 1
    elif fault == "pdf": receipt["selected_pdf_receipt"]["sha256"] = "0" * 64
    elif fault == "evidence": receipt["recognition_evidence_run_id"] = "other"
    elif fault == "missing": values.clear()
    else: values = dict.fromkeys(values, "-")
    before = snapshot(engine)
    _, error = recovery.accept_review(identity, source_receipt=receipt, values=values, edited_by="explicit-operator",
        expected_revision=revision)
    assert error
    assert snapshot(engine) == before


@pytest.mark.parametrize("actor", [None, "", "   "])
def test_missing_actor_never_records_acceptance(reviewed_order, actor):
    identity, _, review, engine = reviewed_order
    before = snapshot(engine)
    result, error = recovery.accept_review(identity, source_receipt=review["source_receipt"],
        values={cell["sheet_cell"]: "" for cell in review["review_cells"]}, edited_by=actor,
        expected_revision=review["source_receipt"]["workflow_revision"])
    assert result is None and error == "recognition_operator_identity_required"
    assert snapshot(engine) == before


def test_pdf_changes_between_acknowledgement_and_selection_are_rejected(reviewed_order):
    identity, evidence_id, review, engine = reviewed_order
    accepted, error = recovery.accept_review(identity, source_receipt=review["source_receipt"],
        values={cell["sheet_cell"]: "" for cell in review["review_cells"]}, edited_by="explicit-operator",
        expected_revision=review["source_receipt"]["workflow_revision"])
    assert error is None
    path = Path(review["source_receipt"]["selected_pdf_receipt"]["storage_uri"])
    assert path.is_file()
    path.write_bytes(b"explicit owned source replacement after acknowledgement")
    before = snapshot(engine)
    result, error = workflow.select_ocr_result(identity, evidence_id, expected_revision=accepted["revision"])
    assert result is None and error
    assert snapshot(engine) == before


def test_other_saved_operator_draft_blocks_acceptance_without_loss(reviewed_order):
    identity, evidence_id, _, engine = reviewed_order
    with session_scope() as session:
        state = session.get(OrderWorkflowState, identity)
        session.add(OrderOcrEvidenceRun(id="other-evidence", order_id=identity,
            template_version_id=state.template_version_id, schema_version="authored", status="ready",
            artifact_digest="authored-other", payload_json={"table_raw": "explicit unrelated operator source"}))
        session.flush()
        session.add(OrderSheetDraft(id="other-operator-draft", order_id=identity,
            template_version_id=state.template_version_id, base_evidence_run_id="other-evidence",
            draft_state="saved", draft_sheet_json={"fields": ["qty.regular_x"], "rows": [["19"]]},
            edited_by="previous-operator"))
        session.flush()
        state.evidence_run_id = "other-evidence"
        state.draft_id = "other-operator-draft"
    review, error = recovery.get_review(identity, ocr_result_id=evidence_id)
    assert error is None
    before = snapshot(engine)
    result, error = recovery.accept_review(identity, source_receipt=review["source_receipt"],
        values={cell["sheet_cell"]: "" for cell in review["review_cells"]}, edited_by="explicit-operator",
        expected_revision=review["source_receipt"]["workflow_revision"])
    assert result is None and error == "recognition_acceptance_saved_draft_conflict"
    assert snapshot(engine) == before
