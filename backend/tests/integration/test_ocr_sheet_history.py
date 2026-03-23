import sys
import pathlib
import json
from datetime import date, datetime

from sqlalchemy import delete

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.db import session_scope  # noqa: E402
from src.models.menu import MonthlyMenu, MonthlyMenuEntry  # noqa: E402
from src.models.facility import Facility, FacilityConfig  # noqa: E402
from src.models.order import Order  # noqa: E402
from src.models.order_ocr_cache import OrderOcrCache  # noqa: E402
from src.services import order_service  # noqa: E402
from src.services import config_service  # noqa: E402
from src.services import facility_service  # noqa: E402
from src.services import fax_extractor  # noqa: E402
from src.services.ocr_job_service import create_job, update_job  # noqa: E402
from src.services.fax_extractor import FaxExtractedData, rows_from_markdown  # noqa: E402
from src.workers.ingest_mail_adapter import IngestEmailPayload  # noqa: E402


def _seed_monthly_menu_2026_01() -> None:
    with session_scope() as session:
        menu = session.get(MonthlyMenu, "2026-01")
        if not menu:
            session.add(
                MonthlyMenu(
                    id="2026-01",
                    month_start=date(2026, 1, 1),
                    filename="seed-2026-01.xlsx",
                )
            )
        exists = (
            session.query(MonthlyMenuEntry)
            .filter(
                MonthlyMenuEntry.monthly_menu_id == "2026-01",
                MonthlyMenuEntry.menu_date == date(2026, 1, 8),
                MonthlyMenuEntry.daypart == "昼",
                MonthlyMenuEntry.name == "Menu A",
            )
            .first()
        )
        if not exists:
            session.add(
                MonthlyMenuEntry(
                    id="seed-entry-2026-01-08-lunch-menu-a",
                    monthly_menu_id="2026-01",
                    menu_date=date(2026, 1, 8),
                    daypart="昼",
                    name="Menu A",
                    slot_index=0,
                )
            )


def _seed_monthly_menu_custom_entries(
    *,
    month_id: str,
    month_start: date,
    entries: list[tuple[date, str, str, int]],
) -> None:
    with session_scope() as session:
        menu = session.get(MonthlyMenu, month_id)
        if not menu:
            session.add(
                MonthlyMenu(
                    id=month_id,
                    month_start=month_start,
                    filename=f"seed-{month_id}.xlsx",
                )
            )
        session.execute(delete(MonthlyMenuEntry).where(MonthlyMenuEntry.monthly_menu_id == month_id))
        for idx, (menu_date, daypart, name, slot_index) in enumerate(entries):
            session.add(
                MonthlyMenuEntry(
                    id=f"seed-entry-{month_id}-custom-{idx}",
                    monthly_menu_id=month_id,
                    menu_date=menu_date,
                    daypart=daypart,
                    name=name,
                    slot_index=slot_index,
                )
            )


def _seed_order(*, message_id: str):
    _seed_monthly_menu_2026_01()
    payload = IngestEmailPayload(
        message_id=message_id,
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2026, 1, 8, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint=None,
    )
    lines = [
        {
            "date": "2026-01-08",
            "daypart": "昼",
            "menu_name": "Menu A",
            "diet_type": "regular",
            "area_id": "2F",
            "bag_type": "standard",
            "quantity_original": 2,
        },
        {
            "date": "2026-01-08",
            "daypart": "昼",
            "menu_name": "Menu A",
            "diet_type": "regular",
            "area_id": "3F",
            "bag_type": "standard",
            "quantity_original": 1,
        },
        {
            "date": "2026-01-08",
            "daypart": "昼",
            "menu_name": "Menu A",
            "diet_type": "soft",
            "area_id": "2F",
            "bag_type": "standard",
            "quantity_original": 3,
        },
    ]
    return order_service.create_order_from_ingest(payload, lines=lines)


def _seed_custom_order(*, message_id: str, received_at: datetime, lines: list[dict[str, object]]):
    payload = IngestEmailPayload(
        message_id=message_id,
        pdf_uri="file://dummy.pdf",
        received_at=received_at,
        facility_hint="FAC00001",
        week_hint=None,
    )
    return order_service.create_order_from_ingest(payload, lines=lines)


def _seed_order_without_facility(*, message_id: str):
    payload = IngestEmailPayload(
        message_id=message_id,
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2026, 2, 13, 9, 0, 0),
        facility_hint=None,
        week_hint=None,
    )
    lines = [
        {
            "date": "2026-02-15",
            "daypart": "朝",
            "menu_name": "Menu B",
            "diet_type": "regular",
            "area_id": "2F",
            "bag_type": "standard",
            "quantity_original": 4,
        }
    ]
    return order_service.create_order_from_ingest(payload, lines=lines)


def test_get_ocr_sheet_prefers_identity_when_source_row_indexes_are_shifted():
    order_service.clear_all()
    received_at = datetime(2099, 4, 15, 9, 0, 0)
    _seed_monthly_menu_custom_entries(
        month_id="2099-04",
        month_start=date(2099, 4, 1),
        entries=[
            (date(2099, 4, 15), "昼", "Menu A", 0),
            (date(2099, 4, 15), "昼", "Menu B", 1),
        ],
    )
    order = _seed_custom_order(
        message_id="msg-sheet-source-row-shift-001",
        received_at=received_at,
        lines=[
            {
                "date": "2099-04-15",
                "daypart": "昼",
                "menu_name": "Menu A",
                "diet_type": "regular",
                "area_id": "2F",
                "bag_type": "standard",
                "quantity_original": 11,
                "source_row_index": 1,
            },
            {
                "date": "2099-04-15",
                "daypart": "昼",
                "menu_name": "Menu B",
                "diet_type": "regular",
                "area_id": "2F",
                "bag_type": "standard",
                "quantity_original": 22,
                "source_row_index": 0,
            },
        ],
    )

    sheet, error = order_service.get_ocr_sheet(order["id"])

    assert error is None
    assert sheet is not None
    assert (sheet.get("trace") or {}).get("mapped_mode") == "identity"
    fields = sheet["fields"]
    qty_idx = fields.index("qty.regular_2f")
    menu_idx = fields.index("menu")
    rows_by_menu = {row[menu_idx]: row for row in sheet["rows"]}
    assert rows_by_menu["Menu A"][qty_idx] == "11"
    assert rows_by_menu["Menu B"][qty_idx] == "22"


def test_should_prefer_source_row_candidate_when_duplicate_rows_are_clean():
    quantity_index = {("regular", "2F"): 3}
    duplicate_identity = order_service._sheet_row_identity("2026-01-08", "昼", "Menu A")
    base_rows = [
        {"identity": duplicate_identity, "values": ["01/08", "昼", "Menu A", ""]},
        {"identity": duplicate_identity, "values": ["01/08", "昼", "Menu A", ""]},
    ]
    order_lines = [
        {
            "date": "2026-01-08",
            "daypart": "昼",
            "menu_name": "Menu A",
            "diet_type": "regular",
            "area_id": "2F",
            "quantity_original": 11,
            "source_row_index": 0,
        },
        {
            "date": "2026-01-08",
            "daypart": "昼",
            "menu_name": "Menu A",
            "diet_type": "regular",
            "area_id": "2F",
            "quantity_original": 22,
            "source_row_index": 1,
        },
    ]

    summary = order_service._summarize_order_line_source_row_mapping(
        base_rows=base_rows,
        quantity_index=quantity_index,
        order_lines=order_lines,
    )

    assert summary == {
        "eligible_line_count": 2,
        "matched_source_row_count": 2,
        "mismatched_source_row_count": 0,
        "missing_source_row_count": 0,
        "invalid_identity_line_count": 0,
    }
    assert order_service._should_prefer_source_row_candidate(
        identity_count=1,
        source_row_count=2,
        source_row_summary=summary,
    )


def test_get_ocr_sheet_exposes_roi_review_issues():
    order_service.clear_all()
    order = _seed_order(message_id="msg-sheet-roi-review-001")
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "rows": [
                {
                    "row_index": 0,
                    "date_mmdd": "01/08",
                    "daypart": "昼",
                    "menu": "Menu A",
                    "qty": {
                        "regular_2f": 2,
                        "regular_3f": 1,
                        "soft_2f": 3,
                    },
                }
            ],
            "roi_cell_issues": [
                {
                    "row_index": 0,
                    "field": "qty.regular_2f",
                    "issue_code": "sanity_fail",
                    "severity": "warning",
                    "confidence": 0.88,
                    "value": 66,
                    "max_allowed": 50,
                }
            ],
        },
    )

    sheet, error = order_service.get_ocr_sheet(order["id"])

    assert error is None
    assert sheet is not None
    assert "sheet_ocr_review_required" in (sheet.get("warnings") or [])
    assert (sheet.get("issue_summary") or {}).get("review_required_cell_count") == 1
    issues = sheet.get("cell_issues") or []
    assert len(issues) == 1
    issue = issues[0]
    assert issue["issue_code"] == "sanity_fail"
    assert issue["field"] == "qty.regular_2f"
    fields = sheet.get("fields") or []
    assert issue["column_index"] == fields.index("qty.regular_2f")
    row = (sheet.get("rows") or [])[issue["row_index"]]
    assert row[fields.index("menu")] == "Menu A"


def test_export_ocr_sheet_label_writes_current_sheet_json(tmp_path):
    order_service.clear_all()
    order = _seed_order(message_id="msg-export-sheet-label-001")

    current_sheet, current_error = order_service.get_ocr_sheet(order["id"])

    assert current_error is None
    assert current_sheet is not None

    export_path = tmp_path / "labels" / f"{order['id']}.expected_sheet.json"
    exported, export_error = order_service.export_ocr_sheet_label(
        order["id"],
        output_path=export_path,
    )

    assert export_error is None
    assert exported is not None
    saved_json = json.loads(export_path.read_text(encoding="utf-8"))
    assert saved_json["rows"] == current_sheet["rows"]
    assert saved_json["row_ids"] == current_sheet["row_ids"]
    assert saved_json["fields"] == current_sheet["fields"]
    assert saved_json["header"] == current_sheet["header"]
    assert export_path.exists()
    saved = json.loads(export_path.read_text(encoding="utf-8"))
    assert saved == current_sheet
    assert exported["order_id"] == order["id"]
    assert exported["output_path"] == str(export_path)


def test_save_ocr_sheet_exact_persists_revision_without_reparsing_lines():
    order_service.clear_all()
    order = _seed_order(message_id="msg-sheet-exact-save-001")

    fields = [
        "date_mmdd",
        "daypart",
        "menu",
        "qty.regular_2f",
        "qty.regular_3f",
        "qty.soft_2f",
        "qty.soft_3f",
        "qty.mixer_2f",
        "qty.mixer_3f",
        "remarks",
    ]
    header = ["日付", "区分", "メニュー", "常食2F", "常食3F", "軟菜2F", "軟菜3F", "ミキサー2F", "ミキサー3F", "備考"]
    rows = [["01/08", "昼", "Menu A", "9", "4", "", "", "", "", "manual-save"]]
    row_ids = ["row-exact-save-1"]

    saved, error = order_service.save_ocr_sheet_exact(
        order["id"],
        header=header,
        rows=rows,
        ui_mode="sheet",
        fields=fields,
        row_ids=row_ids,
    )

    assert error is None
    assert saved is not None
    revision = saved.get("revision")
    assert isinstance(revision, dict)
    assert revision.get("ui_mode") == "sheet"
    assert revision.get("sheet_save_only") is True
    assert revision.get("sheet_save_mode") == "exact"
    assert revision.get("row_ids") == row_ids
    assert revision.get("rows") == rows

    current_order = order_service.get_order_by_id(order["id"])
    assert current_order is not None
    quantities = sorted(
        int(line.get("quantity_original"))
        for line in (current_order.get("lines") or [])
        if line.get("quantity_original") is not None
    )
    assert quantities == [1, 2, 3]

    output, output_error = order_service.get_ocr_output(order["id"])
    assert output_error is None
    assert output is not None
    edited_table = output.get("edited_table")
    assert isinstance(edited_table, dict)
    assert edited_table.get("rows") == rows


def test_export_ocr_sheet_label_prefers_exact_saved_sheet_revision(tmp_path):
    order_service.clear_all()
    order = _seed_order(message_id="msg-export-sheet-label-exact-001")

    fields = [
        "date_mmdd",
        "daypart",
        "menu",
        "qty.regular_2f",
        "qty.regular_3f",
        "qty.soft_2f",
        "qty.soft_3f",
        "qty.mixer_2f",
        "qty.mixer_3f",
        "remarks",
    ]
    header = ["日付", "区分", "メニュー", "常食2F", "常食3F", "軟菜2F", "軟菜3F", "ミキサー2F", "ミキサー3F", "備考"]
    rows = [["01/08", "昼", "Menu A", "12", "", "", "", "", "", "gold-label"]]
    row_ids = ["row-exact-export-1"]

    saved, error = order_service.save_ocr_sheet_exact(
        order["id"],
        header=header,
        rows=rows,
        ui_mode="sheet",
        fields=fields,
        row_ids=row_ids,
    )

    assert error is None
    assert saved is not None

    export_path = tmp_path / "labels" / f"{order['id']}.expected_sheet.json"
    exported, export_error = order_service.export_ocr_sheet_label(
        order["id"],
        output_path=export_path,
    )

    assert export_error is None
    assert exported is not None


def test_get_ocr_output_falls_back_to_message_job_first_pass_payload_when_reparse_job_is_unusable(monkeypatch):
    order_service.clear_all()
    order = _seed_order(message_id="msg-ocr-first-pass-fallback-001")
    create_job(f"OCR-{order['id']}", input_reference="gs://order/reparse.json", status="failed")
    create_job(f"OCR-{order['message_id']}", input_reference="gs://message/first-pass.json", status="done")

    first_pass_payload = {
        "status": "success",
        "table_raw": "|日付|区分|メニュー|常食2F|備考|\n|---|---|---|---|---|\n|01/08|昼|Menu A|4||",
        "pages": [
            {
                "page_index": 1,
                "markdown_uri": "gs://bucket/markdown.md",
                "ocr_overlay_uri": "gs://bucket/ocr.png",
                "layout_overlay_uri": "gs://bucket/layout.png",
                "figure_uris": [],
            }
        ],
    }

    def _fake_load_job_output(job, label):
        if not isinstance(job, dict):
            return None
        if job.get("id") == f"OCR-{order['id']}":
            return {"status": "failed", "error": "lines_empty"}
        if job.get("id") == f"OCR-{order['message_id']}":
            return dict(first_pass_payload)
        return None

    monkeypatch.setattr(order_service, "_load_job_output", _fake_load_job_output)

    output, error = order_service.get_ocr_output(order["id"])

    assert error is None
    assert output is not None
    assert output.get("table_raw") == first_pass_payload["table_raw"]
    with session_scope() as session:
        cache = session.get(OrderOcrCache, order["id"])
        assert cache is not None
        assert isinstance(cache.payload, dict)
        assert cache.payload.get("table_raw") == first_pass_payload["table_raw"]


def test_get_ocr_pages_falls_back_to_message_job_pages_when_reparse_job_has_no_page_artifacts(monkeypatch):
    order_service.clear_all()
    order = _seed_order(message_id="msg-ocr-pages-fallback-001")
    create_job(f"OCR-{order['id']}", input_reference="gs://order/reparse.json", status="failed")
    create_job(f"OCR-{order['message_id']}", input_reference="gs://message/first-pass.json", status="done")

    first_pass_payload = {
        "status": "success",
        "pages": [
            {
                "page_index": 1,
                "markdown_uri": None,
                "ocr_overlay_uri": "gs://bucket/ocr-page-1.png",
                "layout_overlay_uri": "gs://bucket/layout-page-1.png",
                "figure_uris": [],
            }
        ],
    }

    def _fake_load_job_output(job, label):
        if not isinstance(job, dict):
            return None
        if job.get("id") == f"OCR-{order['id']}":
            return {"status": "success", "table_raw": "|日付|区分|メニュー|常食2F|"}
        if job.get("id") == f"OCR-{order['message_id']}":
            return dict(first_pass_payload)
        return None

    monkeypatch.setattr(order_service, "_load_job_output", _fake_load_job_output)

    pages, error = order_service.get_ocr_pages(order["id"])

    assert error is None
    assert isinstance(pages, dict)
    assert isinstance(pages.get("pages"), list)
    assert len(pages["pages"]) == 1
    assert pages["pages"][0]["page_index"] == 1
    with session_scope() as session:
        cache = session.get(OrderOcrCache, order["id"])
        assert cache is not None
        assert isinstance(cache.payload, dict)
        assert isinstance(cache.payload.get("pages"), list)
        assert len(cache.payload["pages"]) == 1


def test_get_ocr_pages_requires_recovery_when_overlay_missing(monkeypatch):
    order_service.clear_all()
    order = _seed_order(message_id="msg-ocr-pages-synthetic-001")
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "table_raw": "|日付|区分|メニュー|常食2F|\n|---|---|---|---|\n|01/08|昼|Menu A|2|",
            "combined": {
                "corrected_pdf": "gs://bucket/corrected.pdf",
            },
        },
    )

    pages, error = order_service.get_ocr_pages(order["id"])

    assert pages is None
    assert error == "ocr_evidence_recovery_required"


def test_get_ocr_pages_synthesizes_grid_from_template_expected_columns_without_pdf_detection(monkeypatch):
    order_service.clear_all()
    order = _seed_order(message_id="msg-ocr-pages-template-grid-001")
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "pages": [
                {
                    "page_index": 1,
                    "markdown_uri": None,
                    "ocr_overlay_uri": "gs://bucket/ocr-page-1.png",
                    "layout_overlay_uri": "gs://bucket/layout-page-1.png",
                    "figure_uris": [],
                }
            ]
        },
    )

    monkeypatch.setattr(
        config_service,
        "get_facility_config",
        lambda facility_id: {
            "fax_template": {
                "table_box": [0.1, 0.2, 0.9, 0.8],
                "grid_expected_columns": 4,
            }
        },
    )
    monkeypatch.setattr(order_service, "_signed_url_from_uri", lambda uri: f"signed:{uri}" if uri else None)
    monkeypatch.setattr(
        order_service,
        "detect_table_grid",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("pdf grid detection should not run")),
    )

    pages, error = order_service.get_ocr_pages(order["id"])

    assert error is None
    assert isinstance(pages, dict)
    assert pages["table_box"] == [0.1, 0.2, 0.9, 0.8]
    assert isinstance(pages["grid_column_edges"], list)
    assert len(pages["grid_column_edges"]) == 5
    assert abs(pages["grid_column_edges"][0] - 0.1) < 1e-9
    assert abs(pages["grid_column_edges"][-1] - 0.9) < 1e-9
    assert pages["grid_row_edges"] is None


def test_get_ocr_pages_defers_expensive_grid_detection_in_request_path(monkeypatch):
    order_service.clear_all()
    order = _seed_order(message_id="msg-ocr-pages-overlay-grid-001")
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "pages": [
                {
                    "page_index": 1,
                    "markdown_uri": None,
                    "ocr_overlay_uri": "gs://bucket/ocr-page-1.png",
                    "layout_overlay_uri": "gs://bucket/layout-page-1.png",
                    "figure_uris": [],
                }
            ]
        },
    )

    monkeypatch.setattr(config_service, "get_facility_config", lambda facility_id: {"fax_template": {}})
    monkeypatch.setattr(order_service, "_signed_url_from_uri", lambda uri: f"signed:{uri}" if uri else None)
    monkeypatch.setattr(
        order_service,
        "load_bytes_from_uri",
        lambda uri: (_ for _ in ()).throw(AssertionError("overlay grid detection should not run in request path")),
    )
    monkeypatch.setattr(
        order_service,
        "detect_table_grid_image",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("overlay grid detection should not run")),
    )

    pages, error = order_service.get_ocr_pages(order["id"])

    assert error is None
    assert isinstance(pages, dict)
    assert pages["table_box"] is None
    assert pages["grid_column_edges"] is None
    assert pages["grid_row_edges"] is None
    assert pages["grid_detection_status"] == "deferred"
    assert pages["grid_detection_deferred_reason"] == "missing_template_grid_metadata:table_box,grid_column_edges,grid_row_edges"


