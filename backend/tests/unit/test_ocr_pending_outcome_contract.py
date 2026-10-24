from contextlib import contextmanager
from copy import deepcopy
from hashlib import sha256
from types import SimpleNamespace
import pytest

from src.api import orders
from src.services import order_service, ocr_job_service
from tests.unit.test_h2_operator_repair_revision import repair_order  # noqa: F401
from tests.integration.test_ocr_pipeline_service_wait import owned_pipeline, pipeline_transport  # noqa: F401


@pytest.fixture
def ingest_pipeline(pending_source, monkeypatch, tmp_path, pipeline_transport):
    import json
    from datetime import date, datetime
    from pathlib import Path
    import fitz
    from src.db import session_scope
    from src.models.ingest_delivery import IngestDelivery
    from src.models.ocr_job import OcrJob
    from src.services import ingest_job_service as ingest, ingest_delivery_service as delivery
    from src.services import workflow_attempt_guard_service as guard
    from src.services.ingest_facility_selection_service import capture_selection
    from src.services.canonical_facility_store import read_snapshot
    from src.services.storage_service import load_selected_pdf_bytes
    from src.workers import ingest_worker
    from tests.helpers.ingest_original import authored_sources, configure_delivery
    from tests.unit.test_j1_ingest_lifecycle import add_upload

    payload = deepcopy(pending_source)
    uri = payload["pdf_uri"]
    add_upload(session_scope, payload)
    created = ingest.create_ingest_receipt(payload)
    identity, generation = delivery.delivery_for_job(created["id"], created["generation"], created["input_digest"])
    claim = delivery.claim_delivery(identity, generation)
    assert claim is not None
    pipeline_transport.observations["missing"] = True
    monkeypatch.setenv("OCR_PIPELINE_TIMEOUT_SECONDS", "0")
    with delivery.claim_context(claim), ingest.ingest_execution_context(claim["lease"]):
        ingest_worker._process_ingest_inline(**payload)
        with session_scope() as session:
            recorded = session.get(IngestDelivery, identity)
            handoff = deepcopy(recorded.handoff_receipt)
            assert isinstance(handoff, dict)
            job_id = handoff["job_id"]
            job = session.get(OcrJob, job_id)
            execution = deepcopy(job.metrics["nonworkflow_execution"])
            assert execution["ingest_lease"] == claim["lease"]
            assert "mapping_input" in execution["first_pass_application"]
        with guard.nonworkflow_execution_context(execution):
            original_request = {"ocr_prompt": " custom prompt ", "prompt_preset": "custom-preset",
                "ocr_provider": "gemini", "ocr_model": "custom-model", "llm_assist": True}
            assert ocr_job_service.capture_own_reparse_request(job_id, original_request)
            selected = guard.processing_start_pdf_receipt()
            pdf_bytes = load_selected_pdf_bytes(uri, selected)
            assert sha256(pdf_bytes).hexdigest() == payload["content_sha256"]
            pipeline_transport.observations["missing"] = False
            pipeline_transport.observations["payload_overrides"] = {
                "engine": "yomitoku", "template_id": "authored-component",
                "table_rows": [["2/15", "morning", "meal", "0"]]}
            yield SimpleNamespace(job_id=job_id, order_id=execution["order_id"], execution=execution,
                claim=claim, original_request=original_request, selected=selected, pdf=pdf_bytes,
                original_uri=uri, transport=pipeline_transport, payload=payload)


@pytest.fixture
def pending_source(isolated_formal_schema, monkeypatch, tmp_path):
    import json
    from datetime import date, datetime
    from pathlib import Path
    import fitz
    from src.db import session_scope
    from src.models.menu import MenuMaster, MonthlyMenuEntry, MonthlyMenuItem
    from src.services.ingest_facility_selection_service import capture_selection
    from src.services.canonical_facility_store import read_snapshot
    from tests.helpers.ingest_original import authored_sources, configure_delivery
    configure_delivery(monkeypatch)
    uri, _ = authored_sources(monkeypatch, tmp_path, facility_id="authored-facility",
        menu_date=date(2026, 2, 15), seed_menu=False)
    source = tmp_path / "authored-facilities.json"
    master = json.loads(source.read_text())
    facility = master["facilities"][0]
    facility["fax_template"].update(main_ocr_provider="pipeline",
        main_ocr_row_fields=["date_mmdd", "daypart", "menu", "qty.regular_2f"])
    facility["versions"][0]["facility"] = deepcopy({k: v for k, v in facility.items() if k != "versions"})
    source.write_text(json.dumps(master))
    # An explicit synthetic component original, not a reused live FAX.
    with fitz.open() as pdf:
        page = pdf.new_page()
        page.insert_text((30, 50), "authored-facility / 2026-02-15..2026-02-21 / morning / meal / 0")
        body = pdf.tobytes()
    Path(uri).write_bytes(body)
    from src.models.menu import MenuMaster, MonthlyMenuEntry, MonthlyMenuItem
    with session_scope() as session:
        # Explicit component recipe metadata, not an inferred order quantity.
        session.add(MenuMaster(id="pending-meal-recipe", name="meal", normalized_name="meal",
            qty_per_serving=1, unit_type="g", category="主菜"))
        session.flush()
        session.add(MonthlyMenuItem(id="pending-meal-item", monthly_menu_id="2026-02",
            menu_master_id="pending-meal-recipe", name="meal", diet_type="regular",
            facility_override="authored-facility", daypart="breakfast", category="主菜",
            qty_per_serving=1, unit_type="g", master_resolution_mode="manual"))
        session.add(MonthlyMenuEntry(id="pending-physical-meal", monthly_menu_id="2026-02",
            menu_date=date(2026, 2, 15), daypart="breakfast", name="meal",
            slot_index=0, category="主菜", diet_type="regular", facility_override="authored-facility"))
    payload = {"message_id": "pending-original", "pdf_uri": uri,
        "received_at": datetime(2026, 2, 15).isoformat(), "facility_hint": "authored-facility",
        "week_hint": "2026-02@2026-02-15~2026-02-21", "skip_ocr": False,
        "content_sha256": sha256(body).hexdigest(), "unknown": {"zero": 0, "null": None}}
    payload["facility_selection"] = capture_selection("authored-facility", "2026-02-15",
        read_snapshot().revision, "authored-original", payload["week_hint"])
    return payload


