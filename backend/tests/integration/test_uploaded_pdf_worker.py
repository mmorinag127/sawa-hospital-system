import pathlib
import sys
from datetime import datetime, timedelta

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.db import session_scope  # noqa: E402
from src.models.document import OrderDocument  # noqa: E402
from src.models.ingest_job import IngestJob  # noqa: E402
from src.models.ocr_job import OcrJob  # noqa: E402
from src.models.order import Order  # noqa: E402
from src.models.uploaded_pdf import UploadedPdf  # noqa: E402
from src.services.ocr_job_service import create_job, get_job, update_job  # noqa: E402
from src.services import order_service  # noqa: E402
from src.services.uploaded_pdf_service import (  # noqa: E402
    backfill_uploaded_pdfs_from_ingest_jobs,
    build_ingest_payload,
    claim_uploaded_pdf,
    create_uploaded_pdf_from_upload,
    get_uploaded_pdf,
    list_ready_uploaded_pdf_ids,
)
from src.services.manual_upload_service import ManualUploadSavedFile  # noqa: E402
from src.workers.ingest_mail_adapter import parse_ingest_payload  # noqa: E402
from src.workers import ingest_worker  # noqa: E402


def _saved_file(*, message_id: str, filename: str) -> ManualUploadSavedFile:
    return ManualUploadSavedFile(
        message_id=message_id,
        pdf_uri=f"gs://bucket/{filename}",
        content_sha256=f"sha-{message_id}",
        original_filename=filename,
        received_at=datetime(2026, 4, 6, 10, 0, 0),
    )


def test_build_ingest_payload_derives_explicit_week_from_filename():
    payload = build_ingest_payload(
        {
            "message_id": "upload:sha256:filename-derived-week",
            "storage_uri": "gs://bucket/17.fax000355472_0405_.pdf",
            "received_at": "2026-04-06T00:04:43.617807",
            "facility_hint": None,
            "week_hint": None,
            "facility_name": None,
            "skip_ocr": False,
            "source_kind": "manual_upload",
            "original_filename": "17.fax000355472_0405_.pdf",
            "content_sha256": "sha-filename-derived-week",
        }
    )

    assert payload["week_hint"] == "2026-04@2026-04-05~2026-04-11"


def test_create_order_from_ingest_derives_week_from_filename_when_hint_missing():
    order_service.clear_all()
    payload = parse_ingest_payload(
        {
            "message_id": "upload:sha256:create-order-week-fallback",
            "pdf_uri": "gs://bucket/manual-uploads/2026/04/06/16.fax000355571_0405_.pdf",
            "received_at": "2026-04-06T00:04:43.617807",
            "facility_hint": None,
            "week_hint": None,
            "facility_name": None,
            "skip_ocr": False,
            "source_kind": "manual_upload",
            "original_filename": "16.fax000355571_0405_.pdf",
            "content_sha256": "sha-create-order-week-fallback",
        }
    )

    order = order_service.create_order_from_ingest(payload, lines=[])

    assert order["week_value"] == "2026-04@2026-04-05~2026-04-11"


def test_process_uploaded_pdf_job_marks_completed_when_ingest_creates_order(monkeypatch):
    order_service.clear_all()
    saved = _saved_file(message_id="upload:sha256:worker-success", filename="worker-success.pdf")
    uploaded_pdf, _ = create_uploaded_pdf_from_upload(
        saved=saved,
        facility_hint="FAC00001",
        week_hint="2026-04@2026-04-05~2026-04-11",
        facility_name=None,
        skip_ocr=False,
        source_kind="manual_upload",
    )

    def _fake_process_ingest_job(job_id: str) -> None:
        now = datetime.utcnow()
        with session_scope() as session:
            job = session.get(IngestJob, job_id)
            assert job is not None
            job.status = "done"
            job.started_at = now
            job.finished_at = now
            job.updated_at = now
            job.last_error = None
            payload = dict(job.payload or {})
        order_service.create_order_from_ingest(
            parse_ingest_payload(payload),
            lines=[],
            ocr_attempts=1,
            document_status="success",
            error_message=None,
        )

    monkeypatch.setattr(ingest_worker, "process_ingest_job", _fake_process_ingest_job)

    ingest_worker.process_uploaded_pdf_job(uploaded_pdf["id"])

    completed = get_uploaded_pdf(uploaded_pdf["id"])
    assert completed is not None
    assert completed["status"] == "completed"
    assert completed["current_stage"] == "completed"
    assert str(completed["current_document_id"]).startswith("DOC")
    assert str(completed["current_order_id"]).startswith("ORD")
    with session_scope() as session:
        order = session.get(Order, completed["current_order_id"])
        assert order is not None
        assert order.week_code == "2026-04@2026-04-05~2026-04-11"
        assert order.current_document_id == completed["current_document_id"]
        assert len(order.superseded_document_ids or []) == 1