def test_get_ocr_pages_returns_pages_even_when_grid_metadata_cannot_be_recovered(monkeypatch):
    order_service.clear_all()
    order = _seed_order(message_id="msg-ocr-pages-no-grid-001")
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "pages": [
                {
                    "page_index": 1,
                    "markdown_uri": None,
                    "ocr_overlay_uri": "gs://bucket/ocr-page-1.png",
                    "layout_overlay_uri": "gs://bucket/layout-page-1.png",
                    "figure_uris": [],
                }
            ]
        },
    )

    monkeypatch.setattr(config_service, "get_facility_config", lambda facility_id: {"fax_template": {}})
    monkeypatch.setattr(order_service, "_signed_url_from_uri", lambda uri: f"signed:{uri}" if uri else None)
    monkeypatch.setattr(order_service, "load_bytes_from_uri", lambda uri: b"overlay-bytes")
    monkeypatch.setattr(order_service, "detect_table_grid_image", lambda image_bytes, template: None)
    monkeypatch.setattr(
        order_service,
        "detect_table_grid",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("pdf grid detection should not run")),
    )

    pages, error = order_service.get_ocr_pages(order["id"])

    assert error is None
    assert isinstance(pages, dict)
    assert isinstance(pages["pages"], list)
    assert len(pages["pages"]) == 1
    assert pages["table_box"] is None
    assert pages["grid_column_edges"] is None
    assert pages["grid_row_edges"] is None
    assert pages["grid_detection_status"] == "deferred"


def test_save_order_ocr_cache_preserves_page_artifacts_when_new_payload_is_partial():
    order_service.clear_all()
    order = _seed_order(message_id="msg-ocr-pages-cache-preserve-001")

    order_service._save_order_ocr_cache(
        order["id"],
        {
            "status": "success",
            "pages": [
                {
                    "page_index": 1,
                    "markdown_uri": None,
                    "ocr_overlay_uri": "gs://bucket/ocr-page-1.png",
                    "layout_overlay_uri": "gs://bucket/layout-page-1.png",
                    "figure_uris": [],
                }
            ],
            "combined": {"corrected_pdf": "gs://bucket/corrected.pdf"},
            "template_id": "fax-template",
            "engine": "yomitoku",
            "facility_id": "FAC00001",
        },
    )
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "status": "success",
            "table_raw": "|日付|区分|メニュー|常食2F|",
        },
    )

    cached = order_service._load_order_ocr_cache(order["id"])

    assert isinstance(cached, dict)
    assert isinstance(cached.get("pages"), list)
    assert cached["combined"]["corrected_pdf"] == "gs://bucket/corrected.pdf"
    assert cached["template_id"] == "fax-template"
    assert cached["engine"] == "yomitoku"


def test_export_ocr_sheet_label_rebases_exact_saved_sheet_on_updated_template_header(tmp_path):
    order_service.clear_all()
    order = _seed_order(message_id="msg-export-sheet-label-rebase-001")
    previous_config = config_service.get_facility_config(order["facility"]) or {}

    try:
        current_sheet, current_error = order_service.get_ocr_sheet(order["id"])
        assert current_error is None
        assert current_sheet is not None
        qty_index = current_sheet["fields"].index("qty.regular_2f")
        saved_row = list(current_sheet["rows"][0])
        saved_row[qty_index] = "12"
        saved_row[-1] = "rebased-label"

        saved, error = order_service.save_ocr_sheet_exact(
            order["id"],
            header=current_sheet["header"],
            rows=[saved_row],
            ui_mode="sheet",
            fields=current_sheet["fields"],
            row_ids=[current_sheet["row_ids"][0]],
        )

        assert error is None
        assert saved is not None

        resolved_columns = (
            (((previous_config.get("fax_template") or {}).get("columns")) or [])
        )
        columns = []
        for item in resolved_columns:
            if not isinstance(item, dict):
                continue
            column = dict(item)
            if (
                str(column.get("role") or "").strip().lower() == "quantity"
                and str(column.get("diet_type") or "").strip().lower() == "regular"
                and str(column.get("area_id") or "").strip().lower() == "2f"
            ):
                column["header"] = "新常食2F"
            columns.append(column)

        update_result, update_error = order_service.save_order_facility_template_columns(
            order["id"],
            columns,
        )
        assert update_error is None
        assert isinstance(update_result, dict)

        export_path = tmp_path / "labels" / f"{order['id']}.expected_sheet.json"
        exported, export_error = order_service.export_ocr_sheet_label(
            order["id"],
            output_path=export_path,
        )

        assert export_error is None
        assert exported is not None
        saved_json = json.loads(export_path.read_text(encoding="utf-8"))
        rebased_index = saved_json["fields"].index("qty.regular_2f")
        assert saved_json["header"][rebased_index] == "新常食2F"
        assert saved_json["rows"][0][rebased_index] == "12"
        assert saved_json["rows"][0][-1] == "rebased-label"
    finally:
        assert facility_service.update_config(order["facility"], previous_config)


def test_get_ocr_sheet_exposes_generic_cell_issues():
    order_service.clear_all()
    order = _seed_order(message_id="msg-sheet-generic-review-001")
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "rows": [
                {
                    "row_index": 0,
                    "date_mmdd": "01/08",
                    "daypart": "昼",
                    "menu": "Menu A",
                    "qty": {
                        "regular_2f": 2,
                        "regular_3f": 1,
                        "soft_2f": 3,
                    },
                }
            ],
            "cell_issues": [
                {
                    "row_index": 0,
                    "column_index": 3,
                    "field": "qty.regular_2f",
                    "issue_code": "merged_numeric_cell",
                    "severity": "warning",
                    "source": "yomitoku_structured",
                    "bbox": [0.11, 0.22, 0.33, 0.44],
                    "text": "6\n9",
                }
            ],
        },
    )

    sheet, error = order_service.get_ocr_sheet(order["id"])

    assert error is None
    assert sheet is not None
    assert "sheet_ocr_review_required" in (sheet.get("warnings") or [])
    issues = sheet.get("cell_issues") or []
    assert len(issues) == 1
    issue = issues[0]
    assert issue["issue_code"] == "merged_numeric_cell"
    assert issue["source"] == "yomitoku_structured"
    assert issue["column_index"] == 3


def _seed_monthly_menu_daypart_order_2099_11() -> None:
    with session_scope() as session:
        session.execute(delete(MonthlyMenuEntry).where(MonthlyMenuEntry.monthly_menu_id == "2099-11"))
        session.execute(delete(MonthlyMenu).where(MonthlyMenu.id == "2099-11"))
        menu = session.get(MonthlyMenu, "2099-11")
        if not menu:
            session.add(
                MonthlyMenu(
                    id="2099-11",
                    month_start=date(2099, 11, 1),
                    filename="seed-2099-11.xlsx",
                )
            )
        seed_entries = [
            ("seed-entry-2099-11-15-breakfast", "朝食", "朝メニュー", 1),
            ("seed-entry-2099-11-15-lunch", "昼食", "昼メニュー", 1),
            ("seed-entry-2099-11-15-dinner", "夕食", "夕メニュー", 1),
        ]
        for entry_id, daypart, name, slot_index in seed_entries:
            exists = session.get(MonthlyMenuEntry, entry_id)
            if exists:
                continue
            session.add(
                MonthlyMenuEntry(
                    id=entry_id,
                    monthly_menu_id="2099-11",
                    menu_date=date(2099, 11, 15),
                    daypart=daypart,
                    name=name,
                    slot_index=slot_index,
                )
            )


def _seed_monthly_menu_boundary_2026_01_02() -> None:
    with session_scope() as session:
        session.execute(
            delete(MonthlyMenuEntry).where(
                MonthlyMenuEntry.monthly_menu_id.in_(["2026-01", "2026-02"])
            )
        )
        session.execute(delete(MonthlyMenu).where(MonthlyMenu.id.in_(["2026-01", "2026-02"])))
        session.add_all(
            [
                MonthlyMenu(
                    id="2026-01",
                    month_start=date(2026, 1, 1),
                    filename="seed-2026-01-boundary.xlsx",
                ),
                MonthlyMenu(
                    id="2026-02",
                    month_start=date(2026, 2, 1),
                    filename="seed-2026-02-boundary.xlsx",
                ),
            ]
        )
        session.add_all(
            [
                MonthlyMenuEntry(
                    id="seed-entry-2026-01-31-breakfast-boundary",
                    monthly_menu_id="2026-01",
                    menu_date=date(2026, 1, 31),
                    daypart="朝食",
                    name="Boundary Jan",
                    slot_index=0,
                ),
                MonthlyMenuEntry(
                    id="seed-entry-2026-02-01-breakfast-boundary",
                    monthly_menu_id="2026-02",
                    menu_date=date(2026, 2, 1),
                    daypart="朝食",
                    name="Boundary Feb",
                    slot_index=0,
                ),
            ]
        )


def test_get_ocr_sheet_from_order_lines():
    order_service.clear_all()
    order = _seed_order(message_id="msg-sheet-001")

    sheet, error = order_service.get_ocr_sheet(order["id"])
    assert error is None
    assert sheet is not None
    assert sheet["order_id"] == order["id"]
    assert sheet["facility_id"] == "FAC00001"
    assert sheet["source"] in {"order_lines", "weekly_menu"}
    assert sheet["legacy_available"] is True
    assert sheet.get("quantity_column_count") in {3, 6}
    assert isinstance(sheet.get("fields"), list) and len(sheet["fields"]) >= 1
    assert isinstance(sheet.get("header"), list) and len(sheet["header"]) == len(sheet["fields"])
    assert isinstance(sheet.get("rows"), list) and len(sheet["rows"]) >= 1
    assert isinstance(sheet.get("row_ids"), list) and len(sheet["row_ids"]) == len(sheet["rows"])

    fields = sheet["fields"]
    first_row = sheet["rows"][0]
    regular_2f_idx = next(
        idx for idx, field in enumerate(fields) if field in {"qty.regular_2f", "qty.regular_x"}
    )
    regular_3f_idx = fields.index("qty.regular_3f") if "qty.regular_3f" in fields else None
    soft_2f_idx = next(
        idx for idx, field in enumerate(fields) if field in {"qty.soft_2f", "qty.soft_x"}
    )
    if regular_3f_idx is not None:
        assert first_row[regular_2f_idx] == "2"
        assert first_row[regular_3f_idx] == "1"
    else:
        assert first_row[regular_2f_idx] == "3"
    assert first_row[soft_2f_idx] == "3"


def test_get_ocr_sheet_weekly_menu_daypart_order_is_morning_first():
    order_service.clear_all()
    _seed_monthly_menu_daypart_order_2099_11()
    payload = IngestEmailPayload(
        message_id="msg-sheet-daypart-order-001",
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2099, 11, 15, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2099-11",
    )
    lines = [
        {
            "date": "2099-11-15",
            "daypart": "朝",
            "menu_name": "朝メニュー",
            "diet_type": "regular",
            "area_id": "2F",
            "bag_type": "standard",
            "quantity_original": 1,
        }
    ]
    order = order_service.create_order_from_ingest(payload, lines=lines)

    sheet, error = order_service.get_ocr_sheet(order["id"])
    assert error is None
    assert sheet is not None
    assert sheet["source"] == "weekly_menu"

    fields = sheet["fields"]
    date_idx = next((idx for idx, field in enumerate(fields) if field.startswith("date")), 0)
    daypart_idx = fields.index("daypart")
    menu_idx = fields.index("menu")

    target_rows = [row for row in sheet["rows"] if date_idx < len(row) and row[date_idx] == "11/15"]
    assert len(target_rows) >= 3
    assert [target_rows[0][daypart_idx], target_rows[1][daypart_idx], target_rows[2][daypart_idx]] == [
        "朝",
        "昼",
        "夕",
    ]
    assert [target_rows[0][menu_idx], target_rows[1][menu_idx], target_rows[2][menu_idx]] == [
        "朝メニュー",
        "昼メニュー",
        "夕メニュー",
    ]


def test_get_ocr_sheet_weekly_menu_prefers_persisted_order_lines_over_payload():
    order_service.clear_all()
    _seed_monthly_menu_daypart_order_2099_11()
    payload = IngestEmailPayload(
        message_id="msg-sheet-payload-priority-001",
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2099, 11, 15, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2099-11",
    )
    # Simulate persisted lines with wrong menu identity mapping.
    lines = [
        {
            "date": "2099-11-15",
            "daypart": "昼",
            "menu_name": "昼メニュー",
            "diet_type": "regular",
            "area_id": "2F",
            "bag_type": "standard",
            "quantity_original": 6,
        },
        {
            "date": "2099-11-15",
            "daypart": "夕",
            "menu_name": "夕メニュー",
            "diet_type": "regular",
            "area_id": "2F",
            "bag_type": "standard",
            "quantity_original": 5,
        },
    ]
    order = order_service.create_order_from_ingest(payload, lines=lines)
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "table_rows": [
                ["11/15", "夕", "OCRノイズメニュー", "7", "", "", "", "", "", "payload-note"],
                ["11/15", "夕", "OCRノイズメニュー2", "", "", "", "", "", "", ""],
                ["11/15", "夕", "OCRノイズメニュー3", "", "", "", "", "", "", ""],
            ],
            "date_strings": ["11/15"],
        },
    )

    sheet, error = order_service.get_ocr_sheet(order["id"])
    assert error is None
    assert sheet is not None
    assert sheet["source"] == "weekly_menu"

    fields = sheet["fields"]
    qty_idx = next(
        idx
        for idx, field in enumerate(fields)
        if field in {"qty.regular_2f", "qty.regular_x"}
    )
    daypart_idx = fields.index("daypart")
    menu_idx = fields.index("menu")

    target_rows = [row for row in sheet["rows"] if row and row[0] == "11/15"]
    assert len(target_rows) >= 3
    breakfast = next(row for row in target_rows if row[daypart_idx] == "朝" and row[menu_idx] == "朝メニュー")
    lunch = next(row for row in target_rows if row[daypart_idx] == "昼" and row[menu_idx] == "昼メニュー")
    dinner = next(row for row in target_rows if row[daypart_idx] == "夕" and row[menu_idx] == "夕メニュー")
    assert breakfast[qty_idx] == ""
    assert lunch[qty_idx] == "6"
    assert dinner[qty_idx] == "5"


def test_apply_payload_cells_by_menu_priority_prefers_exact_menu_match_over_row_index():
    fields = ["date_mmdd", "daypart", "menu", "qty.regular_2f", "remarks"]
    quantity_index = order_service._build_sheet_quantity_index(fields)
    rows = [
        {"values": ["02/15", "昼", "A", "", ""]},
        {"values": ["02/15", "昼", "B", "", ""]},
        {"values": ["02/15", "昼", "C", "", ""]},
    ]
    payload_rows = [
        ["02/15", "昼", "B", "20", "note-b"],
        ["02/15", "昼", "A", "10", "note-a"],
        ["02/15", "昼", "C", "30", "note-c"],
    ]

    stats = order_service._apply_payload_cells_by_menu_priority(
        rows=rows,
        fields=fields,
        quantity_index=quantity_index,
        payload_rows=payload_rows,
    )

    assert stats["exact"] == 3
    assert rows[0]["values"][3] == "10"
    assert rows[1]["values"][3] == "20"
    assert rows[2]["values"][3] == "30"
    assert rows[0]["values"][4] == "note-a"
    assert rows[1]["values"][4] == "note-b"
    assert rows[2]["values"][4] == "note-c"


def test_apply_payload_cells_by_menu_priority_supports_partial_match():
    fields = ["date_mmdd", "daypart", "menu", "qty.regular_2f"]
    quantity_index = order_service._build_sheet_quantity_index(fields)
    rows = [
        {"values": ["02/15", "朝", "じゃが芋のコンソメ煮", ""]},
        {"values": ["02/15", "朝", "キャベツサラダ", ""]},
    ]
    payload_rows = [
        ["02/15", "朝", "キャベツサラダ", "8"],
        ["02/15", "朝", "じゃがいものコンソメ煮", "7"],
    ]

    stats = order_service._apply_payload_cells_by_menu_priority(
        rows=rows,
        fields=fields,
        quantity_index=quantity_index,
        payload_rows=payload_rows,
    )

    assert stats["partial"] >= 1
    assert rows[0]["values"][3] == "7"
    assert rows[1]["values"][3] == "8"


def test_apply_payload_cells_by_menu_priority_falls_back_to_row_index():
    fields = ["date_mmdd", "daypart", "menu", "qty.regular_2f", "qty.soft_2f"]
    quantity_index = order_service._build_sheet_quantity_index(fields)
    rows = [
        {"values": ["02/15", "昼", "Menu A", "", ""]},
    ]
    payload_rows = [
        ["02/15", "昼", "", "5", "2"],
    ]

    stats = order_service._apply_payload_cells_by_menu_priority(
        rows=rows,
        fields=fields,
        quantity_index=quantity_index,
        payload_rows=payload_rows,
    )

    assert stats["row_index"] == 1
    # Temporary policy: quantity columns use raw column positions.
    assert rows[0]["values"][3] == "5"
    assert rows[0]["values"][4] == "2"


def test_apply_payload_cells_by_menu_priority_row_index_skips_free_text_noise():
    fields = ["date_mmdd", "daypart", "menu", "qty.regular_2f", "qty.soft_2f"]
    quantity_index = order_service._build_sheet_quantity_index(fields)
    rows = [
        {"values": ["02/15", "昼", "Menu A", "", ""]},
    ]
    payload_rows = [
        # Free-text/noise row (no quantity columns) should not be row-index mapped.
        ["", "3000", "自由領域メモ", "", ""],
        # Quantity-only row remains eligible for row-index fallback.
        ["02/15", "昼", "", "5", "2"],
    ]

    stats = order_service._apply_payload_cells_by_menu_priority(
        rows=rows,
        fields=fields,
        quantity_index=quantity_index,
        payload_rows=payload_rows,
    )

    assert stats["row_index"] == 1
    assert rows[0]["values"][3] == "5"
    assert rows[0]["values"][4] == "2"


def test_apply_payload_cells_by_menu_priority_row_index_prefers_direct_candidate_for_same_index():
    fields = ["date_mmdd", "daypart", "menu", "qty.regular_2f"]
    quantity_index = order_service._build_sheet_quantity_index(fields)
    rows = [
        {"values": ["02/15", "昼", "じゃが芋のコンソメ煮", ""]},
        {"values": ["02/15", "昼", "キャベツサラダ", ""]},
        {"values": ["02/15", "昼", "豚肉とれんこんの炒め煮", ""]},
    ]
    payload_rows = [
        ["02/15", "昼", "じゃが芋のコンソメ煮", ""],
        ["02/15", "昼", "", "23"],
        ["02/15", "昼", "豚肉とれんこんの炒め煮", ""],
    ]

    stats = order_service._apply_payload_cells_by_menu_priority(
        rows=rows,
        fields=fields,
        quantity_index=quantity_index,
        payload_rows=payload_rows,
    )

    # Quantity-only row should be consumed by direct row-index for the same row.
    assert stats["row_index"] >= 1
    assert stats.get("exact", 0) >= 0
    assert rows[0]["values"][3] == ""
    assert rows[1]["values"][3] == "23"
    assert rows[2]["values"][3] == ""


def test_apply_payload_cells_by_menu_priority_row_index_rejects_cross_date_candidates():
    fields = ["date_mmdd", "menu", "qty.regular_2f"]
    quantity_index = order_service._build_sheet_quantity_index(fields)
    rows = [
        {"values": ["02/15", "Menu A", ""]},
        {"values": ["02/15", "Menu B", ""]},
        {"values": ["02/16", "Menu C", ""]},
    ]
    payload_rows = [
        ["02/15", "Menu A", ""],
        ["", "", ""],
        ["02/16", "", "12"],
    ]

    stats = order_service._apply_payload_cells_by_menu_priority(
        rows=rows,
        fields=fields,
        quantity_index=quantity_index,
        payload_rows=payload_rows,
    )

    assert stats["row_index"] == 1
    assert rows[0]["values"][2] == ""
    assert rows[1]["values"][2] == ""
    assert rows[2]["values"][2] == "12"