@pytest.fixture
def native_pending_recovery(pending_source, monkeypatch, tmp_path):
    from datetime import datetime
    from pathlib import Path
    from src.db import session_scope
    from src.models.order import Order, OrderLine
    from src.models.ocr_job import OcrJob
    from src.models.document import OrderDocument
    from src.models.uploaded_pdf import UploadedPdf
    from src.services import uploaded_pdf_service as uploads
    from src.services import ingest_job_service as ingest, ingest_delivery_service as delivery
    from src.services import order_workflow_v2_service as workflow
    from src.services import workflow_attempt_guard_service as guard
    from src.services import hakodate_cell_ocr_batch_service as batch
    from src.services.storage_service import load_selected_pdf_bytes
    from src.workers.ingest_mail_adapter import parse_ingest_payload
    from tests.unit.test_h2_operator_repair_revision import _provider_output_files

    payload = pending_source
    source = Path(payload["pdf_uri"])
    saved = SimpleNamespace(message_id=payload["message_id"], pdf_uri=str(source),
        received_at=datetime.fromisoformat(payload["received_at"]), original_filename=source.name,
        content_sha256=payload["content_sha256"])
    upload, duplicate = uploads.create_uploaded_pdf_from_upload(saved=saved,
        facility_hint=payload["facility_hint"], week_hint=payload["week_hint"], facility_name=None,
        skip_ocr=False, source_kind="manual_upload", page_count=1,
        facility_selection=payload["facility_selection"], intake_protocol="native_ingest_v2")
    assert duplicate is False
    intent = upload["ingest_receipt"]
    identity, generation = delivery.delivery_for_job(intent["id"], intent["generation"], intent["input_digest"])
    claim = delivery.claim_delivery(identity, generation)
    parsed = parse_ingest_payload(claim["lease"]["payload"])
    job_id = "pending-native-recovery"
    with delivery.claim_context(claim), ingest.ingest_execution_context(claim["lease"]):
        ingest.prepare_ingest_ocr_job(claim["lease"], job_id, parsed)
        registered = order_service.create_order_from_ingest(parsed, lines=None,
            document_status="processing", error_message="ocr_pending", registration_job_id=job_id)
        linked = registered["ingest_registration_receipt"]
        assert uploads.refresh_uploaded_pdf_links(upload["id"], lease=claim["lease"]["uploaded_lease"],
            order_id=linked["order_id"], document_id=linked["document_id"]) is not None
        order_id = linked["order_id"]
        with session_scope() as session:
            order = session.get(Order, order_id)
            workflow.initialize_ingest_context(session, order, payload["facility_selection"], job_id)
            document_id = order.current_document_id
            # An unrelated stored component row is intentionally not an OCR input.
            session.add(Order(id="unrelated-saved-order", document_uri="component://unrelated",
                message_id="unrelated-saved-message"))
            session.flush()
            session.add(OrderLine(id="unrelated-saved-999", order_id="unrelated-saved-order",
                menu_name="meal", quantity_original=999, quantity_corrected=999,
                change_note="unrelated operator value"))
        boundary = guard.capture_nonworkflow_execution(job_id, order_id=order_id,
            input_reference=str(source), expected_document_id=document_id, ingest_ocr_mode="hakodate")
        assert boundary["intake_protocol"] == "native_ingest_v2"
        assert "first_pass_application" not in boundary
        with guard.nonworkflow_execution_context(boundary):
            captured = guard.processing_start_read_input()
            original_pdf = load_selected_pdf_bytes(str(source), boundary["selected_pdf_receipt"])
            assert sha256(original_pdf).hexdigest() == payload["content_sha256"]
            calls, outputs = [], []
            def render(**kwargs):
                assert kwargs["order_id"] == order_id
                return {"configured_input": deepcopy(captured["configured_input"]),
                    "selected_pdf_receipt": deepcopy(boundary["selected_pdf_receipt"]),
                    "_verified_pdf_bytes": load_selected_pdf_bytes(str(source), boundary["selected_pdf_receipt"])}
            def recognize(*, item, output_dir, **kwargs):
                assert bytes(item["_verified_pdf_bytes"]) == bytes(original_pdf)
                regions = [{**deepcopy(region), "bbox": [i * 20, 0, i * 20 + 10, 10]}
                           for i, region in enumerate(item["original_target_semantics"])]
                assert regions and all(region["role"] == "quantity" for region in regions)
                values = {region["sheet_cell"]: "0" for region in regions}
                records = [{**region, "ocr_text": "0", "recognizer_score": 1.0,
                            "recognizer_accepted_candidate": {"normalized_digits": "0"}}
                           for region in regions]
                produced = _provider_output_files(output_dir, records=records, regions=regions, sheet_values=values)
                calls.append(deepcopy(records))
                outputs.append(produced)
                return SimpleNamespace(metrics={"expected_sheet_cells": item["original_expected_sheet_cells"]},
                    expected_sheet_cells=tuple(item["original_expected_sheet_cells"]), outputs=produced,
                    ocr_engine="component-external-recognizer"), None
            # Same render/recognizer leaf boundary as the shared native producer
            # fixture. No owner, cache, evidence, terminal or recovery result is doubled.
            monkeypatch.setattr(order_service, "_build_live_hakodate_manifest_item", render)
            monkeypatch.setattr(batch, "build_hakodate_best_method_for_manifest_item", recognize)
            with session_scope() as session:
                job = session.get(OcrJob, job_id)
                assert job.status == "running"
                assert "rerun_terminal_receipt" not in job.metrics
                assert session.get(OrderDocument, document_id).status == "processing"
                assert session.get(UploadedPdf, upload["id"]).status == "processing"
                assert session.query(OrderLine).filter_by(order_id=order_id).count() == 0
            yield SimpleNamespace(job_id=job_id, order_id=order_id, boundary=boundary,
                claim=claim, source=source, pdf=bytes(original_pdf), calls=calls, outputs=outputs,
                upload_id=upload["id"], source_config=tmp_path / "authored-facilities.json")


