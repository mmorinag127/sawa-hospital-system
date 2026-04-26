import pathlib
import sys
from datetime import datetime
from types import SimpleNamespace

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.db import session_scope  # noqa: E402
from src.models.ingest_job import IngestJob  # noqa: E402
from src.models.uploaded_pdf import UploadedPdf  # noqa: E402
from src.services import ingest_job_service, order_service  # noqa: E402
from src.services import workflow_state_service  # noqa: E402
from src.services.ocr_job_service import create_job, get_job, update_job  # noqa: E402
from src.services.uploaded_pdf_service import get_uploaded_pdf, list_ready_uploaded_pdf_ids  # noqa: E402
from src.workers import ingest_worker  # noqa: E402
from src.workers.ingest_mail_adapter import parse_ingest_payload  # noqa: E402


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


def test_process_uploaded_pdf_job_marks_completed_and_links_entities(monkeypatch):
    order_service.clear_all()
    message_id = "msg-uploaded-pdf-complete"
    uploaded_pdf_id = "UPLtestcomplete"
    _seed_uploaded_pdf(uploaded_pdf_id=uploaded_pdf_id, message_id=message_id)
    payload = {
        "message_id": message_id,
        "pdf_uri": f"gs://bucket/{uploaded_pdf_id}.pdf",
        "received_at": datetime.utcnow().isoformat(),
        "facility_hint": "FAC001",
        "week_hint": "2026-02@2026-02-15~2026-02-21",
        "source_kind": "manual_upload",
        "original_filename": f"{uploaded_pdf_id}.pdf",
        "content_sha256": f"sha-{uploaded_pdf_id}",
    }
    ingest_job_service.create_ingest_job(payload, force=True)

    def _fake_process_ingest_job(job_id: str) -> None:
        job_payload = ingest_job_service.get_ingest_payload(job_id)
        parsed = parse_ingest_payload(job_payload)
        order_service.create_order_from_ingest(
            parsed,
            lines=[
                {
                    "date": "2026-02-20",
                    "daypart": "昼",
                    "menu_name": "Menu A",
                    "diet_type": "regular",
                    "area_id": "2F",
                    "bag_type": "standard",
                    "quantity_original": 2,
                }
            ],
            ocr_attempts=1,
            document_status="success",
        )
        ingest_job_service.complete_ingest_job(job_id)

    monkeypatch.setattr(ingest_worker, "process_ingest_job", _fake_process_ingest_job)

    ingest_worker.process_uploaded_pdf_job(uploaded_pdf_id)

    row = get_uploaded_pdf(uploaded_pdf_id)
    assert row is not None
    assert row["status"] == "completed"
    assert row["current_stage"] == "completed"
    assert row["current_order_id"]
    assert row["current_document_id"]


def test_process_uploaded_pdf_job_uses_canonical_order_ocr_job_id_for_placeholder_order(monkeypatch):
    order_service.clear_all()
    message_id = "msg-uploaded-pdf-canonical-ocr-job"
    uploaded_pdf_id = "UPLtestcanonicaljob"
    _seed_uploaded_pdf(uploaded_pdf_id=uploaded_pdf_id, message_id=message_id)

    captured: dict[str, str | None] = {"ocr_job_id": None}

    def _fake_process_ingest_job(job_id: str) -> None:
        job_payload = ingest_job_service.get_ingest_payload(job_id)
        captured["ocr_job_id"] = str((job_payload or {}).get("ocr_job_id") or "").strip() or None
        ingest_job_service.complete_ingest_job(job_id)

    monkeypatch.setattr(ingest_worker, "process_ingest_job", _fake_process_ingest_job)

    ingest_worker.process_uploaded_pdf_job(uploaded_pdf_id)

    row = get_uploaded_pdf(uploaded_pdf_id)
    assert row is not None
    assert row["status"] == "completed"
    assert row["current_order_id"]
    assert captured["ocr_job_id"] == f"OCR-{row['current_order_id']}"


