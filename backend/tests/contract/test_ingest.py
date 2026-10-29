"""Authored PDF/JSON component contracts; no live FAX or native recognition claim."""
import sys
import pathlib
from copy import deepcopy
from datetime import date, datetime, timedelta
from hashlib import sha256

import fitz
import pytest
from fastapi.testclient import TestClient
from google.api_core.exceptions import NotFound
from google.cloud import tasks_v2
from sqlalchemy import select

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.main import app  # noqa: E402
from src import db  # noqa: E402
from src.services import order_service, manual_upload_service  # noqa: E402
from src.api import ingest as ingest_api  # noqa: E402
from src.services.manual_upload_service import ManualUploadSavedFile  # noqa: E402
from src.db import session_scope  # noqa: E402
from src.models.document import OrderDocument  # noqa: E402
from src.models.ingest_job import IngestJob  # noqa: E402
from src.models.ingest_delivery import IngestDelivery  # noqa: E402
from src.models.ocr_job import OcrJob  # noqa: E402
from src.models.uploaded_pdf import UploadedPdf  # noqa: E402
from src.models.order import Order  # noqa: E402
from src.services.uploaded_pdf_service import claim_uploaded_pdf  # noqa: E402
from tests.contract.auth_support import hospital_operator  # noqa: E402, F401
from tests.helpers.ingest_original import (  # noqa: E402
    _authored_selection,
    configure_delivery, authored_sources, register_authored_original,
    authored_pipeline_provider, process_uploaded_original,
)
from src.services import ingest_job_service as retry_ingest_jobs  # noqa: E402
from src.services import uploaded_pdf_service as retry_uploads  # noqa: E402
from src.services import ingest_delivery_service as retry_delivery  # noqa: E402
from src.services import ocr_job_service as ocr_jobs  # noqa: E402
from src.services.canonical_facility_store import read_snapshot  # noqa: E402
from src.workers import ingest_worker  # noqa: E402


def _operator_header() -> dict[str, str]:
    raise AssertionError("registered hospital operator fixture required")


@pytest.fixture(autouse=True)
def _registered_google_operator(isolated_formal_schema, hospital_operator, monkeypatch):
    # Preserve callers of the local header helper; real roles/grants stay in SQL.
    monkeypatch.setattr(sys.modules[__name__], "_operator_header", lambda: hospital_operator.headers)
    transport = _CloudTasks()
    monkeypatch.setattr(tasks_v2, "CloudTasksClient", lambda: transport)
    return transport


def _database_snapshot():
    with session_scope() as session:
        return {table.name: deepcopy([dict(row) for row in session.execute(
            select(table).order_by(*table.primary_key.columns)).mappings()])
            for table in db.Base.metadata.sorted_tables}


class _CloudTasks:
    """Only the external task transport is doubled; dispatch and intents are real."""
    def __init__(self):
        self.tasks = {}
        self.lookups = []
        self.created = []

    def get_task(self, *, request):
        self.lookups.append(request["name"])
        assert request["response_view"] == tasks_v2.Task.View.FULL
        if request["name"] not in self.tasks:
            raise NotFound("authored task absent")
        return self.tasks[request["name"]]

    def create_task(self, *, request):
        task = tasks_v2.Task(request["task"])
        assert task.name.startswith(request["parent"] + "/tasks/")
        self.created.append(task.name)
        self.tasks[task.name] = task
        return task


class _UploadCase:
    def __init__(self, client, form, menu_date, transport):
        self.client, self.form, self.menu_date, self.transport = client, form, menu_date, transport

    def pdf(self, label="authored", pages=1):
        with fitz.open() as doc:
            for index in range(pages):
                page = doc.new_page()
                page.insert_text((72, 72), "\n".join([
                    "Synthetic ingest contract, not a live FAX",
                    self.form["facility_hint"], self.form["week_hint"],
                    f"{label} page {index + 1}",
                    f"{self.menu_date:%m/%d} lunch Menu A 2",
                ]))
            content = doc.tobytes(garbage=3, deflate=True)
        with fitz.open(stream=content, filetype="pdf") as checked:
            assert len(checked) == pages
            for index, page in enumerate(checked):
                text = page.get_text()
                assert self.form["facility_hint"] in text
                assert self.form["week_hint"] in text
                assert f"{label} page {index + 1}" in text
                assert f"{self.menu_date:%m/%d} lunch Menu A 2" in text
        return content

    def upload(self, files, **overrides):
        return self.client.post("/ingest/upload", files=files,
            data={**self.form, **overrides}, headers=_operator_header())

    def finish(self, item):
        assert process_uploaded_original(item["uploaded_pdf_id"])["result"] == "business_done"
        uploaded = retry_uploads.get_uploaded_pdf(item["uploaded_pdf_id"])
        assert uploaded["status"] == "completed"
        original = retry_ingest_jobs.get_ingest_job_snapshot(item["ingest_job_id"])
        assert original["status"] == "done"
        identity, generation = retry_delivery.delivery_for_job(
            original["id"], original["generation"], original["input_digest"])
        with session_scope() as session:
            intent = session.get(IngestDelivery, identity)
            assert intent.generation == generation and intent.state == "ACKNOWLEDGED"
            assert intent.terminal_receipt
            order = session.get(Order, uploaded["current_order_id"])
            document = session.get(OrderDocument, uploaded["current_document_id"])
            assert order and document
            assert order.current_document_id == document.id
            assert document.source_email_id == item["message_id"]
            assert document.storage_uri == item["pdf_uri"]
            assert order.facility_code == self.form["facility_hint"]
            assert order.week_code == self.form["week_hint"]
            assert order.template_version_id
        return uploaded