@pytest.fixture
def waiting_job(owned_pipeline):
    from src.db import session_scope
    from src.models.ocr_job import OcrJob
    f = owned_pipeline
    output = f.run(wait=False)
    result = order_service._mark_reparse_job_awaiting_output(
        f.job_id, input_reference=output["input_reference"],
        output_reference=output["output_reference"], recovery_delay_seconds=30)
    assert result["outcome"] == "pending"
    with session_scope() as session:
        row = session.get(OcrJob, f.job_id)
        values = {column.name: deepcopy(getattr(row, column.name)) for column in OcrJob.__table__.columns}
    job = SimpleNamespace(**values, attempt=f.attempt, revision=f.revision,
        selected_document=f.receipt()["selected_pdf_receipt"]["document_id"],
        delivery_input=output["input_reference"], delivery_output=output["output_reference"],
        original_start=deepcopy(values["metrics"]["reparse_read_input"]))
    return job, result


def job_snapshot(job):
    from src.db import get_engine
    from tests.unit.test_disabled_facility_columns import _snapshot
    return _snapshot(get_engine())


def test_real_waiting_write_returns_nonterminal_original_fence(waiting_job):
    job, result = waiting_job
    assert job.status == "awaiting_output"
    assert job.input_reference == job.metrics["selected_pdf_receipt"]["storage_uri"]
    assert job.input_reference != job.delivery_input
    assert result["pending_receipt"]["input_reference"] == job.delivery_input
    assert "rerun_terminal_receipt" not in job.metrics
    assert job.metrics["reparse_read_input"] == job.original_start
    assert result["pending_receipt"] == job.metrics["pending_receipt"]
    assert order_service.is_own_pending_ocr_result(
        result, order_id=job.order_id, job_id=job.id,
        workflow_attempt_id=job.attempt, workflow_revision=job.revision,
    )
    assert result["pending_receipt"]["selected_document_id"] == job.selected_document
    assert not order_service.is_own_pending_ocr_result(result, order_id=job.order_id, job_id=job.id)


@pytest.mark.parametrize("outcome", ["pending", "missing", "wrong_revision"])
def test_actual_reparse_callback_distinguishes_wait_from_missing_terminal(monkeypatch, waiting_job, outcome):
    job, result = waiting_job
    supplied = deepcopy(result) if outcome != "missing" else {}
    if outcome == "wrong_revision":
        supplied["pending_receipt"]["workflow_revision"] = job.revision - 1
    monkeypatch.setattr(order_service, "reparse_order", lambda *args, **kwargs: (supplied, None))
    completed = []
    def complete(*args, **kwargs):
        completed.append(kwargs)
        return None, "superseded"
    monkeypatch.setattr(orders, "_complete_workflow_ocr_run", complete)
    before = job_snapshot(job)
    def invoke():
        return orders._run_reparse_background(
            job.order_id, ocr_prompt=" custom prompt ", selected_document_id=job.selected_document, llm_assist=True,
            expected_revision=job.revision, workflow_attempt_id=job.attempt, ocr_job_id=job.id,
        )
    if outcome == "pending":
        assert invoke() == supplied
        assert completed == []
        assert job.status == "awaiting_output"
    else:
        with pytest.raises(ValueError, match="reparse_persisted_result_missing"):
            invoke()
        assert completed == []
    assert job_snapshot(job) == before


