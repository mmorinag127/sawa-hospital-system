import pathlib
import sys
from datetime import date, datetime

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.db import session_scope  # noqa: E402
from src.models.ingest_job import IngestJob  # noqa: E402
from src.models.uploaded_pdf import UploadedPdf  # noqa: E402
from src.services import ingest_job_service, order_service  # noqa: E402
from src.services import workflow_state_service  # noqa: E402
from src.services.ocr_job_service import get_job  # noqa: E402
from src.services.uploaded_pdf_service import get_uploaded_pdf, list_ready_uploaded_pdf_ids  # noqa: E402
from src.workers import ingest_worker  # noqa: E402
from tests.helpers.ingest_original import (  # noqa: E402
    configure_delivery, reserve_uploaded_original, process_uploaded_original, authored_sources,
    authored_pipeline_provider,
    register_authored_original,
)

pytestmark = pytest.mark.usefixtures("isolated_formal_schema", "delivery_configuration")


@pytest.fixture
def delivery_configuration(monkeypatch):
    configure_delivery(monkeypatch)


def _seed_uploaded_pdf(
    *,
    uploaded_pdf_id: str,
    message_id: str,
    status: str = "pending",
    current_stage: str = "uploaded",
    attempt_count: int = 0,
    max_attempts: int = 5,
    lease_owner: str | None = None,
    lease_expires_at: datetime | None = None,
    next_retry_at: datetime | None = None,
) -> None:
    now = datetime.utcnow()
    with session_scope() as session:
        session.add(
            UploadedPdf(
                id=uploaded_pdf_id,
                message_id=message_id,
                content_sha256=f"sha-{uploaded_pdf_id}",
                source_kind="manual_upload",
                original_filename=f"{uploaded_pdf_id}.pdf",
                storage_uri=f"gs://bucket/{uploaded_pdf_id}.pdf",
                received_at=now,
                facility_hint="FAC001",
                week_hint="2026-02@2026-02-15~2026-02-21",
                status=status,
                current_stage=current_stage,
                attempt_count=attempt_count,
                max_attempts=max_attempts,
                lease_owner=lease_owner,
                lease_expires_at=lease_expires_at,
                next_retry_at=next_retry_at,
                created_at=now,
                updated_at=now,
            )
        )


def test_owned_delivery_marks_completed_and_links_entities(monkeypatch, tmp_path):
    order_service.clear_all()
    message_id = "msg-uploaded-pdf-complete"
    uploaded_pdf_id = "UPLtestcomplete"
    _seed_uploaded_pdf(uploaded_pdf_id=uploaded_pdf_id, message_id=message_id)
    uri, digest = authored_sources(monkeypatch, tmp_path, facility_id="FAC001", menu_date=date(2026, 2, 20))
    with session_scope() as session:
        row = session.get(UploadedPdf, uploaded_pdf_id)
        row.storage_uri, row.content_sha256 = uri, digest
    receipt = reserve_uploaded_original(uploaded_pdf_id, effective_date=date(2026, 2, 20))
    assert receipt["id"] == message_id
    register_authored_original(uploaded_pdf_id, effective_date=date(2026, 2, 20))
    authored_pipeline_provider(monkeypatch, table_rows=[["02/20", "昼", "Menu A", "2"]])
    result = process_uploaded_original(uploaded_pdf_id)
    assert result["result"] == "business_done"

    row = get_uploaded_pdf(uploaded_pdf_id)
    assert row is not None
    assert row["status"] == "completed"
    assert row["current_stage"] == "completed"
    assert row["current_order_id"]
    assert row["current_document_id"]
    from src.models.order import OrderLine
    with session_scope() as session:
        line = session.query(OrderLine).filter_by(order_id=row["current_order_id"]).one()
        assert line.quantity_original == 2
        assert line.date == date(2026, 2, 20)
        assert line.area_id == "2F"
        assert line.menu_name == "Menu A"