@pytest.fixture
def upload_case(monkeypatch, tmp_path, _registered_google_operator):
    client = TestClient(app)
    sources = {}

    def build(facility="FAC001", menu_date=date(2026, 2, 15),
              week="2026-02@2026-02-15~2026-02-21"):
        configure_delivery(monkeypatch)
        # The shared blank PDF is unused. Actual upload PDFs are authored/checked above.
        authored_sources(monkeypatch, tmp_path, facility_id=facility, menu_date=menu_date)
        for path in (tmp_path / "authored-facilities.json", tmp_path / "authored-policy.json"):
            sources[path] = path.read_bytes()
        monkeypatch.delenv("RAW_BUCKET", raising=False)
        monkeypatch.setattr(manual_upload_service, "_local_storage_base", lambda: tmp_path / "uploads")
        transport = _registered_google_operator
        authored_pipeline_provider(monkeypatch,
            table_rows=[[f"{menu_date:%m/%d}", "昼", "Menu A", "2"]])
        return _UploadCase(client, {
            "facility_hint": facility, "week_hint": week,
            "effective_date": menu_date.isoformat(),
            "expected_canonical_revision": read_snapshot().revision,
            "selected_version_id": "authored-original",
            "received_at": datetime.combine(menu_date, datetime.min.time()).isoformat(),
        }, menu_date, transport)

    yield build
    client.close()
    assert {path: path.read_bytes() for path in sources} == sources


def _placeholder_pdf_bytes() -> bytes:
    doc = fitz.open()
    try:
        page = doc.new_page()
        page.insert_text((72, 72), "Page 1")
        return doc.tobytes(garbage=3, deflate=True)
    finally:
        doc.close()


def test_ingest_upload_requires_operator_auth(upload_case):
    case = upload_case()
    files = {"pdf_file": ("upload.pdf", case.pdf(), "application/pdf")}
    before = _database_snapshot()
    unauthorized = case.client.post("/ingest/upload", files=files, data=case.form)
    assert unauthorized.status_code == 401
    assert _database_snapshot() == before
    assert case.transport.created == []
    authorized = case.upload(files)
    assert authorized.status_code == 202
    assert authorized.json()["accepted"] is True
    case.finish(authorized.json())


def test_ingest_upload_single_response_keeps_legacy_fields_and_items(upload_case):
    case = upload_case()
    res = case.upload({"pdf_file": ("single.pdf", case.pdf(), "application/pdf")})
    assert res.status_code == 202
    body = res.json()
    assert body["accepted"] is True
    assert body["count"] == 1
    assert isinstance(body["items"], list) and len(body["items"]) == 1
    assert body["message_id"] == body["items"][0]["message_id"]
    assert body["uploaded_pdf_id"] == body["items"][0]["uploaded_pdf_id"]
    assert body["ingest_job_id"] == body["items"][0]["ingest_job_id"]
    assert body["pdf_uri"] == body["items"][0]["pdf_uri"]
    assert body["duplicate_blocked"] == body["items"][0]["duplicate_blocked"]
    assert body["order_id"] == body["items"][0]["order_id"]
    assert body["existing_order_id"] == body["items"][0]["existing_order_id"]
    assert body["existing_order_preview"] == body["items"][0]["existing_order_preview"]
    assert body["intake_decision"] == body["items"][0]["intake_decision"]
    assert body["status"] == body["items"][0]["status"]
    assert body["current_stage"] == body["items"][0]["current_stage"]
    assert body["page_number"] == 1
    assert body["total_pages"] == 1
    assert body["order_id"] is None  # Acceptance is not synchronous registration.
    case.finish(body)


def test_ingest_upload_requires_facility_and_week_context(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "false")
    client = TestClient(app)

    res = client.post(
        "/ingest/upload",
        files={"pdf_file": ("single.pdf", b"%PDF-1.4\n%EOF\n", "application/pdf")},
        data={"facility_hint": "FAC001"},
        headers=_operator_header(),
    )

    assert res.status_code == 400
    detail = res.json()["detail"]
    assert detail["error"] == "upload_context_required"
    assert detail["missing"] == ["week_hint"]


