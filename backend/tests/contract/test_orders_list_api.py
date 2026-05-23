import pathlib
import sys
from datetime import datetime

from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

import src.api.orders as orders_api  # noqa: E402
from src.main import app  # noqa: E402


client = TestClient(app)


def test_list_orders_default_is_lightweight_without_runtime(monkeypatch) -> None:
    monkeypatch.setattr(
        orders_api.order_service,
        "list_orders",
        lambda status=None, include_archived=False, limit=None: [
            {
                "id": "ORD-LIST-DEFAULT-001",
                "status": "要確認",
                "document": "file://dummy.pdf",
                "message_id": "msg-list-default-001",
                "received_at": datetime(2026, 3, 24, 9, 0, 0).isoformat(),
            }
        ],
    )
    monkeypatch.setattr(
        orders_api.order_service,
        "_load_order_ocr_cache_map",
        lambda _order_ids: (_ for _ in ()).throw(AssertionError("default list path must not hydrate runtime cache")),
    )
    monkeypatch.setattr(
        orders_api,
        "_attach_order_review_summary",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("default list path must not attach review summary")),
    )
    monkeypatch.setattr(
        orders_api,
        "_attach_order_workflow_context",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("default list path must not refresh workflow context")
        ),
    )

    res = client.get("/orders")

    assert res.status_code == 200
    assert res.json()["orders"][0]["id"] == "ORD-LIST-DEFAULT-001"


def test_get_draft_sheet_reads_saved_artifacts_without_rebuilding_hakodate_projection(monkeypatch) -> None:
    monkeypatch.setattr(
        orders_api.order_service,
        "get_order_by_id",
        lambda order_id: {"id": order_id, "status": "要確認"},
    )
    monkeypatch.setattr(
        orders_api.order_service,
        "get_latest_sheet_draft",
        lambda order_id, **_kwargs: {
            "id": "ODR-SAVED-001",
            "order_id": order_id,
            "base_evidence_run_id": "EVD-SAVED-001",
            "draft_state": "draft_ready",
            "blockers_json": [],
            "warnings_json": ["review_required"],
            "draft_sheet_json": {
                "fields": ["date", "menu", "qty.normal"],
                "header": ["日付", "献立", "常食"],
                "rows": [["04/26", "大豆のトマト煮", "41"]],
                "row_ids": ["row-1"],
                "source": "hakodate_ocr_evidence_sheet",
                "blockers": [],
                "warnings": [],
            },
        },
    )
    monkeypatch.setattr(
        orders_api.order_service,
        "get_cached_hakodate_assignment_preview",
        lambda _order_id: {
            "status": "ready",
            "target_cells": [{"target_cell_id": "cell-1", "sheet_cell": "D3", "bbox": [1, 2, 3, 4]}],
            "assignments": [{"target_cell_id": "cell-1", "assigned_value": "41"}],
            "sheet_output": {"cells": {"D3": {"value_normalized": "41"}}},
        },
    )
    monkeypatch.setattr(
        orders_api.order_service,
        "build_order_hakodate_projected_sheet",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("draft-sheet read path must not rebuild Hakodate projection")
        ),
    )

    res = client.get("/orders/ORD-DRAFT-READ/draft-sheet?compact=1&quantity_assignment_strategy=hakodate")

    assert res.status_code == 200
    payload = res.json()
    assert payload["draft_id"] == "ODR-SAVED-001"
    assert payload["rows"] == [["04/26", "大豆のトマト煮", "41"]]
    assert payload["hakodate_assignment"]["assignments"] == [
        {"target_cell_id": "cell-1", "assigned_value": "41"}
    ]


def test_get_draft_sheet_blocks_when_saved_artifact_is_missing(monkeypatch) -> None:
    monkeypatch.setattr(
        orders_api.order_service,
        "get_order_by_id",
        lambda order_id: {"id": order_id, "status": "要確認"},
    )
    monkeypatch.setattr(
        orders_api.order_service,
        "get_latest_sheet_draft",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        orders_api.order_service,
        "build_order_hakodate_projected_sheet",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("missing saved artifact must not trigger read-time projection")
        ),
    )

    res = client.get("/orders/ORD-DRAFT-MISSING/draft-sheet?compact=1&quantity_assignment_strategy=hakodate")

    assert res.status_code == 200
    payload = res.json()
    assert payload["rows"] == []
    assert payload["review_state"] == "blocked"
    assert "hakodate_sheet_artifact_missing" in payload["blockers"]


