from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import json

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker

from src import db
from src.models.order import Order
from src.models.order_ocr_cache import OrderOcrCache
from src.models.ocr_job import OcrJob
from src.models.order_workflow_state import OrderWorkflowState
from src.services import ocr_job_service, order_service, workflow_attempt_guard_service


@pytest.fixture
def workflow_database(tmp_path, monkeypatch, delivery_sqlite_template):
    from tests.integration.sqlite_fixture_template import file_digest, sqlite_engine_from_template

    template, expected = delivery_sqlite_template
    database = sqlite_engine_from_template(template, tmp_path / "workflow-ocr.sqlite3",
                                           expected_digest=expected, future=True)
    factory = sessionmaker(database, autoflush=False, future=True)
    monkeypatch.setattr(db, "SessionLocal", factory)
    try:
        yield database, factory
    finally:
        database.dispose()
        assert file_digest(template) == expected, "workflow SQLite template was modified"


def _add_order_and_workflow(factory, *, order_id: str, revision: int = 7) -> None:
    with factory.begin() as session:
        session.add(
            Order(
                id=order_id,
                facility_code="FAC_TEST",
                week_code="2026-10-05",
                status="要確認",
                document_uri="file:///workflow-ocr.pdf",
                message_id=f"message-{order_id}",
                received_at=datetime.utcnow(),
            )
        )
        session.add(
            OrderWorkflowState(
                order_id=order_id,
                revision=revision,
                state="context_confirmed",
                secondary_actions_json={"workflow_v2": {}},
                blockers_json=[],
                warnings_json=[],
                last_transition_at=datetime.utcnow(),
            )
        )
        session.flush()
        session.get(OrderWorkflowState, order_id).revision = revision


def _metrics() -> dict:
    return {
        "job_id": "OCR-ORD-enqueue",
        "request_mode": "ocr_rerun",
        "processing_stage": "queued",
        "result_state": "processing",
        "workflow_completion_revision": 8,
    }


def _database_snapshot(database):
    with database.connect() as connection:
        return {table.name: [dict(row) for row in connection.execute(
            sa.select(table).order_by(*table.primary_key.columns)).mappings()]
            for table in db.Base.metadata.sorted_tables}


@pytest.mark.parametrize("owner", ["ORD-foreign", None, ""], ids=["foreign", "null", "blank"])
@pytest.mark.parametrize("status,age,stale_action,force", [
    ("running", 0, "retry", False),
    ("running", 2, "wait", False),
    ("running", 2, "retry", False),
    ("failed", 2, "retry", False),
    ("running", 0, "retry", True),
], ids=["active", "stale-wait", "stale-retry", "terminal", "force"])
def test_reservation_refuses_foreign_or_unknown_owner_without_any_write(workflow_database, record_property, owner, status, age, stale_action, force):
    """Stored legacy rows, including an invalid blank FK, are not adoptable jobs."""
    from src.models.order_ocr_evidence_run import OrderOcrEvidenceRun
    from src.models.order_sheet_draft import OrderSheetDraft

    database, factory = workflow_database
    for order_id in ("ORD-requester", "ORD-foreign"):
        _add_order_and_workflow(factory, order_id=order_id)
    recorded_at = datetime.utcnow() - timedelta(hours=age)
    with factory.begin() as session:
        session.add(OrderOcrEvidenceRun(id="foreign-evidence", order_id="ORD-foreign", schema_version="component",
                    status="diagnostic_only", payload_json={"original": "diagnostics"}, artifact_manifest_json={"owned": "foreign-artifact"},
                    artifact_digest=hashlib.sha256(json.dumps({"original": "diagnostics"}, sort_keys=True).encode()).hexdigest()))
        session.flush()
        session.add(OrderSheetDraft(id="foreign-draft", order_id="ORD-foreign", base_evidence_run_id="foreign-evidence",
                    draft_sheet_json={"original": "stored draft"}, edited_by="original-operator"))
        session.add(OrderOcrCache(order_id="ORD-foreign", payload={"original": "cache"}))
        session.flush()
        workflow = session.get(OrderWorkflowState, "ORD-foreign")
        workflow.evidence_run_id, workflow.draft_id = "foreign-evidence", "foreign-draft"
        session.add(OcrJob(id="OCR-owner-guard", order_id=owner, status=status,
                    input_reference="component-only:foreign-input", output_reference="component-only:foreign-output",
                    input_artifact_digest="original-diagnostic-digest", error_message="original error", template_id="original-template",
                    metrics={"request_mode": "ocr_rerun", "stage_updated_at": recorded_at.isoformat(), "original": {"diagnostic": True}},
                    created_at=recorded_at, updated_at=recorded_at))
    before = _database_snapshot(database)
    with factory.begin() as session:
        job, error = ocr_job_service.reserve_workflow_reparse_job(
            session, job_id="OCR-owner-guard", order_id="ORD-requester",
            input_reference="component-only:requested-input", template_version_id=None,
            metrics=_metrics(), stale_action=stale_action, force=force,
        )
    after = _database_snapshot(database)
    record_property("sql_before", json.dumps(before, default=str, sort_keys=True))
    record_property("sql_after", json.dumps(after, default=str, sort_keys=True))
    assert job is None
    assert error == "ocr_job_order_mismatch"
    assert after == before


