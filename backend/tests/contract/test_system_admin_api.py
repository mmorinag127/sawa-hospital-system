import pathlib
import sys
from datetime import datetime, timedelta

from fastapi.testclient import TestClient
ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.main import app  # noqa: E402
from src.db import session_scope  # noqa: E402
from src.models.ingest_job import IngestJob  # noqa: E402
from src.models.ocr_job import OcrJob  # noqa: E402
from src.models.uploaded_pdf import UploadedPdf  # noqa: E402
from src.services import order_service  # noqa: E402
from src.services.ingest_job_service import create_ingest_job  # noqa: E402
from src.services.ocr_pipeline_state_store import save_pipeline_error, save_pipeline_request  # noqa: E402
from src.services.ocr_job_service import create_job, update_job  # noqa: E402
from src.workers.ingest_mail_adapter import IngestEmailPayload  # noqa: E402


def _create_seed_order(message_id: str) -> dict:
    payload = IngestEmailPayload(
        message_id=message_id,
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2026, 2, 20, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint=None,
    )
    lines = [
        {
            "date": "2026-02-20",
            "daypart": "昼",
            "menu_name": "Menu A",
            "diet_type": "regular",
            "area_id": "2F",
            "bag_type": "standard",
            "quantity_original": 2,
        }
    ]
    return order_service.create_order_from_ingest(payload, lines=lines)


def test_system_status_and_admin_endpoints():
    order_service.clear_all()
    _create_seed_order("msg-system-api-001")
    client = TestClient(app)

    status_res = client.get("/system/status")
    assert status_res.status_code == 200
    status_payload = status_res.json()
    assert isinstance(status_payload.get("db_quota"), dict)
    quality = status_payload.get("ocr_reparse_quality") or {}
    assert isinstance(quality.get("gate"), dict)
    assert quality.get("gate", {}).get("status") in {"pass", "fail", "insufficient_data", "error"}
    assert (quality.get("scope") or {}).get("job_type") == "llm_reparse_only"
    assert (quality.get("scope") or {}).get("mode") in {"explicit_only", "legacy_heuristic"}
    pipeline = status_payload.get("ocr_pipeline") or {}
    assert pipeline.get("trigger_mode") in {"gcs_only", "gcs_http", "http_only"}
    assert pipeline.get("wait_strategy") == "poll_output_gcs"
    assert isinstance(pipeline.get("sync_wait_supported"), bool)
    intake = status_payload.get("intake") or {}
    assert intake.get("mode") == "manual_upload"
    assert intake.get("manual_upload_enabled") is True
    assert isinstance((intake.get("manual_upload_storage") or {}).get("configured"), bool)

    quota_res = client.get("/system/db/quota")
    assert quota_res.status_code == 200
    assert quota_res.json().get("resource")

    download_res = client.get("/system/db/download")
    assert download_res.status_code == 200
    assert len(download_res.content) > 0

    bad_clear_res = client.post("/system/clear-all", json={"confirm": "INVALID"})
    assert bad_clear_res.status_code == 400

    clear_res = client.post(
        "/system/clear-all",
        json={"confirm": "CLEAR_ALL", "include_audit_logs": True},
    )
    assert clear_res.status_code == 200
    clear_payload = clear_res.json()
    assert clear_payload.get("result", {}).get("total_removed", 0) >= 1


def test_system_status_reports_manual_upload_mode(monkeypatch):
    monkeypatch.setenv("INGEST_MODE", "manual_upload")
    client = TestClient(app)

    res = client.get("/system/status")

    assert res.status_code == 200
    payload = res.json()
    intake = payload.get("intake") or {}
    assert intake.get("mode") == "manual_upload"
    assert intake.get("manual_upload_enabled") is True


def test_system_status_and_health_include_uploaded_pdf_backlog():
    order_service.clear_all()
    now = datetime.utcnow()
    with session_scope() as session:
        session.add_all(
            [
                UploadedPdf(
                    id="UPL-system-pending",
                    message_id="msg-system-pending",
                    content_sha256="sha-system-pending",
                    source_kind="manual_upload",
                    original_filename="pending.pdf",
                    storage_uri="gs://bucket/pending.pdf",
                    received_at=now - timedelta(minutes=30),
                    status="pending",
                    current_stage="uploaded",
                    attempt_count=0,
                    max_attempts=5,
                    created_at=now - timedelta(minutes=30),
                    updated_at=now - timedelta(minutes=30),
                ),
                UploadedPdf(
                    id="UPL-system-stale",
                    message_id="msg-system-stale",
                    content_sha256="sha-system-stale",
                    source_kind="manual_upload",
                    original_filename="stale.pdf",
                    storage_uri="gs://bucket/stale.pdf",
                    received_at=now - timedelta(minutes=20),
                    status="processing",
                    current_stage="ingest_running",
                    attempt_count=1,
                    max_attempts=5,
                    lease_owner="worker:test",
                    lease_expires_at=now - timedelta(minutes=1),
                    created_at=now - timedelta(minutes=20),
                    updated_at=now - timedelta(minutes=20),
                ),
            ]
        )

    client = TestClient(app)

    status_res = client.get("/system/status")
    assert status_res.status_code == 200
    status_payload = status_res.json()
    uploaded_pdfs = status_payload.get("uploaded_pdfs") or {}
    assert uploaded_pdfs.get("pending_count") == 1
    assert uploaded_pdfs.get("stale_lease_count") == 1
    assert uploaded_pdfs.get("eligible_backlog_count") == 2

    health_res = client.get("/health/backlog")
    assert health_res.status_code == 200
    health_payload = health_res.json()
    assert health_payload.get("uploaded_pdf_queue_depth") == 2
    assert (health_payload.get("uploaded_pdfs") or {}).get("pending_count") == 1