def test_apply_payload_quantities_numeric_only_skips_cross_date_rows():
    fields = ["date_mmdd", "daypart", "menu", "qty.regular_2f", "remarks"]
    quantity_index = order_service._build_sheet_quantity_index(fields)
    rows = [
        {"values": ["02/15", "朝", "Menu A", "", "keep-a"]},
        {"values": ["02/15", "昼", "Menu B", "", "keep-b"]},
    ]
    payload_rows = [
        ["12/31", "夕", "OCRノイズA", "20", "payload-note-a"],
        ["01/01", "朝", "OCRノイズB", "10", "payload-note-b"],
    ]

    stats = order_service._apply_payload_quantities_numeric_only(
        rows=rows,
        fields=fields,
        quantity_index=quantity_index,
        payload_rows=payload_rows,
    )

    assert stats["row_index"] == 0
    assert stats["exact"] == 0
    assert stats["partial"] == 0
    assert stats["neighbor"] == 0
    assert rows[0]["values"][0] == "02/15"
    assert rows[0]["values"][1] == "朝"
    assert rows[0]["values"][2] == "Menu A"
    assert rows[0]["values"][3] == ""
    assert rows[0]["values"][4] == "keep-a"
    assert rows[1]["values"][0] == "02/15"
    assert rows[1]["values"][1] == "昼"
    assert rows[1]["values"][2] == "Menu B"
    assert rows[1]["values"][3] == ""
    assert rows[1]["values"][4] == "keep-b"


def test_apply_payload_quantities_numeric_only_allows_row_index_when_payload_has_no_dates():
    fields = ["date_mmdd", "daypart", "menu", "qty.regular_2f", "remarks"]
    quantity_index = order_service._build_sheet_quantity_index(fields)
    rows = [
        {"values": ["02/15", "朝", "Menu A", "", "keep-a"]},
        {"values": ["02/15", "昼", "Menu B", "", "keep-b"]},
    ]
    payload_rows = [
        ["", "", "OCRノイズA", "20", "payload-note-a"],
        ["", "", "OCRノイズB", "10", "payload-note-b"],
    ]

    stats = order_service._apply_payload_quantities_numeric_only(
        rows=rows,
        fields=fields,
        quantity_index=quantity_index,
        payload_rows=payload_rows,
    )

    assert stats["row_index"] == 2
    assert rows[0]["values"][3] == "20"
    assert rows[1]["values"][3] == "10"


def test_apply_payload_quantities_numeric_only_drops_sparse_column_spike_value():
    fields = ["date_mmdd", "daypart", "menu", "qty.regular_2f", "qty.regular_3f", "qty.mixer_3f", "remarks"]
    quantity_index = order_service._build_sheet_quantity_index(fields)
    rows = [
        {"values": ["02/15", "朝", "Menu A", "", "", "", "keep-a"]},
        {"values": ["02/15", "昼", "Menu B", "", "", "", "keep-b"]},
    ]
    payload_rows = [
        ["02/15", "朝", "OCR A", "4", "9", "", "note-a"],
        ["02/15", "昼", "OCR B", "4", "9", "58", "note-b"],
    ]

    stats = order_service._apply_payload_quantities_numeric_only(
        rows=rows,
        fields=fields,
        quantity_index=quantity_index,
        payload_rows=payload_rows,
    )

    assert stats["row_index"] == 2
    assert rows[0]["values"][3] == "4"
    assert rows[0]["values"][4] == "9"
    assert rows[0]["values"][5] == ""
    assert rows[1]["values"][3] == "4"
    assert rows[1]["values"][4] == "9"
    assert rows[1]["values"][5] == ""


def test_apply_payload_quantities_numeric_only_fills_two_row_cluster_edge_gap():
    fields = ["date_mmdd", "daypart", "menu", "qty.regular_3f", "qty.soft_2f", "remarks"]
    quantity_index = order_service._build_sheet_quantity_index(fields)
    rows = [
        {"values": ["02/15", "朝", "Menu A", "", "", "keep-a"]},
        {"values": ["02/15", "朝", "Menu B", "", "", "keep-b"]},
    ]
    payload_rows = [
        ["", "", "", "", "", ""],
        ["", "", "", "4", "9", ""],
    ]

    stats = order_service._apply_payload_quantities_numeric_only(
        rows=rows,
        fields=fields,
        quantity_index=quantity_index,
        payload_rows=payload_rows,
    )

    assert stats["row_index"] == 1
    assert stats.get("cluster_fill", 0) >= 2
    assert rows[0]["values"][3] == "4"
    assert rows[0]["values"][4] == "9"
    assert rows[1]["values"][3] == "4"
    assert rows[1]["values"][4] == "9"


def test_apply_payload_quantities_numeric_only_skips_cluster_fill_when_consensus_disabled():
    fields = ["date_mmdd", "daypart", "menu", "qty.regular_3f", "qty.soft_2f", "remarks"]
    quantity_index = order_service._build_sheet_quantity_index(fields)
    rows = [
        {"values": ["02/15", "朝", "Menu A", "", "", "keep-a"]},
        {"values": ["02/15", "朝", "Menu B", "", "", "keep-b"]},
    ]
    payload_rows = [
        ["", "", "", "", "", ""],
        ["", "", "", "4", "9", ""],
    ]

    stats = order_service._apply_payload_quantities_numeric_only(
        rows=rows,
        fields=fields,
        quantity_index=quantity_index,
        payload_rows=payload_rows,
        enable_daypart_consensus=False,
    )

    assert stats["row_index"] == 1
    assert stats.get("cluster_fill", 0) == 0
    assert rows[0]["values"][3] == ""
    assert rows[0]["values"][4] == ""
    assert rows[1]["values"][3] == "4"
    assert rows[1]["values"][4] == "9"


def test_apply_payload_quantities_numeric_only_fills_leading_blank_cluster_from_daypart_consensus():
    fields = ["date_mmdd", "daypart", "menu", "qty.regular_2f", "remarks"]
    quantity_index = order_service._build_sheet_quantity_index(fields)
    rows = [
        {"values": ["02/15", "朝", "Menu A1", "", ""]},
        {"values": ["02/15", "朝", "Menu A2", "", ""]},
        {"values": ["02/16", "朝", "Menu B1", "", ""]},
        {"values": ["02/16", "朝", "Menu B2", "", ""]},
        {"values": ["02/17", "朝", "Menu C1", "", ""]},
        {"values": ["02/17", "朝", "Menu C2", "", ""]},
        {"values": ["02/18", "朝", "Menu D1", "", ""]},
        {"values": ["02/18", "朝", "Menu D2", "", ""]},
    ]
    payload_rows = [
        ["02/15", "朝", "Menu A1", "", ""],
        ["02/15", "朝", "Menu A2", "", ""],
        ["02/16", "朝", "Menu B1", "20", ""],
        ["02/16", "朝", "Menu B2", "", ""],
        ["02/17", "朝", "Menu C1", "20", ""],
        ["02/17", "朝", "Menu C2", "", ""],
        ["02/18", "朝", "Menu D1", "20", ""],
        ["02/18", "朝", "Menu D2", "", ""],
    ]

    stats = order_service._apply_payload_quantities_numeric_only(
        rows=rows,
        fields=fields,
        quantity_index=quantity_index,
        payload_rows=payload_rows,
        allow_heuristics=False,
    )

    assert stats["row_index"] >= 3
    assert stats.get("cluster_fill", 0) >= 5
    assert rows[0]["values"][3] == "20"
    assert rows[1]["values"][3] == "20"
    assert all(row["values"][3] == "20" for row in rows)


def test_apply_payload_quantities_numeric_only_does_not_fill_leading_blank_cluster_with_low_support():
    fields = ["date_mmdd", "daypart", "menu", "qty.regular_2f", "remarks"]
    quantity_index = order_service._build_sheet_quantity_index(fields)
    rows = [
        {"values": ["02/15", "朝", "Menu A1", "", ""]},
        {"values": ["02/15", "朝", "Menu A2", "", ""]},
        {"values": ["02/16", "朝", "Menu B1", "", ""]},
        {"values": ["02/16", "朝", "Menu B2", "", ""]},
        {"values": ["02/17", "朝", "Menu C1", "", ""]},
        {"values": ["02/17", "朝", "Menu C2", "", ""]},
    ]
    payload_rows = [
        ["02/15", "朝", "Menu A1", "", ""],
        ["02/15", "朝", "Menu A2", "", ""],
        ["02/16", "朝", "Menu B1", "20", ""],
        ["02/16", "朝", "Menu B2", "", ""],
        ["02/17", "朝", "Menu C1", "20", ""],
        ["02/17", "朝", "Menu C2", "", ""],
    ]

    stats = order_service._apply_payload_quantities_numeric_only(
        rows=rows,
        fields=fields,
        quantity_index=quantity_index,
        payload_rows=payload_rows,
        allow_heuristics=False,
    )

    assert stats["row_index"] >= 2
    assert stats.get("cluster_fill", 0) >= 2
    assert rows[0]["values"][3] == ""
    assert rows[1]["values"][3] == ""
    assert rows[2]["values"][3] == "20"
    assert rows[3]["values"][3] == "20"
    assert rows[4]["values"][3] == "20"
    assert rows[5]["values"][3] == "20"


def test_apply_weekly_menu_order_line_cluster_consensus_fill_fills_third_row():
    fields = ["date_mmdd", "daypart", "menu", "qty.regular_2f", "qty.regular_3f", "remarks"]
    quantity_index = order_service._build_sheet_quantity_index(fields)
    rows = [
        {"values": ["02/08", "昼", "Menu A", "45", "", ""]},
        {"values": ["02/08", "昼", "Menu B", "45", "", ""]},
        {"values": ["02/08", "昼", "Menu C", "", "", ""]},
        {"values": ["02/09", "昼", "Menu D", "44", "", ""]},
    ]

    filled = order_service._apply_weekly_menu_order_line_cluster_consensus_fill(
        rows=rows,
        fields=fields,
        quantity_index=quantity_index,
    )

    assert filled >= 1
    assert rows[2]["values"][3] == "45"


def test_llm_allows_order_line_cluster_consensus_fill_defaults_false():
    assert order_service._llm_allows_order_line_cluster_consensus_fill(None) is False
    assert order_service._llm_allows_order_line_cluster_consensus_fill({}) is False
    assert (
        order_service._llm_allows_order_line_cluster_consensus_fill(
            {"_reparse_debug": {"sheet_cluster_fill_decision": ""}}
        )
        is False
    )


def test_llm_allows_order_line_cluster_consensus_fill_accepts_explicit_allow():
    assert (
        order_service._llm_allows_order_line_cluster_consensus_fill(
            {"_reparse_debug": {"sheet_cluster_fill_decision": "allow"}}
        )
        is True
    )
    assert (
        order_service._llm_allows_order_line_cluster_consensus_fill(
            {"_sheet_fill_decision": True}
        )
        is True
    )


def test_apply_payload_quantities_numeric_only_copies_explicit_span_marker_cluster():
    fields = ["date_mmdd", "daypart", "menu", "qty.regular_2f", "remarks"]
    quantity_index = order_service._build_sheet_quantity_index(fields)
    rows = [
        {"values": ["02/15", "朝", "Menu A", "", ""]},
        {"values": ["02/15", "朝", "Menu B", "", ""]},
        {"values": ["02/15", "朝", "Menu C", "", ""]},
    ]
    payload_rows = [
        ["", "", "OCR-A", ")20)20)20", ""],
    ]

    stats = order_service._apply_payload_quantities_numeric_only(
        rows=rows,
        fields=fields,
        quantity_index=quantity_index,
        payload_rows=payload_rows,
        allow_heuristics=False,
    )

    assert stats["row_index"] == 1
    assert stats.get("span_copy", 0) >= 2
    assert rows[0]["values"][3] == "20"
    assert rows[1]["values"][3] == "20"
    assert rows[2]["values"][3] == "20"


def test_apply_payload_quantities_numeric_only_applies_parenthesized_span_row_hint():
    fields = ["date_mmdd", "daypart", "menu", "qty.regular_2f", "remarks"]
    quantity_index = order_service._build_sheet_quantity_index(fields)
    rows = [
        {"values": ["02/19", "昼", "Menu A", "", ""]},
        {"values": ["02/19", "昼", "Menu B", "", ""]},
        {"values": ["02/19", "昼", "Menu C", "", ""]},
    ]
    payload_rows = [
        ["02/19", "昼", "Menu A", "20", ""],
        ["", "", "取川(2)", "", ""],
        ["", "", "Menu C", "", ""],
    ]

    stats = order_service._apply_payload_quantities_numeric_only(
        rows=rows,
        fields=fields,
        quantity_index=quantity_index,
        payload_rows=payload_rows,
        allow_heuristics=False,
    )

    assert stats["row_index"] >= 2
    assert stats.get("span_copy", 0) >= 2
    assert rows[0]["values"][3] == "20"
    assert rows[1]["values"][3] == "20"
    assert rows[2]["values"][3] == "20"


def test_apply_payload_quantities_numeric_only_ignores_low_purity_quantity_column():
    fields = [
        "date_mmdd",
        "daypart",
        "menu",
        "qty.regular_3f",
        "qty.soft_2f",
        "qty.mixer_3f",
        "remarks",
    ]
    quantity_index = order_service._build_sheet_quantity_index(fields)
    rows = [
        {"values": ["02/15", "昼", f"Menu {idx}", "", "", "", ""]}
        for idx in range(8)
    ]
    payload_rows = [
        ["", "", "", "4", "9", "58", ""],
        ["", "", "", "4", "9", "8", ""],
        ["", "", "", "4", "9", "タ", ""],
        ["", "", "", "4", "9", "\"", ""],
        ["", "", "", "4", "9", "a", ""],
        ["", "", "", "4", "9", "END", ""],
        ["", "", "", "4", "9", "", ""],
        ["", "", "", "4", "9", "", ""],
    ]

    stats = order_service._apply_payload_quantities_numeric_only(
        rows=rows,
        fields=fields,
        quantity_index=quantity_index,
        payload_rows=payload_rows,
    )

    assert stats["row_index"] == 8
    assert all(row["values"][3] == "4" for row in rows)
    assert all(row["values"][4] == "9" for row in rows)
    assert all(row["values"][5] == "" for row in rows)


def test_extract_sheet_rows_from_payload_supports_escaped_newlines():
    facility_cfg = config_service.get_facility_config("FAC00001") or {}
    template = facility_cfg.get("fax_template") or {}
    payload = {
        "table_raw": (
            "| 日付 | 区分 | メニュー | 常食2F | 常食3F | 軟菜2F | 軟菜3F | ミキサー2F | ミキサー3F | 備考 |\\n"
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |\\n"
            "| 02/15 | 朝 | 朝メニュー | 7 |  |  |  |  |  |  |"
        )
    }
    rows = order_service._extract_sheet_rows_from_payload(payload, template)
    assert len(rows) == 1
    assert rows[0][0] == "02/15"
    assert rows[0][1] == "朝"
    assert rows[0][2] == "朝メニュー"
    assert rows[0][3] == "7"


def test_parse_sheet_quantity_cell_rescues_common_single_glyph_noise():
    assert order_service._parse_sheet_quantity_cell("g") == 9
    assert order_service._parse_sheet_quantity_cell("4.") == 4
    assert order_service._parse_sheet_quantity_cell("2.1") == 21
    assert order_service._parse_sheet_quantity_cell("1.5") == 15
    assert order_service._parse_sheet_quantity_cell("A") is None


def test_sanitize_payload_table_raw_trims_non_table_tail():
    payload = {
        "table_raw": (
            "| 日付 | 区分 | 献立 | 常食 |\n"
            "| --- | --- | --- | --- |\n"
            "| 02/15 | 朝 | Menu A | 7 |\n"
            "\n"
            "自由領域の文章\n"
            "16\n"
        )
    }
    sanitized = order_service._sanitize_payload_table_raw(payload)
    assert sanitized.get("table_raw_truncated") is True
    assert sanitized["table_raw"].splitlines() == [
        "| 日付 | 区分 | 献立 | 常食 |",
        "| --- | --- | --- | --- |",
        "| 02/15 | 朝 | Menu A | 7 |",
    ]


def test_apply_payload_cells_ignores_outlier_quantities():
    fields = ["date_mmdd", "daypart", "menu", "qty.regular_2f"]
    quantity_index = order_service._build_sheet_quantity_index(fields)
    rows = [
        {"values": ["02/15", "朝", "Menu A", ""]},
    ]
    payload_rows = [
        ["02/15", "朝", "Menu A", "3000"],
    ]

    order_service._apply_payload_cells_by_menu_priority(
        rows=rows,
        fields=fields,
        quantity_index=quantity_index,
        payload_rows=payload_rows,
    )

    assert rows[0]["values"][3] == ""


def test_parse_sheet_quantity_cell_rejects_embedded_text_tokens():
    assert order_service._parse_sheet_quantity_cell("23") == 23
    assert order_service._parse_sheet_quantity_cell("（23）") == 23
    assert order_service._parse_sheet_quantity_cell("99") is None
    assert order_service._parse_sheet_quantity_cell("副23") is None
    assert order_service._parse_sheet_quantity_cell("No.23") is None


def test_get_ocr_sheet_falls_back_to_ocr_table_when_weekly_menu_missing():
    order_service.clear_all()
    with session_scope() as session:
        session.execute(delete(MonthlyMenuEntry).where(MonthlyMenuEntry.monthly_menu_id == "2099-12"))
        session.execute(delete(MonthlyMenu).where(MonthlyMenu.id == "2099-12"))
    payload = IngestEmailPayload(
        message_id="msg-sheet-ocr-fallback-001",
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2099, 12, 15, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint=None,
    )
    lines = [
        {
            "date": "2099-12-15",
            "daypart": "朝",
            "menu_name": "切干大根煮",
            "diet_type": "regular",
            "area_id": "2F",
            "bag_type": "standard",
            "quantity_original": 7,
        }
    ]
    order = order_service.create_order_from_ingest(payload, lines=lines)
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "table_rows": [
                ["12/15", "朝", "じゃが芋のコンソメ煮", "", "", "", "", "", "", ""],
                ["12/15", "朝", "切干大根煮", "7", "", "", "", "", "", ""],
            ],
            "date_strings": ["12/15"],
        },
    )

    sheet, error = order_service.get_ocr_sheet(order["id"])
    assert error is None
    assert sheet is not None
    assert sheet["source"] in {"ocr_table", "ocr_table+ocr_payload"}
    assert "sheet_weekly_menu_missing" in (sheet.get("warnings") or [])
    assert len(sheet["rows"]) >= 2

    fields = sheet["fields"]
    menu_idx = fields.index("menu")
    regular_2f_idx = next(
        idx for idx, field in enumerate(fields) if field in {"qty.regular_2f", "qty.regular_x"}
    )

    first_row = sheet["rows"][0]
    second_row = sheet["rows"][1]
    assert first_row[menu_idx] == "じゃが芋のコンソメ煮"
    assert second_row[menu_idx] == "切干大根煮"
    assert first_row[regular_2f_idx] == ""
    assert second_row[regular_2f_idx] == "7"