def test_resume_uses_original_waiting_receipt_and_own_returned_id(monkeypatch, waiting_job):
    job, result = waiting_job
    receipt = result["pending_receipt"]
    seen = []
    payload = {"recognized_quantity": 0}
    monkeypatch.setattr(order_service, "get_job_request_mode", lambda job: "llm_reparse")
    def load(ref, **kwargs):
        assert ref == job.delivery_output
        return payload
    def resume(order_id, **kwargs):
        seen.append(kwargs)
        return {"reparse_evidence_run_id": "own-evidence", "terminal_receipt": {
            "job_id": job.id, "order_id": job.order_id, "workflow_attempt_id": job.attempt,
            "workflow_revision": job.revision, "evidence_run_id": "own-evidence", "status": "done",
        }}, None
    monkeypatch.setattr(order_service, "_load_pipeline_output_with_retry", load)
    monkeypatch.setattr(order_service, "_output_is_pending", lambda payload: False)
    monkeypatch.setattr(order_service, "reparse_order", resume)
    assert order_service.reconcile_pending_workflow_reparse({"id": job.id, "order_id": job.order_id, "metrics": {}}, receipt)
    assert seen[0]["workflow_attempt_id"] == job.attempt
    assert seen[0]["expected_workflow_revision"] == job.revision
    assert seen[0]["selected_document_id"] == job.selected_document
    assert seen[0]["resume_first_pass_payload"] == {"recognized_quantity": 0}
    assert seen[0]["resume_first_pass_output_reference"] == job.delivery_output
    assert seen[0]["ocr_prompt"] == " custom prompt "
    assert seen[0]["prompt_preset"] == "custom-preset"
    assert seen[0]["ocr_provider"] == "gemini"
    assert seen[0]["ocr_model"] == "custom-model"
    assert seen[0]["llm_assist"] is True


def test_foreign_delivery_cannot_create_waiting_receipt(waiting_job):
    job, _ = waiting_job
    before = job_snapshot(job)
    with order_service.workflow_ocr_write_context(job.attempt, job.revision, job.id, job.order_id):
        assert order_service._mark_reparse_job_awaiting_output(
            job.id, input_reference="foreign-upload", output_reference=job.delivery_output,
        ) is None
    assert job_snapshot(job) == before


def test_changed_selected_source_cannot_register_delivery(waiting_job):
    job, _ = waiting_job
    before = job_snapshot(job)
    with order_service.workflow_ocr_write_context(job.attempt, job.revision, job.id, job.order_id):
        assert not ocr_job_service.record_own_pipeline_delivery(
            job.id, selected_input_reference="changed-document", input_reference="new-upload",
            output_reference="new-output", generation="new-generation",
        )
    assert job_snapshot(job) == before


def test_superseded_execution_cannot_register_request_or_delivery(monkeypatch, waiting_job):
    job, _ = waiting_job
    before = job_snapshot(job)
    with order_service.workflow_ocr_write_context("old-attempt", 6, job.id, job.order_id):
        assert not ocr_job_service.capture_own_reparse_request(job.id, {
            "ocr_prompt": None, "prompt_preset": None, "ocr_provider": None, "ocr_model": None, "llm_assist": False,
        })
        assert not ocr_job_service.record_own_pipeline_delivery(
            job.id, selected_input_reference=job.input_reference, input_reference="new-upload",
            output_reference="new-output", generation="new-generation",
        )
    assert job_snapshot(job) == before


def test_original_request_null_presence_is_required_and_immutable(waiting_job):
    job, _ = waiting_job
    original = order_service._extract_reparse_request_kwargs_from_job({"metrics": job.metrics})
    with order_service.workflow_ocr_write_context(job.attempt, job.revision, job.id, job.order_id):
        assert ocr_job_service.capture_own_reparse_request(job.id, original)
        assert not ocr_job_service.capture_own_reparse_request(job.id, {**original, "ocr_model": None})
    metadata = order_service._build_reparse_request_metadata(
        ocr_prompt=None, prompt_preset=None, requested_provider=None, ocr_model=None, llm_assist=False,
    )
    assert order_service._extract_reparse_request_kwargs_from_job({"metrics": metadata}) == {
        "ocr_prompt": None, "prompt_preset": None, "ocr_provider": None, "ocr_model": None, "llm_assist": False,
    }
    del metadata["reparse_ocr_model"]
    with pytest.raises(ValueError, match="reparse_original_request_missing"):
        order_service._extract_reparse_request_kwargs_from_job({"metrics": metadata})


