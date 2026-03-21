import sys
import pathlib
import base64
from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.main import app  # noqa: E402
from src.services import order_service  # noqa: E402
from src.api import ingest as ingest_api  # noqa: E402
from src.services.manual_upload_service import ManualUploadSavedFile  # noqa: E402
from src.db import session_scope  # noqa: E402
from src.models.document import OrderDocument  # noqa: E402
from sqlalchemy import select  # noqa: E402


def _basic_header(username: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    return {"Authorization": f"Basic {token}"}


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
        data={"facility_hint": "FAC001"},
        headers=_basic_header("operator", "secret"),
    )

    assert res.status_code == 202
    body = res.json()
    assert body["accepted"] is True
    assert body["count"] == 1
    assert isinstance(body["items"], list) and len(body["items"]) == 1
    assert body["message_id"] == body["items"][0]["message_id"]
    assert body["ingest_job_id"] == body["items"][0]["ingest_job_id"]
    assert body["pdf_uri"] == body["items"][0]["pdf_uri"]
    assert body["duplicate_blocked"] == body["items"][0]["duplicate_blocked"]
    assert body["order_id"] == body["items"][0]["order_id"]
    assert body["existing_order_id"] == body["items"][0]["existing_order_id"]


def test_ingest_upload_rejects_non_pdf(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "false")
    monkeypatch.setenv("OPERATOR_USER", "operator")
    monkeypatch.setenv("OPERATOR_PASSWORD", "secret")
    client = TestClient(app)

    res = client.post(
        "/ingest/upload",
        files={"pdf_file": ("upload.txt", b"hello world", "text/plain")},
        headers=_basic_header("operator", "secret"),
    )

    assert res.status_code == 400
    assert res.json()["detail"] == "pdf_file must be a PDF"


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
    enqueue_results = iter([("job-1", True), ("job-1", False)])

    monkeypatch.setattr(ingest_api, "save_uploaded_pdf", lambda **kwargs: saved)
    monkeypatch.setattr(ingest_api, "enqueue_ingest_async", lambda payload, force=False: next(enqueue_results))
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
            data={"facility_hint": "FAC001", "week_hint": "WEK2025W52"},
            headers=_basic_header("operator", "secret"),
        )

    assert res.status_code == 202
    orders = order_service.list_orders()
    assert len(orders) == 1
    assert orders[0]["status"] == "要確認"
    assert orders[0]["facility"] == "FAC001"
    with session_scope() as session:
        document = session.execute(select(OrderDocument)).scalars().first()
        assert document is not None
        assert document.storage_uri
        assert document.ocr_attempts >= 1


def test_ingest_upload_accepts_multiple_pdfs_and_returns_per_file_items(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "false")
    monkeypatch.setenv("OPERATOR_USER", "operator")
    monkeypatch.setenv("OPERATOR_PASSWORD", "secret")
    client = TestClient(app)
    saved_calls: list[tuple[str, bytes]] = []
    enqueue_calls: list[dict] = []
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

    def _fake_enqueue(payload: dict, force: bool = False):
        enqueue_calls.append({"payload": payload, "force": force})
        suffix = payload["message_id"].split("-")[-1]
        return (f"job-{suffix}", True)

    monkeypatch.setattr(ingest_api, "save_uploaded_pdf", _fake_save_uploaded_pdf)
    monkeypatch.setattr(ingest_api, "enqueue_ingest_async", _fake_enqueue)
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
    assert [name for name, _ in saved_calls] == ["first.pdf", "second.pdf"]
    assert len(enqueue_calls) == 2
    assert enqueue_calls[0]["payload"]["message_id"] == "msg-first"
    assert enqueue_calls[1]["payload"]["message_id"] == "msg-second"


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

    enqueue_results = iter(
        [
            ("job-1", True),
            ("job-2", True),
            ("job-3", False),
            ("job-4", True),
        ]
    )

    monkeypatch.setattr(ingest_api, "save_uploaded_pdf", _fake_save_uploaded_pdf)
    monkeypatch.setattr(ingest_api, "enqueue_ingest_async", lambda payload, force=False: next(enqueue_results))
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
        headers=_basic_header("operator", "secret"),
    )

    assert second.status_code == 202
    body = second.json()
    assert body["count"] == 2
    assert body["items"][0]["duplicate_blocked"] is True
    assert body["items"][0]["existing_order_id"] == dup_order_id
    assert body["items"][1]["duplicate_blocked"] is False
    assert body["items"][1]["order_id"] == "ORD-4"