def test_apply_ocr_table_saves_revision_and_output_uses_edited():
    order_service.clear_all()
    order = _seed_order(message_id="msg-sheet-002")

    fields = [
        "date_mmdd",
        "daypart",
        "menu",
        "qty.regular_2f",
        "qty.regular_3f",
        "qty.soft_2f",
        "qty.soft_3f",
        "qty.mixer_2f",
        "qty.mixer_3f",
        "remarks",
    ]
    header = ["日付", "区分", "メニュー", "常食2F", "常食3F", "軟菜2F", "軟菜3F", "ミキサー2F", "ミキサー3F", "備考"]
    rows = [["01/08", "昼", "Menu A", "3", "1", "", "", "", "", "manual"]]
    row_ids = ["row-1"]

    updated, error = order_service.apply_ocr_table(
        order["id"],
        header=header,
        rows=rows,
        ui_mode="sheet",
        fields=fields,
        row_ids=row_ids,
    )
    assert error is None
    assert updated is not None
    assert updated["reparse"]["provider"] == "structured_rows"

    history, error = order_service.get_ocr_edit_history(order["id"])
    assert error is None
    assert history is not None
    assert isinstance(history.get("revisions"), list)
    assert len(history["revisions"]) >= 1
    latest = history.get("latest")
    assert isinstance(latest, dict)
    assert latest.get("ui_mode") == "sheet"
    assert latest.get("row_count") == 1
    assert latest.get("row_ids") == row_ids

    output, error = order_service.get_ocr_output(order["id"])
    assert error is None
    assert output is not None
    assert output.get("ocr_source") == "edited"
    edited_table = output.get("edited_table")
    assert isinstance(edited_table, dict)
    assert edited_table.get("row_ids") == row_ids


def test_rows_from_markdown_maps_kanji_regular_column():
    master = config_service.load_facility_master()
    base_template = master.get("fax_template_base", {})
    facility = next(
        fac for fac in master.get("facilities", []) if fac.get("facility_id") == "FAC00001"
    )
    template = facility.get("fax_template") or config_service._merge_template(
        base_template,
        facility.get("fax_template_override"),
    )

    markdown = """
|日 付|区 分||献立|常<br>☆||薬食||変更(1)|変更(2)|備考欄|
|-|-|-|-|-|-|-|-|-|-|-|
|2/15|朝|WD|豚肉とれんこんの炒め煮|33|||||||
""".strip()
    rows = rows_from_markdown(markdown, template)
    assert isinstance(rows, list)
    assert len(rows) == 1
    first = rows[0]
    # qty.regular_2f column
    assert first[3] == "33"


def test_rows_from_markdown_maps_hana_getsu_subheader_and_escaped_pipe():
    master = config_service.load_facility_master()
    base_template = master.get("fax_template_base", {})
    facility = next(
        fac for fac in master.get("facilities", []) if fac.get("facility_id") == "FAC00003"
    )
    template = facility.get("fax_template") or config_service._merge_template(
        base_template,
        facility.get("fax_template_override"),
    )

    markdown = """
|日 付||区 分|献立|常食||軟菜||ミキサー||魚焼\\(常食\\)|備考欄|
|-|-|-|-|-|-|-|-|-|-|-|-|
|||||花|月|花|月|花|月|||
|2/15||朝|じゃが芋のコンソメ煮|8|6|||2|3|||
|||夕|\\|鶏肉のケチャップ炒め|8|7|||2|3|||
""".strip()
    rows = rows_from_markdown(markdown, template)
    assert isinstance(rows, list)
    assert len(rows) == 2

    first = rows[0]
    assert first[2] == "じゃが芋のコンソメ煮"
    assert first[3] == "8"
    assert first[4] == "6"
    assert first[7] == "2"
    assert first[8] == "3"

    second = rows[1]
    assert second[2] == "|鶏肉のケチャップ炒め"
    assert second[3] == "8"
    assert second[4] == "7"
    assert second[7] == "2"
    assert second[8] == "3"


def test_merge_header_rows_keeps_forbidden_split_markers_unprefixed():
    merged = fax_extractor._merge_header_rows(
        ["日付", "区分", "献立", "常食", "", "ミキサー", "", "", "", "備考欄"],
        ["", "", "", "2F", "3F", "2F", "3F", "肉禁", "魚禁", ""],
    )

    assert merged[7] == "肉禁"
    assert merged[8] == "魚禁"


def test_merge_header_rows_avoids_duplicate_forbidden_parent_and_change_prefix():
    merged = fax_extractor._merge_header_rows(
        ["日付", "区分", "メニュー", "常食", "禁食", "", "変更1", "", "備考欄"],
        ["", "", "", "", "禁食", "", "", "変更2", ""],
    )

    assert merged[4] == "禁食"
    assert merged[7] == "変更2"


def test_rows_from_markdown_maps_split_forbidden_subheaders_without_parent_group():
    template = {
        "main_ocr_row_fields": [
            "date_mmdd",
            "daypart",
            "menu",
            "qty.regular_2f",
            "qty.regular_3f",
            "qty.mixer_2f",
            "qty.mixer_3f",
            "qty.no_meat_x",
            "qty.no_fish_x",
            "remarks",
        ]
    }
    markdown = """
|日付|区分|献立|常食||ミキサー||||備考欄|
|-|-|-|-|-|-|-|-|-|-|
||||2F|3F|2F|3F|肉禁|魚禁||
|2/15|朝|Menu A|8|6|1|2|3|4||
""".strip()

    rows = rows_from_markdown(markdown, template)

    assert rows == [["2/15", "朝", "Menu A", "8", "6", "1", "2", "3", "4", ""]]


def test_rows_from_markdown_maps_three_row_forbidden_split_headers_and_change2():
    template = {
        "main_ocr_row_fields": [
            "date_mmdd",
            "daypart",
            "menu",
            "qty.regular_x",
            "qty.no_meat_x",
            "qty.no_fish_x",
            "qty.change_1_x",
            "qty.change_2_x",
            "remarks",
        ]
    }
    markdown = """
|日付|区分|メニュー|常食|禁食|||変更1||備考欄|
|-|-|-|-|-|-|-|-|-|-|
|||||禁食||||変更2||
|||||肉禁|魚禁||||
|2/15|朝|Menu A|20||4|5|6|7||
""".strip()

    rows = rows_from_markdown(markdown, template)

    assert rows == [["2/15", "朝", "Menu A", "20", "4", "5", "6", "7", ""]]


def test_rows_from_markdown_infers_columns_from_sparse_header():
    master = config_service.load_facility_master()
    base_template = master.get("fax_template_base", {})
    facility = next(
        fac for fac in master.get("facilities", []) if fac.get("facility_id") == "FAC00013"
    )
    template = facility.get("fax_template") or config_service._merge_template(
        base_template,
        facility.get("fax_template_override"),
    )

    markdown = """
||||||変更の|変更(2)|備考|
|-|-|-|-|-|-|-|-|-|
||区分||献立|常危|糖尿||||
|2/15|朝||じゃが芋のコンソメ煮|20|4||||
|2/16|昼||キャベツサラダ|18|3||||
""".strip()

    rows = rows_from_markdown(markdown, template)
    assert isinstance(rows, list)
    assert len(rows) == 2

    first = rows[0]
    assert first[0] == "2/15"
    assert first[1] == "朝"
    assert first[2] == "じゃが芋のコンソメ煮"
    assert first[3] == "20"
    assert first[4] == "4"

    second = rows[1]
    assert second[0] == "2/16"
    assert second[1] == "昼"
    assert second[2] == "キャベツサラダ"
    assert second[3] == "18"
    assert second[4] == "3"


def test_apply_order_line_quantities_by_source_row_index_fallback():
    fields = [
        "date_mmdd",
        "daypart",
        "menu",
        "qty.regular_2f",
        "qty.regular_3f",
        "qty.soft_2f",
        "qty.soft_3f",
        "qty.mixer_2f",
        "qty.mixer_3f",
        "remarks",
    ]
    quantity_index = order_service._build_sheet_quantity_index(fields)
    rows = [
        {"row_id": "r0", "values": ["", "", "Menu 0", "", "", "", "", "", "", ""]},
        {"row_id": "r1", "values": ["", "", "Menu 1", "", "", "", "", "", "", ""]},
    ]
    order_lines = [
        {
            "source_row_index": 1,
            "diet_type": "regular",
            "area_id": "2F",
            "quantity_original": 5,
            "quantity_corrected": None,
            "change_note": None,
        }
    ]

    order_service._apply_order_line_quantities_by_source_row_index(
        rows=rows,
        fields=fields,
        quantity_index=quantity_index,
        order_lines=order_lines,
    )
    assert rows[1]["values"][3] == "5"


def test_count_source_row_alignment_penalty_cells_counts_shifted_rows():
    fields = [
        "date_mmdd",
        "daypart",
        "menu",
        "qty.regular_x",
        "remarks",
    ]
    quantity_index = order_service._build_sheet_quantity_index(fields)
    base_rows = [
        {
            "row_id": "r0",
            "values": ["01/08", "朝", "Menu A", "", ""],
            "identity": order_service._sheet_row_identity(date(2026, 1, 8), "朝", "Menu A"),
        },
        {
            "row_id": "r1",
            "values": ["01/09", "朝", "Menu B", "", ""],
            "identity": order_service._sheet_row_identity(date(2026, 1, 9), "朝", "Menu B"),
        },
    ]
    rows_by_source_index = [
        {"row_id": "r0", "values": ["01/08", "朝", "Menu A", "6", ""]},
        {"row_id": "r1", "values": ["01/09", "朝", "Menu B", "5", ""]},
    ]
    order_lines = [
        {
            "date": date(2026, 1, 8),
            "daypart": "朝",
            "menu_name": "Menu A",
            "diet_type": "regular",
            "area_id": "X",
            "quantity_original": 5,
            "quantity_corrected": None,
            "source_row_index": 1,
        },
        {
            "date": date(2026, 1, 9),
            "daypart": "朝",
            "menu_name": "Menu B",
            "diet_type": "regular",
            "area_id": "X",
            "quantity_original": 6,
            "quantity_corrected": None,
            "source_row_index": 0,
        },
    ]

    penalty = order_service._count_source_row_alignment_penalty_cells(
        base_rows=base_rows,
        rows_by_source_index=rows_by_source_index,
        fields=fields,
        quantity_index=quantity_index,
        order_lines=order_lines,
    )

    assert penalty == 2


def test_apply_order_line_quantities_defaults_missing_area_to_x():
    fields = [
        "date_mmdd",
        "daypart",
        "menu",
        "qty.regular_x",
        "qty.soft_x",
        "qty.mixer_x",
        "remarks",
    ]
    quantity_index = order_service._build_sheet_quantity_index(fields)
    rows = [
        {
            "row_id": "r0",
            "values": ["02/16", "朝", "ごった煮", "", "", "", ""],
            "identity": order_service._sheet_row_identity(date(2026, 2, 16), "朝", "ごった煮"),
        }
    ]
    order_lines = [
        {
            "date": date(2026, 2, 16),
            "daypart": "朝",
            "menu_name": "ごった煮",
            "diet_type": "regular",
            "area_id": None,
            "quantity_original": 20,
            "quantity_corrected": None,
            "change_note": None,
        }
    ]

    order_service._apply_order_line_quantities_to_sheet_rows(
        rows=rows,
        fields=fields,
        quantity_index=quantity_index,
        order_lines=order_lines,
    )
    assert rows[0]["values"][3] == "20"


def test_apply_order_line_quantities_does_not_fallback_date_menu_when_daypart_field_exists():
    fields = [
        "date_mmdd",
        "daypart",
        "menu",
        "qty.regular_2f",
        "remarks",
    ]
    quantity_index = order_service._build_sheet_quantity_index(fields)
    rows = [
        {
            "row_id": "r0",
            "values": ["02/16", "朝", "ごった煮", "", ""],
            "identity": order_service._sheet_row_identity(date(2026, 2, 16), "朝", "ごった煮"),
        }
    ]
    order_lines = [
        {
            "date": date(2026, 2, 16),
            "daypart": "昼",
            "menu_name": "ごった煮",
            "diet_type": "regular",
            "area_id": "2F",
            "quantity_original": 20,
            "quantity_corrected": None,
            "change_note": None,
        }
    ]

    order_service._apply_order_line_quantities_to_sheet_rows(
        rows=rows,
        fields=fields,
        quantity_index=quantity_index,
        order_lines=order_lines,
    )
    assert rows[0]["values"][3] == ""


def test_apply_order_line_quantities_fallbacks_date_menu_when_daypart_field_absent():
    fields = [
        "date_mmdd",
        "menu",
        "qty.regular_2f",
        "remarks",
    ]
    quantity_index = order_service._build_sheet_quantity_index(fields)
    rows = [
        {
            "row_id": "r0",
            "values": ["02/16", "ごった煮", "", ""],
            "identity": order_service._sheet_row_identity(date(2026, 2, 16), "", "ごった煮"),
        }
    ]
    order_lines = [
        {
            "date": date(2026, 2, 16),
            "daypart": "昼",
            "menu_name": "ごった煮",
            "diet_type": "regular",
            "area_id": "2F",
            "quantity_original": 20,
            "quantity_corrected": None,
            "change_note": None,
        }
    ]

    order_service._apply_order_line_quantities_to_sheet_rows(
        rows=rows,
        fields=fields,
        quantity_index=quantity_index,
        order_lines=order_lines,
    )
    assert rows[0]["values"][2] == "20"


def test_apply_order_line_quantities_preserves_zero_value():
    fields = [
        "date_mmdd",
        "daypart",
        "menu",
        "qty.regular_2f",
        "remarks",
    ]
    quantity_index = order_service._build_sheet_quantity_index(fields)
    rows = [
        {
            "row_id": "r0",
            "values": ["02/16", "朝", "ごった煮", "", ""],
            "identity": order_service._sheet_row_identity(date(2026, 2, 16), "朝", "ごった煮"),
        }
    ]
    order_lines = [
        {
            "date": date(2026, 2, 16),
            "daypart": "朝",
            "menu_name": "ごった煮",
            "diet_type": "regular",
            "area_id": "2F",
            "quantity_original": 0,
            "quantity_corrected": None,
            "change_note": None,
        }
    ]

    order_service._apply_order_line_quantities_to_sheet_rows(
        rows=rows,
        fields=fields,
        quantity_index=quantity_index,
        order_lines=order_lines,
    )
    assert rows[0]["values"][3] == "0"


def test_collect_missing_weekly_menu_dates_detects_intermediate_gap():
    entries = [
        {"menu_date": date(2099, 12, 26)},
        {"menu_date": date(2099, 12, 27)},
        {"menu_date": date(2099, 12, 28)},
    ]
    rows = [
        {"identity": ("2099-12-26", "朝", "Menu A")},
        {"identity": ("2099-12-28", "朝", "Menu C")},
    ]
    line_dates = {date(2099, 12, 26), date(2099, 12, 28)}
    missing = order_service._collect_missing_weekly_menu_dates(
        entries=entries,
        rows=rows,
        line_dates=line_dates,
    )
    assert missing == [date(2099, 12, 27)]


def test_get_ocr_sheet_returns_warning_when_quantity_column_unmapped():
    order_service.clear_all()
    _seed_monthly_menu_daypart_order_2099_11()
    payload = IngestEmailPayload(
        message_id="msg-sheet-unmapped-area-001",
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2099, 11, 15, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2099-11",
    )
    lines = [
        {
            "date": "2099-11-15",
            "daypart": "朝",
            "menu_name": "朝メニュー",
            "diet_type": "regular",
            "area_id": "9F",
            "bag_type": "standard",
            "quantity_original": 5,
        }
    ]
    order = order_service.create_order_from_ingest(payload, lines=lines)

    original_get = config_service.get_facility_config

    def _mock_get(facility_id: str):
        current = original_get(facility_id)
        if not current or facility_id != "FAC00001":
            return current
        payload_cfg = dict(current)
        template = dict(payload_cfg.get("fax_template") or {})
        template["main_ocr_row_fields"] = [
            "date_mmdd",
            "daypart",
            "menu",
            "qty.regular_2f",
            "qty.regular_3f",
            "qty.soft_2f",
            "qty.soft_3f",
            "qty.mixer_2f",
            "qty.mixer_3f",
            "remarks",
        ]
        payload_cfg["fax_template"] = template
        return payload_cfg

    config_service.get_facility_config = _mock_get
    try:
        sheet, error = order_service.get_ocr_sheet(order["id"])
        assert error is None
        assert sheet is not None
        assert "sheet_quantity_column_unmapped" in (sheet.get("warnings") or [])
        fields = sheet["fields"]
        qty_indexes = [idx for idx, field in enumerate(fields) if str(field).startswith("qty.")]
        date_idx = next((idx for idx, field in enumerate(fields) if str(field).startswith("date")), 0)
        daypart_idx = fields.index("daypart")
        menu_idx = fields.index("menu")
        breakfast = next(
            row
            for row in sheet["rows"]
            if row[date_idx] == "11/15" and row[daypart_idx] == "朝" and row[menu_idx] == "朝メニュー"
        )
        assert all((breakfast[idx] or "") == "" for idx in qty_indexes)
    finally:
        config_service.get_facility_config = original_get


def test_get_ocr_sheet_returns_warning_when_area_is_missing_for_split_quantity_columns():
    order_service.clear_all()
    _seed_monthly_menu_daypart_order_2099_11()
    payload = IngestEmailPayload(
        message_id="msg-sheet-unmapped-missing-area-001",
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2099, 11, 15, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2099-11",
    )
    lines = [
        {
            "date": "2099-11-15",
            "daypart": "朝",
            "menu_name": "朝メニュー",
            "diet_type": "regular",
            "area_id": None,
            "bag_type": "standard",
            "quantity_original": 8,
        }
    ]
    order = order_service.create_order_from_ingest(payload, lines=lines)

    original_get = config_service.get_facility_config

    def _mock_get(facility_id: str):
        current = original_get(facility_id)
        if not current or facility_id != "FAC00001":
            return current
        payload_cfg = dict(current)
        template = dict(payload_cfg.get("fax_template") or {})
        template["main_ocr_row_fields"] = [
            "date_mmdd",
            "daypart",
            "menu",
            "qty.regular_2f",
            "qty.regular_3f",
            "qty.soft_2f",
            "qty.soft_3f",
            "qty.mixer_2f",
            "qty.mixer_3f",
            "remarks",
        ]
        payload_cfg["fax_template"] = template
        return payload_cfg

    config_service.get_facility_config = _mock_get
    try:
        sheet, error = order_service.get_ocr_sheet(order["id"])
        assert error is None
        assert sheet is not None
        assert "sheet_quantity_column_unmapped" in (sheet.get("warnings") or [])
    finally:
        config_service.get_facility_config = original_get


def test_get_ocr_sheet_weekly_menu_falls_back_to_payload_numeric_when_order_lines_unmapped():
    order_service.clear_all()
    _seed_monthly_menu_daypart_order_2099_11()
    payload = IngestEmailPayload(
        message_id="msg-sheet-unmapped-fallback-001",
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2099, 11, 15, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2099-11",
    )
    lines = [
        {
            "date": "2099-11-15",
            "daypart": "朝",
            "menu_name": "朝メニュー",
            "diet_type": "regular",
            "area_id": None,
            "bag_type": "standard",
            "quantity_original": 12,
        }
    ]
    order = order_service.create_order_from_ingest(payload, lines=lines)
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "table_rows": [
                ["11/15", "朝", "朝メニュー", "9", "", "", "", "", "", ""],
            ],
        },
    )

    original_get = config_service.get_facility_config

    def _mock_get(facility_id: str):
        current = original_get(facility_id)
        if not current or facility_id != "FAC00001":
            return current
        payload_cfg = dict(current)
        template = dict(payload_cfg.get("fax_template") or {})
        template["main_ocr_row_fields"] = [
            "date_mmdd",
            "daypart",
            "menu",
            "qty.regular_2f",
            "qty.regular_3f",
            "qty.soft_2f",
            "qty.soft_3f",
            "qty.mixer_2f",
            "qty.mixer_3f",
            "remarks",
        ]
        payload_cfg["fax_template"] = template
        return payload_cfg

    config_service.get_facility_config = _mock_get
    try:
        sheet, error = order_service.get_ocr_sheet(order["id"])
        assert error is None
        assert sheet is not None
        assert sheet["source"] == "weekly_menu+ocr_payload"
        assert "sheet_order_lines_unmapped_fallback_payload" in (sheet.get("warnings") or [])
        fields = sheet["fields"]
        qty_idx = fields.index("qty.regular_2f")
        daypart_idx = fields.index("daypart")
        menu_idx = fields.index("menu")
        date_idx = next((idx for idx, field in enumerate(fields) if field.startswith("date")), 0)
        breakfast = next(
            row
            for row in sheet["rows"]
            if row[date_idx] == "11/15" and row[daypart_idx] == "朝" and row[menu_idx] == "朝メニュー"
        )
        assert breakfast[qty_idx] == "9"
    finally:
        config_service.get_facility_config = original_get


