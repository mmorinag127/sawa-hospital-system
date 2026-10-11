from copy import deepcopy
from datetime import datetime
from hashlib import sha256
from types import SimpleNamespace
from uuid import uuid4
import json

import pytest

from src.db import session_scope
from src.models.order import Order
from src.models.document import OrderDocument
from src.models.uploaded_pdf import UploadedPdf
from src.models.order_ocr_evidence_run import OrderOcrEvidenceRun
from src.models.order_workflow_state import OrderWorkflowState
from src.services import config_service, order_service, order_workflow_v2_service as workflow
from tests.helpers.captured_input_fixture import authored_input, PERIOD
from src.services.template_field_schema_service import canonical_field_name_from_template_column


def test_actual_configured_input_upload_preserves_values_and_generation(monkeypatch):
    from google.cloud import storage
    from src.services import ocr_pipeline_service
    configured = {"facility": {"fax_template": {"template_id": "own", "zero": 0, "unknown": None}},
                  "policy": {"menu_match_min_ratio": 0}}
    uploaded = []
    blob = SimpleNamespace(generation="23", upload_from_string=lambda data, **kwargs: uploaded.append((data, kwargs)))
    names = []
    bucket = SimpleNamespace(blob=lambda name: names.append(name) or blob)
    monkeypatch.setattr(storage, "Client", lambda: SimpleNamespace(bucket=lambda name: bucket))
    receipt = ocr_pipeline_service._upload_configured_input(bucket="own-bucket", object_name="own.pdf", configured_input=configured)
    assert names == ["own.pdf.configured.json"]
    assert json.loads(uploaded[0][0]) == configured
    assert uploaded[0][1] == {"content_type": "application/json", "if_generation_match": 0}
    assert receipt == {"bucket": "own-bucket", "object_name": names[0], "generation": "23", "sha256": sha256(uploaded[0][0]).hexdigest()}
    blob.generation = None
    with pytest.raises(ValueError, match="configured_input_generation_missing"):
        ocr_pipeline_service._upload_configured_input(bucket="own-bucket", object_name="own.pdf", configured_input=configured)


@pytest.mark.parametrize("change", [None, "pdf", "config", "draft", "proof"])
def test_real_bagging_artifact_confirm_uses_original_inputs(isolated_formal_schema, monkeypatch, tmp_path, change):
    from src.models.order_sheet_draft import OrderSheetDraft
    from src.models.order import OrderLine
    order_id, document_id, evidence_id, draft_id, message = [str(uuid4()) for _ in range(5)]
    path = tmp_path / "bagging.pdf"
    body = b"own-bagging-source"
    path.write_bytes(body)
    configured = {"facility": {"facility_id": "fixture", "fax_template": {"template_id": "own", "columns": []}}, "policy": {"quantity_rules": {}}}
    projection_id, policy_path = authored_input(monkeypatch, tmp_path, configured, "fixture")
    quantity_field = canonical_field_name_from_template_column(configured["facility"]["fax_template"]["columns"][3])
    sheet = {"fields": ["date", "daypart", "menu", quantity_field], "rows": [["2026-10-07", "lunch", "own", 0]], "week_id": PERIOD}
    with session_scope() as session:
        session.add(Order(id=order_id, facility_code="fixture", template_version_id=projection_id, week_code=PERIOD, current_document_id=document_id, document_uri=str(path), message_id=message, received_at=datetime(2026, 10, 7)))
        session.add(OrderDocument(id=document_id, storage_uri=str(path), source_email_id=message))
        session.flush()
        session.add(UploadedPdf(id=str(uuid4()), message_id=message, content_sha256=sha256(body).hexdigest(), storage_uri=str(path), original_filename="bagging.pdf", received_at=datetime(2026, 10, 7)))
        session.add(OrderOcrEvidenceRun(id=evidence_id, order_id=order_id, template_version_id=projection_id, schema_version="fixture", payload_json={"quantity": 0}, artifact_digest="own"))
        session.flush()
        session.add(OrderSheetDraft(id=draft_id, order_id=order_id, template_version_id=projection_id, base_evidence_run_id=evidence_id, draft_sheet_json=sheet, draft_state="saved"))
        session.flush()
        session.add(OrderWorkflowState(order_id=order_id, template_version_id=projection_id, evidence_run_id=evidence_id, draft_id=draft_id, state="sheet_saved"))
    with session_scope() as session:
        revision = session.get(OrderWorkflowState, order_id).revision
    accepted, error = workflow.run_bagging(order_id, expected_revision=revision)
    assert error is None
    assert accepted["bagging_result"]["materialization_candidate"]["lines"][0]["quantity_original"] == 0
    revision = accepted["workflow"]["revision"]
    # A real own metadata command/finalizer advance retains, rather than recaptures, artifact inputs.
    with session_scope() as session:
        state = session.get(OrderWorkflowState, order_id)
        state.headline = "own review"
        assert workflow._finalize_workflow_mutation(session, state) is None
        revision = state.revision
    if change == "pdf":
        path.write_bytes(b"replaced")
    elif change == "config":
        configured["policy"]["quantity_rules"]["minimum"] = 1
        policy_path.write_text(json.dumps(configured["policy"]))
    elif change in {"draft", "proof"}:
        with session_scope() as session:
            if change == "draft":
                session.get(OrderSheetDraft, draft_id).draft_sheet_json = {**sheet, "rows": [["2026-10-07", "lunch", "own", 99]]}
            else:
                state = session.get(OrderWorkflowState, order_id)
                meta = workflow._workflow_meta(state)
                meta.pop("bagging_input_proof")
                workflow._write_workflow_meta(state, meta)
                session.flush()
                revision = state.revision
    result, error = workflow.final_confirm(order_id, expected_revision=revision)
    with session_scope() as session:
        lines = session.query(OrderLine).filter(OrderLine.order_id == order_id).all()
        if change is None:
            assert error is None and result is not None
            assert len(lines) == 1 and lines[0].quantity_original == 0
        else:
            assert error is not None and lines == []