@pytest.mark.parametrize("status,force,expected_error", [
    ("running", False, "ocr_rerun_in_progress"),
    ("running", True, None),
    ("failed", False, None),
], ids=["active", "force", "terminal"])
def test_same_owner_reservation_keeps_existing_retry_policy(workflow_database, status, force, expected_error):
    database, factory = workflow_database
    _add_order_and_workflow(factory, order_id="ORD-enqueue")
    with factory.begin() as session:
        session.add(OcrJob(id="OCR-ORD-enqueue", order_id="ORD-enqueue", status=status,
                          input_reference="component-only:original", metrics=_metrics()))
    before = _database_snapshot(database)
    with factory.begin() as session:
        job, error = ocr_job_service.reserve_workflow_reparse_job(
            session, job_id="OCR-ORD-enqueue", order_id="ORD-enqueue",
            input_reference="component-only:retry", template_version_id=None,
            metrics=_metrics(), stale_action="retry", force=force,
        )
        assert error == expected_error
        if expected_error:
            assert job is None
        else:
            assert job.order_id == "ORD-enqueue"
            assert job.status == "running"
            assert job.input_reference == "component-only:retry"
    after = _database_snapshot(database)
    if expected_error:
        assert after == before
    else:
        assert {k: v for k, v in after.items() if k != "ocr_jobs"} == {k: v for k, v in before.items() if k != "ocr_jobs"}


def test_reservation_and_workflow_update_commit_together(workflow_database):
    _database, factory = workflow_database
    _add_order_and_workflow(factory, order_id="ORD-enqueue")

    with factory.begin() as session:
        workflow = session.get(OrderWorkflowState, "ORD-enqueue")
        job, error = ocr_job_service.reserve_workflow_reparse_job(
            session,
            job_id="OCR-ORD-enqueue",
            order_id="ORD-enqueue",
            input_reference="file:///workflow-ocr.pdf",
            template_version_id=None,
            metrics=_metrics(),
            stale_action="retry",
            force=False,
        )
        assert error is None
        assert job is not None
        workflow.state = "ocr_running"
        workflow.revision = 8

    with factory() as session:
        assert session.get(OcrJob, "OCR-ORD-enqueue").status == "running"
        assert session.get(OrderWorkflowState, "ORD-enqueue").revision == 8


def test_completion_revision_is_recorded_after_the_last_workflow_flush(workflow_database):
    _database, factory = workflow_database
    _add_order_and_workflow(factory, order_id="ORD-final-revision")

    with factory.begin() as session:
        workflow = session.get(OrderWorkflowState, "ORD-final-revision")
        # Existing evidence/draft cleanup can flush workflow before the queue state.
        workflow.evidence_run_id = None
        workflow.draft_id = None
        session.flush()
        job, error = ocr_job_service.reserve_workflow_reparse_job(
            session,
            job_id="OCR-ORD-final-revision",
            order_id="ORD-final-revision",
            input_reference="file:///workflow-ocr.pdf",
            template_version_id=None,
            metrics={
                "job_id": "OCR-ORD-final-revision",
                "request_mode": "ocr_rerun",
                "processing_stage": "queued",
                "result_state": "processing",
            },
            stale_action="retry",
            force=False,
        )
        assert error is None
        workflow.state = "ocr_running"
        session.flush()
        metrics = dict(job.metrics or {})
        metrics["workflow_completion_revision"] = workflow.revision
        job.metrics = metrics
        session.flush()

    with factory() as session:
        workflow = session.get(OrderWorkflowState, "ORD-final-revision")
        job = session.get(OcrJob, "OCR-ORD-final-revision")
        assert job.metrics["workflow_completion_revision"] == workflow.revision