def test_get_ocr_sheet_weekly_menu_prefers_payload_when_order_lines_have_stale_quantity_family():
    order_service.clear_all()
    _seed_monthly_menu_daypart_order_2099_11()
    payload = IngestEmailPayload(
        message_id="msg-sheet-unmapped-stale-family-001",
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2099, 11, 15, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2099-11",
    )
    lines = [
        {
            "date": "2099-11-15",
            "daypart": "朝",
            "menu_name": "朝メニュー",
            "diet_type": "regular",
            "area_id": "X",
            "bag_type": "standard",
            "quantity_original": 20,
        },
        {
            "date": "2099-11-15",
            "daypart": "朝",
            "menu_name": "朝メニュー",
            "diet_type": "staff",
            "area_id": "X",
            "bag_type": "standard",
            "quantity_original": 4,
        },
        {
            "date": "2099-11-15",
            "daypart": "朝",
            "menu_name": "朝メニュー",
            "diet_type": "business",
            "area_id": "X",
            "bag_type": "standard",
            "quantity_original": 5,
        },
        {
            "date": "2099-11-15",
            "daypart": "朝",
            "menu_name": "朝メニュー",
            "diet_type": "no_meat",
            "area_id": "X",
            "bag_type": "standard",
            "quantity_original": 6,
        },
        {
            "date": "2099-11-15",
            "daypart": "朝",
            "menu_name": "朝メニュー",
            "diet_type": "no_fish",
            "area_id": "X",
            "bag_type": "standard",
            "quantity_original": 7,
        },
        {
            "date": "2099-11-15",
            "daypart": "朝",
            "menu_name": "朝メニュー",
            "diet_type": "sesame_allergy",
            "area_id": "X",
            "bag_type": "standard",
            "quantity_original": 8,
        },
        {
            "date": "2099-11-15",
            "daypart": "朝",
            "menu_name": "朝メニュー",
            "diet_type": "change_1",
            "area_id": "X",
            "bag_type": "standard",
            "quantity_original": 9,
        },
    ]
    order = order_service.create_order_from_ingest(payload, lines=lines)
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "table_rows": [
                ["11/15", "朝", "朝メニュー", "20", "4", "6", "7", "8", "9", ""],
            ],
        },
    )

    original_get = config_service.get_facility_config

    def _mock_get(facility_id: str):
        current = original_get(facility_id)
        if not current or facility_id != "FAC00001":
            return current
        payload_cfg = dict(current)
        template = dict(payload_cfg.get("fax_template") or {})
        template["main_ocr_row_fields"] = [
            "date_mmdd",
            "daypart",
            "menu",
            "qty.regular_x",
            "qty.staff_x",
            "qty.no_meat_x",
            "qty.no_fish_x",
            "qty.sesame_allergy_x",
            "qty.change_1_x",
            "remarks",
        ]
        payload_cfg["fax_template"] = template
        return payload_cfg

    config_service.get_facility_config = _mock_get
    try:
        sheet, error = order_service.get_ocr_sheet(order["id"])
        assert error is None
        assert sheet is not None
        assert sheet["source"] == "weekly_menu+ocr_payload"
        assert "sheet_order_lines_unmapped_fallback_payload" in (sheet.get("warnings") or [])
        assert "sheet_quantity_column_unmapped" not in (sheet.get("warnings") or [])
        fields = sheet["fields"]
        assert fields == [
            "date_mmdd",
            "daypart",
            "menu",
            "qty.regular_x",
            "qty.staff_x",
            "qty.no_meat_x",
            "qty.no_fish_x",
            "qty.sesame_allergy_x",
            "qty.change_1_x",
            "remarks",
        ]
        breakfast = sheet["rows"][0]
        assert breakfast[3:9] == ["20", "4", "6", "7", "8", "9"]
    finally:
        config_service.get_facility_config = original_get


def test_get_ocr_sheet_prefers_identity_over_payload_row_index_tie_without_menu_signal(monkeypatch):
    order_service.clear_all()
    payload = IngestEmailPayload(
        message_id="msg-sheet-payload-rowindex-tie-001",
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2026, 1, 8, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2026-01",
    )
    order = order_service.create_order_from_ingest(payload, lines=[])
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "table_rows": [
                ["", "", "", "6", ""],
                ["", "", "", "5", ""],
            ]
        },
    )

    def _fake_build_sheet_menu_entries(**_kwargs):
        return ([{"row": 0}, {"row": 1}], "ocr_table")

    def _fake_build_rows_from_menu_entries(**kwargs):
        fields = kwargs["fields"]
        field_index = kwargs["field_index"]
        rows = []
        for row_id, menu_date, mmdd, menu_name in (
            ("row-1", date(2026, 1, 8), "01/08", "Menu A"),
            ("row-2", date(2026, 1, 9), "01/09", "Menu B"),
        ):
            values = [""] * len(fields)
            values[field_index["date_mmdd"]] = mmdd
            values[field_index["daypart"]] = "朝"
            values[field_index["menu"]] = menu_name
            rows.append(
                {
                    "row_id": row_id,
                    "values": values,
                    "identity": order_service._sheet_row_identity(menu_date, "朝", menu_name),
                }
            )
        return rows, "ocr_table"

    def _fake_build_sheet_lines_from_ocr_payload(**_kwargs):
        return [
            {
                "date": date(2026, 1, 8),
                "daypart": "朝",
                "menu_name": "Menu A",
                "diet_type": "regular",
                "area_id": "2F",
                "quantity_original": 5,
            },
            {
                "date": date(2026, 1, 9),
                "daypart": "朝",
                "menu_name": "Menu B",
                "diet_type": "regular",
                "area_id": "2F",
                "quantity_original": 6,
            },
        ]

    monkeypatch.setattr(order_service, "_build_sheet_menu_entries", _fake_build_sheet_menu_entries)
    monkeypatch.setattr(order_service, "_build_rows_from_menu_entries", _fake_build_rows_from_menu_entries)
    monkeypatch.setattr(order_service, "_build_sheet_lines_from_ocr_payload", _fake_build_sheet_lines_from_ocr_payload)

    sheet, error = order_service.get_ocr_sheet(order["id"])

    assert error is None
    assert sheet is not None
    assert sheet["trace"]["mapped_mode"] == "identity"
    menu_idx = sheet["fields"].index("menu")
    qty_idx = sheet["fields"].index("qty.regular_2f")
    actual = {row[menu_idx]: row[qty_idx] for row in sheet["rows"]}
    assert actual["Menu A"] == "5"
    assert actual["Menu B"] == "6"


def test_resolve_sheet_week_prefers_ocr_month_over_stale_hints():
    received_at = datetime(2026, 2, 3, 9, 0, 0)
    payload = {
        "table_raw": """
|日付|区分|献立|常|
|-|-|-|-|
|2/15|朝|A|10|
""".strip()
    }
    resolved = order_service._resolve_sheet_week_id(
        current_week_id=None,
        received_at=received_at,
        order_lines=[],
        ocr_payload=payload,
        facility_id="FAC00001",
        week_hints=["2025-12"],
    )
    assert resolved == "2026-02"


def test_resolve_sheet_week_ignores_far_ocr_month_and_old_hint():
    received_at = datetime(2026, 2, 13, 9, 0, 0)
    payload = {
        "table_raw": """
|日付|区分|献立|常|
|-|-|-|-|
|12/26|朝|A|10|
""".strip()
    }

    original_builder = order_service._build_position_menu_entries

    def _mock_build_position_menu_entries(month_id: str):
        if month_id == "2026-02":
            return [
                {
                    "menu_name": "A",
                    "menu_date": date(2026, 2, 26),
                    "daypart_key": "朝",
                    "slot_index": 0,
                    "order": 0,
                }
            ]
        if month_id == "2025-12":
            return [
                {
                    "menu_name": "A",
                    "menu_date": date(2025, 12, 26),
                    "daypart_key": "朝",
                    "slot_index": 0,
                    "order": 0,
                }
            ]
        return []

    order_service._build_position_menu_entries = _mock_build_position_menu_entries
    try:
        resolved = order_service._resolve_sheet_week_id(
            current_week_id=None,
            received_at=received_at,
            order_lines=[],
            ocr_payload=payload,
            facility_id="FAC00001",
            week_hints=["2025-12"],
        )
        assert resolved == "2026-02"
    finally:
        order_service._build_position_menu_entries = original_builder


def test_resolve_sheet_week_extends_explicit_range_when_ocr_dates_run_longer():
    received_at = datetime(2026, 3, 23, 9, 0, 0)
    payload = {
        "table_raw": """
|日付|区分|献立|常|
|-|-|-|-|
|3/22|朝|A|10|
|3/23|朝|B|10|
|3/24|朝|C|10|
|3/25|朝|D|10|
|3/26|朝|E|10|
|3/27|朝|F|10|
|3/28|朝|G|10|
|3/29|朝|H|10|
""".strip()
    }
    resolved = order_service._resolve_sheet_week_id(
        current_week_id="2026-03@2026-03-22~2026-03-28",
        received_at=received_at,
        order_lines=[],
        ocr_payload=payload,
        facility_id="FAC00003",
        week_hints=[],
    )
    assert resolved == "2026-03@2026-03-22~2026-03-29"


def test_collect_sheet_dates_ignores_footer_timestamp_when_table_dates_exist():
    payload = {
        "table_raw": """
|日付|区分|献立|常|
|-|-|-|-|
|2/15|朝|A|10|
|2/16|朝|B|11|
OV:LL 08/10/920Z
""".strip()
    }
    received_at = datetime(2026, 2, 13, 9, 0, 0)
    dates = order_service._collect_sheet_dates_from_payload(payload, received_at)
    assert len(dates) >= 2
    assert all(item.month == 2 for item in dates)


def test_collect_sheet_dates_infers_weekday_hint_beyond_legacy_row_scan_window():
    received_at = datetime(2026, 3, 23, 9, 0, 0)
    payload = {
        "table_rows": [
            ["3/22", "朝", "A", "10"],
            ["3/23", "朝", "B", "10"],
            ["3/24", "朝", "C", "10"],
            ["3/25", "朝", "D", "10"],
            ["3/26", "朝", "E", "10"],
        ]
        + [["", "", "", ""] for _ in range(36)]
        + [["LZ/E\n(金)", "", "F", "10"]]
    }

    dates = order_service._collect_sheet_dates_from_payload(payload, received_at)

    assert date(2026, 3, 27) in dates


def test_collect_sheet_dates_reads_structured_table_rows_for_weekday_hint():
    received_at = datetime(2026, 3, 23, 9, 0, 0)
    payload = {
        "tables": [
            {
                "table_id": "t1",
                "page_index": 1,
                "rows": [
                    ["3/26\n(木)", "朝", "ごぼうと竹輪の煮物", "7"],
                    ["", "", "いんげんの味噌和え", "7"],
                    ["LZ/E\n(金)", "朝", "豆腐と大根の含め煮", "7"],
                    ["", "", "白菜のおかか和え", "7"],
                ],
            }
        ]
    }

    dates = order_service._collect_sheet_dates_from_payload(payload, received_at)

    assert date(2026, 3, 26) in dates
    assert date(2026, 3, 27) in dates


def test_build_position_menu_entries_from_ocr_payload_infers_noisy_weekday_anchor():
    template = {
        "main_ocr_row_fields": ["date_mmdd", "daypart", "menu", "qty.regular_x"],
        "large_cell_mode": True,
    }
    payload = {
        "table_rows": [
            ["3/26\n(木)", "朝", "ごぼうと竹輪の煮物", "7"],
            ["", "", "いんげんの味噌和え", "7"],
            ["LZ/E\n(金)", "朝", "豆腐と大根の含め煮", "7"],
            ["", "", "白菜のおかか和え", "7"],
        ]
    }

    entries = order_service._build_position_menu_entries_from_ocr_payload(
        payload=payload,
        template=template,
        received_at=datetime(2026, 3, 23, 9, 0, 0),
    )

    dates = sorted({item.get("menu_date") for item in entries if item.get("menu_date")})
    assert dates == [date(2026, 3, 26), date(2026, 3, 27)]


def test_merge_weekly_menu_entries_with_ocr_tail_appends_out_of_range_days_only():
    weekly_entries = [
        {"menu_date": date(2026, 3, 22), "daypart_key": "朝", "menu_name": "A", "slot_index": 0, "order": 0},
        {"menu_date": date(2026, 3, 26), "daypart_key": "夕", "menu_name": "B", "slot_index": 1, "order": 1},
    ]
    ocr_entries = [
        {"menu_date": date(2026, 3, 26), "daypart_key": "夕", "menu_name": "B", "slot_index": 1, "order": 1},
        {"menu_date": date(2026, 3, 27), "daypart_key": "朝", "menu_name": "C", "slot_index": 2, "order": 2},
    ]

    merged = order_service._merge_weekly_menu_entries_with_ocr_tail(weekly_entries, ocr_entries)

    assert [item.get("menu_date") for item in merged] == [
        date(2026, 3, 22),
        date(2026, 3, 26),
        date(2026, 3, 27),
    ]
    assert [item.get("menu_name") for item in merged] == ["A", "B", "C"]


def test_get_ocr_sheet_without_facility_returns_error():
    order_service.clear_all()
    order = _seed_order_without_facility(message_id="msg-sheet-no-fac-001")

    sheet, error = order_service.get_ocr_sheet(order["id"])
    assert sheet is None
    assert error == "facility_missing"


def test_apply_ocr_table_without_facility_returns_error():
    order_service.clear_all()
    order = _seed_order_without_facility(message_id="msg-sheet-no-fac-002")
    fields = [
        "date_mmdd",
        "daypart",
        "menu",
        "qty.regular_2f",
        "qty.regular_3f",
        "qty.soft_2f",
        "qty.soft_3f",
        "qty.mixer_2f",
        "qty.mixer_3f",
        "remarks",
    ]
    header = ["日付", "区分", "メニュー", "常食2F", "常食3F", "軟菜2F", "軟菜3F", "ミキサー2F", "ミキサー3F", "備考"]
    rows = [["02/15", "朝", "Menu B", "7", "", "", "", "", "", "manual"]]

    updated, error = order_service.apply_ocr_table(
        order["id"],
        header=header,
        rows=rows,
        ui_mode="sheet",
        fields=fields,
        row_ids=["row-no-fac-1"],
    )
    assert updated is None
    assert error == "facility_missing"


def test_get_ocr_sheet_without_order_lines_keeps_weekly_menu_dates():
    order_service.clear_all()
    with session_scope() as session:
        session.execute(delete(MonthlyMenuEntry).where(MonthlyMenuEntry.monthly_menu_id == "2099-12"))
        session.execute(delete(MonthlyMenu).where(MonthlyMenu.id == "2099-12"))
        session.add(
            MonthlyMenu(
                id="2099-12",
                month_start=date(2099, 12, 1),
                filename="seed-2099-12.xlsx",
            )
        )
        session.add_all(
            [
                MonthlyMenuEntry(
                    id="seed-entry-2099-12-26-breakfast-a",
                    monthly_menu_id="2099-12",
                    menu_date=date(2099, 12, 26),
                    daypart="朝食",
                    name="Menu A",
                    slot_index=0,
                ),
                MonthlyMenuEntry(
                    id="seed-entry-2099-12-27-breakfast-b",
                    monthly_menu_id="2099-12",
                    menu_date=date(2099, 12, 27),
                    daypart="朝食",
                    name="Menu B",
                    slot_index=0,
                ),
            ]
        )
    payload = IngestEmailPayload(
        message_id="msg-sheet-payload-dates-001",
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2099, 12, 26, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint=None,
    )
    order = order_service.create_order_from_ingest(payload, lines=[])
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "table_rows": [
                ["12/26", "朝", "Menu A", "7", "", "", "", "", "", ""],
                ["12/27", "朝", "Menu B", "", "", "", "", "", "", ""],
            ]
        },
    )

    sheet, error = order_service.get_ocr_sheet(order["id"])
    assert error is None
    assert sheet is not None
    assert sheet["source"] == "weekly_menu+ocr_payload"
    dates = {row[0] for row in sheet["rows"] if row and row[0]}
    assert "12/26" in dates
    assert "12/27" in dates


def test_get_ocr_sheet_respects_explicit_selected_week_range():
    order_service.clear_all()
    with session_scope() as session:
        session.execute(delete(MonthlyMenuEntry).where(MonthlyMenuEntry.monthly_menu_id == "2099-12"))
        session.execute(delete(MonthlyMenu).where(MonthlyMenu.id == "2099-12"))
        session.add(
            MonthlyMenu(
                id="2099-12",
                month_start=date(2099, 12, 1),
                filename="seed-2099-12.xlsx",
            )
        )
        session.add_all(
            [
                MonthlyMenuEntry(
                    id="seed-entry-2099-12-26-breakfast-a-range",
                    monthly_menu_id="2099-12",
                    menu_date=date(2099, 12, 26),
                    daypart="朝食",
                    name="Menu A",
                    slot_index=0,
                ),
                MonthlyMenuEntry(
                    id="seed-entry-2099-12-27-breakfast-b-range",
                    monthly_menu_id="2099-12",
                    menu_date=date(2099, 12, 27),
                    daypart="朝食",
                    name="Menu B",
                    slot_index=0,
                ),
                MonthlyMenuEntry(
                    id="seed-entry-2099-12-28-breakfast-c-range",
                    monthly_menu_id="2099-12",
                    menu_date=date(2099, 12, 28),
                    daypart="朝食",
                    name="Menu C",
                    slot_index=0,
                ),
            ]
        )
    payload = IngestEmailPayload(
        message_id="msg-sheet-week-range-001",
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2099, 12, 26, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2099-12",
    )
    order = order_service.create_order_from_ingest(payload, lines=[])
    assert order_service.set_week(order["id"], "2099-12@2099-12-26~2099-12-27") is True

    sheet, error = order_service.get_ocr_sheet(order["id"])
    assert error is None
    assert sheet is not None
    assert sheet["week_id"] == "2099-12@2099-12-26~2099-12-27"
    dates = [row[0] for row in sheet["rows"] if row and row[0]]
    assert dates == ["12/26", "12/27"]