def test_owned_runner_uses_canonical_order_ocr_job_id(monkeypatch, tmp_path):
    order_service.clear_all()
    message_id = "msg-uploaded-pdf-canonical-ocr-job"
    uploaded_pdf_id = "UPLtestcanonicaljob"
    _seed_uploaded_pdf(uploaded_pdf_id=uploaded_pdf_id, message_id=message_id)
    uri, digest = authored_sources(monkeypatch, tmp_path, facility_id="FAC001", menu_date=date(2026, 2, 20))
    with session_scope() as session:
        row = session.get(UploadedPdf, uploaded_pdf_id)
        row.storage_uri, row.content_sha256 = uri, digest
    reserve_uploaded_original(uploaded_pdf_id, effective_date=date(2026, 2, 20))
    registered = register_authored_original(uploaded_pdf_id, effective_date=date(2026, 2, 20))
    authored_pipeline_provider(monkeypatch, table_rows=[["02/20", "昼", "Menu A", "2"]])
    assert process_uploaded_original(uploaded_pdf_id)["result"] == "business_done"
    row = get_uploaded_pdf(uploaded_pdf_id)
    assert row["status"] == "completed"
    assert row["current_order_id"] == registered["id"]
    from tests.helpers.ingest_original import recorded_upload_ocr_job
    assert recorded_upload_ocr_job(uploaded_pdf_id)["order_id"] == row["current_order_id"]


def test_owned_delivery_business_failure_is_terminal_after_stale_lease(monkeypatch, tmp_path):
    order_service.clear_all()
    message_id = "msg-uploaded-pdf-retry"
    uploaded_pdf_id = "UPLtestretry"
    stale_lease = datetime.utcnow().replace(microsecond=0)
    _seed_uploaded_pdf(
        uploaded_pdf_id=uploaded_pdf_id,
        message_id=message_id,
        status="processing",
        current_stage="ingest_running",
        attempt_count=1,
        lease_owner="worker:stale",
        lease_expires_at=stale_lease,
    )
    uri, digest = authored_sources(monkeypatch, tmp_path, facility_id="FAC001", menu_date=date(2026, 2, 20))
    with session_scope() as session:
        row = session.get(UploadedPdf, uploaded_pdf_id)
        row.storage_uri, row.content_sha256 = uri, digest
    reserve_uploaded_original(uploaded_pdf_id, effective_date=date(2026, 2, 20))
    ready_ids = list_ready_uploaded_pdf_ids(limit=10)
    assert uploaded_pdf_id in ready_ids
    register_authored_original(uploaded_pdf_id, effective_date=date(2026, 2, 20))
    before = ingest_job_service.get_ingest_job_snapshot(message_id)
    assert uploaded_pdf_id not in list_ready_uploaded_pdf_ids(limit=10)

    from src.services.ocr_job_service import OwnPipelineLineageError
    calls = []
    def provider_failure(**kwargs):
        calls.append(kwargs)
        raise OwnPipelineLineageError("simulated_failure")
    monkeypatch.setattr(ingest_worker, "run_ocr_pipeline", provider_failure)
    result = process_uploaded_original(uploaded_pdf_id)

    row = get_uploaded_pdf(uploaded_pdf_id)
    assert row is not None
    assert calls, (result, row["last_error_message"])
    assert row["status"] == "failed"
    assert row["current_stage"] == "failed"
    assert row["last_error_code"] == "ingest_failed"
    assert row["last_error_message"] == "first_pass_output_rejected"
    assert row["attempt_count"] == 2
    assert row["next_retry_at"] is None
    assert row["alerted_at"] is not None
    assert uploaded_pdf_id not in list_ready_uploaded_pdf_ids(limit=10)
    after = ingest_job_service.get_ingest_job_snapshot(message_id)
    assert after["generation"] == before["generation"]
    assert after["input_digest"] == before["input_digest"]
    assert after["generation_attempts"] == 1
    assert after["status"] == "error"
    assert process_uploaded_original(uploaded_pdf_id)["acknowledged"] is True
    assert ingest_job_service.get_ingest_job_snapshot(message_id) == after
    assert len(calls) == 1