def test_conflicted_transaction_rolls_back_reserved_job_and_workflow_change(workflow_database):
    _database, factory = workflow_database
    _add_order_and_workflow(factory, order_id="ORD-conflict")

    with pytest.raises(RuntimeError, match="workflow_revision_conflict"):
        with factory.begin() as session:
            workflow = session.get(OrderWorkflowState, "ORD-conflict")
            job, error = ocr_job_service.reserve_workflow_reparse_job(
                session,
                job_id="OCR-ORD-conflict",
                order_id="ORD-conflict",
                input_reference="file:///workflow-ocr.pdf",
                template_version_id=None,
                metrics=_metrics(),
                stale_action="retry",
                force=False,
            )
            assert error is None
            assert job is not None
            workflow.state = "ocr_running"
            raise RuntimeError("workflow_revision_conflict")

    with factory() as session:
        assert session.get(OcrJob, "OCR-ORD-conflict") is None
        workflow = session.get(OrderWorkflowState, "ORD-conflict")
        assert workflow.state == "context_confirmed"
        assert workflow.revision == 7


def test_stale_retry_reuses_job_inside_the_same_transaction(workflow_database):
    _database, factory = workflow_database
    _add_order_and_workflow(factory, order_id="ORD-stale")
    with factory.begin() as session:
        session.add(
            OcrJob(
                id="OCR-ORD-stale",
                order_id="ORD-stale",
                status="running",
                input_reference="file:///old.pdf",
                metrics={
                    "request_mode": "ocr_rerun",
                    "stage_updated_at": (datetime.utcnow() - timedelta(hours=2)).isoformat(),
                },
                created_at=datetime.utcnow() - timedelta(hours=2),
                updated_at=datetime.utcnow() - timedelta(hours=2),
            )
        )

    with factory.begin() as session:
        job, error = ocr_job_service.reserve_workflow_reparse_job(
            session,
            job_id="OCR-ORD-stale",
            order_id="ORD-stale",
            input_reference="file:///current.pdf",
            template_version_id=None,
            metrics=_metrics(),
            stale_action="retry",
            force=False,
        )
        assert error is None
        assert job.input_reference == "file:///current.pdf"
        assert job.status == "running"


def test_wait_on_stale_job_leaves_no_job_mutation(workflow_database):
    _database, factory = workflow_database
    _add_order_and_workflow(factory, order_id="ORD-wait")
    with factory.begin() as session:
        session.add(
            OcrJob(
                id="OCR-ORD-wait",
                order_id="ORD-wait",
                status="running",
                input_reference="file:///old.pdf",
                metrics={
                    "request_mode": "ocr_rerun",
                    "stage_updated_at": (datetime.utcnow() - timedelta(hours=2)).isoformat(),
                },
                created_at=datetime.utcnow() - timedelta(hours=2),
                updated_at=datetime.utcnow() - timedelta(hours=2),
            )
        )

    with factory.begin() as session:
        job, error = ocr_job_service.reserve_workflow_reparse_job(
            session,
            job_id="OCR-ORD-wait",
            order_id="ORD-wait",
            input_reference="file:///current.pdf",
            template_version_id=None,
            metrics=_metrics(),
            stale_action="wait",
            force=False,
        )
        assert job is None
        assert error == "ocr_rerun_stale_wait"

    with factory() as session:
        job = session.get(OcrJob, "OCR-ORD-wait")
        assert job.status == "running"
        assert job.input_reference == "file:///old.pdf"


