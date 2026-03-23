import pathlib
import sys
from datetime import datetime

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.services import draft_sheet_service, ocr_evidence_service, order_service  # noqa: E402
from src.workers.ingest_mail_adapter import IngestEmailPayload  # noqa: E402


def _seed_order(message_id: str) -> dict:
    payload = IngestEmailPayload(
        message_id=message_id,
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2026, 3, 22, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2026-03",
    )
    return order_service.create_order_from_ingest(
        payload,
        lines=[
            {
                "date": "2026-03-22",
                "daypart": "朝",
                "menu_name": "Menu A",
                "diet_type": "regular",
                "area_id": "2F",
                "bag_type": "standard",
                "quantity_original": 3,
            }
        ],
    )


def _sample_payload(quantity: str = "3") -> dict:
    return {
        "template_id": "fax_layout_regular_soft_mixer_forbidden_v1",
        "pages": [
            {
                "page_index": 1,
                "ocr_overlay_uri": "gs://bucket/ocr-page-1.png",
                "layout_overlay_uri": "gs://bucket/layout-page-1.png",
                "figure_uris": [],
            }
        ],
        "table_raw": f"|日付|区分|メニュー|常食2F|\n|---|---|---|---|\n|03/22|朝|Menu A|{quantity}|",
        "tables": [
            {
                "table_id": "p1_t1",
                "page_index": 1,
                "rows": [["日付", "区分", "メニュー", "常食2F"], ["03/22", "朝", "Menu A", quantity]],
            }
        ],
        "template_resolution": {
            "resolved_template_id": "fax_layout_regular_soft_mixer_forbidden_v1",
            "blocked": False,
            "blocked_reasons": [],
        },
        "table_box": [0.1, 0.2, 0.9, 0.8],
        "grid_column_edges": [0.1, 0.5, 0.9],
        "grid_row_edges": [0.2, 0.4, 0.8],
    }


def test_build_initial_sheet_draft_prefers_latest_saved_draft() -> None:
    order_service.clear_all()
    order = _seed_order("msg-draft-saved")

    draft_sheet_service.persist_sheet_draft(
        order_id=order["id"],
        draft_sheet_json={
            "order_id": order["id"],
            "source": "manual_draft",
            "fields": ["col1"],
            "header": ["数量"],
            "rows": [["9"]],
            "row_ids": ["row-1"],
        },
        draft_state="draft_ready",
        edited_by="tester",
    )

    built = draft_sheet_service.build_initial_sheet_draft(order["id"])

    assert isinstance(built, dict)
    assert built["source"] == "manual_draft"
    assert built["rows"] == [["9"]]


def test_build_initial_sheet_draft_from_latest_evidence_run() -> None:
    order_service.clear_all()
    order = _seed_order("msg-draft-evidence")

    evidence = ocr_evidence_service.persist_evidence_run(
        order_id=order["id"],
        payload=_sample_payload("5"),
        schema_version="v1_legacy",
        producer_version="test",
        source="test-evidence",
    )

    built = draft_sheet_service.build_initial_sheet_draft(order["id"])

    assert isinstance(evidence, dict)
    assert isinstance(built, dict)
    assert built["source"] == "ocr_evidence"
    assert built["header"] == ["日付", "区分", "メニュー", "常食2F"]
    assert built["rows"] == [["03/22", "朝", "Menu A", "5"]]
    assert built["base_evidence_run_id"] == evidence["id"]


def test_build_initial_sheet_draft_falls_back_to_legacy_cache_revision() -> None:
    order_service.clear_all()
    order = _seed_order("msg-draft-legacy")

    order_service._save_order_ocr_cache(
        order["id"],
        {
            "table_raw": "|日付|区分|メニュー|常食2F|\n|---|---|---|---|\n|03/22|朝|Menu A|3|",
            "_edited_ocr": {
                "latest": {
                    "ui_mode": "sheet",
                    "fields": ["date_mmdd", "daypart", "menu", "qty"],
                    "header": ["日付", "区分", "メニュー", "常食2F"],
                    "rows": [["03/22", "朝", "Menu A", "8"]],
                    "row_ids": ["row-a"],
                }
            },
        },
    )

    built = draft_sheet_service.build_initial_sheet_draft(order["id"])

    assert isinstance(built, dict)
    assert built["source"] == "edited_sheet"
    assert built["rows"] == [["03/22", "朝", "Menu A", "8"]]


def test_order_service_build_initial_sheet_draft_prefers_semantic_sheet(monkeypatch) -> None:
    order_service.clear_all()
    order = _seed_order("msg-draft-semantic-sheet")

    monkeypatch.setattr(
        order_service,
        "get_ocr_sheet",
        lambda _order_id: (
            {
                "source": "weekly_menu+ocr_payload",
                "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f"],
                "header": ["日付", "区分", "メニュー", "常食2F"],
                "rows": [["03/22", "朝", "Menu A", "6"]],
                "row_ids": ["semantic-1"],
                "warnings": ["sheet_ocr_review_required"],
            },
            None,
        ),
    )

    built = order_service.build_initial_sheet_draft(order["id"])

    assert isinstance(built, dict)
    assert built["source"] == "weekly_menu+ocr_payload"
    assert built["fields"] == ["date_mmdd", "daypart", "menu", "qty.regular_2f"]
    assert built["header"] == ["日付", "区分", "メニュー", "常食2F"]
    assert built["rows"] == [["03/22", "朝", "Menu A", "6"]]
    assert built["row_ids"] == ["semantic-1"]