def test_ingest_week_options_returns_upload_week_choices(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "false")
    expected_options = [
        {
            "week_id": "2026-02@2026-02-01~2026-02-07",
            "label": "2026-02 (02/01-02/07)",
            "date_from": "2026-02-01",
            "date_to": "2026-02-07",
        }
    ]
    calls = []

    def build_week_options(month_id, facility_id):
        calls.append((month_id, facility_id))
        return expected_options

    monkeypatch.setattr(
        ingest_api.week_candidate_service,
        "build_week_option_entries",
        build_week_options,
    )
    client = TestClient(app)

    res = client.get(
        "/ingest/week-options?month_id=2026-02&facility_id=FAC001",
        headers=_operator_header(),
    )

    assert res.status_code == 200
    assert res.json()["options"] == expected_options
    assert calls == [("2026-02", "FAC001")]


def test_ingest_upload_splits_two_page_pdf_into_two_items(upload_case):
    case = upload_case()
    res = case.upload({"pdf_file": ("multi.pdf", case.pdf(pages=2), "application/pdf")})
    assert res.status_code == 202
    body = res.json()
    assert body["count"] == 2
    assert len(body["items"]) == 2
    assert [item["page_number"] for item in body["items"]] == [1, 2]
    assert all(item["total_pages"] == 2 for item in body["items"])
    assert body["items"][0]["message_id"] != body["items"][1]["message_id"]
    assert all(":split:" in item["message_id"] for item in body["items"])
    orders = []
    for index, item in enumerate(body["items"], 1):
        with fitz.open(item["pdf_uri"]) as saved:
            assert len(saved) == 1
            assert f"authored page {index}" in saved[0].get_text()
        orders.append(case.finish(item)["current_order_id"])
    assert len(set(orders)) == 2


def test_ingest_upload_multiple_files_preserves_request_order(upload_case):
    case = upload_case()
    res = case.upload([
        ("pdf_files", ("first.pdf", case.pdf("first"), "application/pdf")),
        ("pdf_files", ("second.pdf", case.pdf("second"), "application/pdf")),
    ])
    assert res.status_code == 202
    body = res.json()
    assert [item["filename"] for item in body["items"]] == ["first.pdf", "second.pdf"]
    assert len({item["message_id"] for item in body["items"]}) == 2
    for label, item in zip(("first", "second"), body["items"]):
        with fitz.open(item["pdf_uri"]) as saved:
            assert f"{label} page 1" in saved[0].get_text()
        case.finish(item)


def test_ingest_upload_rejects_non_pdf(upload_case):
    case = upload_case()
    before = _database_snapshot()
    res = case.upload({"pdf_file": ("upload.txt", b"hello world", "text/plain")})
    assert res.status_code == 400
    assert res.json()["detail"] == "pdf_file must be a PDF"
    assert _database_snapshot() == before
    assert case.transport.created == []


def test_ingest_upload_rejects_placeholder_pdf(upload_case):
    case = upload_case()
    before = _database_snapshot()
    res = case.upload({"pdf_file": ("first.pdf", _placeholder_pdf_bytes(), "application/pdf")})
    assert res.status_code == 400
    assert res.json()["detail"] == "pdf_file appears to be a placeholder/test PDF"
    assert _database_snapshot() == before
    assert case.transport.created == []


def test_ingest_upload_creates_order_and_blocks_duplicate(upload_case):
    case = upload_case()
    files = {"pdf_file": ("order.pdf", case.pdf(), "application/pdf")}
    first = case.upload(files)
    assert first.status_code == 202
    first_body = first.json()
    assert first_body["duplicate_blocked"] is False
    assert first_body["message_id"].startswith("upload:sha256:")
    assert first_body["order_id"] is None
    registered = case.finish(first_body)
    first_order_id = registered["current_order_id"]
    assert isinstance(first_order_id, str) and first_order_id
    before = _database_snapshot()
    started = datetime.utcnow()
    second = case.upload(files)
    finished = datetime.utcnow()
    assert second.status_code == 202
    second_body = second.json()
    assert second_body["message_id"] == first_body["message_id"]
    assert second_body["uploaded_pdf_id"] == first_body["uploaded_pdf_id"]
    assert second_body["duplicate_blocked"] is True
    assert second_body["order_id"] in {"", None}
    assert second_body["existing_order_id"] == first_order_id
    assert len(case.transport.created) == 1
    after = _database_snapshot()
    observed = next(row for row in after[UploadedPdf.__tablename__] if row["id"] == first_body["uploaded_pdf_id"])
    expected = deepcopy(before)
    original = next(row for row in expected[UploadedPdf.__tablename__] if row["id"] == observed["id"])
    assert started <= observed["updated_at"] <= finished
    original["updated_at"] = observed["updated_at"]
    assert after == expected
    assert process_uploaded_original(first_body["uploaded_pdf_id"])["duplicate"] is True


def test_ingest_upload_persists_split_pdf_total_page_count(upload_case):
    case = upload_case()
    res = case.upload({"pdf_file": ("order.pdf", case.pdf(pages=2), "application/pdf")})
    assert res.status_code == 202
    items = res.json()["items"]
    with session_scope() as session:
        observed_page_counts = [session.get(UploadedPdf, item["uploaded_pdf_id"]).page_count for item in items]
    assert observed_page_counts == [2, 2]
    assert res.json()["count"] == 2
    for item in items:
        case.finish(item)


