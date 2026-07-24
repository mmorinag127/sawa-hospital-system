import sys
import pathlib
import base64
import asyncio
import time
from datetime import datetime

import fitz
from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.main import app  # noqa: E402
from src.services import order_service  # noqa: E402
from src.api import ingest as ingest_api  # noqa: E402
from src.services.manual_upload_service import ManualUploadSavedFile  # noqa: E402
from src.db import session_scope  # noqa: E402
from src.models.document import OrderDocument  # noqa: E402
from src.models.ingest_job import IngestJob  # noqa: E402
from src.models.order import Order  # noqa: E402
from src.services.uploaded_pdf_service import claim_uploaded_pdf, mark_uploaded_pdf_completed  # noqa: E402
from src.workers.ingest_mail_adapter import IngestEmailPayload  # noqa: E402
from sqlalchemy import select  # noqa: E402


def _basic_header(username: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    return {"Authorization": f"Basic {token}"}


def _two_page_pdf_bytes() -> bytes:
    doc = fitz.open()
    try:
        first = doc.new_page()
        first.insert_text((72, 72), "FAX page one")
        second = doc.new_page()
        second.insert_text((72, 72), "FAX page two")
        return doc.tobytes(garbage=3, deflate=True)
    finally:
        doc.close()


def _placeholder_pdf_bytes() -> bytes:
    doc = fitz.open()
    try:
        page = doc.new_page()
        page.insert_text((72, 72), "Page 1")
        return doc.tobytes(garbage=3, deflate=True)
    finally:
        doc.close()


def test_ingest_upload_requires_operator_auth(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "false")
    monkeypatch.setenv("OPERATOR_USER", "operator")
    monkeypatch.setenv("OPERATOR_PASSWORD", "secret")
    client = TestClient(app)

    unauthorized = client.post(
        "/ingest/upload",
        files={"pdf_file": ("upload.pdf", b"%PDF-1.4\n%EOF\n", "application/pdf")},
    )
    assert unauthorized.status_code == 401

    authorized = client.post(
        "/ingest/upload",
        files={"pdf_file": ("upload.pdf", b"%PDF-1.4\n%EOF\n", "application/pdf")},
        data={"facility_hint": "FAC001", "week_hint": "2026-02@2026-02-15~2026-02-21"},
        headers=_basic_header("operator", "secret"),
    )
    assert authorized.status_code == 202
    assert authorized.json()["accepted"] is True


def test_ingest_upload_single_response_keeps_legacy_fields_and_items(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "false")
    monkeypatch.setenv("OPERATOR_USER", "operator")
    monkeypatch.setenv("OPERATOR_PASSWORD", "secret")
    order_service.clear_all()
    client = TestClient(app)

    res = client.post(
        "/ingest/upload",
        files={"pdf_file": ("single.pdf", b"%PDF-1.4\n%EOF\n", "application/pdf")},
        data={"facility_hint": "FAC001", "week_hint": "2026-02@2026-02-15~2026-02-21"},
        headers=_basic_header("operator", "secret"),
    )

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


def test_ingest_upload_requires_facility_and_week_context(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "false")
    monkeypatch.setenv("OPERATOR_USER", "operator")
    monkeypatch.setenv("OPERATOR_PASSWORD", "secret")
    client = TestClient(app)

    res = client.post(
        "/ingest/upload",
        files={"pdf_file": ("single.pdf", b"%PDF-1.4\n%EOF\n", "application/pdf")},
        data={"facility_hint": "FAC001"},
        headers=_basic_header("operator", "secret"),
    )

    assert res.status_code == 400
    detail = res.json()["detail"]
    assert detail["error"] == "upload_context_required"
    assert detail["missing"] == ["week_hint"]


def test_ingest_week_options_returns_upload_week_choices(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "false")
    monkeypatch.setenv("OPERATOR_USER", "operator")
    monkeypatch.setenv("OPERATOR_PASSWORD", "secret")
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
        headers=_basic_header("operator", "secret"),
    )

    assert res.status_code == 200
    assert res.json()["options"] == expected_options
    assert calls == [("2026-02", "FAC001")]