def test_get_ocr_sheet_without_order_lines_filters_weekly_menu_by_payload_dates():
    order_service.clear_all()
    with session_scope() as session:
        session.execute(delete(MonthlyMenuEntry).where(MonthlyMenuEntry.monthly_menu_id == "2099-02"))
        session.execute(delete(MonthlyMenu).where(MonthlyMenu.id == "2099-02"))
        session.add(
            MonthlyMenu(
                id="2099-02",
                month_start=date(2099, 2, 1),
                filename="seed-2099-02.xlsx",
            )
        )
        session.add_all(
            [
                MonthlyMenuEntry(
                    id="seed-entry-2099-02-01-breakfast-a",
                    monthly_menu_id="2099-02",
                    menu_date=date(2099, 2, 1),
                    daypart="朝食",
                    name="Menu 01",
                    slot_index=0,
                ),
                MonthlyMenuEntry(
                    id="seed-entry-2099-02-08-breakfast-a",
                    monthly_menu_id="2099-02",
                    menu_date=date(2099, 2, 8),
                    daypart="朝食",
                    name="Menu 08",
                    slot_index=0,
                ),
                MonthlyMenuEntry(
                    id="seed-entry-2099-02-09-breakfast-a",
                    monthly_menu_id="2099-02",
                    menu_date=date(2099, 2, 9),
                    daypart="朝食",
                    name="Menu 09",
                    slot_index=0,
                ),
                MonthlyMenuEntry(
                    id="seed-entry-2099-02-10-breakfast-a",
                    monthly_menu_id="2099-02",
                    menu_date=date(2099, 2, 10),
                    daypart="朝食",
                    name="Menu 10",
                    slot_index=0,
                ),
            ]
        )
    payload = IngestEmailPayload(
        message_id="msg-sheet-payload-filter-001",
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2099, 2, 10, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint=None,
    )
    order = order_service.create_order_from_ingest(payload, lines=[])
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "table_rows": [
                ["2/8", "朝", "Menu 08", "5", "", "", "", "", "", ""],
                ["2/9", "朝", "Menu 09", "6", "", "", "", "", "", ""],
                ["2/10", "朝", "Menu 10", "7", "", "", "", "", "", ""],
            ]
        },
    )

    sheet, error = order_service.get_ocr_sheet(order["id"])
    assert error is None
    assert sheet is not None
    assert sheet["source"] == "weekly_menu+ocr_payload"

    date_idx = next((idx for idx, field in enumerate(sheet["fields"]) if field.startswith("date")), 0)
    dates = {row[date_idx] for row in sheet["rows"] if row and date_idx < len(row) and row[date_idx]}
    assert dates == {"02/08", "02/09", "02/10"}


def test_get_ocr_sheet_without_date_anchors_scopes_by_payload_row_count():
    order_service.clear_all()
    with session_scope() as session:
        session.execute(delete(MonthlyMenuEntry).where(MonthlyMenuEntry.monthly_menu_id == "2099-03"))
        session.execute(delete(MonthlyMenu).where(MonthlyMenu.id == "2099-03"))
        session.add(
            MonthlyMenu(
                id="2099-03",
                month_start=date(2099, 3, 1),
                filename="seed-2099-03.xlsx",
            )
        )
        entries = []
        for day in range(1, 29):
            entries.append(
                MonthlyMenuEntry(
                    id=f"seed-entry-2099-03-{day:02d}-breakfast-a",
                    monthly_menu_id="2099-03",
                    menu_date=date(2099, 3, day),
                    daypart="朝食",
                    name=f"Menu {day:02d}",
                    slot_index=0,
                )
            )
        session.add_all(entries)
    payload = IngestEmailPayload(
        message_id="msg-sheet-payload-rowcount-scope-001",
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2099, 3, 20, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint=None,
    )
    order = order_service.create_order_from_ingest(payload, lines=[])
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "table_rows": [
                ["", "", f"OCR {idx}", "5", "", "", "", "", "", ""]
                for idx in range(7)
            ]
        },
    )

    sheet, error = order_service.get_ocr_sheet(order["id"])
    assert error is None
    assert sheet is not None
    assert sheet["source"] == "weekly_menu+ocr_payload"
    assert len(sheet["rows"]) == 7

    date_idx = next((idx for idx, field in enumerate(sheet["fields"]) if field.startswith("date")), 0)
    dates = [
        row[date_idx]
        for row in sheet["rows"]
        if row and date_idx < len(row) and row[date_idx]
    ]
    assert dates
    assert "03/20" in set(dates)


def test_get_ocr_sheet_weekly_menu_ignores_payload_dates_for_row_selection():
    order_service.clear_all()
    with session_scope() as session:
        session.execute(delete(MonthlyMenuEntry).where(MonthlyMenuEntry.monthly_menu_id == "2099-12"))
        session.execute(delete(MonthlyMenu).where(MonthlyMenu.id == "2099-12"))
        session.add(
            MonthlyMenu(
                id="2099-12",
                month_start=date(2099, 12, 1),
                filename="seed-2099-12.xlsx",
            )
        )
        session.add_all(
            [
                MonthlyMenuEntry(
                    id="seed-entry-2099-12-26-breakfast-a-2",
                    monthly_menu_id="2099-12",
                    menu_date=date(2099, 12, 26),
                    daypart="朝食",
                    name="Menu A",
                    slot_index=0,
                ),
                MonthlyMenuEntry(
                    id="seed-entry-2099-12-27-breakfast-b-2",
                    monthly_menu_id="2099-12",
                    menu_date=date(2099, 12, 27),
                    daypart="朝食",
                    name="Menu B",
                    slot_index=0,
                ),
            ]
        )
    payload = IngestEmailPayload(
        message_id="msg-sheet-payload-dates-merge-001",
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2099, 12, 26, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint=None,
    )
    # Simulate sparse persisted lines after manual apply: only one date remains.
    lines = [
        {
            "date": "2099-12-26",
            "daypart": "朝",
            "menu_name": "Menu A",
            "diet_type": "regular",
            "area_id": "2F",
            "bag_type": "standard",
            "quantity_original": 7,
        }
    ]
    order = order_service.create_order_from_ingest(payload, lines=lines)
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "table_rows": [
                ["12/26", "朝", "Menu A", "7", "", "", "", "", "", ""],
                ["12/27", "朝", "Menu B", "", "", "", "", "", "", ""],
            ]
        },
    )

    sheet, error = order_service.get_ocr_sheet(order["id"])
    assert error is None
    assert sheet is not None
    dates = {row[0] for row in sheet["rows"] if row and row[0]}
    assert dates == {"12/26"}


def test_get_ocr_sheet_includes_intermediate_weekly_menu_dates_when_line_dates_sparse():
    order_service.clear_all()
    with session_scope() as session:
        session.execute(delete(MonthlyMenuEntry).where(MonthlyMenuEntry.monthly_menu_id == "2099-12"))
        session.execute(delete(MonthlyMenu).where(MonthlyMenu.id == "2099-12"))
        session.add(
            MonthlyMenu(
                id="2099-12",
                month_start=date(2099, 12, 1),
                filename="seed-2099-12.xlsx",
            )
        )
        session.add_all(
            [
                MonthlyMenuEntry(
                    id="seed-entry-2099-12-26-breakfast-a-3",
                    monthly_menu_id="2099-12",
                    menu_date=date(2099, 12, 26),
                    daypart="朝食",
                    name="Menu A",
                    slot_index=0,
                ),
                MonthlyMenuEntry(
                    id="seed-entry-2099-12-27-breakfast-b-3",
                    monthly_menu_id="2099-12",
                    menu_date=date(2099, 12, 27),
                    daypart="朝食",
                    name="Menu B",
                    slot_index=0,
                ),
                MonthlyMenuEntry(
                    id="seed-entry-2099-12-28-breakfast-c-3",
                    monthly_menu_id="2099-12",
                    menu_date=date(2099, 12, 28),
                    daypart="朝食",
                    name="Menu C",
                    slot_index=0,
                ),
            ]
        )
    payload = IngestEmailPayload(
        message_id="msg-sheet-intermediate-dates-001",
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2099, 12, 26, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2099-12",
    )
    # Simulate sparse parsed dates (middle day missing in OCR/order lines).
    lines = [
        {
            "date": "2099-12-26",
            "daypart": "朝",
            "menu_name": "Menu A",
            "diet_type": "regular",
            "area_id": "2F",
            "bag_type": "standard",
            "quantity_original": 7,
        },
        {
            "date": "2099-12-28",
            "daypart": "朝",
            "menu_name": "Menu C",
            "diet_type": "regular",
            "area_id": "2F",
            "bag_type": "standard",
            "quantity_original": 9,
        },
    ]
    order = order_service.create_order_from_ingest(payload, lines=lines)

    sheet, error = order_service.get_ocr_sheet(order["id"])
    assert error is None
    assert sheet is not None
    assert sheet["source"] == "weekly_menu"
    dates = {row[0] for row in sheet["rows"] if row and row[0]}
    assert "12/26" in dates
    assert "12/27" in dates
    assert "12/28" in dates


def test_get_ocr_sheet_returns_error_when_template_fields_invalid():
    order_service.clear_all()
    order = _seed_order(message_id="msg-sheet-invalid-template-001")
    original_get = config_service.get_facility_config

    def _mock_get(facility_id: str):
        current = original_get(facility_id)
        if not current or facility_id != "FAC00001":
            return current
        payload = dict(current)
        template = dict(payload.get("fax_template") or {})
        template["main_ocr_row_fields"] = ["date_mmdd", "menu", "invalid_field"]
        payload["fax_template"] = template
        return payload

    config_service.get_facility_config = _mock_get
    try:
        sheet, error = order_service.get_ocr_sheet(order["id"])
        assert sheet is None
        assert error == "sheet_template_field_invalid"
    finally:
        config_service.get_facility_config = original_get


def test_get_ocr_sheet_known_order_fixture_ordc935f9e2():
    order_service.clear_all()
    with session_scope() as session:
        session.execute(delete(Facility).where(Facility.id == "FAC00002"))
        session.execute(delete(FacilityConfig).where(FacilityConfig.facility_id == "FAC00002"))
        session.execute(delete(OrderOcrCache).where(OrderOcrCache.order_id == "ORDc935f9e2"))
        session.execute(delete(Order).where(Order.id == "ORDc935f9e2"))
        session.execute(delete(MonthlyMenuEntry).where(MonthlyMenuEntry.monthly_menu_id == "2026-02"))
        session.execute(delete(MonthlyMenu).where(MonthlyMenu.id == "2026-02"))
        session.add(
            MonthlyMenu(
                id="2026-02",
                month_start=date(2026, 2, 1),
                filename="fixture-2026-02.xlsx",
            )
        )
        session.add_all(
            [
                MonthlyMenuEntry(
                    id="fixture-2026-02-15-breakfast-main",
                    monthly_menu_id="2026-02",
                    menu_date=date(2026, 2, 15),
                    daypart="朝食",
                    name="じゃが芋のコンソメ煮",
                    slot_index=0,
                ),
                MonthlyMenuEntry(
                    id="fixture-2026-02-15-breakfast-sub",
                    monthly_menu_id="2026-02",
                    menu_date=date(2026, 2, 15),
                    daypart="朝食",
                    name="キャベツサラダ",
                    slot_index=1,
                ),
            ]
        )
        session.add(
            Facility(
                id="FAC00002",
                name="Fixture FAC00002",
            )
        )
        session.add(
            FacilityConfig(
                facility_id="FAC00002",
                config_json={
                    "fax_template_override": {
                        "main_ocr_row_fields": [
                            "date_mmdd",
                            "daypart",
                            "menu",
                            "qty.regular_x",
                            "qty.soft_x",
                            "qty.mixer_x",
                            "qty.no_meat_x",
                            "qty.no_fish_x",
                            "remarks",
                        ]
                    }
                },
            )
        )
        session.add(
            Order(
                id="ORDc935f9e2",
                facility_code="FAC00002",
                week_code=None,
                status="要確認",
                document_uri="file://fixture.pdf",
                message_id="fixture:fax000335185_0215-1.pdf",
                received_at=datetime(2026, 2, 3, 3, 7, 39),
            )
        )
        session.add(
            OrderOcrCache(
                order_id="ORDc935f9e2",
                payload={
                    "table_raw": """
|日付|区分|献立|常食|軟菜|ミキサー|肉禁|魚禁|備考|
|-|-|-|-|-|-|-|-|-|
|2/15|朝|じゃが芋のコンソメ煮|||||||
|2/15|朝|キャベツサラダ|23|||||||
""".strip()
                },
            )
        )

    sheet, error = order_service.get_ocr_sheet("ORDc935f9e2")
    assert error is None
    assert sheet is not None
    assert sheet["order_id"] == "ORDc935f9e2"
    fields = sheet["fields"]
    assert len(fields) == len(set(fields))
    assert "date_mmdd" in fields
    assert "menu" in fields
    assert any(field in fields for field in ("qty.regular_2f", "qty.regular_x"))
    assert any(field in fields for field in ("qty.soft_2f", "qty.soft_x"))
    assert any(field in fields for field in ("qty.mixer_2f", "qty.mixer_x"))
    date_idx = fields.index("date_mmdd")
    assert sheet["rows"][0][date_idx] == "02/15"


def test_extract_sheet_rows_from_payload_merges_blocks_and_collects_unstructured_qty():
    payload = {
        "table_raw": """
|日付|区分|献立|常食|備考|
|-|-|-|-|-|
|2/15|朝|Menu A|23||

|日付|区分|献立|常食|備考|
|-|-|-|-|-|
|2/15|朝|Menu B|||

16
""".strip()
    }
    template = {
        "main_ocr_row_fields": ["date_mmdd", "daypart", "menu", "qty.regular_x", "remarks"]
    }

    rows = order_service._extract_sheet_rows_from_payload(payload, template)
    assert len(rows) == 2
    assert rows[0][2] == "Menu A"
    assert rows[1][2] == "Menu B"

    sanitized = order_service._sanitize_payload_table_raw(payload)
    candidates = order_service._extract_payload_unstructured_quantity_candidates(sanitized)
    assert "16" in candidates


def test_apply_payload_cells_by_menu_priority_recovers_missing_quantities():
    fields = ["date_mmdd", "daypart", "menu", "qty.regular_x", "remarks"]
    quantity_index = {("regular", "X"): 3}
    rows = [
        {"values": ["02/15", "朝", "Menu A", "", ""]},
        {"values": ["02/15", "朝", "Menu B", "", ""]},
        {"values": ["02/15", "昼", "Menu C", "", ""]},
        {"values": ["02/15", "昼", "Menu D", "", ""]},
        {"values": ["02/15", "昼", "Menu E", "", ""]},
        {"values": ["02/15", "夕", "Menu F", "", ""]},
        {"values": ["02/15", "夕", "Menu G", "", ""]},
    ]
    payload_rows = [
        ["2/15", "朝", "Menu A", "23", ""],
        ["2/15", "朝", "Menu B", "", "23"],  # loose numeric in note column
        ["2/15", "昼", "Menu C", "23", ""],
        ["2/15", "昼", "Menu D", "", ""],  # isolated gap
        ["2/15", "昼", "Menu E", "23", ""],
        ["2/15", "夕", "Menu F", "16", ""],
        ["2/15", "夕", "Menu G", "", ""],  # recovered from unstructured tail candidate
    ]

    stats = order_service._apply_payload_cells_by_menu_priority(
        rows=rows,
        fields=fields,
        quantity_index=quantity_index,
        payload_rows=payload_rows,
        payload_unstructured_qty=["16"],
    )

    assert rows[0]["values"][3] == "23"
    assert rows[1]["values"][3] == "23"
    assert rows[3]["values"][3] == "23"
    assert rows[6]["values"][3] == "16"
    assert stats.get("loose_cell", 0) >= 1
    assert stats.get("gap_fill", 0) >= 1
    assert stats.get("unstructured", 0) >= 1


def test_get_ocr_sheet_weekly_menu_blocks_payload_off_month_noise_when_order_lines_exist():
    order_service.clear_all()
    with session_scope() as session:
        session.execute(delete(MonthlyMenuEntry).where(MonthlyMenuEntry.monthly_menu_id == "2099-12"))
        session.execute(delete(MonthlyMenu).where(MonthlyMenu.id == "2099-12"))
        session.add(
            MonthlyMenu(
                id="2099-12",
                month_start=date(2099, 12, 1),
                filename="seed-2099-12.xlsx",
            )
        )
        session.add_all(
            [
                MonthlyMenuEntry(
                    id="seed-entry-2099-12-26-breakfast-a-4",
                    monthly_menu_id="2099-12",
                    menu_date=date(2099, 12, 26),
                    daypart="朝食",
                    name="Menu A",
                    slot_index=0,
                ),
                MonthlyMenuEntry(
                    id="seed-entry-2099-12-27-breakfast-b-4",
                    monthly_menu_id="2099-12",
                    menu_date=date(2099, 12, 27),
                    daypart="朝食",
                    name="Menu B",
                    slot_index=0,
                ),
            ]
        )
    payload = IngestEmailPayload(
        message_id="msg-sheet-payload-offmonth-noise-001",
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2099, 12, 26, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2099-12",
    )
    lines = [
        {
            "date": "2099-12-26",
            "daypart": "朝",
            "menu_name": "Menu A",
            "diet_type": "regular",
            "area_id": "2F",
            "bag_type": "standard",
            "quantity_original": 7,
        },
        {
            "date": "2099-12-27",
            "daypart": "朝",
            "menu_name": "Menu B",
            "diet_type": "regular",
            "area_id": "2F",
            "bag_type": "standard",
            "quantity_original": 8,
        },
    ]
    order = order_service.create_order_from_ingest(payload, lines=lines)
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "table_rows": [
                ["11/01", "朝", "NOISE MENU", "99", "", "", "", "", "", ""],
                ["12/26", "朝", "Menu A", "1", "", "", "", "", "", ""],
                ["12/27", "朝", "Menu B", "2", "", "", "", "", "", ""],
            ]
        },
    )

    sheet, error = order_service.get_ocr_sheet(order["id"])
    assert error is None
    assert sheet is not None
    assert sheet["source"] == "weekly_menu"
    fields = sheet["fields"]
    qty_idx = next(
        idx
        for idx, field in enumerate(fields)
        if field in {"qty.regular_2f", "qty.regular_x"}
    )
    daypart_idx = fields.index("daypart")
    menu_idx = fields.index("menu")
    date_idx = next((idx for idx, field in enumerate(fields) if field.startswith("date")), 0)

    dates = {row[date_idx] for row in sheet["rows"] if row and date_idx < len(row) and row[date_idx]}
    assert dates == {"12/26", "12/27"}
    assert all("NOISE" not in row[menu_idx] for row in sheet["rows"] if len(row) > menu_idx)

    menu_a = next(
        row
        for row in sheet["rows"]
        if row[date_idx] == "12/26" and row[daypart_idx] == "朝" and row[menu_idx] == "Menu A"
    )
    menu_b = next(
        row
        for row in sheet["rows"]
        if row[date_idx] == "12/27" and row[daypart_idx] == "朝" and row[menu_idx] == "Menu B"
    )
    assert menu_a[qty_idx] == "7"
    assert menu_b[qty_idx] == "8"


def test_get_ocr_sheet_weekly_menu_reflects_update_lines_over_stale_payload():
    order_service.clear_all()
    _seed_monthly_menu_daypart_order_2099_11()
    payload = IngestEmailPayload(
        message_id="msg-sheet-update-lines-priority-001",
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2099, 11, 15, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2099-11",
    )
    initial_lines = [
        {
            "line_id": "line-initial-1",
            "date": "2099-11-15",
            "daypart": "朝",
            "menu_name": "朝メニュー",
            "diet_type": "regular",
            "area_id": "2F",
            "bag_type": "standard",
            "quantity_original": 6,
        }
    ]
    order = order_service.create_order_from_ingest(payload, lines=initial_lines)
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "table_rows": [
                ["11/15", "朝", "OCRノイズメニュー", "99", "", "", "", "", "", "payload-note"],
            ]
        },
    )
    replaced_lines = [
        {
            "line_id": "line-updated-1",
            "date": "2099-11-15",
            "daypart": "朝",
            "menu_name": "朝メニュー",
            "diet_type": "regular",
            "area_id": "2F",
            "bag_type": "standard",
            "quantity_original": 20,
        }
    ]
    assert order_service.update_lines(order["id"], replaced_lines) is True

    sheet, error = order_service.get_ocr_sheet(order["id"])
    assert error is None
    assert sheet is not None
    assert sheet["source"] == "weekly_menu"
    fields = sheet["fields"]
    qty_idx = next(
        idx
        for idx, field in enumerate(fields)
        if field in {"qty.regular_2f", "qty.regular_x"}
    )
    daypart_idx = fields.index("daypart")
    menu_idx = fields.index("menu")
    date_idx = next((idx for idx, field in enumerate(fields) if field.startswith("date")), 0)

    breakfast = next(
        row
        for row in sheet["rows"]
        if row[date_idx] == "11/15" and row[daypart_idx] == "朝" and row[menu_idx] == "朝メニュー"
    )
    assert breakfast[qty_idx] == "20"