def test_ingest_upload_exposes_existing_order_preview_for_facility_week_match(upload_case):
    case = upload_case()
    first = case.upload({"pdf_file": ("existing.pdf", case.pdf("existing"), "application/pdf")})
    assert first.status_code == 202
    seed = case.finish(first.json())
    res = case.upload({"pdf_file": ("preview_0215_.pdf", case.pdf("preview"), "application/pdf")})
    assert res.status_code == 202
    body = res.json()
    preview = body["existing_order_preview"]
    assert preview["order_id"] == seed["current_order_id"]
    assert preview["match_reason"] == "facility_week"
    assert preview["facility_code"] == "FAC001"
    assert preview["week_code"] == "2026-02@2026-02-15~2026-02-21"
    assert body["intake_decision"] == "reuse_existing_order"
    assert case.finish(body)["current_order_id"] == seed["current_order_id"]


def test_ingest_upload_contract_creates_order(upload_case):
    # WEK2025W52 is retired invalid syntax; explicitly authored ISO week 52 dates.
    case = upload_case("FAC00001", date(2025, 12, 22), "2025-12@2025-12-22~2025-12-28")
    res = case.upload({"pdf_file": ("file1.pdf", case.pdf(), "application/pdf")})
    assert res.status_code == 202
    case.finish(res.json())
    orders = order_service.list_orders()
    assert len(orders) == 1
    assert orders[0]["status"] == "要確認"
    assert orders[0]["facility"] == "FAC00001"
    with session_scope() as session:
        order = session.get(Order, orders[0]["id"])
        assert order is not None
        assert order.current_document_id
        document = session.get(OrderDocument, order.current_document_id)
        assert document is not None
        assert document.storage_uri
        assert document.status in {"processing", "processed", "success", "error"}


def test_ingest_upload_accepts_multiple_pdfs_and_returns_per_file_items(upload_case):
    case = upload_case()
    pdfs = [case.pdf("first"), case.pdf("second")]
    res = case.upload([
        ("pdf_files", (name, data, "application/pdf"))
        for name, data in zip(("first.pdf", "second.pdf"), pdfs)
    ])
    assert res.status_code == 202
    body = res.json()
    assert body["accepted"] is True
    assert body["count"] == 2
    assert len(body["items"]) == 2
    assert body["message_id"] == body["items"][0]["message_id"]
    assert body["items"][0]["filename"] == "first.pdf"
    assert body["items"][1]["filename"] == "second.pdf"
    assert body["items"][0]["duplicate_blocked"] is False
    assert body["items"][1]["duplicate_blocked"] is False
    assert len({item["uploaded_pdf_id"] for item in body["items"]}) == 2
    assert len(case.transport.created) == 2
    for item, content in zip(body["items"], pdfs):
        assert pathlib.Path(item["pdf_uri"]).read_bytes() == content
        payload = retry_ingest_jobs.get_ingest_payload(item["ingest_job_id"])
        assert payload["message_id"] == item["message_id"]
        assert payload["content_sha256"] == sha256(content).hexdigest()
        assert item["order_id"] is None
        case.finish(item)


def test_ingest_upload_multiple_pdfs_preserves_duplicate_detection_per_file(upload_case):
    case = upload_case()
    duplicate_pdf = case.pdf("duplicate")
    first = case.upload([
        ("pdf_files", ("dup.pdf", duplicate_pdf, "application/pdf")),
        ("pdf_files", ("new.pdf", case.pdf("new"), "application/pdf")),
    ])
    assert first.status_code == 202
    first_items = first.json()["items"]
    dup_order_id = case.finish(first_items[0])["current_order_id"]
    case.finish(first_items[1])
    second = case.upload([
        ("pdf_files", ("dup-again.pdf", duplicate_pdf, "application/pdf")),
        ("pdf_files", ("third.pdf", case.pdf("third"), "application/pdf")),
    ])
    assert second.status_code == 202
    body = second.json()
    assert body["count"] == 2
    assert body["items"][0]["duplicate_blocked"] is True
    assert body["items"][0]["existing_order_id"] == dup_order_id
    assert body["items"][0]["uploaded_pdf_id"] == first_items[0]["uploaded_pdf_id"]
    assert body["items"][1]["duplicate_blocked"] is False
    assert len(case.transport.created) == 3
    assert body["items"][1]["uploaded_pdf_id"] not in {x["uploaded_pdf_id"] for x in first_items}
    case.finish(body["items"][1])