def test_process_uploaded_pdf_job_reclaims_stale_processing_and_schedules_retry(monkeypatch):
    order_service.clear_all()
    saved = _saved_file(message_id="upload:sha256:worker-stale", filename="worker-stale.pdf")
    uploaded_pdf, _ = create_uploaded_pdf_from_upload(
        saved=saved,
        facility_hint="FAC00002",
        week_hint="2026-04@2026-04-05~2026-04-11",
        facility_name=None,
        skip_ocr=False,
        source_kind="manual_upload",
    )
    claimed = claim_uploaded_pdf(uploaded_pdf["id"], worker_instance="stale-worker")
    assert claimed is not None

    with session_scope() as session:
        row = session.get(UploadedPdf, uploaded_pdf["id"])
        assert row is not None
        row.lease_expires_at = datetime.utcnow() - timedelta(minutes=5)
        row.updated_at = datetime.utcnow() - timedelta(minutes=5)

    assert uploaded_pdf["id"] in list_ready_uploaded_pdf_ids(limit=10)

    def _fake_process_ingest_job(job_id: str) -> None:
        now = datetime.utcnow()
        with session_scope() as session:
            job = session.get(IngestJob, job_id)
            assert job is not None
            job.status = "error"
            job.started_at = now
            job.updated_at = now
            job.last_error = "ocr_timeout"

    monkeypatch.setattr(ingest_worker, "process_ingest_job", _fake_process_ingest_job)

    ingest_worker.process_uploaded_pdf_job(uploaded_pdf["id"])

    retried = get_uploaded_pdf(uploaded_pdf["id"])
    assert retried is not None
    assert retried["status"] == "retry_wait"
    assert retried["current_stage"] == "retry_wait"
    assert retried["last_error_code"] == "ingest_error"
    assert retried["last_error_message"] == "ocr_timeout"
    assert retried["attempt_count"] == 2
    assert retried["next_retry_at"] is not None