def test_ingest_upload_splits_two_page_pdf_into_two_items(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "false")
    monkeypatch.setenv("OPERATOR_USER", "operator")
    monkeypatch.setenv("OPERATOR_PASSWORD", "secret")
    order_service.clear_all()
    client = TestClient(app)

    res = client.post(
        "/ingest/upload",
        files={"pdf_file": ("multi.pdf", _two_page_pdf_bytes(), "application/pdf")},
        data={"facility_hint": "FAC001", "week_hint": "2026-02@2026-02-15~2026-02-21"},
        headers=_basic_header("operator", "secret"),
    )

    assert res.status_code == 202
    body = res.json()
    assert body["count"] == 2
    assert len(body["items"]) == 2
    assert [item["page_number"] for item in body["items"]] == [1, 2]
    assert all(item["total_pages"] == 2 for item in body["items"])
    assert body["items"][0]["message_id"] != body["items"][1]["message_id"]
    assert all(":split:" in item["message_id"] for item in body["items"])


def test_ingest_upload_multiple_files_preserves_request_order_under_parallel_processing(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "false")
    monkeypatch.setenv("OPERATOR_USER", "operator")
    monkeypatch.setenv("OPERATOR_PASSWORD", "secret")
    client = TestClient(app)

    async def _fake_handle_uploaded_pdf(**kwargs):
        pdf_file = kwargs["pdf_file"]
        filename = str(pdf_file.filename or "")
        if filename == "first.pdf":
            await asyncio.sleep(0.05)
            item_id = "msg-first"
        else:
            await asyncio.sleep(0.01)
            item_id = "msg-second"
        return {
            "accepted": True,
            "filename": filename,
            "message_id": item_id,
            "items": [
                {
                    "accepted": True,
                    "filename": filename,
                    "message_id": item_id,
                    "page_number": 1,
                    "total_pages": 1,
                }
            ],
        }

    monkeypatch.setattr(ingest_api, "_handle_uploaded_pdf", _fake_handle_uploaded_pdf)

    res = client.post(
        "/ingest/upload",
        files=[
            ("pdf_files", ("first.pdf", b"%PDF-1.4\n%EOF\n", "application/pdf")),
            ("pdf_files", ("second.pdf", b"%PDF-1.4\n%EOF\n", "application/pdf")),
        ],
        data={"facility_hint": "FAC001", "week_hint": "2026-02@2026-02-15~2026-02-21"},
        headers=_basic_header("operator", "secret"),
    )

    assert res.status_code == 202
    body = res.json()
    assert [item["filename"] for item in body["items"]] == ["first.pdf", "second.pdf"]
    assert [item["message_id"] for item in body["items"]] == ["msg-first", "msg-second"]


def test_ingest_upload_rejects_non_pdf(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "false")
    monkeypatch.setenv("OPERATOR_USER", "operator")
    monkeypatch.setenv("OPERATOR_PASSWORD", "secret")
    client = TestClient(app)

    res = client.post(
        "/ingest/upload",
        files={"pdf_file": ("upload.txt", b"hello world", "text/plain")},
        data={"facility_hint": "FAC001", "week_hint": "2026-02@2026-02-15~2026-02-21"},
        headers=_basic_header("operator", "secret"),
    )

    assert res.status_code == 400
    assert res.json()["detail"] == "pdf_file must be a PDF"


def test_ingest_upload_rejects_placeholder_pdf(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "false")
    monkeypatch.setenv("OPERATOR_USER", "operator")
    monkeypatch.setenv("OPERATOR_PASSWORD", "secret")
    client = TestClient(app)

    res = client.post(
        "/ingest/upload",
        files={"pdf_file": ("first.pdf", _placeholder_pdf_bytes(), "application/pdf")},
        data={"facility_hint": "FAC001", "week_hint": "2026-02@2026-02-15~2026-02-21"},
        headers=_basic_header("operator", "secret"),
    )

    assert res.status_code == 400
    assert res.json()["detail"] == "pdf_file appears to be a placeholder/test PDF"