def test_ingest_uploads_endpoints_expose_uploaded_pdf_rows(upload_case):
    case = upload_case()
    upload = case.upload({"pdf_file": ("visible.pdf", case.pdf(), "application/pdf")})
    assert upload.status_code == 202
    uploaded_pdf_id = upload.json()["uploaded_pdf_id"]
    case.finish(upload.json())
    before = _database_snapshot()
    listing = case.client.get("/ingest/uploads", headers=_operator_header())
    assert listing.status_code == 200
    items = listing.json()["items"]
    assert any(item["id"] == uploaded_pdf_id for item in items)
    detail = case.client.get(f"/ingest/uploads/{uploaded_pdf_id}", headers=_operator_header())
    assert detail.status_code == 200
    row = detail.json()
    assert row["id"] == uploaded_pdf_id
    assert row["message_id"] == upload.json()["message_id"]
    assert row["status"] in {"completed", "retry_wait", "manual_review", "processing", "pending"}
    assert "linked_order" in row
    assert "supersede_summary" in row
    assert row["linked_order"]["id"] == row["current_order_id"]
    assert _database_snapshot() == before


def test_ingest_uploads_list_does_not_backfill_legacy_manual_upload_jobs():
    client = TestClient(app)
    with session_scope() as session:
        session.add(IngestJob(
            id="upload:sha256:legacy-visible", status="pending", attempts=0,
            payload={
                "message_id": "upload:sha256:legacy-visible",
                "pdf_uri": "gs://bucket/manual-uploads/2026/04/06/legacy-visible.pdf",
                "received_at": "2026-04-06T00:04:43.617807",
                "facility_hint": "FAC00001", "week_hint": "2026-04@2026-04-05~2026-04-11",
                "facility_name": "施設A", "skip_ocr": False, "source_kind": "manual_upload",
                "original_filename": "legacy-visible.pdf", "content_sha256": "sha-legacy-visible",
            }))
    before = _database_snapshot()
    listing = client.get("/ingest/uploads", headers=_operator_header())
    assert listing.status_code == 200
    assert not any(item["message_id"] == "upload:sha256:legacy-visible" for item in listing.json()["items"])
    assert _database_snapshot() == before
    with session_scope() as session:
        job = session.get(IngestJob, "upload:sha256:legacy-visible")
        assert job.payload["original_filename"] == "legacy-visible.pdf"
        assert job.payload["pdf_uri"] == "gs://bucket/manual-uploads/2026/04/06/legacy-visible.pdf"
        assert job.status == "pending" and job.attempts == 0
        assert session.execute(select(IngestDelivery)).scalars().all() == []


def _observe_retry_dispatch(monkeypatch, processed_uploaded_pdf_ids, restarted_job_ids):
    """Wrap real restart and dispatch; only the Cloud Tasks transport is doubled."""
    restart = retry_ingest_jobs.restart_ingest_job
    dispatch = ingest_api.enqueue_uploaded_pdf_async

    def observed_restart(job_id, *, write_session=None):
        assert write_session is not None
        assert write_session.in_transaction()
        restarted_job_ids.append(job_id)
        return restart(job_id, write_session=write_session)

    monkeypatch.setattr(retry_ingest_jobs, "restart_ingest_job", observed_restart)

    def observed_dispatch(uploaded_pdf_id):
        processed_uploaded_pdf_ids.append(uploaded_pdf_id)
        return dispatch(uploaded_pdf_id)

    monkeypatch.setattr(ingest_api, "enqueue_uploaded_pdf_async", observed_dispatch)


def _assert_retry_original_reset(saved, before, previous_delivery, original_payload):
    after = retry_ingest_jobs.get_ingest_job_snapshot(saved.message_id)
    assert after["generation"] != before["generation"]
    assert after["attempts"] == before["attempts"]
    assert after["generation_attempts"] == 0
    assert after["claim_id"] is None
    assert after["input_digest"] == before["input_digest"]
    assert retry_ingest_jobs.get_ingest_payload(saved.message_id) == original_payload
    assert retry_ingest_jobs.get_ingest_payload(saved.message_id)["content_sha256"] == saved.content_sha256
    assert retry_delivery.delivery_for_job(
        saved.message_id, after["generation"], after["input_digest"],
    ) != previous_delivery


def test_retry_uploaded_pdf_requeues_row(monkeypatch, tmp_path, isolated_formal_schema):
    monkeypatch.setenv("AUTH_DISABLED", "false")
    order_service.clear_all()
    client = TestClient(app)
    configure_delivery(monkeypatch)
    uri, digest = authored_sources(monkeypatch, tmp_path,
        facility_id="FAC001", menu_date=date(2026, 2, 15))
    facility_selection = _authored_selection(
        {"facility_hint": "FAC001", "week_hint": "2026-02@2026-02-15~2026-02-21"},
        date(2026, 2, 15),
    )
    saved = ManualUploadSavedFile(
        message_id="upload:sha256:retry-row",
        pdf_uri=uri,
        content_sha256=digest,
        original_filename="retry.pdf",
        received_at=ingest_api._parse_optional_datetime(None),
    )
    processed_uploaded_pdf_ids: list[str] = []

    uploaded_pdf, _ = ingest_api.create_uploaded_pdf_from_upload(
        saved=saved,
        facility_selection=facility_selection,
        facility_hint="FAC001",
        week_hint="2026-02@2026-02-15~2026-02-21",
        facility_name=None,
        skip_ocr=False,
        source_kind="manual_upload",
    )
    ingest_api.requeue_uploaded_pdf(uploaded_pdf["id"])

    restarted_job_ids: list[str] = []
    before = retry_ingest_jobs.get_ingest_job_snapshot(saved.message_id)
    original_payload = retry_ingest_jobs.get_ingest_payload(saved.message_id)
    previous_delivery = retry_delivery.delivery_for_job(
        saved.message_id, before["generation"], before["input_digest"],
    )
    _observe_retry_dispatch(monkeypatch, processed_uploaded_pdf_ids, restarted_job_ids)

    res = client.post(
        f"/ingest/uploads/{uploaded_pdf['id']}/retry",
        headers=_operator_header(),
    )

    assert res.status_code == 202
    body = res.json()
    assert body["accepted"] is True
    assert body["item"]["id"] == uploaded_pdf["id"]
    assert body["item"]["status"] == "pending"
    assert body["item"]["current_stage"] == "uploaded"
    assert processed_uploaded_pdf_ids == [uploaded_pdf["id"]]
    assert restarted_job_ids == [saved.message_id]
    _assert_retry_original_reset(saved, before, previous_delivery, original_payload)