def test_process_uploaded_pdf_job_retries_and_reclaims_stale_leases(monkeypatch):
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
    payload = {
        "message_id": message_id,
        "pdf_uri": f"gs://bucket/{uploaded_pdf_id}.pdf",
        "received_at": datetime.utcnow().isoformat(),
        "facility_hint": "FAC001",
        "week_hint": "2026-02@2026-02-15~2026-02-21",
        "source_kind": "manual_upload",
        "original_filename": f"{uploaded_pdf_id}.pdf",
        "content_sha256": f"sha-{uploaded_pdf_id}",
    }
    ingest_job_service.create_ingest_job(payload, force=True)

    ready_ids = list_ready_uploaded_pdf_ids(limit=10)
    assert uploaded_pdf_id in ready_ids

    def _fake_process_ingest_job(job_id: str) -> None:
        ingest_job_service.fail_ingest_job(job_id, "simulated_failure")

    monkeypatch.setattr(ingest_worker, "process_ingest_job", _fake_process_ingest_job)

    ingest_worker.process_uploaded_pdf_job(uploaded_pdf_id)

    row = get_uploaded_pdf(uploaded_pdf_id)
    assert row is not None
    assert row["status"] == "retry_wait"
    assert row["current_stage"] == "retry_wait"
    assert row["last_error_code"] == "ingest_error"
    assert row["last_error_message"] == "simulated_failure"
    assert row["attempt_count"] == 2


def test_reconcile_completed_ocr_job_finalizes_uploaded_pdf_and_workflow(monkeypatch):
    order_service.clear_all()
    message_id = "upload:sha256:reconcile-first-pass"
    uploaded_pdf_id = "UPLreconcile"
    _seed_uploaded_pdf(
        uploaded_pdf_id=uploaded_pdf_id,
        message_id=message_id,
        status="processing",
        current_stage="ingest_running",
        attempt_count=1,
        lease_owner="worker:test",
        lease_expires_at=datetime.utcnow(),
    )
    payload = {
        "message_id": message_id,
        "pdf_uri": f"gs://bucket/{uploaded_pdf_id}.pdf",
        "received_at": datetime.utcnow().isoformat(),
        "facility_hint": None,
        "week_hint": "2026-02@2026-02-15~2026-02-21",
        "source_kind": "manual_upload",
        "original_filename": f"{uploaded_pdf_id}.pdf",
        "content_sha256": f"sha-{uploaded_pdf_id}",
    }
    ingest_job_service.create_ingest_job(payload, force=True)
    with session_scope() as session:
        job = session.get(IngestJob, message_id)
        assert job is not None
        job.status = "processing"
        job.started_at = datetime.utcnow()
        job.updated_at = datetime.utcnow()

    order = order_service.create_order_from_ingest(
        parse_ingest_payload(payload),
        lines=[],
        ocr_attempts=1,
        document_status="processing",
        error_message="ocr_pending",
    )
    job_id = f"OCR-{message_id}"
    create_job(job_id, input_reference=payload["pdf_uri"], status="running")
    update_job(
        job_id,
        status="running",
        output_reference="gs://bucket/output.json",
        metrics={"request_mode": "ingest_first_pass", "order_id": order["id"]},
    )

    monkeypatch.setattr(
        order_service,
        "_load_pipeline_output_with_retry",
        lambda *_args, **_kwargs: {
            "status": "done",
            "input_reference": payload["pdf_uri"],
            "output_reference": "gs://bucket/output.json",
            "table_raw": "| 02/20 | 昼 | Menu A |",
            "pages": [{"page_index": 1, "tables": [{"rows": [["02/20", "昼", "Menu A"]]}]}],
        },
    )

    assert order_service.reconcile_completed_ocr_job(job_id) is True

    row = get_uploaded_pdf(uploaded_pdf_id)
    assert row is not None
    assert row["status"] == "completed"
    assert row["current_stage"] == "completed"

    ingest_snapshot = ingest_job_service.get_ingest_job_snapshot(message_id)
    assert ingest_snapshot is not None
    assert ingest_snapshot["status"] == "done"

    latest_evidence = order_service.get_latest_ocr_evidence_run(order["id"], backfill_from_cache=False)
    assert latest_evidence is not None

    workflow = workflow_state_service.refresh_workflow_state(order["id"])
    assert workflow is not None
    assert workflow.get("evidence_run_id")
    assert workflow.get("state") != "uploaded"