def test_actual_queue_captures_complete_original_request(monkeypatch, repair_order):
    from google.api_core.exceptions import NotFound
    from google.cloud import tasks_v2
    from src.db import session_scope
    from src.models.ocr_job import OcrJob
    from src.models.workflow_ocr_delivery import WorkflowOcrDelivery
    from tests.helpers.ingest_original import configure_delivery

    configure_delivery(monkeypatch)
    order_id, revision, _ = repair_order
    tasks = {}
    class Client:
        def get_task(self, request):
            if request["name"] not in tasks:
                raise NotFound("component transport has not received this task")
            return tasks_v2.Task(**tasks[request["name"]])
        def create_task(self, request):
            task = deepcopy(request["task"])
            assert task["name"] not in tasks
            tasks[task["name"]] = task
            return tasks_v2.Task(**task)
    # Only the remote task transport is doubled; queue capture, delivery intent,
    # dispatch acknowledgement and CAS are the real application implementations.
    monkeypatch.setattr(tasks_v2, "CloudTasksClient", Client)
    result = orders._enqueue_order_reparse_job(
        order_id, ocr_prompt=" custom prompt ", prompt_preset="custom-preset",
        ocr_provider="gemini", ocr_model="custom-model", llm_assist=True,
        request_mode="llm_reparse", expected_revision=revision)
    assert result["accepted"] is True
    assert result["workflow"]["revision"] == revision + 1
    with session_scope() as session:
        job = session.get(OcrJob, result["ocr_job_id"])
        assert job.order_id == order_id
        original = order_service._extract_reparse_request_kwargs_from_job({"metrics": job.metrics})
        owner = session.query(WorkflowOcrDelivery).filter_by(
            job_id=job.id, workflow_attempt_id=job.metrics["workflow_attempt_id"]).one()
        assert owner.workflow_revision == result["workflow"]["revision"]
        assert owner.selected_pdf_receipt == job.metrics["selected_pdf_receipt"]
        assert owner.request_identity["reparse_read_input"] == job.metrics["reparse_read_input"]
        assert len(tasks) == 1
        assert owner.task_contract["name"] in tasks
    assert original == {
        "ocr_prompt": " custom prompt ", "prompt_preset": "custom-preset", "ocr_provider": "gemini",
        "ocr_model": "custom-model", "llm_assist": True,
    }


def test_actual_force_upload_timeout_records_own_delivery_before_wait(owned_pipeline):
    from src.db import session_scope
    from src.models.ocr_job import OcrJob
    from src.services import ocr_pipeline_service

    f = owned_pipeline
    f.observations["missing"] = True
    f.monkeypatch.setenv("OCR_PIPELINE_TIMEOUT_SECONDS", "0")
    with pytest.raises(ocr_pipeline_service.OCRPipelineOutputPendingError) as pending:
        f.run(force_upload=True)
    recorded = f.receipt()
    assert recorded["job_id"] == f.job_id
    assert recorded["workflow_attempt_id"] == f.attempt
    assert recorded["workflow_revision"] == f.revision
    assert recorded["selected_input_reference"] == f.original_uri
    assert recorded["input_reference"] == pending.value.input_reference != f.original_uri
    assert recorded["output_reference"] == pending.value.output_reference
    assert recorded["input_sha256"] == sha256(f.pdf).hexdigest()
    result = order_service._mark_reparse_job_awaiting_output(
        f.job_id, input_reference=pending.value.input_reference,
        output_reference=pending.value.output_reference)
    assert result["outcome"] == "pending"
    with session_scope() as session:
        job = session.get(OcrJob, f.job_id)
        assert job.status == "awaiting_output"
        assert job.input_reference == f.original_uri
        assert "rerun_terminal_receipt" not in job.metrics
        assert result["pending_receipt"]["pipeline_delivery_receipt"] == job.metrics["pipeline_delivery_receipt"]
    assert result["pending_receipt"]["request_kwargs"]["ocr_model"] == "custom-model"

def test_nonworkflow_new_upload_wait_keeps_original_document_and_request(ingest_pipeline):
    from src.db import session_scope
    from src.models.ocr_job import OcrJob
    from src.services import ocr_pipeline_service
    from tests.unit.test_disabled_facility_columns import _snapshot
    from src.db import get_engine

    f = ingest_pipeline
    output = ocr_pipeline_service.run_ocr_pipeline(pdf_bytes=f.pdf, job_id=f.job_id,
        facility_id="authored-facility", input_reference=f.original_uri,
        force_upload=True, wait_for_output=False)
    r = f.transport.receipt()
    assert r["execution_id"] == f.execution["execution_id"]
    assert r["selected_pdf_receipt"] == f.selected
    assert r["selected_input_reference"] == f.original_uri
    assert r["input_reference"] == output["input_reference"] != f.original_uri
    updated = ocr_job_service.mark_own_pipeline_waiting(
        f.job_id, input_reference=output["input_reference"], output_reference=output["output_reference"],
        error_message="ocr_output_pending", metrics_patch={"processing_stage": "ocr_pipeline"})
    assert updated["input_reference"] == f.original_uri
    assert updated["output_reference"] == output["output_reference"]
    before = _snapshot(get_engine())
    assert ocr_job_service.mark_own_pipeline_waiting(
        f.job_id, input_reference="gs://bucket/input/foreign.pdf",
        output_reference=output["output_reference"], error_message="ocr_output_pending",
        metrics_patch={"processing_stage": "foreign"}) is None
    assert _snapshot(get_engine()) == before
    with session_scope() as session:
        job = session.get(OcrJob, f.job_id)
        assert order_service._extract_reparse_request_kwargs_from_job({"metrics": job.metrics}) == f.original_request