def test_retry_uploaded_pdf_rejects_only_healthy_completed_rows(monkeypatch, tmp_path, isolated_formal_schema):
    monkeypatch.setenv("AUTH_DISABLED", "false")
    order_service.clear_all()
    client = TestClient(app)
    configure_delivery(monkeypatch)
    uri, digest = authored_sources(monkeypatch, tmp_path,
        facility_id="FAC001", menu_date=date(2026, 2, 15))
    facility_selection = _authored_selection(
        {"facility_hint": "FAC001", "week_hint": "2026-02@2026-02-15~2026-02-21"},
        date(2026, 2, 15),
    )
    now = ingest_api._parse_optional_datetime(None)
    completed = ManualUploadSavedFile(
        message_id="upload:sha256:completed-row",
        pdf_uri=uri,
        content_sha256=digest,
        original_filename="completed.pdf",
        received_at=now,
    )
    processing = ManualUploadSavedFile(
        message_id="upload:sha256:processing-row",
        pdf_uri=uri,
        content_sha256=digest,
        original_filename="processing.pdf",
        received_at=now,
    )
    completed_row, _ = ingest_api.create_uploaded_pdf_from_upload(
        saved=completed,
        facility_selection=facility_selection,
        facility_hint="FAC001",
        week_hint="2026-02@2026-02-15~2026-02-21",
        facility_name=None,
        skip_ocr=False,
        source_kind="manual_upload",
    )
    processing_row, _ = ingest_api.create_uploaded_pdf_from_upload(
        saved=processing,
        facility_selection=facility_selection,
        facility_hint="FAC001",
        week_hint="2026-02@2026-02-15~2026-02-21",
        facility_name=None,
        skip_ocr=False,
        source_kind="manual_upload",
    )
    register_authored_original(completed_row["id"], effective_date=date(2026, 2, 15))
    authored_pipeline_provider(monkeypatch, table_rows=[["02/15", "昼", "Menu A", "2"]])
    assert process_uploaded_original(completed_row["id"])["result"] == "business_done"
    completed_state = retry_uploads.get_uploaded_pdf(completed_row["id"])
    assert completed_state["status"] == "completed"
    with session_scope() as session:
        healthy_order = session.get(Order, completed_state["current_order_id"])
        healthy_document = session.get(OrderDocument, completed_state["current_document_id"])
        assert healthy_order.current_document_id == healthy_document.id
        assert healthy_order.message_id == completed.message_id
        assert healthy_document.storage_uri == completed.pdf_uri
        assert healthy_order.week_code == "2026-02@2026-02-15~2026-02-21"
    processing_lease = claim_uploaded_pdf(processing_row["id"], worker_instance="test-worker")
    assert processing_lease is not None
    assert processing_lease["status"] == "processing"
    assert processing_lease["lease_token"]

    completed_res = client.post(
        f"/ingest/uploads/{completed_row['id']}/retry",
        headers=_operator_header(),
    )
    assert completed_res.status_code == 409
    assert completed_res.json()["detail"] == "uploaded_pdf_already_completed"

    processed_uploaded_pdf_ids: list[str] = []
    restarted_job_ids: list[str] = []
    before = retry_ingest_jobs.get_ingest_job_snapshot(processing.message_id)
    original_payload = retry_ingest_jobs.get_ingest_payload(processing.message_id)
    previous_delivery = retry_delivery.delivery_for_job(
        processing.message_id, before["generation"], before["input_digest"],
    )
    _observe_retry_dispatch(monkeypatch, processed_uploaded_pdf_ids, restarted_job_ids)

    processing_res = client.post(
        f"/ingest/uploads/{processing_row['id']}/retry",
        headers=_operator_header(),
    )
    assert processing_res.status_code == 202
    assert processing_res.json()["item"]["status"] == "pending"
    assert processed_uploaded_pdf_ids == [processing_row["id"]]
    assert restarted_job_ids == [processing.message_id]
    current_upload = retry_uploads.get_uploaded_pdf(processing_row["id"])
    assert current_upload["attempt_count"] == processing_lease["attempt_count"]
    assert current_upload["lease_token"] is None
    _assert_retry_original_reset(processing, before, previous_delivery, original_payload)