@pytest.mark.parametrize("replace_pdf", [False, True])
def test_real_reservation_mapper_commit_and_runner_receipt(isolated_formal_schema, monkeypatch, tmp_path, replace_pdf):
    """Real formal schema, ORM version mapper, reservation, source guard and runner."""
    order_id, document_id, evidence_id, message = [str(uuid4()) for _ in range(4)]
    path = tmp_path / "source.pdf"
    original = b"selected-source-contract-zero"
    path.write_bytes(original)
    configured = {"facility": {"facility_id": "fixture", "fax_template": {"template_id": "fixture-template", "unknown": None}},
                  "policy": {"quantity_rules": {"minimum": 0}}}
    projection_id, _ = authored_input(monkeypatch, tmp_path, configured, "fixture")
    now = datetime(2026, 10, 7)
    with session_scope() as session:
        session.add(Order(id=order_id, facility_code="fixture", template_version_id=projection_id, week_code=PERIOD, current_document_id=document_id,
                          document_uri=str(path), message_id=message, received_at=now))
        session.add(OrderDocument(id=document_id, storage_uri=str(path), source_email_id=message, received_at=now))
        session.flush()
        session.add(UploadedPdf(id=str(uuid4()), message_id=message, content_sha256=sha256(original).hexdigest(),
                                storage_uri=str(path), original_filename="source.pdf", received_at=now,
                                current_order_id=order_id, current_document_id=document_id))
        session.add(OrderOcrEvidenceRun(id=evidence_id, order_id=order_id, template_version_id=projection_id, schema_version="fixture", status="ready",
                                       source="fixture", payload_json={"quantity": 0}, artifact_digest="fixture-digest"))
        session.flush()
        session.add(OrderWorkflowState(order_id=order_id, template_version_id=projection_id, evidence_run_id=evidence_id, state="ocr_selected"))
    with session_scope() as session:
        initial_revision = session.get(OrderWorkflowState, order_id).revision
    sheet = {"quantity": 0}
    accepted, error = workflow.start_sheet_auto_edit_job(order_id=order_id, sheet=sheet, expected_revision=initial_revision)
    assert error is None
    receipt = accepted["job"]
    with session_scope() as session:
        current = session.get(OrderWorkflowState, order_id)
        stored = workflow._workflow_meta(current)[workflow.SHEET_AUTO_EDIT_JOB_META_KEY]
        assert current.revision == initial_revision + 1
        assert receipt["workflow_completion_revision"] == stored["workflow_completion_revision"] == current.revision
        assert stored["start_input"]["workflow_revision"] == current.revision
    calls = []
    monkeypatch.setattr(workflow, "render_pdf_to_png_bytes", lambda **kwargs: b"png")
    def propose(**kwargs):
        calls.append(deepcopy(kwargs["sheet"]))
        return {"quantity": 0}
    monkeypatch.setattr(workflow.workflow_v2_sheet_review_service, "propose_auto_sheet_edits", propose)
    if replace_pdf:
        path.write_bytes(b"different-selected-source")
    workflow.run_sheet_auto_edit_job(order_id=order_id, job_id=receipt["job_id"], sheet=sheet,
                                     expected_revision=receipt["workflow_completion_revision"])
    with session_scope() as session:
        current = session.get(OrderWorkflowState, order_id)
        terminal = workflow._workflow_meta(current)[workflow.SHEET_AUTO_EDIT_JOB_META_KEY]
        assert current.revision == initial_revision + 2
        assert terminal["workflow_result_revision"] == current.revision
        if replace_pdf:
            assert calls == []
            assert terminal["status"] == "failed" and terminal["result"] is None
            assert terminal["error"] == "selected_pdf_bytes_changed"
        else:
            assert calls == [sheet]
            assert terminal["status"] == "done" and terminal["result"] == {"quantity": 0}