def test_owned_first_pass_finalizes_without_implicit_latest_evidence_adoption(monkeypatch, tmp_path):
    order_service.clear_all()
    message_id = "upload:sha256:reconcile-first-pass"
    uploaded_pdf_id = "UPLreconcile"
    _seed_uploaded_pdf(uploaded_pdf_id=uploaded_pdf_id, message_id=message_id)
    uri, digest = authored_sources(monkeypatch, tmp_path, facility_id="FAC001", menu_date=date(2026, 2, 20))
    with session_scope() as session:
        row = session.get(UploadedPdf, uploaded_pdf_id)
        row.storage_uri, row.content_sha256 = uri, digest
    reserve_uploaded_original(uploaded_pdf_id, effective_date=date(2026, 2, 20))
    registered = register_authored_original(uploaded_pdf_id, effective_date=date(2026, 2, 20))
    authored_pipeline_provider(monkeypatch, table_rows=[["02/20", "昼", "Menu A", "2"]])
    assert process_uploaded_original(uploaded_pdf_id)["result"] == "business_done"
    row = get_uploaded_pdf(uploaded_pdf_id)
    assert row["status"] == "completed"
    assert row["current_stage"] == "completed"
    assert ingest_job_service.get_ingest_job_snapshot(message_id)["status"] == "done"
    evidence = order_service.get_latest_ocr_evidence_run(registered["id"], backfill_from_cache=False)
    assert evidence is not None
    workflow = workflow_state_service.refresh_workflow_state(registered["id"])
    assert workflow is not None
    assert workflow.get("evidence_run_id") is None
    assert workflow.get("active_evidence_run_id") is None
    assert workflow["apply_gate"]["can_apply"] is False
    assert order_service.ocr_evidence_service.get_selected_evidence_run(registered["id"]) is None


def test_owned_first_pass_duplicate_keeps_evidence_and_original_receipt(monkeypatch, tmp_path):
    order_service.clear_all()
    message_id = "upload:sha256:reconcile-idempotent"
    uploaded_pdf_id = "UPLreconcileidempotent"
    _seed_uploaded_pdf(uploaded_pdf_id=uploaded_pdf_id, message_id=message_id)
    uri, digest = authored_sources(monkeypatch, tmp_path, facility_id="FAC001", menu_date=date(2026, 2, 20))
    with session_scope() as session:
        row = session.get(UploadedPdf, uploaded_pdf_id)
        row.storage_uri, row.content_sha256 = uri, digest
    reserve_uploaded_original(uploaded_pdf_id, effective_date=date(2026, 2, 20))
    registered = register_authored_original(uploaded_pdf_id, effective_date=date(2026, 2, 20))
    authored_pipeline_provider(monkeypatch, table_rows=[["02/20", "昼", "Menu A", "2"]])
    assert process_uploaded_original(uploaded_pdf_id)["result"] == "business_done"
    evidence = order_service.get_latest_ocr_evidence_run(registered["id"], backfill_from_cache=False)
    assert evidence is not None
    from tests.helpers.ingest_original import recorded_upload_ocr_job
    before = recorded_upload_ocr_job(uploaded_pdf_id)
    job_id = before["id"]
    monkeypatch.setattr(ingest_worker, "run_ocr_pipeline", lambda **kwargs: pytest.fail("duplicate provider execution"))
    assert process_uploaded_original(uploaded_pdf_id)["acknowledged"] is True
    latest = order_service.get_latest_ocr_evidence_run(registered["id"], backfill_from_cache=False)
    assert latest["id"] == evidence["id"]
    assert get_job(job_id) == before
    assert before["metrics"]["evidence_run_id"] == evidence["id"]
    captured = before["metrics"]["nonworkflow_execution"]["start_orders"][registered["id"]]["processing_start_input"]
    assert captured["order"]["template_version_id"] == captured["original_template_projection"]["id"]
    assert captured["original_template_lineage"]["authored_version"]["facility"] == captured["configured_input"]["facility"]


@pytest.mark.parametrize("message_id,ocr_job_id", [
    ("upload:sha256:inline-first-pass", None),
    ("upload:sha256:inline-canonical", "OCR-ORDinlinecanonical"),
])
def test_unowned_inline_cannot_finalize_or_create_payload_job(monkeypatch, message_id, ocr_job_id):
    order_service.clear_all()
    with session_scope() as session:
        from src.models.order import Order
        from src.models.document import OrderDocument
        from src.models.ocr_job import OcrJob
        before = [session.query(model).count() for model in (Order, OrderDocument, OcrJob)]
    monkeypatch.setattr(ingest_worker, "run_ocr_pipeline", lambda **kwargs: pytest.fail("unowned provider"))
    with pytest.raises(ValueError, match="^ingest_execution_lease_required$"):
        ingest_worker._process_ingest_inline(message_id=message_id, pdf_uri="gs://bucket/input.pdf",
            received_at=datetime.utcnow().isoformat(), facility_hint=None,
            week_hint="2026-02@2026-02-15~2026-02-21", facility_name=None, date_hints=[],
            skip_ocr=False, source_kind="manual_upload", original_filename="inline.pdf",
            content_sha256="sha-inline", ocr_job_id=ocr_job_id)
    with session_scope() as session:
        assert [session.query(model).count() for model in (Order, OrderDocument, OcrJob)] == before