def test_superseded_resume_does_not_load_or_mutate(monkeypatch, waiting_job):
    job, result = waiting_job
    before = job_snapshot(job)
    monkeypatch.setattr(order_service, "get_job_request_mode", lambda job: "llm_reparse")
    def forbidden(*args, **kwargs):
        raise AssertionError("superseded resume loaded output")
    monkeypatch.setattr(order_service, "_load_pipeline_output_with_retry", forbidden)
    stale = deepcopy(result["pending_receipt"])
    stale["workflow_attempt_id"] = "superseded-attempt"
    stale["workflow_revision"] = job.revision - 1
    assert not order_service.reconcile_pending_workflow_reparse({"id": job.id, "order_id": job.order_id}, stale)
    assert job_snapshot(job) == before


def test_actual_rerun_callback_keeps_own_pending_nonterminal(monkeypatch, waiting_job):
    job, result = waiting_job
    @contextmanager
    def slot(**kwargs):
        yield {}
    def forbidden(*args, **kwargs):
        raise AssertionError("pending result terminalized")
    monkeypatch.setattr(orders, "acquire_ocr_execution_slot", slot)
    monkeypatch.setattr(orders, "get_ocr_job", lambda job_id: {"metrics": {}})
    monkeypatch.setattr(orders.ocr_job_service, "update_job_for_workflow_attempt", lambda *args, **kwargs: ({}, True))
    monkeypatch.setattr(order_service, "rerun_ocr_evidence_only", lambda *args, **kwargs: (result, None))
    monkeypatch.setattr(orders, "_complete_workflow_ocr_run", forbidden)
    orders._run_ocr_rerun_background(job.order_id, job.id, expected_revision=job.revision, workflow_attempt_id=job.attempt)
    assert job.status == "awaiting_output"


def test_auto_llm_pending_does_not_save_failed_diagnostic(monkeypatch):
    from src.workers import ingest_worker

    result = {"outcome": "pending", "status": "awaiting_output", "output_reference": "own-output", "pending_receipt": {
        "job_id": "real-job", "order_id": "order", "workflow_attempt_id": None,
        "workflow_revision": None, "execution_id": "start-execution", "output_reference": "own-output",
    }}
    monkeypatch.setattr(ingest_worker, "run_order_reparse", lambda *args, **kwargs: (result, None))
    def forbidden(*args, **kwargs):
        raise AssertionError("pending auto LLM saved terminal diagnostic")
    monkeypatch.setattr(order_service, "_persist_ocr_rerun_completion", forbidden)
    ingest_worker._run_auto_llm_reparse(
        "order", job_id="real-job", execution_input={"job_id": "real-job", "execution_id": "start-execution"},
        own_payload={"recognized_quantity": 0},
        original_request={"ocr_prompt": None, "prompt_preset": None, "ocr_provider": None,
                          "ocr_model": None, "llm_assist": True},
    )


def test_firstpass_recovery_pending_never_requires_evidence_or_terminal(monkeypatch):
    execution = {"job_id": "real-job", "order_id": "order"}
    monkeypatch.setattr(order_service, "current_nonworkflow_execution", lambda: execution)
    monkeypatch.setattr(order_service, "get_ocr_job", lambda job_id: {
        "id": job_id, "order_id": "order", "metrics": {}, "output_reference": "own-output",
    })
    monkeypatch.setattr(order_service, "get_job_request_mode", lambda job: "ingest_first_pass")
    monkeypatch.setattr(order_service, "_resolve_message_id_for_ocr_job", lambda *args, **kwargs: None)
    monkeypatch.setattr(order_service, "_load_pipeline_output_with_retry", lambda *args, **kwargs: {"pending": True})
    monkeypatch.setattr(order_service, "_output_is_pending", lambda payload: True)
    def forbidden(*args, **kwargs):
        raise AssertionError("pending firstpass applied result")
    monkeypatch.setattr(order_service, "create_order_from_ingest", forbidden)
    monkeypatch.setattr(order_service, "_persist_ocr_rerun_completion", forbidden)
    assert not order_service._reconcile_completed_ocr_job_with_input("real-job")