def test_old_attempt_cannot_finish_or_fail_the_retried_job(workflow_database, monkeypatch, tmp_path):
    import json
    from copy import deepcopy
    from datetime import date
    from hashlib import sha256
    from src.models.document import OrderDocument
    from src.models.facility import Facility
    from src.models.uploaded_pdf import UploadedPdf
    from src.services import canonical_facility_lineage as lineage, config_service
    _database, factory = workflow_database
    _add_order_and_workflow(factory, order_id="ORD-attempt")
    pdf = tmp_path / "attempt.pdf"
    pdf.write_bytes(b"original attempt ownership input")
    configured = {"facility": {"facility_id": "FAC_TEST", "facility_name": "FAC_TEST", "areas": None, "fax_template": {"template_id": "own", "page": 1, "columns": [{"column_id": "menu", "index": 0, "source_index": 0, "role": "menu"}]}}, "policy": {}}
    configured["facility"]["fax_template"]["columns"].append({
        "column_id": "quantity", "index": 1, "source_index": 1, "role": "quantity",
        "diet_type": "regular", "area_id": "X", "name": "qty.regular_x",
    })
    physical = [{"menu_date": "2026-10-07", "daypart": "lunch", "slot_index": 0, "name": "own"}]
    # Synthetic concurrency input, not a live facility or recognition receipt.
    authored = {"version_id": "own", "valid_from": "2026-10-05", "valid_to": "2026-10-11", "facility": configured["facility"]}
    master = tmp_path / "attempt-facilities.json"
    master.write_text(json.dumps({"facilities": [{**configured["facility"], "versions": [authored]}]}), encoding="utf-8")
    policy = tmp_path / "attempt-policy.json"
    policy.write_text(json.dumps(configured["policy"]), encoding="utf-8")
    monkeypatch.delenv("FACILITY_MASTER_URI", raising=False)
    monkeypatch.delenv("K_SERVICE", raising=False)
    monkeypatch.setenv("FACILITY_MASTER_PATH", str(master))
    monkeypatch.setattr(config_service, "INGEST_POLICY_PATH", policy)
    monkeypatch.setattr(order_service, "_build_position_menu_entries_safe", lambda *args, session=None: deepcopy(physical))
    with factory.begin() as session:
        selected, _ = lineage.resolve(session, "FAC_TEST", date(2026, 10, 5))
        session.add(Facility(id=selected["facility_id"], name=selected["facility_name"]))
        session.flush()
        _, projection = lineage.materialize(session, selected)
        template_version_id = projection.id
        order = session.get(Order, "ORD-attempt")
        order.template_version_id = template_version_id
        order.week_code = "2026-10@2026-10-05~2026-10-11"
        order.document_uri = str(pdf)
        order.current_document_id = "attempt-document"
        session.add(OrderDocument(id="attempt-document", storage_uri=str(pdf), source_email_id=order.message_id))
        session.add(UploadedPdf(id="attempt-pdf", storage_uri=str(pdf), message_id=order.message_id, original_filename="attempt.pdf", content_sha256=sha256(pdf.read_bytes()).hexdigest(), received_at=order.received_at))
    with factory.begin() as session:
        job, error = ocr_job_service.reserve_workflow_reparse_job(
            session,
            job_id="OCR-ORD-attempt",
            order_id="ORD-attempt",
            input_reference=str(pdf),
            template_version_id=template_version_id,
            metrics={**_metrics(), "workflow_attempt_id": "attempt-old"},
            stale_action="retry",
            force=False,
        )
        assert error is None
        assert job.metrics["workflow_attempt_id"] == "attempt-old"

    with factory.begin() as session:
        job, error = ocr_job_service.reserve_workflow_reparse_job(
            session,
            job_id="OCR-ORD-attempt",
            order_id="ORD-attempt",
            input_reference=str(pdf),
            template_version_id=template_version_id,
            metrics={**_metrics(), "workflow_attempt_id": "attempt-new"},
            stale_action="retry",
            force=True,
        )
        assert error is None
        assert job.metrics["workflow_attempt_id"] == "attempt-new"

    with factory.begin() as session:
        order = session.get(Order, "ORD-attempt")
        original = workflow_attempt_guard_service.capture_reparse_read_input(session, order)
        receipt = workflow_attempt_guard_service.capture_selected_pdf_receipt(session, order)
        job = session.get(OcrJob, "OCR-ORD-attempt")
        job.metrics = {**job.metrics, "reparse_read_input": original, "selected_pdf_receipt": receipt}
    _job, updated = ocr_job_service.update_job_for_workflow_attempt(
        "OCR-ORD-attempt",
        "attempt-old",
        status="failed",
        error_message="old_attempt_finished",
        metrics={"workflow_attempt_id": "attempt-old"},
    )
    assert updated is False

    _job, updated = ocr_job_service.update_job_for_workflow_attempt(
        "OCR-ORD-attempt",
        "attempt-new",
        order_id="ORD-attempt",
        expected_workflow_revision=7,
        status="done",
        error_message=None,
        metrics={"workflow_attempt_id": "attempt-new", "workflow_completion_revision": 9},
    )
    assert updated is True
    with factory() as session:
        job = session.get(OcrJob, "OCR-ORD-attempt")
        assert job.status == "done"
        assert job.error_message is None
        assert job.metrics["workflow_attempt_id"] == "attempt-new"