def test_ingest_upload_creates_order_and_blocks_duplicate(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "false")
    monkeypatch.setenv("OPERATOR_USER", "operator")
    monkeypatch.setenv("OPERATOR_PASSWORD", "secret")
    client = TestClient(app)
    saved = ManualUploadSavedFile(
        message_id="upload:sha256:test-order",
        pdf_uri="gs://bucket/order.pdf",
        content_sha256="sha-order",
        original_filename="order.pdf",
        received_at=ingest_api._parse_optional_datetime(None),
    )
    uploaded_pdf_results = iter(
        [
            (
                {
                    "id": "UPL-1",
                    "message_id": saved.message_id,
                    "status": "pending",
                    "current_stage": "uploaded",
                },
                False,
            ),
            (
                {
                    "id": "UPL-1",
                    "message_id": saved.message_id,
                    "status": "pending",
                    "current_stage": "uploaded",
                },
                True,
            ),
        ]
    )
    job_results = iter([("job-1", True), ("job-1", False)])
    enqueued_uploaded_pdf_ids: list[str] = []

    monkeypatch.setattr(ingest_api, "save_uploaded_pdf", lambda **kwargs: saved)
    monkeypatch.setattr(
        ingest_api,
        "create_uploaded_pdf_from_upload",
        lambda **kwargs: next(uploaded_pdf_results),
    )
    monkeypatch.setattr(ingest_api, "create_ingest_job", lambda payload, force=False: next(job_results))
    monkeypatch.setattr(
        ingest_api,
        "enqueue_uploaded_pdf_async",
        lambda uploaded_pdf_id: enqueued_uploaded_pdf_ids.append(uploaded_pdf_id),
    )
    monkeypatch.setattr(
        ingest_api,
        "_find_latest_order_id_by_message_id",
        lambda message_id: "ORD-existing" if message_id == saved.message_id else None,
    )
    files = {"pdf_file": ("order.pdf", b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF\n", "application/pdf")}

    first = client.post(
        "/ingest/upload",
        files=files,
        data={"facility_hint": "FAC001", "week_hint": "2026-02@2026-02-15~2026-02-21"},
        headers=_basic_header("operator", "secret"),
    )
    assert first.status_code == 202
    first_body = first.json()
    assert first_body["duplicate_blocked"] is False
    assert first_body["uploaded_pdf_id"] == "UPL-1"
    assert first_body["message_id"].startswith("upload:sha256:")
    first_order_id = first_body.get("order_id")
    assert isinstance(first_order_id, str) and first_order_id

    second = client.post(
        "/ingest/upload",
        files=files,
        data={"facility_hint": "FAC001", "week_hint": "2026-02@2026-02-15~2026-02-21"},
        headers=_basic_header("operator", "secret"),
    )
    assert second.status_code == 202
    second_body = second.json()
    assert second_body["message_id"] == first_body["message_id"]
    assert second_body["duplicate_blocked"] is True
    assert second_body["order_id"] in {"", None}
    assert second_body["existing_order_id"] == first_order_id == "ORD-existing"
    assert enqueued_uploaded_pdf_ids == ["UPL-1"]


def test_ingest_upload_persists_split_pdf_total_page_count(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "false")
    monkeypatch.setenv("OPERATOR_USER", "operator")
    monkeypatch.setenv("OPERATOR_PASSWORD", "secret")
    client = TestClient(app)
    saved_files = [
        ManualUploadSavedFile(
            message_id="upload:sha256:page-1:split:group:1of2",
            pdf_uri="gs://bucket/page-1.pdf",
            content_sha256="sha-page-1",
            original_filename="order__page-01-of-02.pdf",
            received_at=ingest_api._parse_optional_datetime(None),
            page_number=1,
            total_pages=2,
            split_group_id="group",
        ),
        ManualUploadSavedFile(
            message_id="upload:sha256:page-2:split:group:2of2",
            pdf_uri="gs://bucket/page-2.pdf",
            content_sha256="sha-page-2",
            original_filename="order__page-02-of-02.pdf",
            received_at=ingest_api._parse_optional_datetime(None),
            page_number=2,
            total_pages=2,
            split_group_id="group",
        ),
    ]
    observed_page_counts: list[int | None] = []

    def fake_create_uploaded_pdf_from_upload(**kwargs):
        observed_page_counts.append(kwargs.get("page_count"))
        return (
            {
                "id": f"UPL-{len(observed_page_counts)}",
                "message_id": kwargs["saved"].message_id,
                "status": "pending",
                "current_stage": "uploaded",
            },
            False,
        )

    monkeypatch.setattr(ingest_api, "save_uploaded_pdf", lambda **kwargs: saved_files)
    monkeypatch.setattr(ingest_api, "create_uploaded_pdf_from_upload", fake_create_uploaded_pdf_from_upload)
    monkeypatch.setattr(ingest_api, "create_ingest_job", lambda payload, force=False: (payload["message_id"], True))
    monkeypatch.setattr(ingest_api, "enqueue_uploaded_pdf_async", lambda uploaded_pdf_id: None)
    monkeypatch.setattr(ingest_api, "_find_latest_order_id_by_message_id", lambda message_id: None)

    res = client.post(
        "/ingest/upload",
        files={"pdf_file": ("order.pdf", b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF\n", "application/pdf")},
        data={"facility_hint": "FAC001", "week_hint": "2026-02@2026-02-15~2026-02-21"},
        headers=_basic_header("operator", "secret"),
    )

    assert res.status_code == 202
    assert observed_page_counts == [2, 2]
    assert res.json()["count"] == 2


def test_ingest_upload_exposes_existing_order_preview_for_facility_week_match(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "false")
    monkeypatch.setenv("OPERATOR_USER", "operator")
    monkeypatch.setenv("OPERATOR_PASSWORD", "secret")
    order_service.clear_all()
    client = TestClient(app)
    monkeypatch.setattr(ingest_api, "enqueue_uploaded_pdf_async", lambda uploaded_pdf_id: None)

    seed = order_service.create_order_from_ingest(
        IngestEmailPayload(
            message_id="msg-existing-preview",
            pdf_uri="file://existing-preview.pdf",
            received_at=datetime(2026, 2, 15, 10, 0, 0),
            facility_hint="FAC001",
            week_hint="2026-02@2026-02-15~2026-02-21",
        )
    )
    assert seed is not None

    res = client.post(
        "/ingest/upload",
        files={"pdf_file": ("preview_0215_.pdf", b"%PDF-1.4\n%EOF\n", "application/pdf")},
        data={"facility_hint": "FAC001", "week_hint": "2026-02@2026-02-15~2026-02-21"},
        headers=_basic_header("operator", "secret"),
    )

    assert res.status_code == 202
    body = res.json()
    preview = body["existing_order_preview"]
    assert preview["order_id"] == seed["id"]
    assert preview["match_reason"] == "facility_week"
    assert preview["facility_code"] == "FAC001"
    assert preview["week_code"] == "2026-02@2026-02-15~2026-02-21"
    assert body["intake_decision"] == "reuse_existing_order"


def test_ingest_upload_contract_creates_order(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "false")
    monkeypatch.setenv("OPERATOR_USER", "operator")
    monkeypatch.setenv("OPERATOR_PASSWORD", "secret")
    order_service.clear_all()
    client = TestClient(app)
    pdf_path = tmp_path / "file1.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%EOF\n")

    with pdf_path.open("rb") as handle:
        res = client.post(
            "/ingest/upload",
            files={"pdf_file": ("file1.pdf", handle.read(), "application/pdf")},
            data={"facility_hint": "FAC00001", "week_hint": "WEK2025W52"},
            headers=_basic_header("operator", "secret"),
        )

    assert res.status_code == 202
    orders = []
    for _ in range(20):
        orders = order_service.list_orders()
        if orders:
            break
        time.sleep(0.05)
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


def test_ingest_upload_accepts_multiple_pdfs_and_returns_per_file_items(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "false")
    monkeypatch.setenv("OPERATOR_USER", "operator")
    monkeypatch.setenv("OPERATOR_PASSWORD", "secret")
    client = TestClient(app)
    saved_calls: list[tuple[str, bytes]] = []
    enqueue_calls: list[dict] = []
    created_uploaded_pdfs: list[str] = []
    order_ids = {
        "msg-first": "ORD-first",
        "msg-second": "ORD-second",
    }

    def _fake_save_uploaded_pdf(*, pdf_bytes: bytes, original_filename: str, received_at):
        saved_calls.append((original_filename, pdf_bytes))
        suffix = "first" if original_filename == "first.pdf" else "second"
        return ManualUploadSavedFile(
            message_id=f"msg-{suffix}",
            pdf_uri=f"gs://bucket/{original_filename}",
            content_sha256=f"sha-{suffix}",
            original_filename=original_filename,
            received_at=received_at,
        )

    def _fake_create_ingest_job(payload: dict, force: bool = False):
        enqueue_calls.append({"payload": payload, "force": force})
        suffix = payload["message_id"].split("-")[-1]
        return (f"job-{suffix}", True)

    monkeypatch.setattr(ingest_api, "save_uploaded_pdf", _fake_save_uploaded_pdf)
    monkeypatch.setattr(
        ingest_api,
        "create_uploaded_pdf_from_upload",
        lambda *, saved, **kwargs: (
            {
                "id": f"UPL-{saved.message_id}",
                "message_id": saved.message_id,
                "status": "pending",
                "current_stage": "uploaded",
            },
            False,
        ),
    )
    monkeypatch.setattr(ingest_api, "create_ingest_job", _fake_create_ingest_job)
    monkeypatch.setattr(
        ingest_api,
        "enqueue_uploaded_pdf_async",
        lambda uploaded_pdf_id: created_uploaded_pdfs.append(uploaded_pdf_id),
    )
    monkeypatch.setattr(
        ingest_api,
        "_find_latest_order_id_by_message_id",
        lambda message_id: order_ids.get(message_id),
    )

    files = [
        ("pdf_files", ("first.pdf", b"%PDF-1.4\n%EOF\n", "application/pdf")),
        ("pdf_files", ("second.pdf", b"%PDF-1.4\n1 0 obj\n%%EOF\n", "application/pdf")),
    ]

    res = client.post(
        "/ingest/upload",
        files=files,
        data={"facility_hint": "FAC001", "week_hint": "2026-02@2026-02-15~2026-02-21"},
        headers=_basic_header("operator", "secret"),
    )

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
    assert body["items"][0]["order_id"] == "ORD-first"
    assert body["items"][1]["order_id"] == "ORD-second"
    assert body["items"][0]["uploaded_pdf_id"] == "UPL-msg-first"
    assert body["items"][1]["uploaded_pdf_id"] == "UPL-msg-second"
    assert [name for name, _ in saved_calls] == ["first.pdf", "second.pdf"]
    assert len(enqueue_calls) == 2
    assert enqueue_calls[0]["payload"]["message_id"] == "msg-first"
    assert enqueue_calls[1]["payload"]["message_id"] == "msg-second"
    assert created_uploaded_pdfs == ["UPL-msg-first", "UPL-msg-second"]


def test_ingest_upload_multiple_pdfs_preserves_duplicate_detection_per_file(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "false")
    monkeypatch.setenv("OPERATOR_USER", "operator")
    monkeypatch.setenv("OPERATOR_PASSWORD", "secret")
    client = TestClient(app)
    call_index = {"value": 0}

    def _fake_save_uploaded_pdf(*, pdf_bytes: bytes, original_filename: str, received_at):
        call_index["value"] += 1
        idx = call_index["value"]
        return ManualUploadSavedFile(
            message_id=f"msg-{idx}",
            pdf_uri=f"gs://bucket/{original_filename}",
            content_sha256=f"sha-{idx}",
            original_filename=original_filename,
            received_at=received_at,
        )

    job_results = iter(
        [
            ("job-1", True),
            ("job-2", True),
            ("job-3", False),
            ("job-4", True),
        ]
    )
    uploaded_pdf_results = iter(
        [
            ({"id": "UPL-1", "message_id": "msg-1", "status": "pending", "current_stage": "uploaded"}, False),
            ({"id": "UPL-2", "message_id": "msg-2", "status": "pending", "current_stage": "uploaded"}, False),
            ({"id": "UPL-3", "message_id": "msg-3", "status": "pending", "current_stage": "uploaded"}, True),
            ({"id": "UPL-4", "message_id": "msg-4", "status": "pending", "current_stage": "uploaded"}, False),
        ]
    )
    enqueued_uploaded_pdf_ids: list[str] = []

    monkeypatch.setattr(ingest_api, "save_uploaded_pdf", _fake_save_uploaded_pdf)
    monkeypatch.setattr(
        ingest_api,
        "create_uploaded_pdf_from_upload",
        lambda **kwargs: next(uploaded_pdf_results),
    )
    monkeypatch.setattr(ingest_api, "create_ingest_job", lambda payload, force=False: next(job_results))
    monkeypatch.setattr(
        ingest_api,
        "enqueue_uploaded_pdf_async",
        lambda uploaded_pdf_id: enqueued_uploaded_pdf_ids.append(uploaded_pdf_id),
    )
    monkeypatch.setattr(
        ingest_api,
        "_find_latest_order_id_by_message_id",
        lambda message_id: {
            "msg-1": "ORD-1",
            "msg-2": "ORD-2",
            "msg-3": "ORD-1",
            "msg-4": "ORD-4",
        }.get(message_id),
    )

    first_batch = [
        ("pdf_files", ("dup.pdf", b"%PDF-1.4\n%EOF\n", "application/pdf")),
        ("pdf_files", ("new.pdf", b"%PDF-1.4\n1 0 obj\n%%EOF\n", "application/pdf")),
    ]
    first = client.post(
        "/ingest/upload",
        files=first_batch,
        data={"facility_hint": "FAC001", "week_hint": "2026-02@2026-02-15~2026-02-21"},
        headers=_basic_header("operator", "secret"),
    )
    assert first.status_code == 202
    first_items = first.json()["items"]
    dup_order_id = first_items[0]["order_id"]

    second_batch = [
        ("pdf_files", ("dup-again.pdf", b"%PDF-1.4\n%EOF\n", "application/pdf")),
        ("pdf_files", ("third.pdf", b"%PDF-1.4\n2 0 obj\n%%EOF\n", "application/pdf")),
    ]
    second = client.post(
        "/ingest/upload",
        files=second_batch,
        data={"facility_hint": "FAC001", "week_hint": "2026-02@2026-02-15~2026-02-21"},
        headers=_basic_header("operator", "secret"),
    )

    assert second.status_code == 202
    body = second.json()
    assert body["count"] == 2
    assert body["items"][0]["duplicate_blocked"] is True
    assert body["items"][0]["existing_order_id"] == dup_order_id
    assert body["items"][1]["duplicate_blocked"] is False
    assert body["items"][1]["order_id"] == "ORD-4"
    assert enqueued_uploaded_pdf_ids == ["UPL-1", "UPL-2", "UPL-4"]


def test_ingest_uploads_endpoints_expose_uploaded_pdf_rows(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "false")
    monkeypatch.setenv("OPERATOR_USER", "operator")
    monkeypatch.setenv("OPERATOR_PASSWORD", "secret")
    order_service.clear_all()
    client = TestClient(app)

    upload = client.post(
        "/ingest/upload",
        files={"pdf_file": ("visible.pdf", b"%PDF-1.4\n%EOF\n", "application/pdf")},
        data={"facility_hint": "FAC001", "week_hint": "2026-02@2026-02-15~2026-02-21"},
        headers=_basic_header("operator", "secret"),
    )

    assert upload.status_code == 202
    uploaded_pdf_id = upload.json()["uploaded_pdf_id"]

    listing = client.get("/ingest/uploads", headers=_basic_header("operator", "secret"))
    assert listing.status_code == 200
    items = listing.json()["items"]
    assert any(item["id"] == uploaded_pdf_id for item in items)

    detail = client.get(f"/ingest/uploads/{uploaded_pdf_id}", headers=_basic_header("operator", "secret"))
    assert detail.status_code == 200
    row = detail.json()
    assert row["id"] == uploaded_pdf_id
    assert row["message_id"] == upload.json()["message_id"]
    assert row["status"] in {"completed", "retry_wait", "manual_review", "processing", "pending"}
    assert "linked_order" in row
    assert "supersede_summary" in row


def test_ingest_uploads_list_backfills_legacy_manual_upload_jobs(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "false")
    monkeypatch.setenv("OPERATOR_USER", "operator")
    monkeypatch.setenv("OPERATOR_PASSWORD", "secret")
    order_service.clear_all()
    client = TestClient(app)

    with session_scope() as session:
        session.add(
            IngestJob(
                id="upload:sha256:legacy-visible",
                status="pending",
                payload={
                    "message_id": "upload:sha256:legacy-visible",
                    "pdf_uri": "gs://bucket/manual-uploads/2026/04/06/legacy-visible.pdf",
                    "received_at": "2026-04-06T00:04:43.617807",
                    "facility_hint": "FAC00001",
                    "week_hint": "2026-04@2026-04-05~2026-04-11",
                    "facility_name": "施設A",
                    "skip_ocr": False,
                    "source_kind": "manual_upload",
                    "original_filename": "legacy-visible.pdf",
                    "content_sha256": "sha-legacy-visible",
                },
                attempts=0,
            )
        )

    listing = client.get("/ingest/uploads", headers=_basic_header("operator", "secret"))
    assert listing.status_code == 200
    items = listing.json()["items"]
    row = next(item for item in items if item["message_id"] == "upload:sha256:legacy-visible")
    assert row["original_filename"] == "legacy-visible.pdf"
    assert row["storage_uri"] == "gs://bucket/manual-uploads/2026/04/06/legacy-visible.pdf"
    assert row["status"] == "pending"
    assert row["current_stage"] == "uploaded"


def test_retry_uploaded_pdf_requeues_row(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "false")
    monkeypatch.setenv("OPERATOR_USER", "operator")
    monkeypatch.setenv("OPERATOR_PASSWORD", "secret")
    order_service.clear_all()
    client = TestClient(app)
    saved = ManualUploadSavedFile(
        message_id="upload:sha256:retry-row",
        pdf_uri="gs://bucket/retry.pdf",
        content_sha256="sha-retry",
        original_filename="retry.pdf",
        received_at=ingest_api._parse_optional_datetime(None),
    )
    processed_uploaded_pdf_ids: list[str] = []

    uploaded_pdf, _ = ingest_api.create_uploaded_pdf_from_upload(
        saved=saved,
        facility_hint="FAC001",
        week_hint="2026-02@2026-02-15~2026-02-21",
        facility_name=None,
        skip_ocr=False,
        source_kind="manual_upload",
    )
    ingest_api.requeue_uploaded_pdf(uploaded_pdf["id"])

    restarted_job_ids: list[str] = []
    monkeypatch.setattr(
        ingest_api,
        "process_uploaded_pdf_job",
        lambda uploaded_pdf_id: processed_uploaded_pdf_ids.append(uploaded_pdf_id),
    )
    monkeypatch.setattr(
        ingest_api,
        "restart_ingest_job",
        lambda job_id: restarted_job_ids.append(job_id) or True,
    )

    res = client.post(
        f"/ingest/uploads/{uploaded_pdf['id']}/retry",
        headers=_basic_header("operator", "secret"),
    )

    assert res.status_code == 202
    body = res.json()
    assert body["accepted"] is True
    assert body["item"]["id"] == uploaded_pdf["id"]
    assert body["item"]["status"] == "pending"
    assert body["item"]["current_stage"] == "uploaded"
    assert processed_uploaded_pdf_ids == [uploaded_pdf["id"]]
    assert restarted_job_ids == [saved.message_id]


def test_retry_uploaded_pdf_rejects_only_healthy_completed_rows(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "false")
    monkeypatch.setenv("OPERATOR_USER", "operator")
    monkeypatch.setenv("OPERATOR_PASSWORD", "secret")
    order_service.clear_all()
    client = TestClient(app)
    now = ingest_api._parse_optional_datetime(None)
    completed = ManualUploadSavedFile(
        message_id="upload:sha256:completed-row",
        pdf_uri="gs://bucket/completed.pdf",
        content_sha256="sha-completed",
        original_filename="completed.pdf",
        received_at=now,
    )
    processing = ManualUploadSavedFile(
        message_id="upload:sha256:processing-row",
        pdf_uri="gs://bucket/processing.pdf",
        content_sha256="sha-processing",
        original_filename="processing.pdf",
        received_at=now,
    )
    completed_row, _ = ingest_api.create_uploaded_pdf_from_upload(
        saved=completed,
        facility_hint="FAC001",
        week_hint="2026-02@2026-02-15~2026-02-21",
        facility_name=None,
        skip_ocr=False,
        source_kind="manual_upload",
    )
    processing_row, _ = ingest_api.create_uploaded_pdf_from_upload(
        saved=processing,
        facility_hint="FAC001",
        week_hint="2026-02@2026-02-15~2026-02-21",
        facility_name=None,
        skip_ocr=False,
        source_kind="manual_upload",
    )
    mark_uploaded_pdf_completed(completed_row["id"])
    claim_uploaded_pdf(processing_row["id"], worker_instance="test-worker")

    with session_scope() as session:
        healthy_document = OrderDocument(
            id="DOC-RETRY-COMPLETE",
            facility_code="FAC001",
            week_code="2026-02@2026-02-15~2026-02-21",
            storage_uri=completed.pdf_uri,
            source_email_id=completed.message_id,
            received_at=completed.received_at,
            ocr_attempts=1,
            status="processed",
        )
        healthy_order = Order(
            id="ORD-RETRY-COMPLETE",
            facility_code="FAC001",
            week_code="2026-02@2026-02-15~2026-02-21",
            status="要確認",
            current_document_id=healthy_document.id,
            superseded_document_ids=[],
            document_uri=completed.pdf_uri,
            message_id=completed.message_id,
            received_at=completed.received_at,
        )
        session.add(healthy_document)
        session.add(healthy_order)

    completed_res = client.post(
        f"/ingest/uploads/{completed_row['id']}/retry",
        headers=_basic_header("operator", "secret"),
    )
    assert completed_res.status_code == 409
    assert completed_res.json()["detail"] == "uploaded_pdf_already_completed"

    processed_uploaded_pdf_ids: list[str] = []
    restarted_job_ids: list[str] = []
    monkeypatch.setattr(
        ingest_api,
        "process_uploaded_pdf_job",
        lambda uploaded_pdf_id: processed_uploaded_pdf_ids.append(uploaded_pdf_id),
    )
    monkeypatch.setattr(
        ingest_api,
        "restart_ingest_job",
        lambda job_id: restarted_job_ids.append(job_id) or True,
    )
    processing_res = client.post(
        f"/ingest/uploads/{processing_row['id']}/retry",
        headers=_basic_header("operator", "secret"),
    )
    assert processing_res.status_code == 202
    assert processing_res.json()["item"]["status"] == "pending"
    assert processed_uploaded_pdf_ids == [processing_row["id"]]
    assert restarted_job_ids == [processing.message_id]


def test_recover_ready_bulk_runs_ingest_uploaded_and_ocr_recovery(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "false")
    monkeypatch.setenv("OPERATOR_USER", "operator")
    monkeypatch.setenv("OPERATOR_PASSWORD", "secret")
    client = TestClient(app)

    enqueued_ingest_ids: list[str] = []
    enqueued_uploaded_ids: list[str] = []

    monkeypatch.setattr(ingest_api, "reset_stale_processing", lambda limit=20: ["ingest-stale-1"])
    monkeypatch.setattr(ingest_api, "list_pending_jobs", lambda limit=20: ["ingest-stale-1", "ingest-pending-1"])
    monkeypatch.setattr(ingest_api, "enqueue_ingest_job_async", lambda job_id: enqueued_ingest_ids.append(job_id))
    monkeypatch.setattr(ingest_api, "list_ready_uploaded_pdf_ids", lambda limit=20: ["UPL-1", "UPL-2"])
    monkeypatch.setattr(ingest_api, "enqueue_uploaded_pdf_async", lambda uploaded_pdf_id: enqueued_uploaded_ids.append(uploaded_pdf_id))
    monkeypatch.setattr(ingest_api, "run_ocr_job_recovery_once", lambda limit=20: 3)

    res = client.post(
        "/ingest/recover-ready",
        headers=_basic_header("operator", "secret"),
    )

    assert res.status_code == 202
    body = res.json()
    assert body == {
        "accepted": True,
        "limit": 20,
        "ingest_reset": 1,
        "ingest_enqueued": 2,
        "uploaded_enqueued": 2,
        "ocr_recovered": 3,
    }
    assert enqueued_ingest_ids == ["ingest-stale-1", "ingest-pending-1"]
    assert enqueued_uploaded_ids == ["UPL-1", "UPL-2"]


def test_retry_uploaded_pdf_allows_completed_row_with_missing_week(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "false")
    monkeypatch.setenv("OPERATOR_USER", "operator")
    monkeypatch.setenv("OPERATOR_PASSWORD", "secret")
    order_service.clear_all()
    client = TestClient(app)
    now = ingest_api._parse_optional_datetime(None)
    saved = ManualUploadSavedFile(
        message_id="upload:sha256:completed-missing-week",
        pdf_uri="gs://bucket/completed-missing-week.pdf",
        content_sha256="sha-completed-missing-week",
        original_filename="completed-missing-week_0405_.pdf",
        received_at=now,
    )
    row, _ = ingest_api.create_uploaded_pdf_from_upload(
        saved=saved,
        facility_hint=None,
        week_hint=None,
        facility_name=None,
        skip_ocr=False,
        source_kind="manual_upload",
    )

    with session_scope() as session:
        document = OrderDocument(
            id="DOC-RETRY-MISSING-WEEK",
            facility_code=None,
            week_code=None,
            storage_uri=saved.pdf_uri,
            source_email_id=saved.message_id,
            received_at=saved.received_at,
            ocr_attempts=1,
            status="processed",
        )
        order = Order(
            id="ORD-RETRY-MISSING-WEEK",
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

    mark_uploaded_pdf_completed(row["id"])
    processed_uploaded_pdf_ids: list[str] = []
    restarted_job_ids: list[str] = []
    monkeypatch.setattr(
        ingest_api,
        "process_uploaded_pdf_job",
        lambda uploaded_pdf_id: processed_uploaded_pdf_ids.append(uploaded_pdf_id),
    )
    monkeypatch.setattr(
        ingest_api,
        "restart_ingest_job",
        lambda job_id: restarted_job_ids.append(job_id) or True,
    )

    res = client.post(
        f"/ingest/uploads/{row['id']}/retry",
        headers=_basic_header("operator", "secret"),
    )

    assert res.status_code == 202
    assert res.json()["item"]["status"] == "pending"
    assert processed_uploaded_pdf_ids == [row["id"]]
    assert restarted_job_ids == [saved.message_id]