def test_health_backlog_returns_real_ingest_and_ocr_metrics(monkeypatch):
    order_service.clear_all()
    monkeypatch.setenv("OCR_JOB_STALE_MINUTES", "10")
    now = datetime.utcnow()
    with session_scope() as session:
        session.query(IngestJob).delete()
        session.query(OcrJob).delete()
    create_ingest_job(
        {
            "message_id": "msg-backlog-pending",
            "pdf_uri": "file://pending.pdf",
            "received_at": now.isoformat(),
        }
    )
    create_ingest_job(
        {
            "message_id": "msg-backlog-processing",
            "pdf_uri": "file://processing.pdf",
            "received_at": now.isoformat(),
        }
    )
    create_job("OCR-backlog-running", input_reference="file://ocr-running.pdf", status="running")
    create_job("OCR-backlog-skipped", input_reference="file://ocr-skipped.pdf", status="failed")
    create_job("OCR-backlog-awaiting", input_reference="file://ocr-awaiting.pdf", status="awaiting_output")
    create_job("OCR-backlog-recovering-running", input_reference="file://ocr-recovering-running.pdf", status="running")
    update_job("OCR-backlog-skipped", status="failed", error_message="backlog_skipped")
    update_job(
        "OCR-backlog-awaiting",
        status="awaiting_output",
        output_reference="gs://bucket/output/backlog-awaiting.json",
        metrics={
            "result_state": "awaiting_output",
            "stage_updated_at": (now - timedelta(minutes=30)).isoformat(),
            "next_recovery_at": (now - timedelta(minutes=5)).isoformat(),
        },
        error_message="ocr_output_pending",
    )
    update_job(
        "OCR-backlog-recovering-running",
        status="running",
        output_reference="gs://bucket/output/backlog-recovering-running.json",
        metrics={
            "processing_stage": "ocr_pipeline",
            "result_state": "processing",
            "stage_updated_at": (now - timedelta(minutes=30)).isoformat(),
            "next_recovery_at": (now - timedelta(minutes=5)).isoformat(),
        },
        error_message=None,
    )

    with session_scope() as session:
        pending_job = session.get(IngestJob, "msg-backlog-pending")
        processing_job = session.get(IngestJob, "msg-backlog-processing")
        running_job = session.get(OcrJob, "OCR-backlog-running")
        skipped_job = session.get(OcrJob, "OCR-backlog-skipped")
        awaiting_job = session.get(OcrJob, "OCR-backlog-awaiting")
        recovering_running_job = session.get(OcrJob, "OCR-backlog-recovering-running")
        assert pending_job is not None
        assert processing_job is not None
        assert running_job is not None
        assert skipped_job is not None
        assert awaiting_job is not None
        assert recovering_running_job is not None

        pending_job.created_at = now - timedelta(minutes=15)
        pending_job.updated_at = now - timedelta(minutes=15)
        processing_job.status = "processing"
        processing_job.created_at = now - timedelta(hours=1)
        processing_job.started_at = now - timedelta(minutes=45)
        processing_job.updated_at = now - timedelta(minutes=45)
        running_job.created_at = now - timedelta(minutes=20)
        running_job.updated_at = now - timedelta(minutes=20)
        skipped_job.created_at = now - timedelta(minutes=10)
        skipped_job.updated_at = now - timedelta(minutes=5)
        awaiting_job.created_at = now - timedelta(minutes=30)
        awaiting_job.updated_at = now - timedelta(minutes=30)
        recovering_running_job.created_at = now - timedelta(minutes=30)
        recovering_running_job.updated_at = now - timedelta(minutes=30)

    client = TestClient(app)
    res = client.get("/health/backlog")

    assert res.status_code == 200
    payload = res.json()
    assert payload.get("status") == "fail"
    assert payload.get("ingest_queue_depth") == 2
    assert payload.get("ocr_queue_depth") == 1
    assert payload.get("oldest_pending_seconds", 0) >= 60
    ingest = payload.get("ingest") or {}
    ocr = payload.get("ocr") or {}
    assert ingest.get("pending_count") == 1
    assert ingest.get("processing_count") == 1
    assert ingest.get("stale_processing_count") == 1
    assert ingest.get("eligible_backlog_count") == 2
    assert ocr.get("active_count") == 1
    assert ocr.get("awaiting_output_count") == 1
    assert ocr.get("recovering_count") == 0
    assert ocr.get("recoverable_count") == 2
    assert ocr.get("recent_backlog_skipped_count") == 1
    assert ocr.get("stale_count") == 1
    assert int(ocr.get("stale_oldest_seconds") or 0) > 0


def test_system_status_reflects_latest_pipeline_request_in_gcs_only_mode(monkeypatch, tmp_path):
    monkeypatch.setenv("OCR_PIPELINE_STATE_URI", str(tmp_path / "pipeline-state.json"))
    save_pipeline_error("MAIN-old", "OCR pipeline output not found: gs://bucket/output/old.json")
    save_pipeline_request("MAIN-new", "gs://bucket/input/new.pdf")

    client = TestClient(app)
    res = client.get("/system/status")

    assert res.status_code == 200
    pipeline = res.json().get("ocr_pipeline") or {}
    assert pipeline.get("status") == "running"
    assert pipeline.get("last_job_id") == "MAIN-new"
    assert pipeline.get("last_input_ref") == "gs://bucket/input/new.pdf"