def test_quad_consumer_rejects_different_pdf_but_retains_own_revision_advances(monkeypatch):
    configured = {"facility": {"quantity": 0, "unknown": None, "fax_template": {"template_id": "own", "page": 1, "columns": [{"role": "menu", "index": 0}]}}, "policy": {"minimum": 0}}
    source = {"storage_uri": "own-A", "sha256": "a" * 64}
    identity = {"facility_code": "fixture", "week_code": "2026-10"}
    physical = [{"menu_date": "2026-10-07", "daypart": "lunch", "slot_index": 0, "name": "own"}]
    monkeypatch.setattr(order_service, "_build_position_menu_entries_safe", lambda *args: deepcopy(physical))
    override = {"quad_px": [[0, 0], [10, 0], [10, 10], [0, 10]],
                "coordinate_space": {"mode": "render_width", "width": 100},
                "source_receipt": {"selected_pdf_receipt": source, "workflow_revision": 1,
                    "order_input_identity": identity,
                    "configured_input_digest": workflow._stable_json_hash(configured)}}
    override["source_receipt"]["physical_menu_entries"] = deepcopy(physical)
    override["source_receipt"]["geometry_source_digest"] = order_service.geometry_source_digest(source, configured, identity, physical_entries=physical)
    points, _ = order_service._normalize_operator_quad_override(override, render_width=100,
                      selected_pdf_receipt=deepcopy(source), configured_input=deepcopy(configured), order_input_identity=identity)
    assert points == override["quad_px"]
    for foreign in ({**source, "storage_uri": "own-B"}, {**source, "sha256": "b" * 64}):
        with pytest.raises(ValueError, match="quad_source_input_changed"):
            order_service._normalize_operator_quad_override(override, render_width=100,
                                selected_pdf_receipt=foreign, configured_input=configured, order_input_identity=identity)


