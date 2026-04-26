import pathlib
import sys
from datetime import datetime

from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

import src.api.orders as orders_api  # noqa: E402
from src.main import app  # noqa: E402


client = TestClient(app)


def test_list_orders_without_ocr_uses_lightweight_summary(monkeypatch) -> None:
    monkeypatch.setattr(
        orders_api.order_service,
        "list_orders",
        lambda status=None, include_archived=False: [
            {
                "id": "ORD-LIST-001",
                "facility": None,
                "week": "2026-03",
                "week_value": "2026-03",
                "week_label": "2026-03",
                "status": "要確認",
                "document": "file://dummy.pdf",
                "message_id": "msg-list-001",
                "received_at": datetime(2026, 3, 24, 9, 0, 0).isoformat(),
                "ocr_job_id": "OCR-ORD-LIST-001",
            }
        ],
    )
    monkeypatch.setattr(
        orders_api.order_service,
        "_load_order_ocr_cache_map",
        lambda order_ids: {
            "ORD-LIST-001": {
                "table_raw": "03/24 注文",
                "pages": [{"page": 1}],
            }
        },
    )
    monkeypatch.setattr(
        orders_api.workflow_state_service,
        "list_workflow_states",
        lambda order_ids: {
            "ORD-LIST-001": {
                "order_id": "ORD-LIST-001",
                "state": "draft_ready",
                "headline": "下書きを確認してください",
                "primary_action": "確認する",
                "blockers_json": [],
                "warnings_json": ["draft_newer_than_lines"],
            }
        },
    )

    def _unexpected_review(*_args, **_kwargs):
        raise AssertionError("include_ocr=false list path must not attach review summary")

    monkeypatch.setattr(orders_api, "_attach_order_review_summary", _unexpected_review)
    monkeypatch.setattr(
        orders_api,
        "_attach_order_workflow_context",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("include_ocr=false list path must not refresh per-order workflow context")
        ),
    )
    monkeypatch.setattr(
        orders_api.candidate_resolution_service,
        "resolve_order_list_candidates",
        lambda **_kwargs: {
            "resolutions": {
                "facility": {
                    "resolved_value": "FAC00001",
                    "resolved_label": "施設A",
                    "confidence": "high",
                    "blocked": False,
                    "blocked_reasons": [],
                    "requires_user_choice": False,
                    "candidates": [{"value": "FAC00001", "label": "施設A", "score": 0.97}],
                },
                "week": {
                    "resolved_value": "2026-03@2026-03-22~2026-03-28",
                    "resolved_label": "2026-03 (03/22-03/28)",
                    "confidence": "high",
                    "blocked": False,
                    "blocked_reasons": [],
                    "requires_user_choice": False,
                    "candidates": [],
                },
            },
            "requires_user_choice": False,
            "critical_choices": [],
            "confidence_band": "high",
        },
    )

    res = client.get("/orders?include_ocr=false")

    assert res.status_code == 200
    rows = res.json()["orders"]
    assert len(rows) == 1
    row = rows[0]
    assert row["workflow_state"]["state"] == "draft_ready"
    assert row["candidate_resolution"]["resolutions"]["week"]["resolved_value"] == "2026-03@2026-03-22~2026-03-28"
    assert row["candidate_resolution"]["resolutions"]["facility"]["resolved_label"] == "施設A"
    assert row["ocr_status"] == "success"
    assert row["ocr_pages_count"] == 1


def test_list_orders_without_ocr_uses_uploaded_pdf_week_hint_when_order_week_missing(monkeypatch) -> None:
    monkeypatch.setattr(
        orders_api.order_service,
        "list_orders",
        lambda status=None, include_archived=False: [
            {
                "id": "ORD-LIST-002",
                "facility": None,
                "week": None,
                "week_value": None,
                "week_label": None,
                "status": "要確認",
                "document": "file://dummy.pdf",
                "message_id": "upload:sha256:list-002",
                "received_at": datetime(2026, 4, 6, 9, 0, 0),
                "ocr_job_id": "OCR-ORD-LIST-002",
            }
        ],
    )
    monkeypatch.setattr(orders_api.order_service, "_load_order_ocr_cache_map", lambda order_ids: {})
    monkeypatch.setattr(orders_api.workflow_state_service, "list_workflow_states", lambda order_ids: {})
    monkeypatch.setattr(
        orders_api.uploaded_pdf_service,
        "get_uploaded_pdf_by_message_id",
        lambda message_id: {
            "message_id": message_id,
            "storage_uri": "gs://bucket/16.fax000355571_0405_.pdf",
            "received_at": "2026-04-06T09:00:00",
            "facility_hint": None,
            "week_hint": None,
            "facility_name": None,
            "skip_ocr": False,
            "source_kind": "manual_upload",
            "original_filename": "16.fax000355571_0405_.pdf",
            "content_sha256": "sha-list-002",
        },
    )

    res = client.get("/orders?include_ocr=false")

    assert res.status_code == 200
    rows = res.json()["orders"]
    assert len(rows) == 1
    week_resolution = rows[0]["candidate_resolution"]["resolutions"]["week"]
    assert week_resolution["resolved_value"] == "2026-04@2026-04-05~2026-04-11"
    assert week_resolution["requires_user_choice"] is False