def test_hakodate_overlay_preview_endpoint_uses_canonical_service(monkeypatch) -> None:
    monkeypatch.setattr(
        orders_api.order_service,
        "get_order_by_id",
        lambda order_id: {"id": order_id, "status": "要確認"},
    )
    monkeypatch.setattr(
        orders_api.order_service,
        "get_hakodate_overlay_preview",
        lambda order_id: {
            "status": "ready",
            "blockers": [],
            "message": "",
            "overlay_uri": f"gs://bucket/{order_id}/hakodate-overlay.png",
            "overlay_url": "https://signed.example/hakodate-overlay.png",
            "assignment": {
                "target_cells": [{"target_cell_id": "cell-1", "bbox": [1, 2, 3, 4]}],
                "assignments": [{"target_cell_id": "cell-1", "assigned_value": "41"}],
            },
        },
    )
    monkeypatch.setattr(
        orders_api.order_service,
        "get_cached_hakodate_overlay_preview",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("overlay preview endpoint must use the canonical preview service")
        ),
    )

    res = client.get("/orders/ORD-OVERLAY-READ/hakodate-overlay-preview")

    assert res.status_code == 200
    payload = res.json()
    assert payload["status"] == "ready"
    assert payload["overlay_url"] == "https://signed.example/hakodate-overlay.png"
    assert payload["assignment"]["assignments"] == [
        {"target_cell_id": "cell-1", "assigned_value": "41"}
    ]


def test_list_orders_without_ocr_uses_lightweight_summary(monkeypatch) -> None:
    monkeypatch.setattr(
        orders_api.order_service,
        "list_orders",
        lambda status=None, include_archived=False, limit=None: [
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

    res = client.get("/orders?include_ocr=false&include_candidate_summary=true")

    assert res.status_code == 200
    rows = res.json()["orders"]
    assert len(rows) == 1
    row = rows[0]
    assert row["workflow_state"]["state"] == "draft_ready"
    assert row["candidate_resolution"]["resolutions"]["week"]["resolved_value"] == "2026-03@2026-03-22~2026-03-28"
    assert row["candidate_resolution"]["resolutions"]["facility"]["resolved_label"] == "施設A"
    assert row["ocr_status"] == "success"
    assert row["ocr_pages_count"] == 1


def test_list_orders_without_ocr_skips_candidate_resolution_by_default(monkeypatch) -> None:
    monkeypatch.setattr(
        orders_api.order_service,
        "list_orders",
        lambda status=None, include_archived=False, limit=None: [
            {
                "id": "ORD-LIST-NO-CANDIDATE-001",
                "facility": None,
                "week": "2026-03",
                "week_value": "2026-03",
                "status": "要確認",
                "document": "file://dummy.pdf",
                "message_id": "msg-list-no-candidate-001",
                "received_at": datetime(2026, 3, 24, 9, 0, 0).isoformat(),
            }
        ],
    )
    monkeypatch.setattr(
        orders_api.order_service,
        "_load_order_ocr_cache_map",
        lambda order_ids: (_ for _ in ()).throw(
            AssertionError("OCR cache payload must not be loaded for default lightweight list hydration")
        ),
    )
    monkeypatch.setattr(orders_api.workflow_state_service, "list_workflow_states", lambda order_ids: {})
    monkeypatch.setattr(
        orders_api.candidate_resolution_service,
        "resolve_order_list_candidates",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("candidate resolution must be opt-in on order list hydration")
        ),
    )

    res = client.get("/orders?include_ocr=false")

    assert res.status_code == 200
    row = res.json()["orders"][0]
    assert row["id"] == "ORD-LIST-NO-CANDIDATE-001"
    assert "candidate_resolution" not in row


def test_list_orders_without_ocr_uses_uploaded_pdf_week_hint_when_order_week_missing(monkeypatch) -> None:
    monkeypatch.setattr(
        orders_api.order_service,
        "list_orders",
        lambda status=None, include_archived=False, limit=None: [
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

    res = client.get("/orders?include_ocr=false&include_candidate_summary=true")

    assert res.status_code == 200
    rows = res.json()["orders"]
    assert len(rows) == 1
    week_resolution = rows[0]["candidate_resolution"]["resolutions"]["week"]
    assert week_resolution["resolved_value"] == "2026-04@2026-04-05~2026-04-11"
    assert week_resolution["requires_user_choice"] is False