def test_process_uploaded_pdf_job_force_restarts_processing_ingest_job_after_retry(monkeypatch):
    order_service.clear_all()
    saved = _saved_file(message_id="upload:sha256:worker-force-restart", filename="worker-force-restart.pdf")
    uploaded_pdf, _ = create_uploaded_pdf_from_upload(
        saved=saved,
        facility_hint="FAC00003",
        week_hint="2026-04@2026-04-05~2026-04-11",
        facility_name=None,
        skip_ocr=False,
        source_kind="manual_upload",
    )

    with session_scope() as session:
        session.add(
            IngestJob(
                id=saved.message_id,
                status="processing",
                payload={
                    "message_id": saved.message_id,
                    "pdf_uri": saved.pdf_uri,
                    "received_at": saved.received_at.isoformat(),
                    "facility_hint": "FAC00003",
                    "week_hint": "2026-04@2026-04-05~2026-04-11",
                    "source_kind": "manual_upload",
                    "original_filename": saved.original_filename,
                    "content_sha256": saved.content_sha256,
                },
                attempts=1,
                started_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
        )

    # Simulate an operator retry on a row that previously got stuck.
    from src.services.uploaded_pdf_service import requeue_uploaded_pdf  # noqa: E402

    requeue_uploaded_pdf(uploaded_pdf["id"])
    with session_scope() as session:
        row = session.get(UploadedPdf, uploaded_pdf["id"])
        assert row is not None
        row.attempt_count = 1
        row.updated_at = datetime.utcnow()
    seen_statuses: list[str] = []
    seen_started_at: list[datetime | None] = []
    seen_last_errors: list[str | None] = []

    def _fake_process_ingest_job(job_id: str) -> None:
        with session_scope() as session:
            job = session.get(IngestJob, job_id)
            assert job is not None
            seen_statuses.append(str(job.status))
            seen_started_at.append(job.started_at)
            seen_last_errors.append(job.last_error)
            now = datetime.utcnow()
            job.status = "done"
            job.started_at = now
            job.finished_at = now
            job.updated_at = now
            job.last_error = None
            payload = dict(job.payload or {})
        order_service.create_order_from_ingest(
            parse_ingest_payload(payload),
            lines=[],
            ocr_attempts=1,
            document_status="success",
            error_message=None,
        )

    monkeypatch.setattr(ingest_worker, "process_ingest_job", _fake_process_ingest_job)

    ingest_worker.process_uploaded_pdf_job(uploaded_pdf["id"])

    completed = get_uploaded_pdf(uploaded_pdf["id"])
    assert completed is not None
    assert completed["status"] == "completed"
    assert str(completed["current_order_id"]).startswith("ORD")
    assert str(completed["current_document_id"]).startswith("DOC")
    assert seen_statuses == ["pending"]
    assert seen_started_at == [None]
    assert seen_last_errors == [None]


def test_process_uploaded_pdf_job_reuses_existing_message_order_and_fills_week_from_filename(monkeypatch):
    order_service.clear_all()
    saved = _saved_file(
        message_id="upload:sha256:worker-existing-message-order",
        filename="17.fax000355472_0405_.pdf",
    )
    uploaded_pdf, _ = create_uploaded_pdf_from_upload(
        saved=saved,
        facility_hint=None,
        week_hint=None,
        facility_name=None,
        skip_ocr=False,
        source_kind="manual_upload",
    )

    with session_scope() as session:
        document = OrderDocument(
            id="DOC-UPL-NULL-WEEK",
            facility_code=None,
            week_code=None,
            storage_uri=saved.pdf_uri,
            source_email_id=saved.message_id,
            received_at=saved.received_at,
            ocr_attempts=1,
            status="processed",
        )
        order = Order(
            id="ORD-UPL-NULL-WEEK",
            facility_code=None,
            week_code=None,
            status="要確認",
            current_document_id=document.id,
            superseded_document_ids=[],
            document_uri=saved.pdf_uri,
            message_id=saved.message_id,
            received_at=saved.received_at,
        )
        session.add(document)
        session.add(order)

    def _fake_process_ingest_job(job_id: str) -> None:
        now = datetime.utcnow()
        with session_scope() as session:
            job = session.get(IngestJob, job_id)
            assert job is not None
            job.status = "done"
            job.started_at = now
            job.finished_at = now
            job.updated_at = now
            job.last_error = None
            payload = dict(job.payload or {})
        order_service.create_order_from_ingest(
            parse_ingest_payload(payload),
            lines=[],
            ocr_attempts=1,
            document_status="success",
            error_message=None,
        )

    monkeypatch.setattr(ingest_worker, "process_ingest_job", _fake_process_ingest_job)

    ingest_worker.process_uploaded_pdf_job(uploaded_pdf["id"])

    completed = get_uploaded_pdf(uploaded_pdf["id"])
    assert completed is not None
    assert completed["status"] == "completed"
    assert completed["current_order_id"] == "ORD-UPL-NULL-WEEK"

    with session_scope() as session:
        order = session.get(Order, "ORD-UPL-NULL-WEEK")
        assert order is not None
        assert order.week_code == "2026-04@2026-04-05~2026-04-11"
        assert order.current_document_id != "DOC-UPL-NULL-WEEK"
        assert order.superseded_document_ids == ["DOC-UPL-NULL-WEEK"]


def test_process_uploaded_pdf_job_creates_placeholder_order_before_ingest_finishes(monkeypatch):
    order_service.clear_all()
    saved = _saved_file(
        message_id="upload:sha256:worker-placeholder-order",
        filename="11.fax000353442_0405_.pdf",
    )
    uploaded_pdf, _ = create_uploaded_pdf_from_upload(
        saved=saved,
        facility_hint=None,
        week_hint=None,
        facility_name=None,
        skip_ocr=False,
        source_kind="manual_upload",
    )

    def _fake_process_ingest_job(job_id: str) -> None:
        now = datetime.utcnow()
        with session_scope() as session:
            job = session.get(IngestJob, job_id)
            assert job is not None
            job.status = "processing"
            job.started_at = now
            job.updated_at = now

    monkeypatch.setattr(ingest_worker, "process_ingest_job", _fake_process_ingest_job)

    ingest_worker.process_uploaded_pdf_job(uploaded_pdf["id"])

    with session_scope() as session:
        order = (
            session.query(Order)
            .filter(Order.message_id == saved.message_id)
            .order_by(Order.received_at.desc(), Order.id.desc())
            .first()
        )
        assert order is not None
        assert order.week_code == "2026-04@2026-04-05~2026-04-11"
        assert order.status == "要確認"

    retried = get_uploaded_pdf(uploaded_pdf["id"])
    assert retried is not None
    assert retried["status"] == "retry_wait"
    assert str(retried["current_order_id"]).startswith("ORD")


def test_backfill_uploaded_pdfs_from_ingest_jobs_creates_recoverable_rows():
    order_service.clear_all()
    with session_scope() as session:
        session.add(
            IngestJob(
                id="upload:sha256:legacy-processing",
                status="processing",
                payload={
                    "message_id": "upload:sha256:legacy-processing",
                    "pdf_uri": "gs://bucket/manual-uploads/2026/04/06/legacy-processing.pdf",
                    "received_at": "2026-04-06T00:04:43.617807",
                    "facility_hint": "FAC00002",
                    "week_hint": "2026-04@2026-04-05~2026-04-11",
                    "source_kind": "manual_upload",
                    "original_filename": "legacy-processing.pdf",
                    "content_sha256": "sha-legacy-processing",
                },
                attempts=1,
                started_at=datetime.utcnow() - timedelta(hours=2),
                updated_at=datetime.utcnow() - timedelta(hours=2),
            )
        )

    created = backfill_uploaded_pdfs_from_ingest_jobs(limit=20)
    assert created >= 1

    rows = [get_uploaded_pdf(uploaded_pdf_id) for uploaded_pdf_id in list_ready_uploaded_pdf_ids(limit=20)]
    matched = next(row for row in rows if row and row["message_id"] == "upload:sha256:legacy-processing")
    assert matched is not None
    assert matched["status"] == "processing"
    assert matched["current_stage"] == "ingest_running"
    assert matched["attempt_count"] == 1


def test_run_uploaded_pdf_recovery_once_processes_ready_rows(monkeypatch):
    seen_limits: list[int] = []
    submitted_ids: list[str] = []
    stale_reset_limits: list[int] = []

    monkeypatch.setattr(
        ingest_worker.ingest_job_service,
        "reset_stale_processing",
        lambda limit: stale_reset_limits.append(limit) or [],
    )
    monkeypatch.setattr(ingest_worker, "backfill_uploaded_pdfs_from_ingest_jobs", lambda: 3)
    monkeypatch.setattr(
        ingest_worker,
        "list_ready_uploaded_pdf_ids",
        lambda limit: seen_limits.append(limit) or ["UPL-a", "UPL-b"],
    )
    monkeypatch.setattr(
        ingest_worker,
        "_submit_uploaded_pdf_job",
        lambda uploaded_pdf_id: submitted_ids.append(uploaded_pdf_id) or True,
    )

    processed = ingest_worker.run_uploaded_pdf_recovery_once(limit=7)

    assert processed == 2
    assert stale_reset_limits == [7]
    assert seen_limits == [7]
    assert submitted_ids == ["UPL-a", "UPL-b"]


def test_wait_for_pipeline_output_on_ingest_defaults_false_without_http_trigger(monkeypatch):
    monkeypatch.delenv("OCR_PIPELINE_WAIT_FOR_OUTPUT_ON_INGEST", raising=False)
    monkeypatch.delenv("OCR_PIPELINE_URL", raising=False)

    assert ingest_worker._wait_for_pipeline_output_on_ingest() is False


def test_wait_for_pipeline_output_on_ingest_can_be_enabled_explicitly(monkeypatch):
    monkeypatch.setenv("OCR_PIPELINE_WAIT_FOR_OUTPUT_ON_INGEST", "true")
    monkeypatch.delenv("OCR_PIPELINE_URL", raising=False)

    assert ingest_worker._wait_for_pipeline_output_on_ingest() is True


def test_run_uploaded_pdf_recovery_once_resets_stale_ingest_jobs_before_listing(monkeypatch):
    calls: list[tuple[str, object]] = []

    monkeypatch.setattr(
        ingest_worker.ingest_job_service,
        "reset_stale_processing",
        lambda limit: calls.append(("reset", limit)) or ["job-stale-1"],
    )
    monkeypatch.setattr(
        ingest_worker,
        "backfill_uploaded_pdfs_from_ingest_jobs",
        lambda: calls.append(("backfill", None)) or 0,
    )
    monkeypatch.setattr(
        ingest_worker,
        "list_ready_uploaded_pdf_ids",
        lambda limit: calls.append(("list", limit)) or ["UPL-a"],
    )
    monkeypatch.setattr(
        ingest_worker,
        "_submit_uploaded_pdf_job",
        lambda uploaded_pdf_id: calls.append(("submit", uploaded_pdf_id)) or True,
    )

    processed = ingest_worker.run_uploaded_pdf_recovery_once(limit=3)

    assert processed == 1
    assert calls == [
        ("reset", 3),
        ("backfill", None),
        ("list", 3),
        ("submit", "UPL-a"),
    ]


def test_run_ocr_job_recovery_once_reconciles_completed_output(monkeypatch):
    order_service.clear_all()
    with session_scope() as session:
        session.query(OcrJob).delete()
    payload = parse_ingest_payload(
        {
            "message_id": "upload:sha256:ocr-recover-done",
            "pdf_uri": "gs://bucket/manual-uploads/2026/04/06/ocr-recover-done.pdf",
            "received_at": "2026-04-06T00:04:43.617807",
            "facility_hint": "FAC00002",
            "week_hint": "2026-04@2026-04-05~2026-04-11",
            "facility_name": None,
            "skip_ocr": False,
            "source_kind": "manual_upload",
            "original_filename": "ocr-recover-done.pdf",
            "content_sha256": "sha-ocr-recover-done",
        }
    )
    order = order_service.create_order_from_ingest(
        payload,
        lines=None,
        ocr_attempts=0,
        document_status="processing",
        error_message="ocr_pending",
    )
    create_job("OCR-upload:sha256:ocr-recover-done", input_reference=payload.pdf_uri)
    update_job(
        "OCR-upload:sha256:ocr-recover-done",
        status="awaiting_output",
        output_reference="gs://bucket/output/ocr-recover-done.json",
        metrics={
            "request_mode": "ingest_first_pass",
            "order_id": order["id"],
            "stage_updated_at": (datetime.utcnow() - timedelta(minutes=10)).isoformat(),
            "next_recovery_at": (datetime.utcnow() - timedelta(minutes=1)).isoformat(),
        },
        error_message="ocr_output_pending",
    )

    monkeypatch.setattr(
        ingest_worker,
        "load_bytes_from_uri",
        lambda uri: (
            b'{"status":"done","template_id":"fax_layout_regular_forbidden_v1","table_raw":"done","pages":[{"page_index":1}]}'
            if "output/" in uri
            else b"%PDF-1.4"
        ),
    )
    monkeypatch.setattr(
        order_service,
        "load_bytes_from_uri",
        lambda uri: (
            b'{"status":"done","template_id":"fax_layout_regular_forbidden_v1","table_raw":"done","pages":[{"page_index":1}]}'
            if "output/" in uri
            else b"%PDF-1.4"
        ),
    )

    processed = ingest_worker.run_ocr_job_recovery_once(limit=5)

    assert processed == 1
    job = get_job("OCR-upload:sha256:ocr-recover-done")
    assert job is not None
    assert job["status"] == "done"
    parsed, error = order_service.get_ocr_output(order["id"])
    assert error is None
    assert parsed["table_raw"] == "done"


def test_run_ocr_job_recovery_once_resubmits_missing_output(monkeypatch):
    order_service.clear_all()
    with session_scope() as session:
        session.query(OcrJob).delete()
    create_job("OCR-upload:sha256:ocr-recover-retry", input_reference="gs://bucket/original.pdf")
    update_job(
        "OCR-upload:sha256:ocr-recover-retry",
        status="awaiting_output",
        output_reference="gs://bucket/output/missing.json",
        metrics={
            "request_mode": "ingest_first_pass",
            "facility_id": "FAC00002",
            "preferred_template_id": "fax_layout_regular_forbidden_v1",
            "preferred_template_ids": ["fax_layout_regular_forbidden_v1"],
            "auto_recovery_count": 0,
            "stage_updated_at": (datetime.utcnow() - timedelta(minutes=10)).isoformat(),
            "next_recovery_at": (datetime.utcnow() - timedelta(minutes=1)).isoformat(),
        },
        error_message="ocr_output_pending",
    )

    monkeypatch.setattr(
        ingest_worker,
        "load_bytes_from_uri",
        lambda uri: b"%PDF-1.4" if uri == "gs://bucket/original.pdf" else (_ for _ in ()).throw(FileNotFoundError(uri)),
    )
    monkeypatch.setattr(
        ingest_worker,
        "run_ocr_pipeline",
        lambda **_kwargs: {
            "status": "running",
            "input_reference": "gs://bucket/original.pdf",
            "output_reference": "gs://bucket/output/recovered.json",
        },
    )

    processed = ingest_worker.run_ocr_job_recovery_once(limit=5)

    assert processed == 1
    job = get_job("OCR-upload:sha256:ocr-recover-retry")
    assert job is not None
    assert job["status"] == "awaiting_output"
    assert job["output_reference"] == "gs://bucket/output/recovered.json"
    assert (job.get("metrics") or {}).get("auto_recovery_count") == 1


def test_run_ocr_job_recovery_once_resubmits_stale_running_pipeline_job(monkeypatch):
    order_service.clear_all()
    with session_scope() as session:
        session.query(OcrJob).delete()
    monkeypatch.setenv("OCR_JOB_STALE_MINUTES", "10")
    create_job("OCR-upload:sha256:ocr-recover-stale-running", input_reference="gs://bucket/original-stale.pdf")
    update_job(
        "OCR-upload:sha256:ocr-recover-stale-running",
        status="running",
        output_reference="gs://bucket/output/stale-running.json",
        metrics={
            "request_mode": "ingest_first_pass",
            "processing_stage": "ocr_pipeline",
            "result_state": "processing",
            "facility_id": "FAC00002",
            "preferred_template_id": "fax_layout_regular_forbidden_v1",
            "preferred_template_ids": ["fax_layout_regular_forbidden_v1"],
            "auto_recovery_count": 0,
            "stage_updated_at": (datetime.utcnow() - timedelta(minutes=30)).isoformat(),
            "next_recovery_at": (datetime.utcnow() - timedelta(minutes=1)).isoformat(),
        },
        error_message=None,
    )

    monkeypatch.setattr(
        ingest_worker,
        "load_bytes_from_uri",
        lambda uri: b"%PDF-1.4" if uri == "gs://bucket/original-stale.pdf" else (_ for _ in ()).throw(FileNotFoundError(uri)),
    )
    monkeypatch.setattr(
        ingest_worker,
        "run_ocr_pipeline",
        lambda **_kwargs: {
            "status": "running",
            "input_reference": "gs://bucket/original-stale.pdf",
            "output_reference": "gs://bucket/output/stale-running-recovered.json",
        },
    )

    processed = ingest_worker.run_ocr_job_recovery_once(limit=5)

    assert processed == 1
    job = get_job("OCR-upload:sha256:ocr-recover-stale-running")
    assert job is not None
    assert job["status"] == "awaiting_output"
    assert job["output_reference"] == "gs://bucket/output/stale-running-recovered.json"
    assert (job.get("metrics") or {}).get("auto_recovery_count") == 1