def test_get_ocr_sheet_weekly_menu_is_consistent_across_orders_with_same_lines():
    order_service.clear_all()
    _seed_monthly_menu_daypart_order_2099_11()
    base_payload = IngestEmailPayload(
        message_id="msg-sheet-consistency-001",
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2099, 11, 15, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2099-11",
    )
    lines = [
        {
            "line_id": "line-consistent-1",
            "date": "2099-11-15",
            "daypart": "昼",
            "menu_name": "昼メニュー",
            "diet_type": "regular",
            "area_id": "2F",
            "bag_type": "standard",
            "quantity_original": 6,
        },
        {
            "line_id": "line-consistent-2",
            "date": "2099-11-15",
            "daypart": "夕",
            "menu_name": "夕メニュー",
            "diet_type": "regular",
            "area_id": "2F",
            "bag_type": "standard",
            "quantity_original": 5,
        },
    ]
    order_a = order_service.create_order_from_ingest(base_payload, lines=lines)
    payload_b = IngestEmailPayload(
        message_id="msg-sheet-consistency-002",
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2099, 11, 15, 9, 0, 1),
        facility_hint="FAC00001",
        week_hint="2099-11",
    )
    order_b = order_service.create_order_from_ingest(payload_b, lines=lines)
    with session_scope() as session:
        raw = session.get(Order, order_b["id"])
        assert raw is not None
        raw.week_code = None
    order_service._save_order_ocr_cache(
        order_a["id"],
        {
            "table_rows": [
                ["11/15", "夕", "OCR A", "70", "", "", "", "", "", ""],
            ]
        },
    )
    order_service._save_order_ocr_cache(
        order_b["id"],
        {
            "table_rows": [
                ["11/15", "夕", "OCR B", "999", "", "", "", "", "", ""],
            ]
        },
    )

    sheet_a, error_a = order_service.get_ocr_sheet(order_a["id"])
    sheet_b, error_b = order_service.get_ocr_sheet(order_b["id"])
    assert error_a is None
    assert error_b is None
    assert sheet_a is not None
    assert sheet_b is not None
    assert sheet_a["source"] == "weekly_menu"
    assert sheet_b["source"] == "weekly_menu"
    assert sheet_a["fields"] == sheet_b["fields"]
    assert sheet_a["rows"] == sheet_b["rows"]


def test_resolve_sheet_week_month_boundary_prefers_order_line_month_and_is_order_invariant():
    _seed_monthly_menu_boundary_2026_01_02()
    received_at = datetime(2026, 2, 1, 9, 0, 0)
    order_lines = [{"date": date(2026, 2, 1)}]
    payload_a = {
        "table_raw": """
|日付|区分|献立|常|
|-|-|-|-|
|1/31|朝|Boundary Jan|10|
|2/1|朝|Boundary Feb|11|
""".strip()
    }
    payload_b = {
        "table_raw": """
|日付|区分|献立|常|
|-|-|-|-|
|2/1|朝|Boundary Feb|11|
|1/31|朝|Boundary Jan|10|
""".strip()
    }

    resolved_a = order_service._resolve_sheet_week_id(
        current_week_id="2026-01",
        received_at=received_at,
        order_lines=order_lines,
        ocr_payload=payload_a,
        facility_id="FAC00001",
        week_hints=["2026-01"],
    )
    resolved_b = order_service._resolve_sheet_week_id(
        current_week_id="2026-01",
        received_at=received_at,
        order_lines=order_lines,
        ocr_payload=payload_b,
        facility_id="FAC00001",
        week_hints=["2026-01"],
    )

    assert resolved_a == "2026-02"
    assert resolved_b == "2026-02"


def test_get_ocr_sheet_weekly_menu_prefers_order_lines_for_qty_x_and_split_templates():
    order_service.clear_all()
    _seed_monthly_menu_daypart_order_2099_11()
    payload = IngestEmailPayload(
        message_id="msg-sheet-template-variant-priority-001",
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2099, 11, 15, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2099-11",
    )
    lines = [
        {
            "line_id": "line-template-variant-1",
            "date": "2099-11-15",
            "daypart": "昼",
            "menu_name": "昼メニュー",
            "diet_type": "regular",
            "area_id": "2F",
            "bag_type": "standard",
            "quantity_original": 20,
        }
    ]
    order = order_service.create_order_from_ingest(payload, lines=lines)
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "table_rows": [
                ["11/15", "昼", "OCRノイズメニュー", "99", "", "", "", "", "", ""],
            ]
        },
    )

    original_get = config_service.get_facility_config
    cases = [
        (
            "split",
            [
                "date_mmdd",
                "daypart",
                "menu",
                "qty.regular_2f",
                "qty.regular_3f",
                "qty.soft_2f",
                "qty.soft_3f",
                "qty.mixer_2f",
                "qty.mixer_3f",
                "remarks",
            ],
            "qty.regular_2f",
        ),
        (
            "x",
            [
                "date_mmdd",
                "daypart",
                "menu",
                "qty.regular_x",
                "qty.soft_x",
                "qty.mixer_x",
                "remarks",
            ],
            "qty.regular_x",
        ),
    ]
    try:
        for _label, row_fields, qty_field in cases:
            def _mock_get(facility_id: str, _row_fields=row_fields):
                current = original_get(facility_id)
                if not current or facility_id != "FAC00001":
                    return current
                payload_cfg = dict(current)
                template = dict(payload_cfg.get("fax_template") or {})
                template["main_ocr_row_fields"] = _row_fields
                payload_cfg["fax_template"] = template
                return payload_cfg

            config_service.get_facility_config = _mock_get
            sheet, error = order_service.get_ocr_sheet(order["id"])
            assert error is None
            assert sheet is not None
            assert sheet["source"] == "weekly_menu"
            fields = sheet["fields"]
            qty_idx = fields.index(qty_field)
            daypart_idx = fields.index("daypart")
            menu_idx = fields.index("menu")
            date_idx = next((idx for idx, field in enumerate(fields) if field.startswith("date")), 0)

            assert all(
                "OCRノイズ" not in row[menu_idx]
                for row in sheet["rows"]
                if len(row) > menu_idx
            )
            lunch = next(
                row
                for row in sheet["rows"]
                if row[date_idx] == "11/15" and row[daypart_idx] == "昼" and row[menu_idx] == "昼メニュー"
            )
            assert lunch[qty_idx] == "20"
    finally:
        config_service.get_facility_config = original_get


def test_get_ocr_sheet_final_priority_fixed_after_repeated_apply_and_line_updates():
    order_service.clear_all()
    _seed_monthly_menu_daypart_order_2099_11()
    payload = IngestEmailPayload(
        message_id="msg-sheet-repeat-priority-001",
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2099, 11, 15, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2099-11",
    )
    order = order_service.create_order_from_ingest(
        payload,
        lines=[
            {
                "line_id": "line-repeat-initial",
                "date": "2099-11-15",
                "daypart": "昼",
                "menu_name": "昼メニュー",
                "diet_type": "regular",
                "area_id": "2F",
                "bag_type": "standard",
                "quantity_original": 6,
            }
        ],
    )
    fields = [
        "date_mmdd",
        "daypart",
        "menu",
        "qty.regular_2f",
        "qty.regular_3f",
        "qty.soft_2f",
        "qty.soft_3f",
        "qty.mixer_2f",
        "qty.mixer_3f",
        "remarks",
    ]
    header = ["日付", "区分", "メニュー", "常食2F", "常食3F", "軟菜2F", "軟菜3F", "ミキサー2F", "ミキサー3F", "備考"]

    updated_1, error_1 = order_service.apply_ocr_table(
        order["id"],
        header=header,
        rows=[["11/15", "昼", "昼メニュー", "11", "", "", "", "", "", "apply-1"]],
        ui_mode="sheet",
        fields=fields,
        row_ids=["row-repeat-1"],
    )
    assert error_1 is None
    assert updated_1 is not None
    assert order_service.update_lines(
        order["id"],
        [
            {
                "line_id": "line-repeat-update-1",
                "date": "2099-11-15",
                "daypart": "昼",
                "menu_name": "昼メニュー",
                "diet_type": "regular",
                "area_id": "2F",
                "bag_type": "standard",
                "quantity_original": 20,
            }
        ],
    ) is True

    updated_2, error_2 = order_service.apply_ocr_table(
        order["id"],
        header=header,
        rows=[["11/15", "昼", "昼メニュー", "3", "", "", "", "", "", "apply-2"]],
        ui_mode="sheet",
        fields=fields,
        row_ids=["row-repeat-2"],
    )
    assert error_2 is None
    assert updated_2 is not None
    assert order_service.update_lines(
        order["id"],
        [
            {
                "line_id": "line-repeat-final",
                "date": "2099-11-15",
                "daypart": "昼",
                "menu_name": "昼メニュー",
                "diet_type": "regular",
                "area_id": "2F",
                "bag_type": "standard",
                "quantity_original": 30,
            }
        ],
    ) is True

    history, history_error = order_service.get_ocr_edit_history(order["id"])
    assert history_error is None
    assert history is not None
    revisions = history.get("revisions") or []
    assert len(revisions) == 2
    assert revisions[0]["rows"][0][3] == "11"
    assert revisions[1]["rows"][0][3] == "3"

    sheet, sheet_error = order_service.get_ocr_sheet(order["id"])
    assert sheet_error is None
    assert sheet is not None
    assert sheet["source"] == "draft_sheet"
    qty_idx = sheet["fields"].index("qty.regular_2f")
    daypart_idx = sheet["fields"].index("daypart")
    menu_idx = sheet["fields"].index("menu")
    date_idx = next((idx for idx, field in enumerate(sheet["fields"]) if field.startswith("date")), 0)
    lunch = next(
        row
        for row in sheet["rows"]
        if row[date_idx] == "11/15" and row[daypart_idx] == "昼" and row[menu_idx] == "昼メニュー"
    )
    assert lunch[qty_idx] == "3"


def test_get_ocr_sheet_does_not_mutate_order_ocr_cache_timestamp():
    order_service.clear_all()
    _seed_monthly_menu_daypart_order_2099_11()
    payload = IngestEmailPayload(
        message_id="msg-sheet-readonly-cache-001",
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2099, 11, 15, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2099-11",
    )
    order = order_service.create_order_from_ingest(
        payload,
        lines=[
            {
                "line_id": "line-readonly-1",
                "date": "2099-11-15",
                "daypart": "朝",
                "menu_name": "朝メニュー",
                "diet_type": "regular",
                "area_id": "2F",
                "bag_type": "standard",
                "quantity_original": 7,
            }
        ],
    )
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "table_rows": [
                ["11/15", "朝", "朝メニュー", "7", "", "", "", "", "", ""],
            ]
        },
    )
    with session_scope() as session:
        cache_before = session.get(OrderOcrCache, order["id"])
        assert cache_before is not None
        updated_before = cache_before.updated_at

    sheet, error = order_service.get_ocr_sheet(order["id"])
    assert error is None
    assert sheet is not None

    with session_scope() as session:
        cache_after = session.get(OrderOcrCache, order["id"])
        assert cache_after is not None
        updated_after = cache_after.updated_at
    assert updated_after == updated_before


def test_get_ocr_sheet_weekly_menu_numeric_rescue_ignores_loose_note_and_unstructured_values():
    order_service.clear_all()
    _seed_monthly_menu_daypart_order_2099_11()
    payload = IngestEmailPayload(
        message_id="msg-sheet-strict-numeric-001",
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2099, 11, 15, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2099-11",
    )
    order = order_service.create_order_from_ingest(payload, lines=[])
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "table_rows": [
                ["11/15", "朝", "朝メニュー", "7", "", "", "", "", "", ""],
                ["11/15", "昼", "昼メニュー", "", "", "", "", "", "", "note 23"],
            ],
            "_table_raw_unstructured_qty": ["16"],
        },
    )

    sheet, error = order_service.get_ocr_sheet(order["id"])
    assert error is None
    assert sheet is not None
    assert sheet["source"] == "weekly_menu+ocr_payload"
    fields = sheet["fields"]
    qty_idx = next(
        idx
        for idx, field in enumerate(fields)
        if field in {"qty.regular_2f", "qty.regular_x"}
    )
    daypart_idx = fields.index("daypart")
    menu_idx = fields.index("menu")
    date_idx = next((idx for idx, field in enumerate(fields) if field.startswith("date")), 0)

    breakfast = next(
        row
        for row in sheet["rows"]
        if row[date_idx] == "11/15" and row[daypart_idx] == "朝" and row[menu_idx] == "朝メニュー"
    )
    lunch = next(
        row
        for row in sheet["rows"]
        if row[date_idx] == "11/15" and row[daypart_idx] == "昼" and row[menu_idx] == "昼メニュー"
    )
    assert breakfast[qty_idx] == "7"
    assert lunch[qty_idx] == ""


def test_get_ocr_sheet_weekly_menu_preserves_zero_from_order_lines():
    order_service.clear_all()
    _seed_monthly_menu_daypart_order_2099_11()
    payload = IngestEmailPayload(
        message_id="msg-sheet-zero-order-lines-001",
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2099, 11, 15, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2099-11",
    )
    order = order_service.create_order_from_ingest(
        payload,
        lines=[
            {
                "line_id": "line-zero-1",
                "date": "2099-11-15",
                "daypart": "昼",
                "menu_name": "昼メニュー",
                "diet_type": "regular",
                "area_id": "2F",
                "bag_type": "standard",
                "quantity_original": 0,
            }
        ],
    )

    sheet, error = order_service.get_ocr_sheet(order["id"])
    assert error is None
    assert sheet is not None
    assert sheet["source"] == "weekly_menu"
    fields = sheet["fields"]
    qty_idx = fields.index("qty.regular_2f")
    daypart_idx = fields.index("daypart")
    menu_idx = fields.index("menu")
    date_idx = next((idx for idx, field in enumerate(fields) if field.startswith("date")), 0)

    lunch = next(
        row
        for row in sheet["rows"]
        if row[date_idx] == "11/15" and row[daypart_idx] == "昼" and row[menu_idx] == "昼メニュー"
    )
    assert lunch[qty_idx] == "0"


def test_get_ocr_sheet_weekly_menu_payload_rescue_preserves_zero():
    order_service.clear_all()
    _seed_monthly_menu_daypart_order_2099_11()
    payload = IngestEmailPayload(
        message_id="msg-sheet-zero-payload-001",
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2099, 11, 15, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2099-11",
    )
    order = order_service.create_order_from_ingest(payload, lines=[])
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "table_rows": [
                ["11/15", "朝", "朝メニュー", "0", "", "", "", "", "", ""],
            ],
        },
    )

    sheet, error = order_service.get_ocr_sheet(order["id"])
    assert error is None
    assert sheet is not None
    assert sheet["source"] == "weekly_menu+ocr_payload"
    fields = sheet["fields"]
    qty_idx = fields.index("qty.regular_2f")
    daypart_idx = fields.index("daypart")
    menu_idx = fields.index("menu")
    date_idx = next((idx for idx, field in enumerate(fields) if field.startswith("date")), 0)

    breakfast = next(
        row
        for row in sheet["rows"]
        if row[date_idx] == "11/15" and row[daypart_idx] == "朝" and row[menu_idx] == "朝メニュー"
    )
    assert breakfast[qty_idx] == "0"


def test_get_ocr_sheet_suppresses_failed_projected_order_lines_and_uses_payload():
    order_service.clear_all()
    _seed_monthly_menu_daypart_order_2099_11()
    payload = IngestEmailPayload(
        message_id="msg-sheet-suppress-failed-projection-001",
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2099, 11, 15, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2099-11",
    )
    order = order_service.create_order_from_ingest(
        payload,
        lines=[
            {
                "line_id": "line-suppress-1",
                "date": "2099-11-15",
                "daypart": "朝",
                "menu_name": "朝メニュー",
                "diet_type": "regular",
                "area_id": "2F",
                "bag_type": "standard",
                "quantity_original": 30,
            }
        ],
    )
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "table_rows": [
                ["11/15", "朝", "朝メニュー", "7", "", "", "", "", "", ""],
            ],
            "metrics": {
                "status": "failed",
                "result_state": "hard_failed",
                "quality_error": "sheet_column_anomaly",
                "structural_row_projection": {
                    "projected_row_count": 30,
                    "rows_with_projected_quantity": 30,
                    "quantity_cells_copied": 120,
                },
            },
        },
    )

    sheet, error = order_service.get_ocr_sheet(order["id"])

    assert error is None
    assert sheet is not None
    assert sheet["source"] == "weekly_menu+ocr_payload"
    assert "sheet_order_lines_suppressed_reparse_failed" in (sheet.get("warnings") or [])
    fields = sheet["fields"]
    qty_idx = fields.index("qty.regular_2f")
    daypart_idx = fields.index("daypart")
    menu_idx = fields.index("menu")
    date_idx = next((idx for idx, field in enumerate(fields) if field.startswith("date")), 0)
    breakfast = next(
        row
        for row in sheet["rows"]
        if row[date_idx] == "11/15" and row[daypart_idx] == "朝" and row[menu_idx] == "朝メニュー"
    )
    assert breakfast[qty_idx] == "7"
    trace = sheet.get("trace") or {}
    trace_rows = trace.get("rows") or []
    target_idx = next(
        idx
        for idx, row in enumerate(sheet["rows"])
        if row[date_idx] == "11/15" and row[daypart_idx] == "朝" and row[menu_idx] == "朝メニュー"
    )
    assert trace_rows[target_idx][qty_idx] == "ocr_payload"


def test_get_ocr_sheet_suppresses_failed_projected_order_lines_using_job_metrics_fallback():
    order_service.clear_all()
    _seed_monthly_menu_daypart_order_2099_11()
    payload = IngestEmailPayload(
        message_id="msg-sheet-suppress-failed-job-metrics-001",
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2099, 11, 15, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2099-11",
    )
    order = order_service.create_order_from_ingest(
        payload,
        lines=[
            {
                "line_id": "line-suppress-job-1",
                "date": "2099-11-15",
                "daypart": "朝",
                "menu_name": "朝メニュー",
                "diet_type": "regular",
                "area_id": "2F",
                "bag_type": "standard",
                "quantity_original": 30,
            }
        ],
    )
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "table_rows": [
                ["11/15", "朝", "朝メニュー", "7", "", "", "", "", "", ""],
            ],
            "metrics": {
                "status": "success",
            },
        },
    )
    create_job(f"OCR-{order['id']}", input_reference="gs://ocr/reparse.json", status="failed")
    update_job(
        f"OCR-{order['id']}",
        metrics={
            "result_state": "hard_failed",
            "quality_error": "sheet_column_anomaly",
            "structural_row_projection": {
                "projected_row_count": 30,
                "rows_with_projected_quantity": 30,
                "quantity_cells_copied": 120,
            },
        },
    )

    sheet, error = order_service.get_ocr_sheet(order["id"])

    assert error is None
    assert sheet is not None
    assert sheet["source"] == "weekly_menu+ocr_payload"
    assert "sheet_order_lines_suppressed_reparse_failed" in (sheet.get("warnings") or [])
    fields = sheet["fields"]
    qty_idx = fields.index("qty.regular_2f")
    daypart_idx = fields.index("daypart")
    menu_idx = fields.index("menu")
    date_idx = next((idx for idx, field in enumerate(fields) if field.startswith("date")), 0)
    breakfast = next(
        row
        for row in sheet["rows"]
        if row[date_idx] == "11/15" and row[daypart_idx] == "朝" and row[menu_idx] == "朝メニュー"
    )
    assert breakfast[qty_idx] == "7"