def test_two_session_attempt_fence_rejects_old_terminal_update_after_new_claim(workflow_database):
    _database, factory = workflow_database
    _add_order_and_workflow(factory, order_id="ORD-two-session")
    with factory.begin() as session:
        session.add(
            OcrJob(
                id="OCR-ORD-two-session",
                order_id="ORD-two-session",
                status="running",
                input_reference="file:///old.pdf",
                metrics={"workflow_attempt_id": "attempt-old", "request_mode": "ocr_rerun"},
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
        )

    old_attempt_session = factory()
    try:
        # This session represents an already-running old worker. The retry claim
        # commits before its terminal update reaches the database.
        assert old_attempt_session.get(OcrJob, "OCR-ORD-two-session").metrics["workflow_attempt_id"] == "attempt-old"
        with factory.begin() as retry_session:
            job, error = ocr_job_service.reserve_workflow_reparse_job(
                retry_session,
                job_id="OCR-ORD-two-session",
                order_id="ORD-two-session",
                input_reference="file:///retry.pdf",
                template_version_id=None,
                metrics={"workflow_attempt_id": "attempt-new", "request_mode": "ocr_rerun"},
                stale_action="retry",
                force=True,
            )
            assert error is None
            assert job.metrics["workflow_attempt_id"] == "attempt-new"

        _job, updated = ocr_job_service.update_job_for_workflow_attempt(
            "OCR-ORD-two-session",
            "attempt-old",
            status="failed",
            error_message="late_old_worker",
            metrics={"workflow_attempt_id": "attempt-old"},
        )
        assert updated is False
    finally:
        old_attempt_session.close()

    with factory() as session:
        job = session.get(OcrJob, "OCR-ORD-two-session")
        assert job.status == "running"
        assert job.metrics["workflow_attempt_id"] == "attempt-new"


def test_superseded_attempt_cannot_write_order_lines_or_ocr_cache(workflow_database):
    _database, factory = workflow_database
    _add_order_and_workflow(factory, order_id="ORD-write-fence")
    with factory.begin() as session:
        session.add(
            OcrJob(
                id="OCR-ORD-write-fence",
                order_id="ORD-write-fence",
                status="running",
                input_reference="file:///retry.pdf",
                metrics={"workflow_attempt_id": "attempt-new", "request_mode": "ocr_rerun"},
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
        )

    token = order_service._workflow_ocr_attempt_id.set("attempt-old")  # noqa: SLF001
    try:
        with factory.begin() as session:
            assert not order_service._workflow_attempt_write_fence(  # noqa: SLF001
                session, "OCR-ORD-write-fence", "ORD-write-fence"
            )
            # The rejected attempt never reaches either order data or OCR cache writes.
            assert session.get(OrderOcrCache, "ORD-write-fence") is None
            assert session.query(Order).filter(Order.id == "ORD-write-fence").one().lines_updated_at is None
    finally:
        order_service._workflow_ocr_attempt_id.reset(token)  # noqa: SLF001


def test_same_attempt_new_workflow_revision_fails_the_transaction_guard(workflow_database):
    _database, factory = workflow_database
    _add_order_and_workflow(factory, order_id="ORD-revision-fence", revision=7)
    with factory.begin() as session:
        session.add(
            OcrJob(
                id="OCR-ORD-revision-fence",
                order_id="ORD-revision-fence",
                status="running",
                input_reference=session.get(Order, "ORD-revision-fence").document_uri,
                metrics={"workflow_attempt_id": "attempt-current", "request_mode": "ocr_rerun"},
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
        )
    with factory.begin() as session:
        assert not workflow_attempt_guard_service.lock_current_workflow_attempt(
            session,
            job_id="OCR-ORD-revision-fence",
            workflow_attempt_id="attempt-current",
            order_id="ORD-revision-fence",
            expected_workflow_revision=8,
        )