@pytest.mark.parametrize("limit", [20, 1])
def test_recover_ready_bulk_runs_ingest_uploaded_and_ocr_recovery(upload_case, monkeypatch, limit):
    case = upload_case()
    waiting = case.upload({"pdf_file": ("waiting.pdf", case.pdf("waiting", pages=4), "application/pdf")})
    assert waiting.status_code == 202
    waiting_items = waiting.json()["items"]
    provider = ingest_worker.run_ocr_pipeline
    provider_calls = []

    def pending_provider(**kwargs):
        provider_calls.append(kwargs["job_id"])
        output = provider(**kwargs)
        return {**output, "status": "pending"}

    monkeypatch.setattr(ingest_worker, "run_ocr_pipeline", pending_provider)
    for item in waiting_items[:3]:
        assert process_uploaded_original(item["uploaded_pdf_id"])["result"] == "durable_handoff"

    def failed_provider(**kwargs):
        raise ocr_jobs.OwnPipelineLineageError("authored hard failure")

    monkeypatch.setattr(ingest_worker, "run_ocr_pipeline", failed_provider)
    failed_item = waiting_items[3]
    assert process_uploaded_original(failed_item["uploaded_pdf_id"])["result"] == "business_failed"
    failed_before = retry_ingest_jobs.get_ingest_job_snapshot(failed_item["ingest_job_id"])
    assert failed_before["failure_kind"] == "business"
    monkeypatch.setattr(ingest_worker, "run_ocr_pipeline", pending_provider)

    # Provider output is not available yet; real recovery resubmits the captured PDF.
    def missing_output(uri):
        assert uri.startswith("gs://authored-component/output/")
        raise FileNotFoundError(uri)

    monkeypatch.setattr(ingest_worker, "load_bytes_from_uri", missing_output)
    ready = case.upload({"pdf_file": ("ready.pdf", case.pdf("ready", pages=2), "application/pdf")})
    assert ready.status_code == 202
    ready_items = ready.json()["items"]
    originals = [retry_ingest_jobs.get_ingest_job_snapshot(x["ingest_job_id"]) for x in ready_items]
    identity, generation = retry_delivery.delivery_for_job(
        originals[0]["id"], originals[0]["generation"], originals[0]["input_digest"])
    claim = retry_delivery.claim_delivery(identity, generation)
    assert claim["execute"] is True
    past = datetime.utcnow() - timedelta(hours=2)
    with session_scope() as session:
        session.get(IngestJob, originals[0]["id"]).started_at = past
        session.get(UploadedPdf, ready_items[0]["uploaded_pdf_id"]).lease_expires_at = past
        for row in session.execute(select(OcrJob).where(OcrJob.status == "awaiting_output")).scalars():
            row.metrics = {**row.metrics, "next_recovery_at": past.isoformat()}
    candidates = retry_ingest_jobs.list_ocr_recovery_candidates(20)
    assert len(candidates) == 3
    captured = {row["id"]: deepcopy(row["metrics"]["nonworkflow_execution"]) for row in candidates}
    assert all(value["ingest_lease"]["generation"] and value["ingest_lease"]["input_digest"]
        and value["selected_pdf_receipt"] for value in captured.values())
    before = _database_snapshot()
    pending_ids = retry_ingest_jobs.list_pending_jobs(limit)
    ready_ids = retry_uploads.list_ready_uploaded_pdf_ids(limit)
    ingest_ids = list(dict.fromkeys([originals[0]["id"], *pending_ids]))
    task_names = {}
    for item in ready_items:
        original = retry_ingest_jobs.get_ingest_job_snapshot(item["ingest_job_id"])
        identity, _ = retry_delivery.delivery_for_job(original["id"], original["generation"], original["input_digest"])
        with session_scope() as session:
            task_names[item["ingest_job_id"]] = session.get(IngestDelivery, identity).task_contract["name"]
    case.transport.lookups.clear()
    provider_calls.clear()
    started = datetime.utcnow()
    res = case.client.post(f"/ingest/recover-ready?limit={limit}", headers=_operator_header())
    finished = datetime.utcnow()
    assert res.status_code == 202
    body = res.json()
    assert body == {
        "accepted": True, "limit": limit, "ingest_reset": 1,
        "ingest_enqueued": len(ingest_ids), "uploaded_enqueued": len(ready_ids),
        "ocr_recovered": min(limit, 3),
    }
    if limit == 20:
        assert body == {"accepted": True, "limit": 20, "ingest_reset": 1,
            "ingest_enqueued": 2, "uploaded_enqueued": 2, "ocr_recovered": 3}
    expected_lookups = [task_names[job_id] for job_id in ingest_ids]
    expected_lookups += [task_names[next(x["ingest_job_id"] for x in ready_items if x["uploaded_pdf_id"] == uid)]
        for uid in ready_ids]
    assert case.transport.lookups == expected_lookups
    assert len(provider_calls) == min(limit, 3)
    assert retry_ingest_jobs.get_ingest_job_snapshot(failed_item["ingest_job_id"]) == failed_before
    after = _database_snapshot()
    expected = deepcopy(before)
    for original in expected[IngestDelivery.__tablename__]:
        visits = expected_lookups.count(original["task_contract"]["name"])
        if visits:
            current = next(row for row in after[IngestDelivery.__tablename__] if row["id"] == original["id"])
            assert started <= current["updated_at"] <= finished
            original.update(visit_count=original["visit_count"] + visits, updated_at=current["updated_at"])
    recovered_upload_ids = {captured[job_id]["ingest_lease"]["uploaded_lease"]["id"] for job_id in provider_calls}
    for original in expected[UploadedPdf.__tablename__]:
        if original["id"] in recovered_upload_ids:
            current = next(row for row in after[UploadedPdf.__tablename__] if row["id"] == original["id"])
            assert started <= current["updated_at"] <= finished
            original["updated_at"] = current["updated_at"]
    for original in expected[OcrJob.__tablename__]:
        if original["id"] in provider_calls:
            current = next(row for row in after[OcrJob.__tablename__] if row["id"] == original["id"])
            assert started <= current["updated_at"] <= finished
            assert started <= datetime.fromisoformat(current["progress_json"]["stage_updated_at"]) <= finished
            interval = ingest_worker.get_auto_recovery_interval_seconds()
            assert started + timedelta(seconds=interval) <= datetime.fromisoformat(current["progress_json"]["next_recovery_at"]) <= finished + timedelta(seconds=interval)
            original["updated_at"] = current["updated_at"]
            original["progress_json"].update(
                auto_recovery_count=original["progress_json"].get("auto_recovery_count", 0) + 1,
                next_recovery_at=current["progress_json"]["next_recovery_at"],
                stage_updated_at=current["progress_json"]["stage_updated_at"],
            )
    assert after == expected
    with session_scope() as session:
        for job_id, capture in captured.items():
            row = session.get(OcrJob, job_id)
            assert row.metrics["nonworkflow_execution"] == capture
            assert row.status == "awaiting_output"
            assert row.metrics.get("auto_recovery_count", 0) == (1 if job_id in provider_calls else 0)