def test_get_ocr_sheet_ocr_table_numeric_rescue_ignores_loose_note_and_unstructured_values():
    order_service.clear_all()
    payload = IngestEmailPayload(
        message_id="msg-sheet-strict-numeric-ocr-table-001",
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2099, 10, 15, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2099-10",
    )
    order = order_service.create_order_from_ingest(payload, lines=[])
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "table_rows": [
                ["10/15", "朝", "Menu A", "7", "", "", "", "", "", ""],
                ["10/15", "昼", "Menu B", "", "", "", "", "", "", "note 23"],
            ],
            "_table_raw_unstructured_qty": ["16"],
        },
    )

    sheet, error = order_service.get_ocr_sheet(order["id"])
    assert error is None
    assert sheet is not None
    assert sheet["source"] == "ocr_table+ocr_payload"
    fields = sheet["fields"]
    qty_idx = next(
        idx
        for idx, field in enumerate(fields)
        if field in {"qty.regular_2f", "qty.regular_x"}
    )
    daypart_idx = fields.index("daypart")
    menu_idx = fields.index("menu")
    date_idx = next((idx for idx, field in enumerate(fields) if field.startswith("date")), 0)

    breakfast = next(
        row
        for row in sheet["rows"]
        if row[date_idx] == "10/15" and row[daypart_idx] == "朝" and row[menu_idx] == "Menu A"
    )
    lunch = next(
        row
        for row in sheet["rows"]
        if row[date_idx] == "10/15" and row[daypart_idx] == "昼" and row[menu_idx] == "Menu B"
    )
    assert breakfast[qty_idx] == "7"
    assert lunch[qty_idx] == ""


def test_get_ocr_sheet_includes_cell_trace_for_quantity_sources():
    order_service.clear_all()
    _seed_monthly_menu_daypart_order_2099_11()
    payload = IngestEmailPayload(
        message_id="msg-sheet-trace-001",
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2099, 11, 15, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2099-11",
    )
    order = order_service.create_order_from_ingest(
        payload,
        lines=[
            {
                "line_id": "line-trace-1",
                "date": "2099-11-15",
                "daypart": "昼",
                "menu_name": "昼メニュー",
                "diet_type": "regular",
                "area_id": "2F",
                "bag_type": "standard",
                "quantity_original": 20,
            }
        ],
    )

    sheet, error = order_service.get_ocr_sheet(order["id"])
    assert error is None
    assert sheet is not None
    trace = sheet.get("trace")
    assert isinstance(trace, dict)
    trace_rows = trace.get("rows")
    assert isinstance(trace_rows, list)
    assert len(trace_rows) == len(sheet["rows"])
    assert all(isinstance(item, list) for item in trace_rows)
    assert all(len(item) == len(sheet["fields"]) for item in trace_rows)

    fields = sheet["fields"]
    qty_idx = fields.index("qty.regular_2f")
    daypart_idx = fields.index("daypart")
    menu_idx = fields.index("menu")
    date_idx = next((idx for idx, field in enumerate(fields) if field.startswith("date")), 0)
    target_idx = next(
        idx
        for idx, row in enumerate(sheet["rows"])
        if row[date_idx] == "11/15" and row[daypart_idx] == "昼" and row[menu_idx] == "昼メニュー"
    )
    assert trace_rows[target_idx][qty_idx] == "order_lines"


def test_get_ocr_sheet_exposes_yomitoku_review_issues():
    order_service.clear_all()
    order = _seed_order(message_id="msg-sheet-yomitoku-review-001")
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "pages": [
                {
                    "page_index": 1,
                    "tables": [
                        {
                            "table_id": "p1_t1",
                            "rows": [
                                ["日付", "区分", "献立", "数量", "備考"],
                                ["", "", "", "常食", ""],
                                ["01/08", "昼", "Menu A", "2", ""],
                            ],
                        }
                    ],
                }
            ],
            "yomitoku_cell_issues": [
                {
                    "table_id": "p1_t1",
                    "page_index": 1,
                    "source_row_index": 0,
                    "column_index": 3,
                    "issue_code": "merged_numeric_cell",
                    "severity": "high",
                    "source": "yomitoku_structured",
                }
            ],
        },
    )

    sheet, error = order_service.get_ocr_sheet(order["id"])

    assert error is None
    assert sheet is not None
    assert "sheet_ocr_review_required" in (sheet.get("warnings") or [])
    assert (sheet.get("issue_summary") or {}).get("review_required_cell_count") == 1
    issues = sheet.get("cell_issues") or []
    assert len(issues) == 1
    assert issues[0]["issue_code"] == "merged_numeric_cell"
    assert issues[0]["source"] == "yomitoku_structured"


def test_review_ocr_table_with_llm_persists_patch_candidates_and_uses_latest_draft_as_baseline(monkeypatch):
    order_service.clear_all()
    order = _seed_order(message_id="msg-sheet-llm-review-loop-001")
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "pages": [
                {
                    "page_index": 1,
                    "tables": [
                        {
                            "table_id": "p1_t1",
                            "rows": [
                                [
                                    "日付",
                                    "区分",
                                    "メニュー",
                                    "常食2F",
                                    "常食3F",
                                    "軟菜2F",
                                    "軟菜3F",
                                    "ミキサー2F",
                                    "ミキサー3F",
                                    "備考",
                                ],
                                ["01/08", "昼", "Menu A", "2", "1", "3", "", "", "", "first-pass"],
                            ],
                        }
                    ],
                }
            ],
            "table_raw": "|日付|区分|メニュー|常食2F|常食3F|軟菜2F|軟菜3F|ミキサー2F|ミキサー3F|備考|\n|---|---|---|---|---|---|---|---|---|---|\n|01/08|昼|Menu A|2|1|3||||first-pass|",
            "cell_issues": [
                {
                    "table_id": "p1_t1",
                    "page_index": 1,
                    "source_row_index": 0,
                    "column_index": 3,
                    "field": "qty.regular_2f",
                    "issue_code": "merged_numeric_cell",
                    "severity": "high",
                    "source": "yomitoku_structured",
                }
            ],
        },
    )

    captured_prompts: list[dict[str, str]] = []
    response_index = {"value": 0}

    review_batches = [
        {
            "facility_name": "Test Facility",
            "date_strings": ["1/8"],
            "rows": [
                {
                    "date_mmdd": "01/08",
                    "daypart": "昼",
                    "menu": "Menu A",
                    "qty.regular_2f": "5",
                    "qty.regular_3f": "1",
                    "qty.soft_2f": "3",
                    "qty.soft_3f": "",
                    "qty.mixer_2f": "",
                    "qty.mixer_3f": "",
                    "remarks": "first-pass",
                }
            ],
            "table_raw": "|日付|区分|メニュー|常食2F|常食3F|軟菜2F|軟菜3F|ミキサー2F|ミキサー3F|備考|\n|---|---|---|---|---|---|---|---|---|---|\n|01/08|昼|Menu A|5|1|3||||first-pass|",
            "llm_review": {
                "status": "verified",
                "needs_more_review": False,
                "notes": "updated regular 2F from 2 to 5",
                "issues": [],
            },
        },
        {
            "facility_name": "Test Facility",
            "date_strings": ["1/8"],
            "rows": [
                {
                    "date_mmdd": "01/08",
                    "daypart": "昼",
                    "menu": "Menu A",
                    "qty.regular_2f": "7",
                    "qty.regular_3f": "1",
                    "qty.soft_2f": "3",
                    "qty.soft_3f": "",
                    "qty.mixer_2f": "",
                    "qty.mixer_3f": "",
                    "remarks": "first-pass",
                }
            ],
            "table_raw": "|日付|区分|メニュー|常食2F|常食3F|軟菜2F|軟菜3F|ミキサー2F|ミキサー3F|備考|\n|---|---|---|---|---|---|---|---|---|---|\n|01/08|昼|Menu A|7|1|3||||first-pass|",
            "llm_review": {
                "status": "verified",
                "needs_more_review": False,
                "notes": "updated regular 2F from 5 to 7",
                "issues": [],
            },
        },
    ]

    def _fake_load_bytes(_uri: str) -> bytes:
        return b"%PDF-1.4\n%EOF\n"

    def _fake_extract(pdf_bytes, template, facility_id=None, preferred_template_id=None):
        assert pdf_bytes.startswith(b"%PDF-1.4")
        assert facility_id == "FAC00001"
        assert preferred_template_id is None
        captured_prompts.append(
            {
                "system": str(template.get("gemini_ocr_prompt") or template.get("openai_ocr_prompt") or ""),
                "user": str(
                    template.get("gemini_ocr_user_prompt")
                    or template.get("openai_ocr_user_prompt")
                    or ""
                ),
            }
        )
        current = review_batches[response_index["value"]]
        response_index["value"] += 1
        return FaxExtractedData(
            facility_name="Test Facility",
            date_strings=["2026-01-08"],
            table_rows=[],
            tokens=[],
            grid=None,
            ocr_provider="gemini",
            raw_text=json.dumps(current, ensure_ascii=False),
            provider_debug={"provider": "gemini", "model": "review-model-v1"},
        )

    monkeypatch.setattr(order_service, "load_bytes_from_uri", _fake_load_bytes)
    monkeypatch.setattr(order_service, "extract_fax_data", _fake_extract)

    updated_1, error_1 = order_service.review_ocr_table_with_llm(order["id"], provider="gemini")
    assert error_1 is None
    assert updated_1 is not None
    assert updated_1["llm_review"]["provider"] == "gemini"
    assert updated_1["llm_review"]["model"] == "review-model-v1"
    assert updated_1["llm_review"]["baseline_revision_id"] is None
    assert updated_1["llm_review"]["baseline_source"] == "yomitoku"
    assert updated_1["llm_review"]["summary"]["status"] == "verified"
    assert updated_1["llm_review"]["needs_more_review"] is False
    assert updated_1["llm_review"]["output_payload"]["rows"][0]["qty.regular_2f"] == "5"
    first_candidate = updated_1["patch_candidate"]
    assert first_candidate["candidate_state"] == "proposed"
    assert first_candidate["proposed_draft_sheet_json"]["rows"][0][3] == "5"

    history_1, history_error_1 = order_service.get_ocr_edit_history(order["id"])
    assert history_error_1 is None
    assert history_1 is not None
    assert isinstance(history_1["latest"], dict)
    assert history_1["latest"]["ui_mode"] == "evidence"
    assert str(history_1["latest"]["revision_id"]).startswith("OEV")
    assert len(history_1["revisions"]) == 1

    apply_result, apply_error = order_service.apply_patch_candidate_to_draft(
        order["id"],
        candidate_id=first_candidate["id"],
        applied_by="test",
    )
    assert apply_error is None
    assert apply_result is not None
    applied_draft = apply_result["draft"]
    assert applied_draft["latest_patch_candidate_id"] == first_candidate["id"]
    assert applied_draft["draft_sheet_json"]["rows"][0][3] == "5"

    updated_2, error_2 = order_service.review_ocr_table_with_llm(order["id"], provider="gemini")
    assert error_2 is None
    assert updated_2 is not None
    assert updated_2["llm_review"]["baseline_revision_id"] == applied_draft["id"]
    assert updated_2["llm_review"]["baseline_source"] == "draft"
    assert updated_2["llm_review"]["needs_more_review"] is False
    second_candidate = updated_2["patch_candidate"]
    assert second_candidate["proposed_draft_sheet_json"]["rows"][0][3] == "7"

    history_2, history_error_2 = order_service.get_ocr_edit_history(order["id"])
    assert history_error_2 is None
    assert history_2 is not None
    assert isinstance(history_2["latest"], dict)
    assert history_2["latest"]["ui_mode"] == "evidence"
    assert str(history_2["latest"]["revision_id"]).startswith("OEV")
    assert len(history_2["revisions"]) == 1

    assert "Previous yomitoku/LLM markdown" in captured_prompts[0]["user"]
    assert "Previous yomitoku/LLM structured tables/cells" in captured_prompts[0]["user"]
    assert "Current baseline source: yomitoku" in captured_prompts[0]["user"]
    assert '"qty.regular_2f": "2"' in captured_prompts[0]["user"]
    assert f"Current baseline revision_id: {applied_draft['id']}" in captured_prompts[1]["user"]
    assert "Current baseline source: draft" in captured_prompts[1]["user"]
    assert '"qty.regular_2f": "5"' in captured_prompts[1]["user"]


def test_review_ocr_table_with_llm_rejects_invalid_overwrite_and_keeps_latest_baseline(monkeypatch):
    order_service.clear_all()
    order = _seed_order(message_id="msg-sheet-llm-review-reject-001")
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "pages": [
                {
                    "page_index": 1,
                    "tables": [
                        {
                            "table_id": "p1_t1",
                            "rows": [
                                [
                                    "日付",
                                    "区分",
                                    "メニュー",
                                    "常食2F",
                                    "常食3F",
                                    "軟菜2F",
                                    "軟菜3F",
                                    "ミキサー2F",
                                    "ミキサー3F",
                                    "備考",
                                ],
                                ["01/08", "昼", "Menu A", "2", "1", "3", "", "", "", "first-pass"],
                            ],
                        }
                    ],
                }
            ],
            "table_raw": "|日付|区分|メニュー|常食2F|常食3F|軟菜2F|軟菜3F|ミキサー2F|ミキサー3F|備考|\n|---|---|---|---|---|---|---|---|---|---|\n|01/08|昼|Menu A|2|1|3||||first-pass|",
        },
    )

    def _fake_load_bytes(_uri: str) -> bytes:
        return b"%PDF-1.4\n%EOF\n"

    def _fake_extract(pdf_bytes, template, facility_id=None, preferred_template_id=None):
        assert pdf_bytes.startswith(b"%PDF-1.4")
        assert facility_id == "FAC00001"
        return FaxExtractedData(
            facility_name="Test Facility",
            date_strings=["2026-01-08"],
            table_rows=[],
            tokens=[],
            grid=None,
            ocr_provider="gemini",
            raw_text=json.dumps(
                {
                    "facility_name": "Test Facility",
                    "date_strings": ["1/8"],
                    "rows": [
                        {
                            "date_mmdd": "01/08",
                            "daypart": "昼",
                            "menu": "Menu A",
                            "qty.regular_2f": "2",
                            "qty.regular_3f": "1",
                            "qty.soft_2f": "3",
                            "qty.soft_3f": "",
                            "qty.mixer_2f": "",
                            "qty.mixer_3f": "",
                            "remarks": "first-pass",
                        }
                    ],
                    "table_raw": "|日付|区分|メニュー|常食2F|常食3F|軟菜2F|軟菜3F|ミキサー2F|ミキサー3F|備考|\n|---|---|---|---|---|---|---|---|---|---|\n|01/08|昼|Menu A|2|1|3||||first-pass|",
                    "llm_review": {
                        "status": "needs_review",
                        "needs_more_review": True,
                        "notes": "manual check required",
                        "issues": [
                            {
                                "issue_id": "iss-reject-1",
                                "row_id": "row-1",
                                "field": "qty.regular_2f",
                                "issue_code": "misread_quantity",
                                "status": "needs_review",
                                "page_index": 1,
                                "table_id": "p1_t1",
                                "current_text": "2",
                                "confidence": 0.88,
                                "evidence": "image still suggests a changed digit",
                                "reason": "manual_check_required",
                            }
                        ],
                    },
                },
                ensure_ascii=False,
            ),
            provider_debug={"provider": "gemini", "model": "review-model-v1"},
        )

    monkeypatch.setattr(order_service, "load_bytes_from_uri", _fake_load_bytes)
    monkeypatch.setattr(order_service, "extract_fax_data", _fake_extract)

    updated, error = order_service.review_ocr_table_with_llm(order["id"], provider="gemini")

    assert error is None
    assert updated is not None
    assert updated["llm_review"]["baseline_source"] == "yomitoku"
    assert updated["llm_review"]["needs_more_review"] is True
    assert updated["llm_review"]["applied_overwrites"] == []
    assert updated["llm_review"]["issues"][0]["issue_code"] == "misread_quantity"
    patch_candidate = updated["patch_candidate"]
    assert patch_candidate["candidate_state"] == "proposed"
    assert patch_candidate["issues_json"][0]["issue_code"] == "misread_quantity"

    history, history_error = order_service.get_ocr_edit_history(order["id"])
    assert history_error is None
    assert history is not None
    assert isinstance(history["latest"], dict)
    assert history["latest"]["ui_mode"] == "evidence"
    assert str(history["latest"]["revision_id"]).startswith("OEV")
    assert len(history["revisions"]) == 1


def test_review_ocr_table_with_llm_uses_corrected_pdf_variant_when_available(monkeypatch):
    order_service.clear_all()
    order = _seed_order(message_id="msg-sheet-llm-review-corrected-001")
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "pages": [
                {
                    "page_index": 1,
                    "tables": [
                        {
                            "table_id": "p1_t1",
                            "rows": [
                                [
                                    "日付",
                                    "区分",
                                    "メニュー",
                                    "常食2F",
                                    "常食3F",
                                    "軟菜2F",
                                    "軟菜3F",
                                    "ミキサー2F",
                                    "ミキサー3F",
                                    "備考",
                                ],
                                ["01/08", "昼", "Menu A", "2", "1", "3", "", "", "", "first-pass"],
                            ],
                        }
                    ],
                }
            ],
            "combined": {
                "corrected_pdf": "gs://bucket/corrected.pdf",
            },
            "table_raw": "|日付|区分|メニュー|常食2F|常食3F|軟菜2F|軟菜3F|ミキサー2F|ミキサー3F|備考|\n|---|---|---|---|---|---|---|---|---|---|\n|01/08|昼|Menu A|2|1|3||||first-pass|",
        },
    )

    captured_uris: list[str] = []

    def _fake_load_bytes(uri: str) -> bytes:
        captured_uris.append(uri)
        if uri == "gs://bucket/corrected.pdf":
            return b"%PDF-corrected\n%EOF\n"
        if uri == "file://dummy.pdf":
            return b"%PDF-raw\n%EOF\n"
        raise AssertionError(f"unexpected uri: {uri}")

    def _fake_extract(pdf_bytes, template, facility_id=None, preferred_template_id=None):
        assert pdf_bytes == b"%PDF-corrected\n%EOF\n"
        return FaxExtractedData(
            facility_name="Test Facility",
            date_strings=["2026-01-08"],
            table_rows=[],
            tokens=[],
            grid=None,
            ocr_provider="gemini",
            raw_text=json.dumps(
                {
                    "facility_name": "Test Facility",
                    "date_strings": ["1/8"],
                    "rows": [
                        {
                            "date_mmdd": "01/08",
                            "daypart": "昼",
                            "menu": "Menu A",
                            "qty.regular_2f": "5",
                            "qty.regular_3f": "1",
                            "qty.soft_2f": "3",
                            "qty.soft_3f": "",
                            "qty.mixer_2f": "",
                            "qty.mixer_3f": "",
                            "remarks": "first-pass",
                        }
                    ],
                    "table_raw": "|...|",
                    "llm_review": {
                        "status": "verified",
                        "needs_more_review": False,
                        "notes": "used corrected pdf",
                        "issues": [],
                    },
                },
                ensure_ascii=False,
            ),
            provider_debug={"provider": "gemini", "model": "review-model-corrected"},
        )

    monkeypatch.setattr(order_service, "load_bytes_from_uri", _fake_load_bytes)
    monkeypatch.setattr(order_service, "extract_fax_data", _fake_extract)

    updated, error = order_service.review_ocr_table_with_llm(
        order["id"],
        provider="gemini",
        pdf_variant="corrected",
    )

    assert error is None
    assert updated is not None
    assert updated["llm_review"]["pdf_variant_requested"] == "corrected"
    assert updated["llm_review"]["pdf_variant_used"] == "corrected"
    assert "pdf_variant_fallback_reason" not in updated["llm_review"]
    assert captured_uris == ["gs://bucket/corrected.pdf"]
    patch_candidate = updated["patch_candidate"]
    apply_result, apply_error = order_service.apply_patch_candidate_to_draft(
        order["id"],
        candidate_id=patch_candidate["id"],
        applied_by="test",
    )
    assert apply_error is None
    assert apply_result is not None

    sheet, sheet_error = order_service.get_ocr_sheet(order["id"])
    assert sheet_error is None
    assert sheet is not None
    assert "sheet_ocr_review_required" not in (sheet.get("warnings") or [])
    issues = sheet.get("cell_issues") or []
    assert issues == []
    assert sheet["rows"][0][3] == "5"