@pytest.mark.parametrize("outcome", ["failed", "warning_done", "valid_done", "pending"])
def test_actual_owned_pipeline_gate_and_recovery_block_failed_stages(isolated_formal_schema, monkeypatch, tmp_path, outcome):
    from src.services import ocr_job_service as jobs
    from src.services.workflow_attempt_guard_service import capture_nonworkflow_execution, nonworkflow_execution_context
    from src.workers import ingest_worker
    order_id, document_id, message, job_id = [str(uuid4()) for _ in range(4)]
    path = tmp_path / "pipeline.pdf"
    body = b"own-pipeline-body"
    path.write_bytes(body)
    configured = {"facility": {"facility_id": "fixture", "fax_template": {"template_id": "own"}}, "policy": {}}
    projection_id, _ = authored_input(monkeypatch, tmp_path, configured, "fixture")
    with session_scope() as session:
        session.add(Order(id=order_id, facility_code="fixture", template_version_id=projection_id, week_code=PERIOD, received_at=datetime(2026, 10, 7), current_document_id=document_id, document_uri=str(path), message_id=message))
        session.add(OrderDocument(id=document_id, storage_uri=str(path), source_email_id=message))
        session.flush()
        session.add(UploadedPdf(id=str(uuid4()), message_id=message, content_sha256=sha256(body).hexdigest(), storage_uri=str(path), original_filename="pipeline.pdf", received_at=datetime(2026, 10, 7)))
    jobs.create_job(job_id, input_reference=str(path), order_id=order_id)
    jobs.update_job(job_id, status="running")
    start = capture_nonworkflow_execution(job_id, order_id=order_id, input_reference=str(path))
    target = "gs://own-bucket/output/own.pdf.g19.json"
    with nonworkflow_execution_context(start):
        assert jobs.record_own_pipeline_delivery(job_id, selected_input_reference=str(path), input_reference="gs://own-bucket/input/own.pdf", output_reference=target, generation="19", input_sha256=sha256(body).hexdigest())
        result = {"status": "failed" if outcome == "failed" else "running" if outcome == "pending" else "done",
                  "stage": "ocr" if outcome == "pending" else "error" if outcome == "failed" else "done",
                  "job_id": "own-bucket:input/own.pdf:19", "input_reference": "gs://own-bucket/input/own.pdf", "input_generation": "19", "output_reference": target,
                  "execution_owner": {"job_id": job_id, "order_id": order_id, "workflow_attempt_id": None, "workflow_revision": None, "nonworkflow_execution_id": start["execution_id"]},
                  "input_sha256": sha256(body).hexdigest(), "configured_input_sha256": sha256(json.dumps(configured, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
                  "canonical_adoption_allowed": outcome == "valid_done", "canonical_stages": {"classification": "passed", "roi": "passed", "correction": "disabled"},
                  "table_rows": [["2026-10-07", "lunch", "own", 0]]}
        monkeypatch.setattr(ingest_worker, "load_bytes_from_uri", lambda _: json.dumps(result).encode())
        if outcome in {"failed", "warning_done"}:
            with pytest.raises(jobs.OwnPipelineLineageError, match="ocr_pipeline_required_stage_failed"):
                jobs.validate_own_pipeline_result(result, target)
            with pytest.raises(jobs.OwnPipelineLineageError, match="ocr_pipeline_required_stage_failed"):
                ingest_worker._load_completed_ocr_output(target)
            from src.services import ocr_evidence_service
            try:
                evidence = ocr_evidence_service.persist_evidence_run(order_id, {**result, "engine": "yomitoku"}, workflow_job_id=job_id)
            except jobs.OwnPipelineLineageError as exc:
                assert str(exc) == "ocr_pipeline_required_stage_failed"
            else:
                assert evidence is None
            with session_scope() as session:
                assert session.query(OrderOcrEvidenceRun).filter(OrderOcrEvidenceRun.order_id == order_id).count() == 0
        elif outcome == "pending":
            assert ingest_worker._load_completed_ocr_output(target) is None
        else:
            assert ingest_worker._load_completed_ocr_output(target)["table_rows"][0][-1] == 0


def test_materialization_uses_captured_policy_and_blocks_changed_adoption(monkeypatch):
    template = {"columns": [
        {"index": 0, "role": "date", "name": "date"},
        {"index": 1, "role": "daypart", "name": "daypart"},
        {"index": 2, "role": "menu", "name": "menu"},
        {"index": 3, "role": "quantity", "diet_type": "regular", "area_id": None},
    ]}
    configured = {"facility": {"facility_id": "fixture", "fax_template": template},
                  "policy": {"quantity_rules": {"minimum": 0}, "menu_match_min_ratio": 0}}
    field = canonical_field_name_from_template_column(template["columns"][3])
    draft = {"id": "own-draft", "draft_sheet_json": {"fields": ["date", "daypart", "menu", field], "rows": [["2026-10-07", "morning", "fixture", 0]], "week_id": PERIOD}}
    monkeypatch.setattr(config_service, "load_ingest_policy", lambda: pytest.fail("late cached policy"))
    monkeypatch.setattr(order_service, "_resolve_effective_sheet_template", lambda **_: pytest.fail("late cached template"))
    seen = []
    monkeypatch.setattr(order_service, "_draft_sheet_supports_direct_materialization", lambda **_: True)
    def build(**kwargs):
        seen.append(kwargs)
        return [{"date": datetime(2026, 10, 7).date(), "daypart": "morning", "menu_name": "fixture", "quantity_original": 0}]
    monkeypatch.setattr(order_service, "_build_materialization_lines_from_sheet_rows", build)
    monkeypatch.setattr(order_service, "_build_materialization_quantity_guard_detail", lambda **_: None)
    candidate = order_service._build_materialization_candidate_from_draft_record("own", draft_record=draft, facility_id="fixture",
                    existing_week_code=PERIOD, received_at=datetime(2026, 10, 7), configured_input=configured)
    assert seen and candidate["configured_input"] == configured
    assert seen[0]["date_policy"] == configured["policy"]
    assert candidate["lines"][0]["quantity_original"] == 0
    monkeypatch.setattr(config_service, "capture_authored_processing_config", lambda _: {"policy": {"minimum": 1}})
    with pytest.raises(order_service.ConfirmMaterializationError, match="captured authored configuration changed"):
        order_service._materialize_confirmed_lines_from_candidate(SimpleNamespace(), SimpleNamespace(facility_code="fixture"), candidate)