def test_retry_uploaded_pdf_allows_completed_row_with_missing_week(monkeypatch, tmp_path, isolated_formal_schema):
    monkeypatch.setenv("AUTH_DISABLED", "false")
    order_service.clear_all()
    client = TestClient(app)
    configure_delivery(monkeypatch)
    uri, digest = authored_sources(monkeypatch, tmp_path,
        facility_id="FAC001", menu_date=date(2026, 2, 15))
    facility_selection = _authored_selection(
        {"facility_hint": "FAC001", "week_hint": "2026-02@2026-02-15~2026-02-21"},
        date(2026, 2, 15),
    )
    now = ingest_api._parse_optional_datetime(None)
    saved = ManualUploadSavedFile(
        message_id="upload:sha256:completed-missing-week",
        pdf_uri=uri,
        content_sha256=digest,
        original_filename="authored-component_20260215.pdf",
        received_at=now,
    )
    row, _ = ingest_api.create_uploaded_pdf_from_upload(
        saved=saved,
        facility_selection=facility_selection,
        facility_hint="FAC001",
        week_hint="2026-02@2026-02-15~2026-02-21",
        facility_name=None,
        skip_ocr=False,
        source_kind="manual_upload",
    )

    register_authored_original(row["id"], effective_date=date(2026, 2, 15))
    authored_pipeline_provider(monkeypatch, table_rows=[["02/15", "昼", "Menu A", "2"]])
    assert process_uploaded_original(row["id"])["result"] == "business_done"
    completed = retry_uploads.get_uploaded_pdf(row["id"])
    assert completed["status"] == "completed"
    with session_scope() as session:
        order = session.get(Order, completed["current_order_id"])
        assert order.current_document_id == completed["current_document_id"]
        assert order.week_code == "2026-02@2026-02-15~2026-02-21"
        order.week_code = None  # Explicit legacy-corrupted component fixture only.

    processed_uploaded_pdf_ids: list[str] = []
    restarted_job_ids: list[str] = []
    before = retry_ingest_jobs.get_ingest_job_snapshot(saved.message_id)
    original_payload = retry_ingest_jobs.get_ingest_payload(saved.message_id)
    previous_delivery = retry_delivery.delivery_for_job(
        saved.message_id, before["generation"], before["input_digest"],
    )
    _observe_retry_dispatch(monkeypatch, processed_uploaded_pdf_ids, restarted_job_ids)

    res = client.post(
        f"/ingest/uploads/{row['id']}/retry",
        headers=_operator_header(),
    )

    assert res.status_code == 202
    assert res.json()["item"]["status"] == "pending"
    assert processed_uploaded_pdf_ids == [row["id"]]
    assert restarted_job_ids == [saved.message_id]
    _assert_retry_original_reset(saved, before, previous_delivery, original_payload)