def test_reconcile_completed_ocr_job_is_idempotent_once_job_metrics_carry_evidence(monkeypatch):
    order_service.clear_all()
    message_id = "upload:sha256:reconcile-idempotent"
    uploaded_pdf_id = "UPLreconcileidempotent"
    _seed_uploaded_pdf(
        uploaded_pdf_id=uploaded_pdf_id,
        message_id=message_id,
        status="processing",
        current_stage="ingest_running",
        attempt_count=1,
        lease_owner="worker:test",
        lease_expires_at=datetime.utcnow(),
    )
    payload = {
        "message_id": message_id,
        "pdf_uri": f"gs://bucket/{uploaded_pdf_id}.pdf",
        "received_at": datetime.utcnow().isoformat(),
        "facility_hint": None,
        "week_hint": "2026-02@2026-02-15~2026-02-21",
        "source_kind": "manual_upload",
        "original_filename": f"{uploaded_pdf_id}.pdf",
        "content_sha256": f"sha-{uploaded_pdf_id}",
    }
    ingest_job_service.create_ingest_job(payload, force=True)
    with session_scope() as session:
        job = session.get(IngestJob, message_id)
        assert job is not None
        job.status = "processing"
        job.started_at = datetime.utcnow()
        job.updated_at = datetime.utcnow()

    order = order_service.create_order_from_ingest(
        parse_ingest_payload(payload),
        lines=[],
        ocr_attempts=1,
        document_status="processing",
        error_message="ocr_pending",
    )
    job_id = f"OCR-{message_id}"
    create_job(job_id, input_reference=payload["pdf_uri"], status="done")
    update_job(
        job_id,
        status="done",
        output_reference="gs://bucket/output-idempotent.json",
        metrics={"request_mode": "ingest_first_pass", "order_id": order["id"]},
    )

    monkeypatch.setattr(
        order_service,
        "_load_pipeline_output_with_retry",
        lambda *_args, **_kwargs: {
            "status": "done",
            "input_reference": payload["pdf_uri"],
            "output_reference": "gs://bucket/output-idempotent.json",
            "table_raw": "| 02/20 | 昼 | Menu A |",
            "pages": [{"page_index": 1, "tables": [{"rows": [["02/20", "昼", "Menu A"]]}]}],
        },
    )

    assert order_service.reconcile_completed_ocr_job(job_id) is True
    first_evidence = order_service.get_latest_ocr_evidence_run(order["id"], backfill_from_cache=False)
    assert first_evidence is not None
    first_evidence_id = first_evidence["id"]

    monkeypatch.setattr(
        order_service,
        "_load_pipeline_output_with_retry",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not reload first-pass output after reconcile")),
    )

    assert order_service.reconcile_completed_ocr_job(job_id) is True
    latest_evidence = order_service.get_latest_ocr_evidence_run(order["id"], backfill_from_cache=False)
    assert latest_evidence is not None
    assert latest_evidence["id"] == first_evidence_id
    job = get_job(job_id)
    assert job is not None
    metrics = job.get("metrics") or {}
    assert metrics.get("evidence_run_id") == first_evidence_id