def own_delivery_payload(job, status="done"):
    receipt = job.metrics["pipeline_delivery_receipt"]
    return {"engine": "yomitoku", "status": status, "job_id": receipt["pipeline_job_id"],
            "template_id": job.template_id,
            "input_reference": receipt["input_reference"], "input_generation": receipt["generation"],
            "input_sha256": receipt["input_sha256"], "output_reference": receipt["output_reference"],
            "configured_input_sha256": receipt["configured_input_sha256"],
            "execution_owner": {"job_id": receipt["job_id"], "order_id": receipt["order_id"],
                "workflow_attempt_id": receipt["workflow_attempt_id"], "workflow_revision": receipt["workflow_revision"],
                "nonworkflow_execution_id": receipt["execution_id"]},
            "canonical_adoption_allowed": True,
            "canonical_stages": {"classification": "passed", "roi": "passed", "correction": "passed"},
            "table_rows": [["2/15", "morning", "meal", "0"]]}


@pytest.mark.parametrize("field,value", [
    ("input_generation", "41"), ("input_reference", "foreign-pdf"),
    ("output_reference", "old-output"), ("input_sha256", "different-pdf"), ("job_id", "foreign-job"),
])
def test_actual_result_gate_rejects_foreign_generation_and_pdf(waiting_job, field, value):
    job, _ = waiting_job
    payload = own_delivery_payload(job)
    payload[field] = value
    before = job_snapshot(job)
    with order_service.workflow_ocr_write_context(job.attempt, job.revision, job.id, job.order_id):
        with pytest.raises(ocr_job_service.OwnPipelineLineageError):
            ocr_job_service.validate_own_pipeline_result(payload, job.delivery_output)
    assert job_snapshot(job) == before


def test_generation_pinned_storage_read_calls_actual_loader(monkeypatch):
    from src.services import storage_service

    calls = []
    class Blob:
        def download_as_bytes(self, **kwargs):
            calls.append(("download", kwargs))
            return b"own-source-pdf"
    class Bucket:
        def blob(self, name, **kwargs):
            calls.append((name, kwargs))
            return Blob()
    monkeypatch.setattr(storage_service, "_get_thread_gcs_client", lambda: SimpleNamespace(bucket=lambda name: Bucket()))
    assert storage_service.load_bytes_from_uri("gs://own-bucket/input/current.pdf", generation="42") == b"own-source-pdf"
    assert calls == [("input/current.pdf", {"generation": 42}), ("download", {"if_generation_match": 42})]
    with pytest.raises(ValueError, match="storage_generation_invalid"):
        storage_service.load_bytes_from_uri("gs://own-bucket/input/current.pdf", generation="old")


def test_fax_pipeline_reuses_exact_result_zero_without_second_upload(ingest_pipeline):
    from src.services import fax_extractor
    f = ingest_pipeline
    payload = f.transport.output_payload()
    original = deepcopy(payload)
    objects = deepcopy(f.transport.objects)
    template = f.execution["orders"][f.order_id]["configured_input"]["facility"]["fax_template"]
    extracted = fax_extractor.extract_fax_data(f.pdf, template,
        facility_id="authored-facility", own_pipeline_payload=payload)
    assert extracted.table_rows == [["2/15", "morning", "meal", "0"]]
    assert extracted.own_pipeline_payload is payload
    assert payload == original
    assert f.transport.objects == objects


def test_fax_new_execution_returns_raw_own_result_not_selected_baseline(ingest_pipeline):
    from src.services import fax_extractor
    f = ingest_pipeline
    selected_baseline = f.transport.output_payload()
    original = deepcopy(selected_baseline)
    before_uploads = {key for key in f.transport.objects if key.endswith(".pdf")}
    template = f.execution["orders"][f.order_id]["configured_input"]["facility"]["fax_template"]
    extracted = fax_extractor.extract_fax_data(f.pdf, template, facility_id="authored-facility")
    current = f.transport.observations["last_payload"]
    assert extracted.own_pipeline_payload == current
    assert current["input_reference"] != selected_baseline["input_reference"]
    assert current["job_id"] != selected_baseline["job_id"]
    after_uploads = {key for key in f.transport.objects if key.endswith(".pdf")}
    assert len(after_uploads - before_uploads) == 1
    before = deepcopy(f.transport.objects)
    with pytest.raises(ocr_job_service.OwnPipelineLineageError):
        fax_extractor.extract_fax_data(f.pdf, template, facility_id="authored-facility",
                                      own_pipeline_payload=selected_baseline)
    assert f.transport.objects == before
    assert selected_baseline == original
    assert extracted.table_rows == [["2/15", "morning", "meal", "0"]]