def test_process_ingest_inline_finalizes_first_pass_side_effects(monkeypatch):
    order_service.clear_all()
    seen: list[dict[str, str | None]] = []

    monkeypatch.setattr(
        ingest_worker.config_service,
        "load_ingest_policy",
        lambda: {"ocr_retry_limit": 1, "quantity_rules": {}},
    )
    monkeypatch.setattr(
        ingest_worker.config_service,
        "load_facility_master",
        lambda: {"fax_template_base": {}, "facilities": []},
    )
    monkeypatch.setattr(ingest_worker, "load_bytes_from_uri", lambda _uri: b"%PDF-test")
    monkeypatch.setattr(
        ingest_worker,
        "run_ocr_pipeline",
        lambda **_kwargs: {
            "status": "done",
            "input_reference": "gs://bucket/input.pdf",
            "output_reference": "gs://bucket/output.json",
            "table_raw": "|02/15|朝|Menu A|",
            "pages": [{"tables": [{"rows": [["02/15", "朝", "Menu A"]]}]}],
        },
    )
    monkeypatch.setattr(ingest_worker, "get_default_output_bucket", lambda: None)
    monkeypatch.setattr(ingest_worker, "save_output_json", lambda *_args, **_kwargs: "file://ocr-output.json")
    monkeypatch.setattr(
        ingest_worker,
        "extract_fax_data",
        lambda *_args, **_kwargs: SimpleNamespace(
            facility_name=None,
            date_strings=[],
            tokens=[],
            table_rows=[],
        ),
    )
    monkeypatch.setattr(ingest_worker, "_enqueue_auto_llm_reparse", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        ingest_worker.ocr_evidence_service,
        "persist_evidence_run",
        lambda **_kwargs: {"id": "OEVinline"},
    )
    monkeypatch.setattr(
        ingest_worker,
        "create_order_from_ingest",
        lambda *_args, **_kwargs: {"id": "ORDinline"},
    )
    monkeypatch.setattr(
        ingest_worker,
        "finalize_first_pass_side_effects",
        lambda **kwargs: seen.append(
            {
                "job_id": kwargs.get("job_id"),
                "order_id": kwargs.get("order_id"),
                "message_id": kwargs.get("message_id"),
            }
        ),
    )

    ingest_worker._process_ingest_inline(
        message_id="upload:sha256:inline-first-pass",
        pdf_uri="gs://bucket/input.pdf",
        received_at=datetime.utcnow().isoformat(),
        facility_hint=None,
        week_hint="2026-02@2026-02-15~2026-02-21",
        facility_name=None,
        date_hints=[],
        skip_ocr=False,
        source_kind="manual_upload",
        original_filename="inline.pdf",
        content_sha256="sha-inline",
    )

    assert seen == [
        {
            "job_id": "OCR-upload:sha256:inline-first-pass",
            "order_id": "ORDinline",
            "message_id": "upload:sha256:inline-first-pass",
        }
    ]
    job = get_job("OCR-upload:sha256:inline-first-pass")
    assert job is not None
    metrics = job.get("metrics") or {}
    assert metrics.get("request_mode") == "ingest_first_pass"


def test_process_ingest_inline_prefers_payload_ocr_job_id(monkeypatch):
    order_service.clear_all()
    seen: list[dict[str, str | None]] = []

    monkeypatch.setattr(
        ingest_worker.config_service,
        "load_ingest_policy",
        lambda: {"ocr_retry_limit": 0, "quantity_rules": {}},
    )
    monkeypatch.setattr(ingest_worker.config_service, "load_facility_master", lambda: {"fax_template_base": {}, "facilities": []})
    monkeypatch.setattr(ingest_worker, "_enqueue_auto_llm_reparse", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        ingest_worker,
        "create_order_from_ingest",
        lambda *_args, **_kwargs: {"id": "ORDinlinecanonical"},
    )
    monkeypatch.setattr(
        ingest_worker,
        "create_job",
        lambda job_id, input_reference, status="running": seen.append({"job_id": job_id, "input_reference": input_reference}) or ({}, True),
    )

    ingest_worker._process_ingest_inline(
        message_id="upload:sha256:inline-canonical",
        pdf_uri="gs://bucket/input.pdf",
        received_at=datetime.utcnow().isoformat(),
        facility_hint=None,
        week_hint="2026-02@2026-02-15~2026-02-21",
        facility_name=None,
        date_hints=[],
        skip_ocr=False,
        source_kind="manual_upload",
        original_filename="inline.pdf",
        content_sha256="sha-inline",
        ocr_job_id="OCR-ORDinlinecanonical",
    )

    assert seen == [{"job_id": "OCR-ORDinlinecanonical", "input_reference": "gs://bucket/input.pdf"}]