@pytest.mark.parametrize("status", ["running", "failed"])
def test_fax_pending_is_not_generic_failure_and_real_failure_is_not_wait(ingest_pipeline, status):
    from src.db import session_scope
    from src.models.ocr_job import OcrJob
    from src.services import fax_extractor, ocr_pipeline_service
    from src.db import get_engine
    from tests.unit.test_disabled_facility_columns import _snapshot
    f = ingest_pipeline
    f.transport.statuses[:] = [status]
    payload = f.transport.output_payload()
    assert payload["table_rows"] == [["2/15", "morning", "meal", "0"]]
    before = _snapshot(get_engine())
    template = f.execution["orders"][f.order_id]["configured_input"]["facility"]["fax_template"]
    expected = ocr_pipeline_service.OCRPipelineOutputPendingError if status == "running" else ocr_job_service.OwnPipelineLineageError
    with pytest.raises(expected) as error:
        fax_extractor.extract_fax_data(f.pdf, template, facility_id="authored-facility",
                                      own_pipeline_payload=payload)
    assert _snapshot(get_engine()) == before
    if status == "running":
        updated = ocr_job_service.mark_own_pipeline_waiting(
            f.job_id, input_reference=error.value.input_reference,
            output_reference=error.value.output_reference, error_message="ocr_output_pending",
            metrics_patch={"processing_stage": "ocr_pipeline"})
        assert updated["status"] == "awaiting_output"
    else:
        assert str(error.value) == "ocr_pipeline_required_stage_failed"
    with session_scope() as session:
        job = session.get(OcrJob, f.job_id)
        assert "rerun_terminal_receipt" not in job.metrics
        assert "first_pass_terminal_receipt" not in job.metrics


@pytest.mark.parametrize("mapping_case", ["normal", "missing", "wrong_template"])
def test_arrived_firstpass_maps_own_rows_not_saved_lines(native_pending_recovery, mapping_case):
    import json
    from src.db import get_engine, session_scope
    from src.models.ocr_job import OcrJob
    from src.models.order import OrderLine
    from src.models.order_ocr_evidence_run import OrderOcrEvidenceRun
    from src.models.order_sheet_draft import OrderSheetDraft
    from src.models.order_workflow_state import OrderWorkflowState
    from src.services import ingest_delivery_service as delivery
    from tests.unit.test_disabled_facility_columns import _snapshot

    f = native_pending_recovery
    with session_scope() as session:
        unrelated = session.get(OrderLine, "unrelated-saved-999")
        saved_999 = {column.name: deepcopy(getattr(unrelated, column.name)) for column in OrderLine.__table__.columns}
        assert saved_999["quantity_original"] == saved_999["quantity_corrected"] == 999
    if mapping_case != "normal":
        if mapping_case == "missing":
            # Native recovery requires its captured physical original, not the
            # retired first_pass_application.mapping_input dictionary.
            f.source.unlink()
            expected_error = "selected_pdf_verification_failed"
        else:
            master = json.loads(f.source_config.read_text())
            facility = master["facilities"][0]
            facility["fax_template"]["template_id"] = "foreign-template"
            facility["fax_template_id"] = "foreign-template"
            facility["versions"][0]["facility"] = deepcopy({k: v for k, v in facility.items() if k != "versions"})
            f.source_config.write_text(json.dumps(master))
            expected_error = "configured_execution_input_changed"
        configured_source = f.source_config.read_bytes()
        before = _snapshot(get_engine())
        with pytest.raises(ValueError, match=expected_error):
            order_service._reconcile_completed_ocr_job_with_input(f.job_id)
        assert f.calls == []
        assert _snapshot(get_engine()) == before
        assert f.source_config.read_bytes() == configured_source
        return

    # Actual current dispatch: reconcile -> native recovery -> captured native
    # processing -> real H2 producer/persistence. Legacy mapping is absent.
    assert "first_pass_application" not in f.boundary
    assert order_service._reconcile_completed_ocr_job_with_input(f.job_id) is True
    assert len(f.calls) == 1
    assert all(record["ocr_text"] == "0" for record in f.calls[0])
    with session_scope() as session:
        job = session.get(OcrJob, f.job_id)
        terminal = deepcopy(job.metrics["rerun_terminal_receipt"])
        assert job.status == terminal["status"] == "done"
        assert terminal["job_id"] == f.job_id
        assert terminal["execution_id"] == f.boundary["execution_id"]
        evidence = session.get(OrderOcrEvidenceRun, terminal["evidence_run_id"])
        assert evidence.order_id == f.order_id
        stored = evidence.payload_json
        assert [record["raw_text"] for record in stored["hakodate_ocr_evidence_records"]] == ["0"]
        assert stored["_ocr_execution"]["selected_pdf_receipt"] == f.boundary["selected_pdf_receipt"]
        assert stored["_ocr_execution"]["nonworkflow_execution_id"] == f.boundary["execution_id"]
        assert "first_pass_terminal_receipt" not in job.metrics
        lines = session.query(OrderLine).filter_by(order_id=f.order_id).all()
        assert [line.quantity_original for line in lines] == [0]
        assert all(line.quantity_corrected is None for line in lines)
        state = session.get(OrderWorkflowState, f.order_id)
        assert state.evidence_run_id is state.draft_id is state.confirmed_snapshot_id is None
        assert session.query(OrderSheetDraft).filter_by(order_id=f.order_id).count() == 0
        unrelated = session.get(OrderLine, "unrelated-saved-999")
        assert {column.name: getattr(unrelated, column.name) for column in OrderLine.__table__.columns} == saved_999
    delivery.persist_runner_receipt(f.claim["lease"], terminal)
    assert delivery.acknowledge_claim(f.claim) == "business_done"
    assert delivery.inspect_generation(f.claim["delivery_id"], f.claim["generation"])["duplicate"] is True
    assert f.source.read_bytes() == f.pdf
