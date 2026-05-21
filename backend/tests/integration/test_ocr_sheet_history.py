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
from src.services import facility_template_version_service  # noqa: E402
from src.services import ocr_evidence_service  # noqa: E402
from src.services import fax_parser  # noqa: E402
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


def _save_facility_template_columns(facility_id: str, columns: list[dict]) -> None:
    seed_order = _seed_order(
        message_id=f"msg-template-columns-{facility_id}",
        facility_hint=facility_id,
    )
    with session_scope() as session:
        order = session.get(Order, seed_order["id"])
        assert order is not None
        result, error = facility_template_version_service.save_columns_for_order(
            session,
            order=order,
            columns=columns,
            actor="test-versioned-template-columns",
        )
        assert error is None
        assert isinstance(result, dict)


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


def _seed_order(*, message_id: str, facility_hint: str = "FAC00001"):
    _seed_monthly_menu_2026_01()
    payload = IngestEmailPayload(
        message_id=message_id,
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2026, 1, 8, 9, 0, 0),
        facility_hint=facility_hint,
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
    assert rows_by_menu["Menu A"][qty_idx] == ""
    assert rows_by_menu["Menu B"][qty_idx] == ""


def test_get_ocr_sheet_blocks_position_index_quantity_projection_without_menu_identity(monkeypatch):
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
        message_id="msg-sheet-no-position-fallback-001",
        received_at=received_at,
        lines=[],
    )

    ocr_evidence_service.persist_evidence_run(
        order_id=order["id"],
        payload={
            "column_mapping_resolution": {
                "resolved_value": "4:qty.regular_2f",
                "decision_source": "position_fallback",
            },
            "tables": [
                {
                    "table_id": "p1_t1",
                    "page_index": 1,
                    "rows": [
                        ["日付", "区分", "", "献立", "常食", "備考欄"],
                        ["2099-04-15", "昼", "", "", "11", ""],
                        ["2099-04-15", "昼", "", "", "22", ""],
                    ],
                }
            ],
            "table_raw": (
                "|日付|区分||献立|常食|備考欄|\n"
                "|---|---|---|---|---|---|\n"
                "|2099-04-15|昼|||11||\n"
                "|2099-04-15|昼|||22||"
            ),
        },
        schema_version="v1_legacy",
        producer_version="test",
        source="test-evidence",
    )

    def _fail_position_mapping(*_args, **_kwargs):
        raise AssertionError("position fallback must not run on current-sheet quantity path")

    monkeypatch.setattr(order_service, "_apply_menu_position_mapping_safe", _fail_position_mapping)

    sheet, error = order_service.get_ocr_sheet(order["id"])

    assert error is None
    assert sheet is not None
    assert sheet["source"] == "weekly_menu"
    assert "sheet_payload_mapping_blocked_unresolved_template" in (sheet.get("warnings") or [])
    assert "sheet_quantity_column_unmapped" not in (sheet.get("warnings") or [])
    fields = sheet["fields"]
    qty_idx = fields.index("qty.regular_2f")
    menu_idx = fields.index("menu")
    rows_by_menu = {row[menu_idx]: row for row in sheet["rows"]}
    assert rows_by_menu["Menu A"][qty_idx] == ""
    assert rows_by_menu["Menu B"][qty_idx] == ""


def test_get_ocr_sheet_keeps_facility_template_schema_when_payload_template_conflicts():
    order_service.clear_all()
    _seed_monthly_menu_custom_entries(
        month_id="2099-04",
        month_start=date(2099, 4, 1),
        entries=[
            (date(2099, 4, 5), "朝", "豚肉の卵とじ", 0),
        ],
    )
    facility = facility_service.create_facility("Projection Facility", [])
    assert facility_service.update_config(
        facility["id"],
        {
            "fax_template_override": {
                "columns_authoritative": True,
                "columns": [
                    {"index": 0, "role": "date", "header": "日付", "format": "MM/DD"},
                    {"index": 1, "role": "daypart", "header": "区分"},
                    {"index": 2, "role": "aux", "header": "補助"},
                    {"index": 3, "role": "menu_name", "header": "メニュー"},
                    {"index": 4, "role": "quantity", "header": "常食特別", "diet_type": "regular", "area_id": "X"},
                    {"index": 5, "role": "note", "header": "備考"},
                ],
            }
        },
    )

    payload = IngestEmailPayload(
        message_id="msg-facility-schema-payload-template-conflict-001",
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2099, 4, 5, 9, 0, 0),
        facility_hint=facility["id"],
        week_hint=None,
    )
    order = order_service.create_order_from_ingest(
        payload,
        lines=[
            {
                "date": "2099-04-05",
                "daypart": "朝",
                "menu_name": "豚肉の卵とじ",
                "diet_type": "regular",
                "area_id": "X",
                "bag_type": "standard",
                "quantity_original": 5,
            }
        ],
    )
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "template_id": "fax_layout_floor_2f3f_v1",
            "tables": [
                {
                    "table_id": "conflict-table-1",
                    "rows": [
                        ["日付", "区分", "補助", "メニュー", "常食特別", "備考"],
                        ["04/05", "朝", "ま", "豚肉の卵とじ", "5", ""],
                    ],
                }
            ],
            "date_strings": ["04/05"],
        },
    )

    sheet, error = order_service.get_ocr_sheet(order["id"], prefer_order_lines=False)

    assert error is None
    assert sheet is not None
    assert sheet["fields"] == [
        "date_mmdd",
        "daypart",
        "aux.col_2",
        "menu",
        "qty.regular_x",
        "remarks",
    ]
    qty_idx = sheet["fields"].index("qty.regular_x")
    menu_idx = sheet["fields"].index("menu")
    assert sheet["header"][qty_idx] == "常食特別"
    assert sheet["rows"][0][menu_idx] == "豚肉の卵とじ"
    assert sheet["rows"][0][qty_idx] == "5"


def test_build_position_menu_entries_uses_previous_month_menu_when_week_is_covered():
    order_service.clear_all()
    _seed_monthly_menu_custom_entries(
        month_id="2098-03",
        month_start=date(2098, 3, 1),
        entries=[
            (date(2098, 4, 5), "朝", "Covered Breakfast", 0),
            (date(2098, 4, 5), "昼", "Covered Lunch", 1),
        ],
    )

    entries = order_service._build_position_menu_entries_safe("2098-04@2098-04-05~2098-04-11")

    assert [entry["menu_name"] for entry in entries] == ["Covered Breakfast", "Covered Lunch"]
    assert all(entry["menu_date"] == date(2098, 4, 5) for entry in entries)


def test_set_facility_keeps_saved_sheet_until_operator_resolves_context_change():
    order_service.clear_all()
    order = _seed_order(message_id="msg-set-facility-schema-refresh-001")
    facility = facility_service.create_facility("Facility Switch Target", [])
    _save_facility_template_columns(
        facility["id"],
        [
            {"index": 0, "source_index": 0, "role": "date", "header": "日付", "format": "MM/DD"},
            {"index": 1, "source_index": 1, "role": "daypart", "header": "区分"},
            {"index": 2, "source_index": 2, "role": "menu_name", "header": "メニュー"},
            {
                "index": 3,
                "source_index": 3,
                "role": "quantity",
                "header": "施設常食",
                "diet_type": "regular",
                "area_id": "X",
            },
            {"index": 4, "source_index": 4, "role": "note", "header": "備考"},
        ],
    )
    seeded = order_service.persist_sheet_draft(
        order_id=order["id"],
        draft_sheet_json={
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f", "remarks"],
            "header": ["日付", "区分", "メニュー", "旧常食2F", "備考"],
            "rows": [["01/08", "昼", "Menu A", "8", "seeded"]],
            "row_ids": ["row-facility-refresh-1"],
            "source": "draft_ready",
        },
        draft_state="draft_ready",
        blockers=[],
        warnings=[],
        edited_by="test-seed",
    )
    assert seeded is not None

    assert order_service.set_facility(order["id"], facility["id"]) is True

    current = order_service.get_current_sheet_context(order["id"])
    sheet, error = order_service.get_ocr_sheet(order["id"])

    assert current is not None
    assert current["facility_id"] == facility["id"]
    assert error is None
    assert sheet is not None
    assert current["header"][3] == "旧常食2F"
    assert current["fields"][3] == "qty.regular_2f"
    assert sheet["header"][3] == "旧常食2F"
    assert sheet["fields"][3] == "qty.regular_2f"

    repaired, repair_error = order_service.force_overwrite_current_sheet_with_weekly_menu(order["id"])

    assert repair_error is None
    assert isinstance(repaired, dict)
    repaired_payload = repaired["draft_sheet_json"]
    assert repaired_payload["header"][3] == "施設常食"
    assert repaired_payload["fields"][3] == "qty.regular_x"
    assert repaired_payload["rows"][0][3] == "8"

    current = order_service.get_current_sheet_context(order["id"])
    sheet, error = order_service.get_ocr_sheet(order["id"])

    assert current is not None
    assert current["header"][3] == "施設常食"
    assert current["fields"][3] == "qty.regular_x"
    assert current["rows"][0][3] == "8"
    assert error is None
    assert sheet is not None
    assert sheet["header"][3] == "施設常食"
    assert sheet["fields"][3] == "qty.regular_x"
    assert sheet["rows"][0][3] == "8"


def test_set_facility_does_not_mutate_clean_saved_sheet_header_when_fields_match():
    order_service.clear_all()
    order = _seed_order(message_id="msg-set-facility-header-stable-001")
    facility = facility_service.create_facility("Facility Same Field Header", [])
    _save_facility_template_columns(
        facility["id"],
        [
            {"index": 0, "source_index": 0, "role": "date", "header": "日付", "format": "MM/DD"},
            {"index": 1, "source_index": 1, "role": "daypart", "header": "区分"},
            {"index": 2, "source_index": 2, "role": "menu_name", "header": "メニュー"},
            {
                "index": 3,
                "source_index": 3,
                "role": "quantity",
                "header": "別名常食2F",
                "diet_type": "regular",
                "area_id": "2F",
            },
            {"index": 4, "source_index": 4, "role": "note", "header": "備考"},
        ],
    )
    seeded = order_service.persist_sheet_draft(
        order_id=order["id"],
        draft_sheet_json={
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f", "remarks"],
            "header": ["日付", "区分", "メニュー", "旧常食2F", "備考"],
            "rows": [["01/08", "昼", "Menu A", "8", "seeded"]],
            "row_ids": ["row-facility-header-stable-1"],
            "source": "draft_ready",
        },
        draft_state="draft_ready",
        blockers=[],
        warnings=[],
        edited_by="test-seed",
    )
    assert seeded is not None

    assert order_service.set_facility(order["id"], facility["id"]) is True

    current = order_service.get_current_sheet_context(order["id"])
    sheet, error = order_service.get_ocr_sheet(order["id"])

    assert current is not None
    assert current["header"][3] == "旧常食2F"
    assert current["fields"][3] == "qty.regular_2f"
    assert error is None
    assert sheet is not None
    assert sheet["header"][3] == "旧常食2F"
    assert sheet["fields"][3] == "qty.regular_2f"


def test_get_ocr_sheet_does_not_reintroduce_stale_evidence_blockers_for_clean_saved_draft(monkeypatch):
    order_service.clear_all()
    order = _seed_order(message_id="msg-sheet-clean-draft-override-001")
    order_service.persist_ocr_evidence_run(
        order["id"],
        {
            "input_reference": "gs://bucket/orders/order.pdf",
            "pages": [
                {
                    "page_index": 1,
                    "ocr_overlay_uri": "gs://bucket/orders/page1-ocr.png",
                    "layout_overlay_uri": "gs://bucket/orders/page1-layout.png",
                }
            ],
            "table_raw": "\n".join(
                [
                    "|日付|区分|メニュー|常食|",
                    "|---|---|---|---|",
                    "|01/08|昼|Menu A|5|",
                ]
            ),
            "template_resolution": {
                "resolved_template_id": None,
                "candidate_template_ids": ["fax_layout_regular_soft_mixer_forbidden_v1"],
                "confidence": 0.41,
                "blocked": True,
                "blocked_reasons": ["template_resolution_missing"],
            },
            "quantity_subgrid_passes": [],
            "table_box": None,
            "grid_column_edges": [],
            "grid_row_edges": [],
        },
    )
    persisted = order_service.persist_sheet_draft(
        order_id=order["id"],
        draft_sheet_json={
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f", "remarks"],
            "header": ["日付", "区分", "メニュー", "常食2F", "備考"],
            "rows": [["01/08", "昼", "Menu A", "8", ""]],
            "row_ids": ["draft-row-1"],
            "ui_mode": "sheet",
        },
        draft_state="draft_ready",
        blockers=[],
        warnings=[],
    )
    assert persisted is not None
    monkeypatch.setattr(order_service, "_maybe_refresh_semantic_sheet_draft", lambda _order_id, draft: draft)

    sheet, error = order_service.get_ocr_sheet(order["id"])
    current_context = order_service.get_current_sheet_context(order["id"])

    assert error is None
    assert isinstance(sheet, dict)
    assert isinstance(current_context, dict)
    assert sheet["source"] == current_context["source"]
    assert sheet["warnings"] == []
    assert sheet["apply_blockers"] == []
    assert sheet["confirm_blockers"] == []
    assert sheet["can_apply"] is True
    assert sheet["can_confirm"] is True


def test_get_latest_sheet_draft_refresh_prunes_unmatched_stale_rows(monkeypatch):
    order_service.clear_all()
    order = _seed_order(message_id="msg-sheet-refresh-prune-stale-rows-001")
    persisted = order_service.persist_sheet_draft(
        order_id=order["id"],
        draft_sheet_json={
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f", "remarks"],
            "header": ["日付", "区分", "メニュー", "常食2F", "備考"],
            "rows": [
                ["03/22", "昼", "Menu A", "17", ""],
                ["01/01", "\"", "Ghost Menu", "23", ""],
            ],
            "row_ids": ["draft-2", "draft-1"],
            "ui_mode": "sheet",
        },
        draft_state="draft_ready",
        blockers=["sheet_canonical_mismatch"],
        warnings=["sheet_ocr_review_required"],
    )
    assert persisted is not None

    monkeypatch.setattr(
        order_service,
        "_build_best_available_semantic_draft",
        lambda _order_id, use_saved_draft=False: {
            "order_id": order["id"],
            "source": "weekly_menu+ocr_payload",
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f", "remarks"],
            "header": ["日付", "区分", "メニュー", "常食2F", "備考"],
            "rows": [
                ["03/22", "昼", "Menu A", "", ""],
                ["03/23", "昼", "Menu B", "", ""],
            ],
            "row_ids": ["fresh-1", "fresh-2"],
            "warnings": ["sheet_payload_mapping_low_confidence"],
            "ui_mode": "sheet",
        },
    )

    latest = order_service.get_latest_sheet_draft(order["id"])

    assert latest is not None
    assert latest["edited_by"] is None
    sheet = latest["draft_sheet_json"]
    assert sheet["row_ids"] == ["draft-2", "draft-1"]
    assert sheet["rows"] == [
        ["03/22", "昼", "Menu A", "17", ""],
        ["01/01", "\"", "Ghost Menu", "23", ""],
    ]
    assert latest["blockers_json"] == ["sheet_canonical_mismatch"]
    assert latest["warnings_json"] == ["sheet_ocr_review_required"]
    assert any(row[0] == "01/01" for row in sheet["rows"])


def test_get_latest_sheet_draft_refresh_preserves_manual_unmatched_rows_for_clean_draft(monkeypatch):
    order_service.clear_all()
    order = _seed_order(message_id="msg-sheet-refresh-preserve-manual-rows-001")
    persisted = order_service.persist_sheet_draft(
        order_id=order["id"],
        draft_sheet_json={
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f", "remarks"],
            "header": ["日付", "区分", "メニュー", "常食2F", "備考"],
            "rows": [
                ["03/22", "昼", "Menu A", "17", ""],
                ["03/24", "昼", "Manual Extra", "4", "manual"],
            ],
            "row_ids": ["draft-1", "draft-extra"],
            "ui_mode": "sheet",
            "warnings": ["stale-current-warning-should-drop"],
        },
        draft_state="draft_ready",
        blockers=[],
        warnings=[],
    )
    assert persisted is not None

    monkeypatch.setattr(
        order_service,
        "_build_best_available_semantic_draft",
        lambda _order_id, use_saved_draft=False: {
            "order_id": order["id"],
            "source": "weekly_menu+ocr_payload",
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f", "remarks"],
            "header": ["日付", "区分", "メニュー", "常食2F", "備考"],
            "rows": [
                ["03/22", "昼", "Menu A", "", ""],
                ["03/23", "昼", "Menu B", "", ""],
            ],
            "row_ids": ["fresh-1", "fresh-2"],
            "warnings": ["sheet_payload_mapping_low_confidence"],
            "ui_mode": "sheet",
        },
    )

    latest = order_service.get_latest_sheet_draft(order["id"])

    assert latest is not None
    assert latest["edited_by"] is None
    sheet = latest["draft_sheet_json"]
    assert sheet["row_ids"] == ["draft-1", "draft-extra"]
    assert sheet["rows"] == [
        ["03/22", "昼", "Menu A", "17", ""],
        ["03/24", "昼", "Manual Extra", "4", "manual"],
    ]
    assert latest["warnings_json"] == []
    assert sheet["warnings"] == ["stale-current-warning-should-drop"]


def test_get_latest_sheet_draft_refresh_keeps_fresh_quantities_when_stale_current_rows_are_blank(monkeypatch):
    order_service.clear_all()
    order = _seed_order(message_id="msg-sheet-refresh-blank-current-qty-001")
    persisted = order_service.persist_sheet_draft(
        order_id=order["id"],
        draft_sheet_json={
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f", "remarks"],
            "header": ["日付", "区分", "メニュー", "常食2F", "備考"],
            "rows": [
                ["03/22", "昼", "Menu A", "", ""],
                ["03/23", "昼", "Menu B", "", ""],
            ],
            "row_ids": ["draft-1", "draft-2"],
            "ui_mode": "sheet",
        },
        draft_state="auto_apply_blocked",
        blockers=["sheet_quantity_column_unmapped"],
        warnings=["sheet_ocr_review_required"],
    )
    assert persisted is not None

    monkeypatch.setattr(
        order_service,
        "_build_best_available_semantic_draft",
        lambda _order_id, use_saved_draft=False: {
            "order_id": order["id"],
            "source": "weekly_menu+ocr_payload",
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f", "remarks"],
            "header": ["日付", "区分", "メニュー", "常食2F", "備考"],
            "rows": [
                ["03/22", "昼", "Menu A", "23", ""],
                ["03/23", "昼", "Menu B", "18", ""],
            ],
            "row_ids": ["fresh-1", "fresh-2"],
            "warnings": [],
            "ui_mode": "sheet",
        },
    )

    latest = order_service.get_latest_sheet_draft(order["id"])

    assert latest is not None
    assert latest["edited_by"] is None
    sheet = latest["draft_sheet_json"]
    assert sheet["rows"] == [
        ["03/22", "昼", "Menu A", "", ""],
        ["03/23", "昼", "Menu B", "", ""],
    ]
    assert latest["blockers_json"] == ["sheet_quantity_column_unmapped"]
    assert latest["warnings_json"] == ["sheet_ocr_review_required"]


def test_merge_current_draft_quantity_values_into_semantic_sheet_does_not_blank_fresh_values():
    merged = order_service._merge_current_draft_quantity_values_into_semantic_sheet(
        {
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f", "remarks"],
            "rows": [["03/22", "昼", "Menu A", "", ""]],
            "row_ids": ["draft-1"],
        },
        {
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f", "qty.no_meat_x", "remarks"],
            "rows": [["03/22", "昼", "Menu A", "23", "4", ""]],
            "row_ids": ["fresh-1"],
        },
    )

    assert merged is not None
    assert merged["rows"] == [["03/22", "昼", "Menu A", "23", "", ""]]


def test_get_latest_sheet_draft_refresh_rewrites_stale_blockers_even_when_rows_are_unchanged(monkeypatch):
    order_service.clear_all()
    order = _seed_order(message_id="msg-sheet-refresh-clear-stale-blockers-001")
    persisted = order_service.persist_sheet_draft(
        order_id=order["id"],
        draft_sheet_json={
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f", "remarks"],
            "header": ["日付", "区分", "メニュー", "常食2F", "備考"],
            "rows": [["03/22", "昼", "Menu A", "17", ""]],
            "row_ids": ["draft-1"],
            "ui_mode": "sheet",
            "warnings": [],
        },
        draft_state="auto_apply_blocked",
        blockers=["sheet_canonical_mismatch"],
        warnings=[],
    )
    assert persisted is not None

    monkeypatch.setattr(
        order_service,
        "_build_best_available_semantic_draft",
        lambda _order_id, use_saved_draft=False: {
            "order_id": order["id"],
            "source": "weekly_menu+ocr_payload",
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f", "remarks"],
            "header": ["日付", "区分", "メニュー", "常食2F", "備考"],
            "rows": [["03/22", "昼", "Menu A", "17", ""]],
            "row_ids": ["fresh-1"],
            "warnings": [],
            "ui_mode": "sheet",
        },
    )

    latest = order_service.get_latest_sheet_draft(order["id"])

    assert latest is not None
    assert latest["edited_by"] is None
    assert latest["blockers_json"] == ["sheet_canonical_mismatch"]
    assert latest["warnings_json"] == []
    assert latest["draft_sheet_json"]["rows"] == [["03/22", "昼", "Menu A", "17", ""]]


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


def test_get_ocr_output_keeps_first_pass_table_raw_separate_from_edited_table():
    order_service.clear_all()
    order = _seed_order(message_id="msg-ocr-raw-vs-edited-separation-001")
    raw_table = "|日付|区分|メニュー|常食2F|常食3F|軟菜2F|\n|---|---|---|---|---|---|\n|01/08|昼|Menu A|2|1|3|"
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "status": "success",
            "table_raw": raw_table,
            "pages": [
                {
                    "page_index": 1,
                    "markdown_text": raw_table,
                }
            ],
        },
    )

    fields = [
        "date_mmdd",
        "daypart",
        "menu",
        "qty.regular_2f",
        "qty.regular_3f",
        "qty.soft_2f",
    ]
    header = ["日付", "区分", "メニュー", "常食2F", "常食3F", "軟菜2F"]
    rows = [["01/08", "昼", "Menu A", "12", "11", "13"]]
    row_ids = ["row-ocr-separation-1"]

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

    output, output_error = order_service.get_ocr_output(order["id"])

    assert output_error is None
    assert output is not None
    assert output.get("table_raw") == raw_table
    assert output.get("ocr_source") == "edited"
    edited_table = output.get("edited_table")
    assert isinstance(edited_table, dict)
    assert len(edited_table.get("rows") or []) == 1
    assert (edited_table.get("rows") or [])[0][: len(rows[0])] == rows[0]
    with session_scope() as session:
        cache = session.get(OrderOcrCache, order["id"])
        assert cache is not None
        assert isinstance(cache.payload, dict)
        assert cache.payload.get("table_raw") == raw_table


def test_get_ocr_output_without_legacy_edits_blocks_polluted_table_raw_without_raw_snapshot():
    order_service.clear_all()
    order = _seed_order(message_id="msg-ocr-no-legacy-polluted-table-raw-001")
    fields = [
        "date_mmdd",
        "daypart",
        "menu",
        "qty.regular_2f",
        "qty.regular_3f",
        "qty.soft_2f",
    ]
    header = ["日付", "区分", "メニュー", "常食2F", "常食3F", "軟菜2F"]
    polluted_rows = [["01/08", "昼", "Menu A", "70", "137", "137"]]
    polluted_markdown = order_service._build_markdown_table_string(header, polluted_rows)  # noqa: SLF001
    polluted_latest = {
        "revision_id": "OCRREV-polluted-no-raw",
        "edited_at": "2026-02-15T00:00:00",
        "ui_mode": "sheet",
        "fields": fields,
        "header": header,
        "row_ids": ["row-polluted-1"],
        "rows": polluted_rows,
        "row_count": 1,
        "before_digest": "digest-before",
        "after_digest": "digest-after",
        "changed": True,
        "markdown": polluted_markdown,
    }

    with session_scope() as session:
        cache = session.get(OrderOcrCache, order["id"])
        if cache is None:
            cache = OrderOcrCache(order_id=order["id"], payload={})
            session.add(cache)
        cache.payload = {
            "status": "success",
            "table_raw": polluted_markdown,
            "ocr_source": "edited",
            "edited_table": {
                "header": header,
                "rows": polluted_rows,
                "row_ids": ["row-polluted-1"],
                "edited_at": polluted_latest["edited_at"],
                "revision_id": polluted_latest["revision_id"],
            },
            "_edited_ocr": {
                "latest": polluted_latest,
                "revisions": [polluted_latest],
            },
        }

    output, output_error = order_service.get_ocr_output(
        order["id"],
        include_legacy_edits=False,
    )

    assert output_error is None
    assert output is not None
    assert str(output.get("table_raw") or "").strip() == ""
    assert output.get("edited_table") is None
    assert str(output.get("ocr_source") or "").strip().lower() != "edited"


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


def test_get_ocr_pages_preview_only_uses_structured_tables_without_loading_markdown(monkeypatch):
    order_service.clear_all()
    order = _seed_order(message_id="msg-ocr-pages-preview-only-001")
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "pages": [
                {
                    "page_index": 1,
                    "markdown_uri": "gs://bucket/page-1.md",
                    "ocr_overlay_uri": "gs://bucket/ocr-page-1.png",
                    "layout_overlay_uri": "gs://bucket/layout-page-1.png",
                    "figure_uris": ["gs://bucket/page-1.png"],
                    "tables": [
                        {
                            "table_id": "tbl-1",
                            "page_index": 1,
                            "row_count": 2,
                            "col_count": 4,
                            "rows": [
                                ["日付", "区分", "メニュー", "常食"],
                                ["04/26", "朝", "大豆のトマト煮", "11"],
                            ],
                        }
                    ],
                }
            ]
        },
    )

    signed_uris: list[str] = []

    def _capture_signed_url(uri: str | None) -> str | None:
        if not uri:
            return None
        signed_uris.append(uri)
        return f"signed:{uri}"

    monkeypatch.setattr(order_service, "_signed_url_from_uri", _capture_signed_url)
    monkeypatch.setattr(
        order_service,
        "load_bytes_from_uri",
        lambda uri: (_ for _ in ()).throw(AssertionError("markdown should not load for preview_only pages")),
    )

    pages, error = order_service.get_ocr_pages(order["id"], preview_only=True)

    assert error is None
    assert isinstance(pages, dict)
    assert isinstance(pages.get("pages"), list)
    assert len(pages["pages"]) == 1
    page_payload = pages["pages"][0]
    assert page_payload["markdown_text"] is None
    assert page_payload["ocr_overlay_url"] == "signed:gs://bucket/ocr-page-1.png"
    assert page_payload["layout_overlay_url"] is None
    assert page_payload["figure_urls"] == []
    assert pages.get("combined") == {}
    assert signed_uris == ["gs://bucket/ocr-page-1.png"]
    assert page_payload["tables"] == [
        {
            "table_id": "tbl-1",
            "page_index": 1,
            "row_count": 2,
            "col_count": 4,
            "rows": [
                ["日付", "区分", "メニュー", "常食"],
                ["04/26", "朝", "大豆のトマト煮", "11"],
            ],
        }
    ]


def test_get_ocr_pages_does_not_run_heavy_cache_side_effects(monkeypatch):
    order_service.clear_all()
    order = _seed_order(message_id="msg-ocr-pages-light-read-001")
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
            ],
            "hakodate_preprocessing": {
                "target_cell_map": [
                    {
                        "target_cell_id": "cell-1",
                        "sheet_cell": "D3",
                        "bbox": [0.1, 0.2, 0.3, 0.4],
                        "center": [0.2, 0.3],
                    }
                ]
            },
            "hakodate_ocr_evidence_records": [
                {
                    "evidence_id": "ev-1",
                    "engine": "hakodate_cell_crop_ocr",
                    "source_scope": "hakodate_cell_crop_batch",
                    "text": "2",
                    "bbox": [0.12, 0.22, 0.28, 0.38],
                    "center": [0.2, 0.3],
                }
            ],
        },
    )

    monkeypatch.setattr(order_service, "_signed_url_from_uri", lambda uri: f"signed:{uri}" if uri else None)
    monkeypatch.setattr(
        order_service,
        "_augment_hakodate_ocr_payload_artifacts",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("read path must not regenerate Hakodate artifacts")),
    )
    monkeypatch.setattr(
        order_service,
        "persist_ocr_evidence_run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("read path must not persist evidence")),
    )
    monkeypatch.setattr(
        order_service.workflow_state_service,
        "refresh_workflow_state",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("read path must not refresh workflow")),
    )

    pages, error = order_service.get_ocr_pages(
        order["id"],
        preview_only=True,
        quantity_assignment_strategy="hakodate",
    )

    assert error is None
    assert isinstance(pages, dict)
    assert pages["hakodate_overlay_status"] in {"ready", "blocked"}
    assert isinstance(pages.get("hakodate_assignment"), dict)
    assert "target_cells" in pages["hakodate_assignment"]
    assert "assignments" in pages["hakodate_assignment"]
    assert "evidence_records" not in pages["hakodate_assignment"]


def test_hakodate_assignment_rejects_order_document_grid_and_full_page_tesseract():
    payload = {
        "hakodate_preprocessing": {
            "target_cell_map": [
                {
                    "target_cell_id": "order_document_grid-r0c3",
                    "sheet_cell": "R1C4",
                    "worksheet_row": 1,
                    "worksheet_col": 4,
                    "semantic_field": "qty.regular_x",
                    "bbox": [0.1, 0.1, 0.2, 0.2],
                    "center": [0.15, 0.15],
                    "source": "order_document_grid",
                }
            ]
        },
        "hakodate_ocr_evidence_records": [
            {
                "evidence_id": "ev-full-page",
                "run_id": "ORD-test:order-document",
                "engine": "hakodate_full_page_tesseract",
                "source_scope": "order_document_full_page",
                "raw_text": "3",
                "normalized_value": "3",
                "source_bbox": [0.1, 0.1, 0.2, 0.2],
                "center": [0.15, 0.15],
            }
        ],
    }

    assignment = order_service._build_hakodate_evidence_assignment_from_payload(
        order_id="ORD-test",
        facility_id="FAC-test",
        template_id="template-test",
        payload=payload,
    )

    assert assignment["target_cells"] == []
    assert assignment["evidence_records"] == []
    assert "hakodate_target_cell_map_missing" in assignment["blockers"]
    assert "hakodate_ocr_evidence_missing" in assignment["blockers"]


def test_hakodate_overlay_preview_ready_requires_rendered_artifact(monkeypatch):
    target_cell = {
        "target_cell_id": "cell-1",
        "sheet_cell": "D3",
        "bbox": [0.1, 0.2, 0.3, 0.4],
        "center": [0.2, 0.3],
    }
    assignment = {
        "target_cells": [target_cell],
        "evidence_records": [{"evidence_id": "ev-1", "text": "2"}],
        "assignments": [{"target_cell_id": "cell-1", "assigned_value": "2", "sheet_cell": "D3"}],
        "blockers": [],
    }
    saved: dict[str, object] = {}

    monkeypatch.setattr(order_service, "build_order_hakodate_assignment", lambda *_args, **_kwargs: (assignment, None))
    monkeypatch.setattr(order_service, "load_bytes_from_uri", lambda uri: (_ for _ in ()).throw(AssertionError("legacy overlay render must not run")))
    monkeypatch.setattr(order_service, "render_pdf_to_png_bytes", lambda **_kwargs: (_ for _ in ()).throw(AssertionError("legacy overlay render must not run")))
    monkeypatch.setattr(order_service, "get_default_output_bucket", lambda: "bucket")

    def _save_artifact(bucket, job_id, name, data, content_type=None):
        saved.update({"bucket": bucket, "job_id": job_id, "name": name, "data": data, "content_type": content_type})
        return f"gs://{bucket}/artifacts/{job_id}/{name}"

    monkeypatch.setattr(order_service, "_load_order_ocr_cache", lambda _order_id: {})
    monkeypatch.setattr(order_service, "save_artifact_bytes_to_gcs", _save_artifact)
    monkeypatch.setattr(order_service, "_signed_url_from_uri", lambda uri: f"signed:{uri}" if uri else None)

    preview = order_service._build_hakodate_overlay_preview(
        order_id="ORD-render-artifact-test",
        document_uri="gs://bucket/source.pdf",
    )

    assert preview["status"] == "blocked"
    assert "hakodate_overlay_artifact_missing" in preview["blockers"]
    assert saved == {}


def test_hakodate_overlay_preview_uses_pipeline_overlay_for_pipeline_coordinates(monkeypatch):
    target_cell = {
        "target_cell_id": "cell-1",
        "sheet_cell": "D3",
        "bbox": [944, 575, 1114, 638],
        "center": [1029, 606.5],
        "source": "hakodate_best_method_pipeline",
    }
    assignment_item = {"target_cell_id": "cell-1", "assigned_value": "2", "sheet_cell": "D3"}
    assignment = {
        "target_cells": [target_cell],
        "evidence_records": [{"evidence_id": "ev-1", "text": "2"}],
        "assignments": [assignment_item],
        "blockers": [],
    }
    fingerprint = order_service._hakodate_overlay_fingerprint(
        target_cells=[target_cell],
        assignments=[assignment_item],
    )

    monkeypatch.setattr(order_service, "build_order_hakodate_assignment", lambda *_args, **_kwargs: (assignment, None))
    monkeypatch.setattr(
        order_service,
        "_load_order_ocr_cache",
        lambda _order_id: {
            "hakodate_overlay": {
                "uri": "gs://bucket/artifacts/OCR-ORD-test/hakodate-overlay.png",
                "fingerprint": fingerprint,
                "producer": "hakodate_best_method_pipeline",
                "version": order_service.HAKODATE_CANONICAL_PIPELINE_VERSION,
            }
        },
    )
    monkeypatch.setattr(order_service, "_signed_url_from_uri", lambda uri: f"signed:{uri}" if uri else None)
    monkeypatch.setattr(
        order_service,
        "render_pdf_to_png_bytes",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("pipeline overlay must not be redrawn on source PDF")),
    )

    preview = order_service._build_hakodate_overlay_preview(
        order_id="ORD-test",
        document_uri="gs://bucket/source.pdf",
    )

    assert preview["status"] == "ready"
    assert preview["overlay_uri"] == "gs://bucket/artifacts/OCR-ORD-test/hakodate-overlay.png"
    assert preview["overlay_url"] == "signed:gs://bucket/artifacts/OCR-ORD-test/hakodate-overlay.png"


def test_hakodate_overlay_preview_reuses_cached_compact_assignment(monkeypatch):
    cached_assignment = {
        "target_cells": [{"target_cell_id": "cell-1", "bbox": [1, 2, 3, 4]}],
        "assignments": [{"target_cell_id": "cell-1", "assigned_value": "2"}],
        "blockers": [],
    }
    monkeypatch.setattr(
        order_service,
        "_load_order_ocr_cache",
        lambda _order_id: {
            "hakodate_overlay": {
                "uri": "gs://bucket/artifacts/OCR-ORD-test/hakodate-overlay.png",
                "fingerprint": "fp-compact-1",
                "producer": "hakodate_best_method_pipeline",
                "version": order_service.HAKODATE_CANONICAL_PIPELINE_VERSION,
            },
            "hakodate_assignment_preview": {
                "fingerprint": "fp-compact-1",
                "version": order_service.HAKODATE_CANONICAL_PIPELINE_VERSION,
                "assignment": cached_assignment,
            },
        },
    )
    monkeypatch.setattr(
        order_service,
        "build_order_hakodate_assignment",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("cached compact overlay preview must not rebuild assignment")
        ),
    )
    monkeypatch.setattr(order_service, "_signed_url_from_uri", lambda uri: f"signed:{uri}" if uri else None)

    preview = order_service._build_hakodate_overlay_preview(
        order_id="ORD-test",
        document_uri="gs://bucket/source.pdf",
    )

    assert preview["status"] == "ready"
    assert preview["assignment"] == cached_assignment
    assert preview["overlay_url"] == "signed:gs://bucket/artifacts/OCR-ORD-test/hakodate-overlay.png"


def test_hakodate_overlay_preview_blocks_when_pipeline_overlay_missing(monkeypatch):
    assignment = {
        "target_cells": [
            {
                "target_cell_id": "cell-1",
                "sheet_cell": "D3",
                "bbox": [944, 575, 1114, 638],
                "center": [1029, 606.5],
                "source": "hakodate_best_method_pipeline",
            }
        ],
        "evidence_records": [{"evidence_id": "ev-1", "text": "2"}],
        "assignments": [{"target_cell_id": "cell-1", "assigned_value": "2", "sheet_cell": "D3"}],
        "blockers": [],
    }
    saved: dict[str, object] = {}

    monkeypatch.setattr(order_service, "build_order_hakodate_assignment", lambda *_args, **_kwargs: (assignment, None))
    monkeypatch.setattr(order_service, "_load_order_ocr_cache", lambda _order_id: {})
    monkeypatch.setattr(order_service, "load_bytes_from_uri", lambda uri: (_ for _ in ()).throw(AssertionError("legacy overlay render must not run")))
    monkeypatch.setattr(order_service, "render_pdf_to_png_bytes", lambda **_kwargs: (_ for _ in ()).throw(AssertionError("legacy overlay render must not run")))
    monkeypatch.setattr(order_service, "get_default_output_bucket", lambda: "bucket")

    def _save_artifact(bucket, job_id, name, data, content_type=None):
        saved.update({"bucket": bucket, "job_id": job_id, "name": name, "data": data, "content_type": content_type})
        return f"gs://{bucket}/artifacts/{job_id}/{name}"

    monkeypatch.setattr(order_service, "save_artifact_bytes_to_gcs", _save_artifact)
    monkeypatch.setattr(order_service, "_signed_url_from_uri", lambda uri: f"signed:{uri}" if uri else None)

    preview = order_service._build_hakodate_overlay_preview(
        order_id="ORD-pipeline-render-fallback-test",
        document_uri="gs://bucket/source.pdf",
    )

    assert preview["status"] == "blocked"
    assert "hakodate_overlay_artifact_missing" in preview["blockers"]
    assert saved == {}


def test_hakodate_overlay_preview_blocks_when_render_artifact_missing(monkeypatch):
    assignment = {
        "target_cells": [{"target_cell_id": "cell-1", "bbox": [0.1, 0.2, 0.3, 0.4]}],
        "evidence_records": [{"evidence_id": "ev-1", "text": "2"}],
        "assignments": [],
        "blockers": [],
    }

    monkeypatch.setattr(order_service, "build_order_hakodate_assignment", lambda *_args, **_kwargs: (assignment, None))
    monkeypatch.setattr(order_service, "_load_order_ocr_cache", lambda _order_id: {})
    preview = order_service._build_hakodate_overlay_preview(
        order_id="ORD-render-missing-test",
        document_uri="gs://bucket/source.pdf",
    )

    assert preview["status"] == "blocked"
    assert preview["overlay_url"] is None
    assert "hakodate_overlay_artifact_missing" in preview["blockers"]


def test_save_order_ocr_cache_preserves_hakodate_artifacts_when_light_read_payload_lacks_them():
    order_service.clear_all()
    order = _seed_order(message_id="msg-ocr-cache-preserve-hakodate-001")
    target_cell = {
        "target_cell_id": "cell-1",
        "sheet_cell": "D3",
        "bbox": [0.1, 0.2, 0.3, 0.4],
        "center": [0.2, 0.3],
    }
    evidence_record = {
        "evidence_id": "ev-1",
        "engine": "hakodate_cell_crop_ocr",
        "source_scope": "hakodate_cell_crop_batch",
        "text": "2",
        "bbox": [0.12, 0.22, 0.28, 0.38],
        "center": [0.2, 0.3],
    }
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "pages": [{"page_index": 1, "ocr_overlay_uri": "gs://bucket/ocr-page-1.png"}],
            "hakodate_preprocessing": {"target_cell_map": [target_cell]},
            "hakodate_ocr_evidence_records": [evidence_record],
        },
        augment_hakodate_artifacts=False,
        persist_evidence=False,
        refresh_workflow=False,
    )

    order_service._save_order_ocr_cache(
        order["id"],
        {
            "pages": [{"page_index": 1, "ocr_overlay_uri": "gs://bucket/ocr-page-1-updated.png"}],
            "engine": "read-preview",
        },
        augment_hakodate_artifacts=False,
        persist_evidence=False,
        refresh_workflow=False,
    )

    cached = order_service._load_order_ocr_cache(order["id"])

    assert cached is not None
    assert cached["pages"][0]["ocr_overlay_uri"] == "gs://bucket/ocr-page-1-updated.png"
    assert order_service._extract_hakodate_target_cells_from_payload(cached) == [target_cell]
    assert order_service._extract_hakodate_ocr_evidence_records_from_payload(cached, order_id=order["id"]) == [
        evidence_record
    ]


def test_build_hakodate_assignment_repairs_missing_artifacts_and_persists_them(monkeypatch):
    order_service.clear_all()
    order = _seed_order(message_id="msg-hakodate-assignment-repair-001")
    target_cell = {
        "target_cell_id": "cell-1",
        "sheet_cell": "D3",
        "bbox": [0.1, 0.2, 0.3, 0.4],
        "center": [0.2, 0.3],
    }
    evidence_record = {
        "evidence_id": "ev-1",
        "engine": "hakodate_cell_crop_ocr",
        "source_scope": "hakodate_cell_crop_batch",
        "text": "2",
        "bbox": [0.12, 0.22, 0.28, 0.38],
        "center": [0.2, 0.3],
    }
    order_service._save_order_ocr_cache(
        order["id"],
        {"pages": [{"page_index": 1, "ocr_overlay_uri": "gs://bucket/ocr-page-1.png"}]},
        augment_hakodate_artifacts=False,
        persist_evidence=False,
        refresh_workflow=False,
    )

    monkeypatch.setattr(
        order_service,
        "_resolve_effective_sheet_template",
        lambda **_kwargs: (
            {"facility_id": "FAC00001"},
            {"template_id": "template-hakodate", "quantity_assignment_strategy": "hakodate"},
            "template-hakodate",
            None,
        ),
    )
    monkeypatch.setattr(
        order_service,
        "_resolve_llm_review_baseline",
        lambda **_kwargs: {"baseline_source": "test", "rows": []},
    )

    def _fake_augment(*, order_id, payload, template, force_hakodate=False):
        assert order_id == order["id"]
        assert template["template_id"] == "template-hakodate"
        assert force_hakodate is False
        repaired = dict(payload)
        repaired["hakodate_preprocessing"] = {"target_cell_map": [target_cell]}
        repaired["hakodate_ocr_evidence_records"] = [evidence_record]
        return repaired

    monkeypatch.setattr(order_service, "_augment_hakodate_ocr_payload_artifacts", _fake_augment)

    assignment, error = order_service.build_order_hakodate_assignment(order["id"])
    cached = order_service._load_order_ocr_cache(order["id"])

    assert error is None
    assert assignment is not None
    assert assignment["target_cells"] == [target_cell]
    assert assignment["evidence_records"] == [evidence_record]
    assert cached is not None
    assert order_service._extract_hakodate_target_cells_from_payload(cached) == [target_cell]
    assert order_service._extract_hakodate_ocr_evidence_records_from_payload(cached, order_id=order["id"]) == [
        evidence_record
    ]


def test_build_hakodate_assignment_explicit_strategy_forces_repair_when_template_strategy_legacy(monkeypatch):
    order_service.clear_all()
    order = _seed_order(message_id="msg-hakodate-explicit-strategy-repair-001")
    target_cell = {
        "target_cell_id": "cell-1",
        "sheet_cell": "D3",
        "bbox": [0.1, 0.2, 0.3, 0.4],
        "center": [0.2, 0.3],
    }
    evidence_record = {
        "evidence_id": "ev-1",
        "engine": "hakodate_cell_crop_ocr",
        "source_scope": "hakodate_cell_crop_batch",
        "text": "2",
        "bbox": [0.12, 0.22, 0.28, 0.38],
        "center": [0.2, 0.3],
    }
    order_service._save_order_ocr_cache(
        order["id"],
        {"pages": [{"page_index": 1, "ocr_overlay_uri": "gs://bucket/ocr-page-1.png"}]},
        augment_hakodate_artifacts=False,
        persist_evidence=False,
        refresh_workflow=False,
    )
    monkeypatch.setattr(
        order_service,
        "_resolve_effective_sheet_template",
        lambda **_kwargs: (
            {"facility_id": "FAC00001"},
            {"template_id": "template-legacy", "quantity_assignment_strategy": "legacy"},
            "template-legacy",
            None,
        ),
    )
    monkeypatch.setattr(
        order_service,
        "_resolve_llm_review_baseline",
        lambda **_kwargs: {"baseline_source": "test", "rows": []},
    )

    def _fake_augment(*, order_id, payload, template, force_hakodate=False):
        assert order_id == order["id"]
        assert template["template_id"] == "template-legacy"
        assert force_hakodate is True
        repaired = dict(payload)
        repaired["hakodate_preprocessing"] = {"target_cell_map": [target_cell]}
        repaired["hakodate_ocr_evidence_records"] = [evidence_record]
        return repaired

    monkeypatch.setattr(order_service, "_augment_hakodate_ocr_payload_artifacts", _fake_augment)

    assignment, error = order_service.build_order_hakodate_assignment(order["id"], strategy="hakodate")

    assert error is None
    assert assignment is not None
    assert assignment["target_cells"] == [target_cell]
    assert assignment["evidence_records"] == [evidence_record]


def test_build_hakodate_assignment_explicit_strategy_regenerates_stale_artifacts(monkeypatch):
    order_service.clear_all()
    order = _seed_order(message_id="msg-hakodate-explicit-regenerates-stale-001")
    stale_target = {
        "target_cell_id": "cell-1",
        "sheet_cell": "D3",
        "bbox": [0.1, 0.2, 0.3, 0.4],
        "center": [0.2, 0.3],
    }
    fresh_target = {
        "target_cell_id": "cell-1",
        "sheet_cell": "D3",
        "bbox": [0.1, 0.2, 0.3, 0.4],
        "center": [0.2, 0.3],
    }
    stale_evidence = {
        "evidence_id": "ev-stale",
        "engine": "hakodate_cell_crop_ocr",
        "source_scope": "hakodate_cell_crop_batch",
        "text": "9",
        "bbox": [0.12, 0.22, 0.28, 0.38],
        "center": [0.2, 0.3],
    }
    fresh_evidence = {
        "evidence_id": "ev-fresh",
        "engine": "hakodate_cell_crop_ocr",
        "source_scope": "hakodate_cell_crop_batch",
        "text": "2",
        "bbox": [0.12, 0.22, 0.28, 0.38],
        "center": [0.2, 0.3],
    }
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "hakodate_preprocessing": {"target_cell_map": [stale_target]},
            "hakodate_ocr_evidence_records": [stale_evidence],
        },
        augment_hakodate_artifacts=False,
        persist_evidence=False,
        refresh_workflow=False,
    )
    monkeypatch.setattr(
        order_service,
        "_resolve_effective_sheet_template",
        lambda **_kwargs: (
            {"facility_id": "FAC00001"},
            {"template_id": "template-legacy", "quantity_assignment_strategy": "legacy"},
            "template-legacy",
            None,
        ),
    )
    monkeypatch.setattr(
        order_service,
        "_resolve_llm_review_baseline",
        lambda **_kwargs: {"baseline_source": "test", "rows": []},
    )

    def _fake_augment(*, order_id, payload, template, force_hakodate=False):
        assert order_id == order["id"]
        assert force_hakodate is True
        repaired = dict(payload)
        repaired["hakodate_preprocessing"] = {"target_cell_map": [fresh_target]}
        repaired["hakodate_ocr_evidence_records"] = [fresh_evidence]
        return repaired

    monkeypatch.setattr(order_service, "_augment_hakodate_ocr_payload_artifacts", _fake_augment)

    assignment, error = order_service.build_order_hakodate_assignment(order["id"], strategy="hakodate")

    assert error is None
    assert assignment is not None
    assert assignment["evidence_records"] == [fresh_evidence]
    assert stale_evidence not in assignment["evidence_records"]


def test_ensure_hakodate_evidence_draft_current_uses_cache_artifacts_when_evidence_run_missing(monkeypatch):
    order_service.clear_all()
    order = _seed_order(message_id="msg-hakodate-cache-draft-current-001")
    target_cell = {
        "target_cell_id": "D3",
        "sheet_cell": "D3",
        "worksheet_row": 3,
        "worksheet_col": 4,
        "bbox": [0.1, 0.2, 0.3, 0.4],
        "center": [0.2, 0.3],
    }
    evidence_record = {
        "evidence_id": "ev-1",
        "engine": "hakodate_cell_crop_ocr",
        "source_scope": "hakodate_cell_crop_batch",
        "text": "2",
        "bbox": [0.12, 0.22, 0.28, 0.38],
        "center": [0.2, 0.3],
    }
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "hakodate_preprocessing": {"target_cell_map": [target_cell]},
            "hakodate_ocr_evidence_records": [evidence_record],
        },
        augment_hakodate_artifacts=False,
        persist_evidence=False,
        refresh_workflow=False,
    )
    projected_sheet = {
        "fields": ["date", "menu", "qty.regular"],
        "rows": [["04/26", "menu", "2"]],
        "source": "hakodate_ocr_evidence_sheet",
        "hakodate_evidence_projection": {"metrics": {"applied_count": 1}},
    }
    monkeypatch.setattr(order_service, "_latest_hakodate_evidence_available", lambda _order_id: False)
    monkeypatch.setattr(
        order_service,
        "build_order_hakodate_projected_sheet",
        lambda _order_id, **_kwargs: ({"projected_sheet": projected_sheet}, None),
    )

    draft, error = order_service.ensure_hakodate_evidence_draft_current(order["id"])

    assert error is None
    assert draft is not None
    assert draft["draft_sheet_json"]["source"] == "hakodate_ocr_evidence_sheet"
    assert draft["draft_sheet_json"]["rows"][0][2] == "2"


def test_get_ocr_pages_repairs_missing_hakodate_artifacts_for_hakodate_preview(monkeypatch):
    order_service.clear_all()
    order = _seed_order(message_id="msg-ocr-pages-repair-on-hakodate-preview-001")
    target_cell = {
        "target_cell_id": "cell-1",
        "sheet_cell": "D3",
        "bbox": [0.1, 0.2, 0.3, 0.4],
        "center": [0.2, 0.3],
    }
    evidence_record = {
        "evidence_id": "ev-1",
        "engine": "hakodate_cell_crop_ocr",
        "source_scope": "hakodate_cell_crop_batch",
        "text": "2",
        "bbox": [0.12, 0.22, 0.28, 0.38],
        "center": [0.2, 0.3],
    }
    order_service._save_order_ocr_cache(
        order["id"],
        {"pages": [{"page_index": 1, "ocr_overlay_uri": "gs://bucket/ocr-page-1.png"}]},
        augment_hakodate_artifacts=False,
        persist_evidence=False,
        refresh_workflow=False,
    )

    monkeypatch.setattr(order_service, "_signed_url_from_uri", lambda uri: f"signed:{uri}" if uri else None)
    monkeypatch.setattr(
        order_service,
        "_augment_hakodate_ocr_payload_artifacts",
        lambda **_kwargs: {
            "pages": [{"page_index": 1, "ocr_overlay_uri": "gs://bucket/ocr-page-1.png"}],
            "hakodate_preprocessing": {"target_cell_map": [target_cell]},
            "hakodate_ocr_evidence_records": [evidence_record],
        },
    )

    pages, error = order_service.get_ocr_pages(
        order["id"],
        preview_only=True,
        quantity_assignment_strategy="hakodate",
    )

    assert error is None
    assert isinstance(pages, dict)
    assert pages["hakodate_assignment"]["target_cells"] == [target_cell]
    assert pages["hakodate_assignment"]["metrics"]["evidence_count"] == 1


def test_get_ocr_pages_returns_hakodate_overlay_without_legacy_pages(monkeypatch):
    order_service.clear_all()
    order = _seed_order(message_id="msg-ocr-pages-hakodate-overlay-only-001")
    target_cell = {
        "target_cell_id": "H13",
        "sheet_cell": "H13",
        "worksheet_row": 13,
        "worksheet_col": 8,
        "semantic_field": "qty.no_fish_x",
        "bbox": [10, 10, 30, 30],
        "center": [20, 20],
    }
    evidence_record = {
        "evidence_id": "ev-h13",
        "text": "1",
        "normalized_value": "1",
        "bbox": [12, 12, 28, 28],
        "center": [20, 20],
        "confidence": 0.09,
    }
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "hakodate_preprocessing": {"target_cell_map": [target_cell]},
            "hakodate_ocr_evidence_records": [evidence_record],
            "hakodate_overlay": {
                "uri": "gs://bucket/hakodate-overlay.png",
                "fingerprint": "fp",
                "producer": "hakodate_best_method_pipeline",
            },
        },
        augment_hakodate_artifacts=False,
        persist_evidence=False,
        refresh_workflow=False,
    )

    monkeypatch.setattr(order_service, "_signed_url_from_uri", lambda uri: f"signed:{uri}" if uri else None)
    monkeypatch.setattr(
        order_service,
        "_augment_hakodate_ocr_payload_artifacts",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("ocr-pages read path must not repair artifacts")),
    )
    monkeypatch.setattr(
        order_service,
        "_build_hakodate_overlay_preview",
        lambda **_kwargs: {
            "status": "ready",
            "blockers": [],
            "message": "",
            "overlay_uri": "gs://bucket/hakodate-overlay.png",
            "overlay_url": "signed:gs://bucket/hakodate-overlay.png",
            "assignment": {"target_cells": [target_cell], "evidence_records": [evidence_record], "blockers": []},
        },
    )

    pages, error = order_service.get_ocr_pages(
        order["id"],
        preview_only=True,
        quantity_assignment_strategy="hakodate",
    )

    assert error is None
    assert isinstance(pages, dict)
    assert pages["page_count"] == 1
    assert pages["pages"][0]["synthetic_source"] == "hakodate_overlay_only"
    assert pages["pages"][0]["hakodate_overlay_url"] == "signed:gs://bucket/hakodate-overlay.png"
    assert pages["pages"][0]["ocr_overlay_url"] is None
    assert pages["hakodate_overlay_status"] == "ready"


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
    assert pages["page_count"] == 1
    assert pages["active_page_index"] == 1
    assert pages["table_page_index"] == 1
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
    assert sheet["source"] == "weekly_menu+ocr_payload"

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
    assert sheet["source"] == "weekly_menu+ocr_payload"

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


def test_canonicalize_sheet_daypart_rows_defaults_invalid_blocks_by_date_order():
    fields = ["date_mmdd", "daypart", "menu", "qty.regular_2f"]
    rows = [
        ["04/05", '"', "Menu A", ""],
        ["04/05", "61", "Menu B", ""],
        ["04/07", "&", "Menu C", ""],
        ["04/07", "€", "Menu D", ""],
        ["04/11", "品", "Menu E", ""],
    ]

    normalized = order_service._canonicalize_sheet_daypart_rows(rows=rows, fields=fields)

    assert [row[1] for row in normalized] == ["朝", "昼", "朝", "昼", "朝"]


def test_canonicalize_sheet_daypart_rows_respects_supported_anchor_blocks():
    fields = ["date_mmdd", "daypart", "menu", "qty.regular_2f"]
    rows = [
        ["04/05", "61", "Menu A", ""],
        ["04/05", "昼", "Menu B", ""],
        ["04/07", "", "Menu C", ""],
        ["04/07", "夕", "Menu D", ""],
    ]

    normalized = order_service._canonicalize_sheet_daypart_rows(rows=rows, fields=fields)

    assert [row[1] for row in normalized] == ["朝", "昼", "昼", "夕"]


def test_overlay_payload_dayparts_onto_sheet_rows_by_date_order_keeps_menu_rows():
    fields = ["date_mmdd", "daypart", "menu", "qty.regular_2f"]
    rows = [
        {"values": ["04/05", "朝", "豚肉の卵とじ", "4"]},
        {"values": ["04/05", "朝", "いんげんのカニ和え", "4"]},
        {"values": ["04/05", "朝", "サワラの西京焼き", "2"]},
        {"values": ["04/05", "朝", "じゃが芋の煮物", "2"]},
    ]
    payload_rows = [
        ["04/05", "朝", "am", "豚肉の卵とじ"],
        ["", "昼", "W2", "いんげんのカニ和え"],
        ["", "夕", "主人", "サワラの西京焼き"],
        ["", "夕", "DKD", "じゃが芋の煮物"],
    ]

    overlaid, count = order_service._overlay_payload_dayparts_onto_sheet_rows(
        rows=rows,
        fields=fields,
        payload_rows=payload_rows,
    )

    assert count == 3
    assert [row["values"][1] for row in overlaid] == ["朝", "昼", "夕", "夕"]
    assert [row["values"][2] for row in overlaid] == [
        "豚肉の卵とじ",
        "いんげんのカニ和え",
        "サワラの西京焼き",
        "じゃが芋の煮物",
    ]


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


def test_apply_payload_quantities_numeric_only_ignores_payload_daypart_noise_for_row_index_rescue():
    fields = ["date_mmdd", "daypart", "menu", "qty.regular_2f", "remarks"]
    quantity_index = order_service._build_sheet_quantity_index(fields)
    rows = [
        {"values": ["02/15", "朝", "Menu A", "", "keep-a"]},
        {"values": ["02/15", "昼", "Menu B", "", "keep-b"]},
        {"values": ["02/15", "夕", "Menu C", "", "keep-c"]},
    ]
    payload_rows = [
        ["02/15", "朝", "", "20", "payload-note-a"],
        ["", "61", "", "10", "payload-note-b"],
        ["", "€", "", "8", "payload-note-c"],
    ]

    stats = order_service._apply_payload_quantities_numeric_only(
        rows=rows,
        fields=fields,
        quantity_index=quantity_index,
        payload_rows=payload_rows,
    )

    assert stats["row_index"] == 3
    assert rows[0]["values"][3] == "20"
    assert rows[1]["values"][3] == "10"
    assert rows[2]["values"][3] == "8"


def test_apply_payload_quantities_numeric_only_keeps_sparse_column_value_from_direct_payload_row():
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
    assert rows[1]["values"][5] == "58"


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

    assert stats["exact"] >= 3
    assert stats["row_index"] == 0
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

    assert stats["exact"] >= 2
    assert stats["row_index"] == 0
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


def test_apply_payload_quantities_numeric_only_keeps_valid_large_counts():
    fields = [
        "date_mmdd",
        "daypart",
        "menu",
        "qty.soft_x",
        "qty.regular_bag_x",
    ]
    quantity_index = order_service._build_sheet_quantity_index(fields)
    rows = [
        {"values": ["04/05", "夕", "じゃが芋の煮物", "", ""]},
    ]
    payload_rows = [
        ["04/05", "夕", "じゃが芋の煮物", "58", "2"],
    ]

    order_service._apply_payload_quantities_numeric_only(
        rows=rows,
        fields=fields,
        quantity_index=quantity_index,
        payload_rows=payload_rows,
        enable_daypart_consensus=False,
    )

    assert rows[0]["values"][3] == "58"
    assert rows[0]["values"][4] == "2"


def test_parse_sheet_quantity_cell_rejects_embedded_text_tokens():
    assert order_service._parse_sheet_quantity_cell("23") == 23
    assert order_service._parse_sheet_quantity_cell("（23）") == 23
    assert order_service._parse_sheet_quantity_cell("99") == 99
    assert order_service._parse_sheet_quantity_cell("58") == 58
    assert order_service._parse_sheet_quantity_cell("3000") is None
    assert order_service._parse_sheet_quantity_cell("副23") is None
    assert order_service._parse_sheet_quantity_cell("No.23") is None


def test_sanitize_reparse_line_quantities_keeps_valid_large_counts(monkeypatch):
    monkeypatch.delenv("OCR_REPARSE_MAX_QTY", raising=False)
    monkeypatch.delenv("OCR_SHEET_MAX_QTY", raising=False)

    lines = [
        {"menu_name": "A", "quantity_corrected": 58, "quantity_original": 58},
        {"menu_name": "B", "quantity_corrected": 200, "quantity_original": 200},
        {"menu_name": "C", "quantity_corrected": 3000, "quantity_original": 3000},
    ]

    sanitized, stats = order_service._sanitize_reparse_line_quantities(lines)

    assert [line["quantity_corrected"] for line in sanitized] == [58.0, 200.0]
    assert [line["quantity_original"] for line in sanitized] == [58.0, 200.0]
    assert stats["max_abs_qty"] == 999
    assert stats["quantity_dropped"] == 2
    assert stats["lines_dropped"] == 1


def test_get_ocr_sheet_blocks_when_monthly_menu_object_is_missing():
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
    assert isinstance(sheet, dict)
    assert sheet["source"] == "review_blocked"
    assert sheet["rows"] == []
    assert "menu_entries_missing" in (sheet.get("warnings") or [])


def test_current_sheet_context_canonicalizes_invalid_daypart_tokens_when_monthly_menu_is_missing():
    order_service.clear_all()
    with session_scope() as session:
        session.execute(delete(MonthlyMenuEntry).where(MonthlyMenuEntry.monthly_menu_id == "2099-12"))
        session.execute(delete(MonthlyMenu).where(MonthlyMenu.id == "2099-12"))
    payload = IngestEmailPayload(
        message_id="msg-sheet-daypart-canonicalization-001",
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2099, 12, 15, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint=None,
    )
    order = order_service.create_order_from_ingest(payload, lines=None)
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "table_rows": [
                ["12/15", '"', "Menu A", "4", "", "", "", "", "", ""],
                ["12/15", "61", "Menu B", "5", "", "", "", "", "", ""],
                ["12/16", "&", "Menu C", "6", "", "", "", "", "", ""],
                ["12/16", "€", "Menu D", "7", "", "", "", "", "", ""],
                ["12/17", "品", "Menu E", "8", "", "", "", "", "", ""],
            ],
            "date_strings": ["12/15", "12/16", "12/17"],
        },
    )

    sheet, error = order_service.get_ocr_sheet(order["id"])

    assert error is None
    assert isinstance(sheet, dict)
    assert sheet["source"] == "review_blocked"
    assert sheet["rows"] == []
    assert "menu_entries_missing" in (sheet.get("warnings") or [])

    context = order_service.get_current_sheet_context(
        order["id"],
        refresh_draft_from_semantic=True,
        upgrade_generic_from_sheet=True,
        backfill_from_revision=False,
    )

    assert isinstance(context, dict)
    assert context["source"] == "review_blocked"
    assert context["warnings"] == ["menu_entries_missing"]


def test_get_ocr_sheet_blocks_when_monthly_menu_missing_even_if_historical_dayparts_exist():
    order_service.clear_all()
    with session_scope() as session:
        session.execute(delete(MonthlyMenuEntry).where(MonthlyMenuEntry.monthly_menu_id == "2099-12"))
        session.execute(delete(MonthlyMenu).where(MonthlyMenu.id == "2099-12"))

    base_payload = IngestEmailPayload(
        message_id="msg-sheet-history-daypart-source-001",
        pdf_uri="file://historical.pdf",
        received_at=datetime(2099, 12, 15, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2099-12",
    )
    historical_lines = [
        {
            "date": "2099-12-15",
            "daypart": "朝",
            "menu_name": "Menu A",
            "diet_type": "regular",
            "area_id": "2F",
            "bag_type": "standard",
            "quantity_original": 1,
        },
        {
            "date": "2099-12-15",
            "daypart": "昼",
            "menu_name": "Menu B",
            "diet_type": "regular",
            "area_id": "2F",
            "bag_type": "standard",
            "quantity_original": 2,
        },
        {
            "date": "2099-12-16",
            "daypart": "朝",
            "menu_name": "Menu C",
            "diet_type": "regular",
            "area_id": "2F",
            "bag_type": "standard",
            "quantity_original": 3,
        },
        {
            "date": "2099-12-16",
            "daypart": "昼",
            "menu_name": "Menu D",
            "diet_type": "regular",
            "area_id": "2F",
            "bag_type": "standard",
            "quantity_original": 4,
        },
        {
            "date": "2099-12-17",
            "daypart": "朝",
            "menu_name": "Menu E",
            "diet_type": "regular",
            "area_id": "2F",
            "bag_type": "standard",
            "quantity_original": 5,
        },
    ]
    order_service.create_order_from_ingest(base_payload, lines=historical_lines)

    payload = IngestEmailPayload(
        message_id="msg-sheet-history-daypart-source-002",
        pdf_uri="file://current.pdf",
        received_at=datetime(2099, 12, 15, 9, 10, 0),
        facility_hint="FAC00001",
        week_hint=None,
    )
    order = order_service.create_order_from_ingest(payload, lines=None)
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "table_rows": [
                ["12/15", '"', "Menu A", "1", "", "", "", "", "", ""],
                ["12/15", "61", "Menu B", "2", "", "", "", "", "", ""],
                ["12/16", "&", "Menu C", "3", "", "", "", "", "", ""],
                ["12/16", "€", "Menu D", "4", "", "", "", "", "", ""],
                ["12/17", "品", "Menu E", "5", "", "", "", "", "", ""],
            ],
            "date_strings": ["12/15", "12/16", "12/17"],
        },
    )

    sheet, error = order_service.get_ocr_sheet(order["id"])

    assert error is None
    assert isinstance(sheet, dict)
    assert sheet["source"] == "review_blocked"
    assert sheet["rows"] == []
    assert "menu_entries_missing" in (sheet.get("warnings") or [])


def test_build_recoverable_ocr_sheet_payload_stays_blocked_and_stable_when_menu_entries_missing():
    order_service.clear_all()
    with session_scope() as session:
        session.execute(delete(MonthlyMenuEntry).where(MonthlyMenuEntry.monthly_menu_id == "2099-12"))
        session.execute(delete(MonthlyMenu).where(MonthlyMenu.id == "2099-12"))

    payload = IngestEmailPayload(
        message_id="msg-sheet-history-stable-blocked-context-001",
        pdf_uri="file://stable-blocked.pdf",
        received_at=datetime(2099, 12, 15, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint=None,
    )
    order = order_service.create_order_from_ingest(payload, lines=[])
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "table_rows": [
                ["12/15", "朝", "Menu A", "4", "", "", "", "", "", ""],
                ["12/16", "昼", "Menu B", "5", "", "", "", "", "", ""],
            ],
            "date_strings": ["12/15", "12/16"],
        },
    )

    first, first_error = order_service.build_recoverable_ocr_sheet_payload(
        order["id"],
        "menu_entries_missing",
        use_saved_draft=True,
    )
    second, second_error = order_service.build_recoverable_ocr_sheet_payload(
        order["id"],
        "menu_entries_missing",
        use_saved_draft=True,
    )

    assert isinstance(first, dict)
    assert isinstance(second, dict)
    assert first_error is None
    assert second_error is None
    assert first["source"] == "review_blocked"
    assert second["source"] == "review_blocked"
    assert "menu_entries_missing" in (first.get("warnings") or [])
    assert "menu_entries_missing" in (second.get("warnings") or [])
    assert first["rows"] == []
    assert second["rows"] == []
    assert len(first["rows"]) == len(second["rows"]) == 0


def test_build_recoverable_ocr_sheet_payload_prefers_saved_draft_menu_diagnostics_for_menu_blockers():
    order_service.clear_all()
    payload = IngestEmailPayload(
        message_id="msg-sheet-history-saved-draft-menu-diagnostics-001",
        pdf_uri="file://stable-saved-diagnostics.pdf",
        received_at=datetime(2099, 12, 15, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint=None,
    )
    order = order_service.create_order_from_ingest(payload, lines=[])
    order_service.draft_sheet_service.persist_sheet_draft(
        order_id=order["id"],
        draft_sheet_json={
            "source": "review_blocked",
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f"],
            "header": ["日付", "区分", "メニュー", "常食2F"],
            "rows": [],
            "row_ids": [],
            "warnings": ["monthly_menu_object_missing"],
            "menu_diagnostics": {
                "month_id": "2099-12",
                "resolved_week_id": "2099-12",
                "order_codes": ["monthly_menu_object_missing"],
                "row_codes": [],
            },
        },
        draft_state="draft_ready",
        edited_by="test",
    )
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "table_rows": [
                ["12/15", "朝", "Menu A", "4", "", "", "", "", "", ""],
            ],
            "date_strings": ["12/15"],
        },
    )

    recovered, error = order_service.build_recoverable_ocr_sheet_payload(
        order["id"],
        "menu_entries_missing",
        use_saved_draft=True,
    )

    assert error is None
    assert isinstance(recovered, dict)
    assert "monthly_menu_object_missing" in (recovered.get("warnings") or [])
    assert "menu_entries_missing" not in (recovered.get("warnings") or [])
    assert (recovered.get("menu_diagnostics") or {}).get("order_codes") == ["monthly_menu_object_missing"]


def test_get_ocr_sheet_blocks_when_monthly_menu_missing_even_if_historical_dayparts_match_by_date_order():
    order_service.clear_all()
    with session_scope() as session:
        session.execute(delete(MonthlyMenuEntry).where(MonthlyMenuEntry.monthly_menu_id == "2099-12"))
        session.execute(delete(MonthlyMenu).where(MonthlyMenu.id == "2099-12"))

    base_payload = IngestEmailPayload(
        message_id="msg-sheet-history-daypart-order-fallback-001",
        pdf_uri="file://historical.pdf",
        received_at=datetime(2099, 12, 15, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2099-12",
    )
    historical_lines = [
        {
            "date": "2099-12-15",
            "daypart": "朝",
            "menu_name": "Canonical A",
            "diet_type": "regular",
            "area_id": "2F",
            "bag_type": "standard",
            "quantity_original": 1,
        },
        {
            "date": "2099-12-15",
            "daypart": "昼",
            "menu_name": "Canonical B",
            "diet_type": "regular",
            "area_id": "2F",
            "bag_type": "standard",
            "quantity_original": 2,
        },
        {
            "date": "2099-12-16",
            "daypart": "朝",
            "menu_name": "Canonical C",
            "diet_type": "regular",
            "area_id": "2F",
            "bag_type": "standard",
            "quantity_original": 3,
        },
        {
            "date": "2099-12-16",
            "daypart": "昼",
            "menu_name": "Canonical D",
            "diet_type": "regular",
            "area_id": "2F",
            "bag_type": "standard",
            "quantity_original": 4,
        },
    ]
    order_service.create_order_from_ingest(base_payload, lines=historical_lines)

    payload = IngestEmailPayload(
        message_id="msg-sheet-history-daypart-order-fallback-002",
        pdf_uri="file://current.pdf",
        received_at=datetime(2099, 12, 15, 9, 10, 0),
        facility_hint="FAC00001",
        week_hint=None,
    )
    order = order_service.create_order_from_ingest(payload, lines=None)
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "table_rows": [
                ["12/15", '"', "OCR A", "1", "", "", "", "", "", ""],
                ["12/15", "61", "OCR B", "2", "", "", "", "", "", ""],
                ["12/16", "&", "OCR C", "3", "", "", "", "", "", ""],
                ["12/16", "€", "OCR D", "4", "", "", "", "", "", ""],
            ],
            "date_strings": ["12/15", "12/16"],
        },
    )

    sheet, error = order_service.get_ocr_sheet(order["id"])

    assert error is None
    assert isinstance(sheet, dict)
    assert sheet["source"] == "review_blocked"
    assert sheet["rows"] == []
    assert "menu_entries_missing" in (sheet.get("warnings") or [])


def test_get_ocr_sheet_blocks_when_monthly_menu_missing_even_if_ocr_rows_can_be_week_scoped():
    order_service.clear_all()
    with session_scope() as session:
        session.execute(delete(MonthlyMenuEntry).where(MonthlyMenuEntry.monthly_menu_id == "2099-12"))
        session.execute(delete(MonthlyMenu).where(MonthlyMenu.id == "2099-12"))

    base_payload = IngestEmailPayload(
        message_id="msg-sheet-history-daypart-week-scope-001",
        pdf_uri="file://historical.pdf",
        received_at=datetime(2099, 12, 15, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2099-12@2099-12-15~2099-12-17",
    )
    historical_lines = [
        {
            "date": "2099-12-15",
            "daypart": "朝",
            "menu_name": "Menu A",
            "diet_type": "regular",
            "area_id": "2F",
            "bag_type": "standard",
            "quantity_original": 1,
        },
        {
            "date": "2099-12-15",
            "daypart": "昼",
            "menu_name": "Menu B",
            "diet_type": "regular",
            "area_id": "2F",
            "bag_type": "standard",
            "quantity_original": 2,
        },
        {
            "date": "2099-12-16",
            "daypart": "朝",
            "menu_name": "Menu C",
            "diet_type": "regular",
            "area_id": "2F",
            "bag_type": "standard",
            "quantity_original": 3,
        },
    ]
    order_service.create_order_from_ingest(base_payload, lines=historical_lines)

    payload = IngestEmailPayload(
        message_id="msg-sheet-history-daypart-week-scope-002",
        pdf_uri="file://current.pdf",
        received_at=datetime(2099, 12, 15, 9, 10, 0),
        facility_hint="FAC00001",
        week_hint="2099-12@2099-12-15~2099-12-17",
    )
    order = order_service.create_order_from_ingest(payload, lines=None)
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "table_rows": [
                ["12/12", "朝", "Offweek 1", "1", "", "", "", "", "", ""],
                ["12/12", "昼", "Offweek 2", "2", "", "", "", "", "", ""],
                ["12/15", "\"", "Menu A", "1", "", "", "", "", "", ""],
                ["12/15", "61", "Menu B", "2", "", "", "", "", "", ""],
                ["12/16", "&", "Menu C", "3", "", "", "", "", "", ""],
            ],
            "date_strings": ["12/12", "12/15", "12/16"],
        },
    )

    sheet, error = order_service.get_ocr_sheet(order["id"])

    assert error is None
    assert isinstance(sheet, dict)
    assert sheet["source"] == "review_blocked"
    assert sheet["rows"] == []
    assert "monthly_menu_object_missing" in (sheet.get("warnings") or [])


def test_prefer_week_scoped_ocr_entries_falls_back_to_original_rows_when_clip_would_empty():
    entries = [
        {"menu_date": date(2099, 12, 12), "daypart_key": "朝", "menu_name": "Offweek 1"},
        {"menu_date": date(2099, 12, 12), "daypart_key": "昼", "menu_name": "Offweek 2"},
    ]

    scoped = order_service._prefer_week_scoped_ocr_entries(
        entries,
        "2099-12@2099-12-15~2099-12-17",
    )

    assert [item["menu_name"] for item in scoped] == ["Offweek 1", "Offweek 2"]


def test_get_ocr_sheet_blocks_when_monthly_menu_missing_even_if_ocr_rows_can_be_semantically_ordered():
    order_service.clear_all()
    with session_scope() as session:
        session.execute(delete(MonthlyMenuEntry).where(MonthlyMenuEntry.monthly_menu_id == "2099-12"))
        session.execute(delete(MonthlyMenu).where(MonthlyMenu.id == "2099-12"))

    base_payload = IngestEmailPayload(
        message_id="msg-sheet-history-daypart-week-order-001",
        pdf_uri="file://historical.pdf",
        received_at=datetime(2099, 12, 15, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2099-12@2099-12-15~2099-12-17",
    )
    historical_lines = [
        {
            "date": "2099-12-15",
            "daypart": "朝",
            "menu_name": "Menu A",
            "diet_type": "regular",
            "area_id": "2F",
            "bag_type": "standard",
            "quantity_original": 1,
        },
        {
            "date": "2099-12-15",
            "daypart": "昼",
            "menu_name": "Menu B",
            "diet_type": "regular",
            "area_id": "2F",
            "bag_type": "standard",
            "quantity_original": 2,
        },
        {
            "date": "2099-12-16",
            "daypart": "朝",
            "menu_name": "Menu C",
            "diet_type": "regular",
            "area_id": "2F",
            "bag_type": "standard",
            "quantity_original": 3,
        },
        {
            "date": "2099-12-17",
            "daypart": "夕",
            "menu_name": "Menu D",
            "diet_type": "regular",
            "area_id": "2F",
            "bag_type": "standard",
            "quantity_original": 4,
        },
    ]
    order_service.create_order_from_ingest(base_payload, lines=historical_lines)

    payload = IngestEmailPayload(
        message_id="msg-sheet-history-daypart-week-order-002",
        pdf_uri="file://current.pdf",
        received_at=datetime(2099, 12, 15, 9, 10, 0),
        facility_hint="FAC00001",
        week_hint="2099-12@2099-12-15~2099-12-17",
    )
    order = order_service.create_order_from_ingest(payload, lines=None)
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "table_rows": [
                ["12/16", "&", "Menu C", "3", "", "", "", "", "", ""],
                ["12/15", "\"", "Menu A", "1", "", "", "", "", "", ""],
                ["12/17", "品", "Menu D", "4", "", "", "", "", "", ""],
                ["12/15", "61", "Menu B", "2", "", "", "", "", "", ""],
            ],
            "date_strings": ["12/16", "12/15", "12/17"],
        },
    )

    sheet, error = order_service.get_ocr_sheet(order["id"])

    assert error is None
    assert isinstance(sheet, dict)
    assert sheet["source"] == "review_blocked"
    assert sheet["rows"] == []
    assert "monthly_menu_object_missing" in (sheet.get("warnings") or [])


def test_get_ocr_sheet_blocks_broken_structured_daypart_ocr_when_weekly_shell_is_missing():
    order_service.clear_all()
    with session_scope() as session:
        session.execute(delete(MonthlyMenuEntry).where(MonthlyMenuEntry.monthly_menu_id == "2099-12"))
        session.execute(delete(MonthlyMenu).where(MonthlyMenu.id == "2099-12"))

    payload = IngestEmailPayload(
        message_id="msg-sheet-daypart-structured-blocks-001",
        pdf_uri="file://structured.pdf",
        received_at=datetime(2099, 12, 15, 9, 10, 0),
        facility_hint="FAC00001",
        week_hint=None,
    )
    order = order_service.create_order_from_ingest(payload, lines=None)
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "tables": [
                {
                    "table_id": "p1_t1",
                    "page_index": 1,
                    "rows": [
                        ["日付", "区分", "献立", "常食2F"],
                        ["", "", "", ""],
                        ["12/15", "朝", "Menu A", "1"],
                        ["", "", "Menu B", "1"],
                        ["", "&", "Menu C", "1"],
                        ["", "", "Menu D", "1"],
                        ["", "", "Menu E", "1"],
                        ["", "タ", "Menu F", "1"],
                        ["", "", "Menu G", "1"],
                        ["", "", "Menu H", "1"],
                    ],
                    "cells": [
                        {"row_index": 2, "col_index": 1, "row_span": 2, "col_span": 1, "text": "朝"},
                        {"row_index": 4, "col_index": 1, "row_span": 3, "col_span": 1, "text": "&"},
                        {"row_index": 7, "col_index": 1, "row_span": 3, "col_span": 1, "text": "タ"},
                    ],
                }
            ],
            "table_rows": [
                ["12/15", "朝", "Menu A", "1"],
                ["", "", "Menu B", "1"],
                ["", "&", "Menu C", "1"],
                ["", "", "Menu D", "1"],
                ["", "", "Menu E", "1"],
                ["", "タ", "Menu F", "1"],
                ["", "", "Menu G", "1"],
                ["", "", "Menu H", "1"],
            ],
            "date_strings": ["12/15"],
        },
    )

    sheet, error = order_service.get_ocr_sheet(order["id"])

    assert error is None
    assert isinstance(sheet, dict)
    assert sheet["source"] == "review_blocked"
    assert sheet["rows"] == []
    assert "menu_entries_missing" in (sheet.get("warnings") or [])


def test_current_sheet_context_canonicalizes_invalid_daypart_tokens_from_clean_saved_draft():
    order_service.clear_all()
    with session_scope() as session:
        session.execute(delete(MonthlyMenuEntry).where(MonthlyMenuEntry.monthly_menu_id == "2099-12"))
        session.execute(delete(MonthlyMenu).where(MonthlyMenu.id == "2099-12"))
    payload = IngestEmailPayload(
        message_id="msg-sheet-daypart-clean-draft-001",
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2099, 12, 15, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint=None,
    )
    order = order_service.create_order_from_ingest(payload, lines=None)
    saved = order_service.persist_sheet_draft(
        order_id=order["id"],
        draft_sheet_json={
            "order_id": order["id"],
            "source": "draft_sheet",
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f"],
            "header": ["日付", "区分", "メニュー", "常食2F"],
            "rows": [
                ["12/15", '"', "Menu A", "4"],
                ["12/15", "61", "Menu B", "5"],
                ["12/16", "&", "Menu C", "6"],
                ["12/16", "€", "Menu D", "7"],
                ["12/17", "品", "Menu E", "8"],
            ],
            "row_ids": [f"draft-{idx + 1}" for idx in range(5)],
        },
        blockers=[],
        warnings=[],
        edited_by="test",
    )

    assert saved is not None

    context = order_service.get_current_sheet_context(
        order["id"],
        refresh_draft_from_semantic=True,
        upgrade_generic_from_sheet=True,
        backfill_from_revision=False,
    )

    assert isinstance(context, dict)
    assert context["draft_id"] is not None
    daypart_idx = context["fields"].index("daypart")
    menu_idx = context["fields"].index("menu")
    rows_by_menu = {row[menu_idx]: row for row in context["rows"] if menu_idx < len(row)}
    assert [rows_by_menu[name][daypart_idx] for name in ["Menu A", "Menu B", "Menu C", "Menu D", "Menu E"]] == [
        "朝",
        "昼",
        "朝",
        "昼",
        "朝",
    ]


def test_get_ocr_sheet_saved_draft_path_does_not_surface_invalid_daypart_tokens():
    order_service.clear_all()
    with session_scope() as session:
        session.execute(delete(MonthlyMenuEntry).where(MonthlyMenuEntry.monthly_menu_id == "2099-12"))
        session.execute(delete(MonthlyMenu).where(MonthlyMenu.id == "2099-12"))
    payload = IngestEmailPayload(
        message_id="msg-sheet-invalid-daypart-saved-draft-001",
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2099, 12, 15, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint=None,
    )
    order = order_service.create_order_from_ingest(payload, lines=None)
    saved = order_service.persist_sheet_draft(
        order_id=order["id"],
        draft_sheet_json={
            "order_id": order["id"],
            "source": "draft_sheet",
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f"],
            "header": ["日付", "区分", "メニュー", "常食2F"],
            "rows": [
                ["12/15", '"', "Menu A", "4"],
                ["12/15", "61", "Menu B", "5"],
                ["12/16", "&", "Menu C", "6"],
                ["12/16", "€", "Menu D", "7"],
                ["12/17", "品", "Menu E", "8"],
            ],
            "row_ids": [f"draft-{idx + 1}" for idx in range(5)],
        },
        blockers=[],
        warnings=[],
        edited_by="test",
    )

    assert saved is not None

    sheet, error = order_service.get_ocr_sheet(order["id"])
    current_context = order_service.get_current_sheet_context(order["id"])

    assert error is None
    assert sheet is not None
    assert isinstance(current_context, dict)
    assert sheet["source"] == current_context["source"]
    daypart_idx = sheet["fields"].index("daypart")
    assert all(
        daypart_idx < len(row) and row[daypart_idx] in {"朝", "昼", "夕"}
        for row in sheet["rows"]
    )


def test_get_latest_sheet_draft_rebases_clean_saved_draft_to_weekly_shell(monkeypatch):
    order_service.clear_all()
    order = _seed_order(message_id="msg-weekly-shell-rebase-clean-draft-001")

    saved = order_service.persist_sheet_draft(
        order_id=order["id"],
        draft_sheet_json={
            "order_id": order["id"],
            "source": "draft_sheet",
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f", "remarks"],
            "header": ["日付", "区分", "メニュー", "常食2F", "備考"],
            "rows": [
                ["01/08", "朝", "Menu A", "7", ""],
                ["01/08", "夕", "Menu B", "8", ""],
                ["01/08", "夕", "Menu C WRONG", "9", ""],
            ],
            "row_ids": ["draft-1", "draft-2", "draft-3"],
        },
        draft_state="draft_ready",
        blockers=[],
        warnings=[],
        edited_by="test",
    )
    assert saved is not None

    monkeypatch.setattr(
        order_service,
        "_build_best_available_semantic_draft",
        lambda order_id, use_saved_draft=False, evidence_run_override=None: {
            "order_id": order["id"],
            "source": "weekly_menu+ocr_payload",
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f", "remarks"],
            "header": ["日付", "区分", "メニュー", "常食2F", "備考"],
            "rows": [
                ["01/08", "朝", "Menu A", "1", ""],
                ["01/08", "昼", "Menu B", "2", ""],
                ["01/08", "夕", "Menu C", "3", ""],
            ],
            "row_ids": ["fresh-1", "fresh-2", "fresh-3"],
            "warnings": [],
        },
    )

    latest = order_service.get_latest_sheet_draft(
        order["id"],
        backfill_from_revision=False,
        upgrade_generic_from_sheet=False,
    )

    assert latest is not None
    payload = latest["draft_sheet_json"]
    assert payload["source"] == "draft_sheet"
    assert payload["rows"] == [
        ["01/08", "朝", "Menu A", "7", ""],
        ["01/08", "夕", "Menu B", "8", ""],
        ["01/08", "夕", "Menu C WRONG", "9", ""],
    ]




def test_save_reparse_candidate_as_draft_canonicalizes_invalid_daypart_tokens():
    order_service.clear_all()
    with session_scope() as session:
        session.execute(delete(MonthlyMenuEntry).where(MonthlyMenuEntry.monthly_menu_id == "2026-04"))
        session.execute(delete(MonthlyMenu).where(MonthlyMenu.id == "2026-04"))
    payload = IngestEmailPayload(
        message_id="msg-sheet-invalid-daypart-reparse-candidate-001",
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2026, 4, 5, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint=None,
    )
    order = order_service.create_order_from_ingest(payload, lines=[])

    order_service._save_reparse_candidate_as_draft(
        order_id=order["id"],
        template={
            "main_ocr_row_fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f"],
        },
        rows=[
            ["04/05", '"', "Menu A", "4"],
            ["04/05", "61", "Menu B", "5"],
            ["04/06", "&", "Menu C", "6"],
            ["04/06", "€", "Menu D", "7"],
            ["04/07", "品", "Menu E", "8"],
        ],
        before_digest="before",
        review_state="auto_apply_blocked",
        review_blockers=["quantity_review_required"],
        review_warnings=[],
    )

    latest = order_service.get_latest_sheet_draft(
        order["id"],
        backfill_from_revision=False,
        upgrade_generic_from_sheet=False,
    )

    assert latest is not None
    draft_payload = latest["draft_sheet_json"]
    assert latest["latest_patch_candidate_id"] == "reparse-candidate"
    daypart_idx = draft_payload["fields"].index("daypart")
    assert [row[daypart_idx] for row in draft_payload["rows"][:5]] == ["朝", "昼", "朝", "昼", "朝"]


def test_get_current_sheet_context_ignores_reparse_candidate_draft_for_current_surface():
    order_service.clear_all()
    with session_scope() as session:
        session.execute(delete(MonthlyMenuEntry).where(MonthlyMenuEntry.monthly_menu_id == "2099-12"))
        session.execute(delete(MonthlyMenu).where(MonthlyMenu.id == "2099-12"))
    payload = IngestEmailPayload(
        message_id="msg-sheet-current-context-ignores-reparse-candidate-001",
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2099, 12, 15, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint=None,
    )
    order = order_service.create_order_from_ingest(payload, lines=[])
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "table_rows": [
                ["12/15", "朝", "Menu A", "4", "", "", "", "", "", ""],
                ["12/15", "昼", "Menu B", "5", "", "", "", "", "", ""],
            ],
            "date_strings": ["12/15"],
        },
    )
    saved = order_service.persist_sheet_draft(
        order_id=order["id"],
        draft_sheet_json={
            "source": "reparse_candidate",
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f"],
            "header": ["日付", "区分", "メニュー", "常食2F"],
            "rows": [["12/15", "夕", "Wrong Menu", "9"]],
            "row_ids": ["draft-1"],
        },
        draft_state="auto_apply_blocked",
        blockers=["sheet_llm_audit_failed"],
        warnings=[],
        edited_by="reparse-reject-candidate",
    )
    assert saved is not None

    context = order_service.get_current_sheet_context(
        order["id"],
        refresh_draft_from_semantic=True,
        upgrade_generic_from_sheet=True,
        backfill_from_revision=False,
    )

    assert isinstance(context, dict)
    assert context["draft_id"] is None
    assert context["source"] == "review_blocked"
    assert context["rows"] == []
    assert "menu_entries_missing" in (context["warnings"] or [])
    assert "Wrong Menu" not in [cell for row in (context["rows"] or []) for cell in row]


def test_get_ocr_sheet_does_not_rehydrate_revision_into_current_surface(monkeypatch):
    order_service.clear_all()
    with session_scope() as session:
        session.execute(delete(MonthlyMenuEntry).where(MonthlyMenuEntry.monthly_menu_id == "2099-12"))
        session.execute(delete(MonthlyMenu).where(MonthlyMenu.id == "2099-12"))
    payload = IngestEmailPayload(
        message_id="msg-sheet-ignore-revision-current-surface-001",
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2099, 12, 15, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint=None,
    )
    order = order_service.create_order_from_ingest(payload, lines=[])
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "table_rows": [
                ["12/15", "朝", "Menu A", "4", "", "", "", "", "", ""],
            ],
            "date_strings": ["12/15"],
        },
    )

    calls = {"count": 0}

    def _unexpected_revision(**_kwargs):
        calls["count"] += 1
        return {
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f"],
            "header": ["日付", "区分", "メニュー", "常食2F"],
            "rows": [["01/01", "朝", "Revision Menu", "9"]],
            "row_ids": ["rev-1"],
            "revision_id": "REV-1",
        }

    monkeypatch.setattr(order_service, "_select_order_sheet_revision", _unexpected_revision)

    sheet, error = order_service.get_ocr_sheet(order["id"])

    assert error is None
    assert isinstance(sheet, dict)
    assert sheet["source"] == "review_blocked"
    assert sheet["rows"] == []
    assert "menu_entries_missing" in (sheet.get("warnings") or [])
    assert calls["count"] == 0


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


def test_apply_ocr_table_preserves_explicit_selected_week_over_stale_materialization_candidate(monkeypatch):
    order_service.clear_all()
    order = _seed_order(message_id="msg-sheet-explicit-week-preserve-001")
    assert order_service.set_week(order["id"], "2026-05@2026-05-01~2026-05-02") is True
    refreshed_before = order_service.get_order_by_id(order["id"])
    assert refreshed_before is not None
    explicit_week = refreshed_before["persisted_week_value"]

    monkeypatch.setattr(
        order_service,
        "_build_materialization_candidate_from_draft_record",
        lambda *args, **kwargs: {
            "lines": [
                {
                    "date": "2026-01-08",
                    "daypart": "昼",
                    "menu_name": "Menu A",
                    "diet_type": "regular",
                    "area_id": "2F",
                    "bag_type": "standard",
                    "quantity_original": 3,
                    "quantity_corrected": None,
                    "change_note": "manual",
                }
            ],
            "derived_week_code": "2026-01@2026-01-04~2026-01-10",
        },
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
    rows = [["01/08", "昼", "Menu A", "3", "1", "", "", "", "", "manual"]]

    updated, error = order_service.apply_ocr_table(
        order["id"],
        header=header,
        rows=rows,
        ui_mode="sheet",
        fields=fields,
        row_ids=["row-explicit-week-1"],
    )

    assert error is None
    assert updated is not None
    refreshed = order_service.get_order_by_id(order["id"])
    assert refreshed is not None
    assert refreshed["persisted_week_value"] == explicit_week


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


def test_parse_order_lines_does_not_fill_forward_blank_menu_name_in_large_cell_mode():
    template = {
        "header_rows": 0,
        "large_cell_mode": True,
        "fill_forward_roles": ["date", "daypart", "menu_name"],
        "fill_missing_date_with_first_seen": True,
        "columns": [
            {"index": 0, "role": "date"},
            {"index": 1, "role": "daypart"},
            {"index": 2, "role": "menu_name"},
            {"index": 3, "role": "quantity", "diet_type": "soft", "area_id": "X"},
        ],
    }
    rows = [
        ["4/8(水)", "朝", "アジの南蛮漬 さつま芋の煮物", "1"],
        ["", "", "", "1"],
        ["", "", "小松菜のおかか和え", "1"],
    ]

    lines = fax_parser.parse_order_lines(
        rows,
        template,
        datetime(2026, 4, 8, 9, 0, 0),
        quantity_rules={"zero_as_empty": False},
    )

    menu_names = [line.get("menu_name") for line in lines]
    assert menu_names.count("アジの南蛮漬 さつま芋の煮物") == 1
    assert menu_names.count("小松菜のおかか和え") == 1
    assert None in menu_names


def test_build_position_menu_entries_from_ocr_payload_does_not_duplicate_blank_menu_rows(monkeypatch):
    template = {
        "main_ocr_row_fields": ["date_mmdd", "daypart", "menu", "qty.soft_x", "qty.regular_bag_x"],
        "large_cell_mode": True,
        "fill_forward_roles": ["date", "daypart", "menu_name"],
        "fill_missing_date_with_first_seen": True,
    }
    rows = [
        ["4/8(水)", "朝", "アジの南蛮漬 さつま芋の煮物", "0", "0"],
        ["", "", "", "0", "0"],
        ["", "", "小松菜のおかか和え", "0", "0"],
    ]

    monkeypatch.setattr(
        order_service,
        "_extract_sheet_rows_from_payload_uncanonicalized",
        lambda payload, template: rows,
    )

    entries = order_service._build_position_menu_entries_from_ocr_payload(
        payload={"tables": []},
        template=template,
        received_at=datetime(2026, 4, 8, 9, 0, 0),
    )

    assert [item.get("menu_name") for item in entries] == [
        "アジの南蛮漬 さつま芋の煮物",
        "小松菜のおかか和え",
    ]


def test_get_ocr_sheet_rejects_broken_ocr_shell_when_no_weekly_source_exists(monkeypatch):
    order_service.clear_all()
    with session_scope() as session:
        session.execute(delete(MonthlyMenuEntry).where(MonthlyMenuEntry.monthly_menu_id == "2099-04"))
        session.execute(delete(MonthlyMenu).where(MonthlyMenu.id == "2099-04"))
    payload = IngestEmailPayload(
        message_id="msg-reconstructed-order-001",
        pdf_uri="file://current.pdf",
        received_at=datetime(2099, 4, 8, 9, 0, 0),
        facility_hint="FAC00005",
        week_hint=None,
    )
    order = order_service.create_order_from_ingest(payload, lines=None)

    ocr_payload = {
        "table_rows": [
            ["4/8(水)", "朝", "けんちん煮", "0", "0", "", "", "", "", ""],
            ["", "", "白菜と平天のお浸し", "0", "0", "", "", "", "", ""],
            ["", "", "アジの南蛮漬 さつま芋の煮物", "0", "0", "", "", "", "", ""],
            ["", "", "", "0", "0", "", "", "", "", ""],
            ["", "", "小松菜のおかか和え", "0", "0", "", "", "", "", ""],
            ["", "昼", "鶏肉の治部煮", "58", "2", "", "", "", "", ""],
            ["", "", "ポテトソテー", "58", "2", "", "", "", "", ""],
            ["", "夕", "ならあえ", "58", "2", "", "", "", "", ""],
        ],
        "date_strings": ["4/8"],
    }
    order_service._save_order_ocr_cache(order["id"], ocr_payload)

    sheet, error = order_service.get_ocr_sheet(order["id"])

    assert error is None
    assert isinstance(sheet, dict)
    assert sheet["source"] == "review_blocked"
    assert sheet["rows"] == []
    assert "menu_entries_missing" in (sheet.get("warnings") or [])


def test_force_overwrite_current_sheet_with_weekly_menu_persists_repair_mode_and_preserves_quantities(monkeypatch):
    order_service.clear_all()
    order = _seed_order(message_id="msg-force-weekly-001")

    seeded = order_service.persist_sheet_draft(
        order_id=order["id"],
        draft_sheet_json={
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f", "remarks"],
            "header": ["日付", "区分", "メニュー", "常食2F", "備考"],
            "rows": [["01/08", "昼", "Menu A", "7", ""]],
            "row_ids": ["row-force-weekly-1"],
            "source": "draft_sheet",
        },
        draft_state="draft_ready",
        blockers=[],
        warnings=[],
        edited_by="test-seed",
    )
    assert seeded is not None

    monkeypatch.setattr(
        order_service,
        "get_ocr_sheet",
        lambda order_id, use_saved_draft=False, evidence_run_override=None: (
            {
                "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f", "remarks"],
                "header": ["日付", "区分", "メニュー", "常食2F", "備考"],
                "resolved_week_id": "2026-01@2026-01-08~2026-01-08",
                "week_id": "2026-01@2026-01-08~2026-01-08",
                "facility_id": "FAC00001",
            },
            None,
        ),
    )
    monkeypatch.setattr(
        order_service,
        "_build_position_menu_entries_safe",
        lambda week_id, facility_id=None: [
            {
                "menu_name": "Menu A",
                "menu_date": date(2026, 1, 8),
                "daypart_key": "昼",
                "slot_index": 0,
                "order": 0,
            }
        ],
    )

    repaired, error = order_service.force_overwrite_current_sheet_with_weekly_menu(order["id"])

    assert error is None
    assert isinstance(repaired, dict)
    payload = repaired["draft_sheet_json"]
    qty_idx = payload["fields"].index("qty.regular_2f")
    assert payload["rows"][0][qty_idx] == "7"
    assert payload["repair_mode"] == "forced_weekly_menu_overwrite"
    assert "forced_weekly_menu_overwrite" in payload["warnings"]

    current = order_service.get_current_sheet_context(order["id"])
    assert isinstance(current, dict)
    assert current.get("repair_mode") == "forced_weekly_menu_overwrite"
    assert current["rows"][0][qty_idx] == "7"


def test_force_overwrite_current_sheet_with_facility_schema_blanks_quantities_and_survives_refresh(monkeypatch):
    order_service.clear_all()
    order = _seed_order(message_id="msg-force-facility-001")

    monkeypatch.setattr(
        order_service,
        "_build_best_available_semantic_draft",
        lambda order_id, use_saved_draft=False, evidence_run_override=None: {
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f", "remarks"],
            "header": ["日付", "区分", "メニュー", "常食2F", "備考"],
            "rows": [["01/08", "昼", "Menu A", "9", "from-ocr"]],
            "row_ids": ["row-force-facility-1"],
            "source": "weekly_menu+ocr_payload",
            "warnings": ["quantity_review_required"],
        },
    )

    repaired, error = order_service.force_overwrite_current_sheet_with_facility_schema(
        order["id"],
        blank_quantities=True,
    )

    assert error is None
    assert isinstance(repaired, dict)
    payload = repaired["draft_sheet_json"]
    qty_idx = payload["fields"].index("qty.regular_2f")
    assert payload["rows"][0][qty_idx] == ""
    assert payload["repair_mode"] == "forced_facility_schema_overwrite"
    assert "forced_facility_schema_overwrite" in payload["warnings"]
    assert "forced_quantity_manual_entry_required" in payload["warnings"]

    monkeypatch.setattr(
        order_service,
        "_build_best_available_semantic_draft",
        lambda order_id, use_saved_draft=False, evidence_run_override=None: {
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f", "remarks"],
            "header": ["日付", "区分", "メニュー", "常食2F", "備考"],
            "rows": [["01/08", "昼", "WRONG", "99", "stale"]],
            "row_ids": ["row-force-facility-1"],
            "source": "weekly_menu+ocr_payload",
            "warnings": [],
        },
    )

    current = order_service.get_current_sheet_context(order["id"])
    assert isinstance(current, dict)
    assert current.get("repair_mode") == "forced_facility_schema_overwrite"
    assert current["rows"][0][2] == "Menu A"
    assert current["rows"][0][qty_idx] == ""


def test_force_overwrite_current_sheet_with_weekly_menu_can_blank_quantities(monkeypatch):
    order_service.clear_all()
    order = _seed_order(message_id="msg-force-weekly-blank-001")

    monkeypatch.setattr(
        order_service,
        "get_ocr_sheet",
        lambda _order_id, **_kwargs: (
            {
                "rows": [["01/08", "昼", "Menu A", "7", "from-current"]],
                "row_ids": ["row-force-weekly-blank-1"],
                "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f", "remarks"],
                "header": ["日付", "区分", "メニュー", "常食2F", "備考"],
                "resolved_week_id": "2026-01@2026-01-08~2026-01-08",
                "week_id": "2026-01@2026-01-08~2026-01-08",
                "facility_id": "FAC00001",
            },
            None,
        ),
    )
    monkeypatch.setattr(
        order_service,
        "_build_position_menu_entries_safe",
        lambda week_id, facility_id=None: [
            {
                "menu_name": "Menu A",
                "menu_date": date(2026, 1, 8),
                "daypart_key": "昼",
                "slot_index": 0,
                "order": 0,
            }
        ],
    )

    repaired, error = order_service.force_overwrite_current_sheet_with_weekly_menu(
        order["id"],
        blank_quantities=True,
    )

    assert error is None
    assert isinstance(repaired, dict)
    payload = repaired["draft_sheet_json"]
    qty_idx = payload["fields"].index("qty.regular_2f")
    assert payload["rows"][0][qty_idx] == ""
    assert payload["repair_mode"] == "forced_weekly_menu_overwrite"
    assert "forced_weekly_menu_overwrite" in payload["warnings"]
    assert "forced_quantity_manual_entry_required" in payload["warnings"]


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


def test_get_ocr_sheet_prefers_ocr_payload_lines_over_raw_payload_rows(monkeypatch):
    order_service.clear_all()
    payload = IngestEmailPayload(
        message_id="msg-sheet-ocr-lines-over-payload-rows-001",
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
                ["01/08", "朝", "Menu A", "99", ""],
                ["01/09", "朝", "Menu B", "88", ""],
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
    trace_rows = (sheet.get("trace") or {}).get("rows") or []
    assert trace_rows[0][qty_idx] == "ocr_payload"
    assert trace_rows[1][qty_idx] == "ocr_payload"


def test_get_ocr_sheet_weekly_menu_shell_uses_first_pass_rows_for_raw_block_projection(monkeypatch):
    order_service.clear_all()
    _seed_monthly_menu_custom_entries(
        month_id="2026-04",
        month_start=date(2026, 4, 1),
        entries=[
            (date(2026, 4, 29), "朝", "Menu A", 0),
            (date(2026, 4, 29), "朝", "Menu B", 1),
            (date(2026, 4, 29), "昼", "Menu C", 0),
            (date(2026, 4, 29), "昼", "Menu D", 1),
            (date(2026, 4, 29), "昼", "Menu E", 2),
        ],
    )
    payload = IngestEmailPayload(
        message_id="msg-sheet-first-pass-raw-block-001",
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2026, 4, 29, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint=None,
    )
    order = order_service.create_order_from_ingest(payload, lines=[])

    def _fake_get_ocr_output_without_legacy_edits(_order_id, persist_cache=False):
        return ({"status": "success", "tables": []}, None)

    def _fake_build_sheet_menu_entries(**_kwargs):
        return ([{"row": idx} for idx in range(5)], "weekly_menu")

    def _fake_build_rows_from_menu_entries(**kwargs):
        fields = kwargs["fields"]
        field_index = kwargs["field_index"]
        rows = []
        for row_id, menu_date, mmdd, daypart, menu_name in (
            ("row-1", date(2026, 4, 29), "04/29", "朝", "Menu A"),
            ("row-2", date(2026, 4, 29), "04/29", "朝", "Menu B"),
            ("row-3", date(2026, 4, 29), "04/29", "昼", "Menu C"),
            ("row-4", date(2026, 4, 29), "04/29", "昼", "Menu D"),
            ("row-5", date(2026, 4, 29), "04/29", "昼", "Menu E"),
        ):
            values = [""] * len(fields)
            values[field_index["date_mmdd"]] = mmdd
            values[field_index["daypart"]] = daypart
            values[field_index["menu"]] = menu_name
            rows.append(
                {
                    "row_id": row_id,
                    "values": values,
                    "identity": order_service._sheet_row_identity(menu_date, daypart, menu_name),
                }
            )
        return rows, "weekly_menu"

    def _fake_build_sheet_lines_from_ocr_payload(**_kwargs):
        return [
            {
                "date": date(2026, 4, 20),
                "daypart": "朝",
                "menu_name": "Menu B",
                "diet_type": "regular",
                "area_id": "2F",
                "quantity_original": 70,
            }
        ]

    def _build_payload_row(template, *, date_text, daypart, menu, regular):
        fields, field_index = order_service._build_sheet_fields_and_indexes(template)
        values = [""] * len(fields)
        values[field_index["date_mmdd"]] = date_text
        values[field_index["daypart"]] = daypart
        values[field_index["menu"]] = menu
        values[field_index["qty.regular_2f"]] = str(regular)
        return values

    def _fake_extract_sheet_rows_from_payload(_payload, template):
        return [
            _build_payload_row(template, date_text="04/20", daypart="朝", menu="Polluted A", regular=999),
            _build_payload_row(template, date_text="", daypart="朝", menu="Polluted B", regular=999),
        ]

    def _fake_extract_first_pass_rows_from_payload(_payload, template):
        return [
            _build_payload_row(template, date_text="04/20", daypart="朝", menu="", regular=70),
            _build_payload_row(template, date_text="", daypart="朝", menu="Menu B", regular=70),
            _build_payload_row(template, date_text="", daypart="昼", menu="Menu C", regular=58),
            _build_payload_row(template, date_text="", daypart="昼", menu="Menu D", regular=59),
            _build_payload_row(template, date_text="", daypart="昼", menu="Menu E", regular=60),
        ]

    monkeypatch.setattr(order_service, "_get_ocr_output_without_legacy_edits", _fake_get_ocr_output_without_legacy_edits)
    monkeypatch.setattr(order_service, "_build_sheet_menu_entries", _fake_build_sheet_menu_entries)
    monkeypatch.setattr(order_service, "_build_rows_from_menu_entries", _fake_build_rows_from_menu_entries)
    monkeypatch.setattr(order_service, "_build_sheet_lines_from_ocr_payload", _fake_build_sheet_lines_from_ocr_payload)
    monkeypatch.setattr(order_service, "_extract_sheet_rows_from_payload", _fake_extract_sheet_rows_from_payload)
    monkeypatch.setattr(order_service, "_extract_first_pass_rows_from_payload", _fake_extract_first_pass_rows_from_payload)

    sheet, error = order_service.get_ocr_sheet(order["id"])

    assert error is None
    assert sheet is not None
    assert sheet["source"] == "weekly_menu+ocr_payload"
    assert (sheet.get("trace") or {}).get("mapped_mode") == "raw_ocr_block"
    qty_idx = sheet["fields"].index("qty.regular_2f")
    menu_idx = sheet["fields"].index("menu")
    actual = {row[menu_idx]: row[qty_idx] for row in sheet["rows"]}
    assert actual == {
        "Menu A": "70",
        "Menu B": "70",
        "Menu C": "58",
        "Menu D": "59",
        "Menu E": "60",
    }
    assert "sheet_quantity_column_unmapped" not in (sheet.get("warnings") or [])


def test_get_ocr_sheet_weekly_menu_shell_blocks_unresolved_first_pass_groups(monkeypatch):
    order_service.clear_all()
    _seed_monthly_menu_custom_entries(
        month_id="2026-04",
        month_start=date(2026, 4, 1),
        entries=[
            (date(2026, 4, 29), "朝", "Menu A", 0),
            (date(2026, 4, 29), "朝", "Menu B", 1),
        ],
    )
    payload = IngestEmailPayload(
        message_id="msg-sheet-first-pass-unresolved-001",
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2026, 4, 29, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint=None,
    )
    order = order_service.create_order_from_ingest(payload, lines=[])

    def _fake_get_ocr_output_without_legacy_edits(_order_id, persist_cache=False):
        return ({"status": "success", "tables": []}, None)

    def _fake_build_sheet_menu_entries(**_kwargs):
        return ([{"row": idx} for idx in range(2)], "weekly_menu")

    def _fake_build_rows_from_menu_entries(**kwargs):
        fields = kwargs["fields"]
        field_index = kwargs["field_index"]
        rows = []
        for row_id, menu_date, mmdd, daypart, menu_name in (
            ("row-1", date(2026, 4, 29), "04/29", "朝", "Menu A"),
            ("row-2", date(2026, 4, 29), "04/29", "朝", "Menu B"),
        ):
            values = [""] * len(fields)
            values[field_index["date_mmdd"]] = mmdd
            values[field_index["daypart"]] = daypart
            values[field_index["menu"]] = menu_name
            rows.append(
                {
                    "row_id": row_id,
                    "values": values,
                    "identity": order_service._sheet_row_identity(menu_date, daypart, menu_name),
                }
            )
        return rows, "weekly_menu"

    def _fake_build_sheet_lines_from_ocr_payload(**_kwargs):
        return [
            {
                "date": date(2026, 4, 20),
                "daypart": "朝",
                "menu_name": "Unknown A",
                "diet_type": "regular",
                "area_id": "2F",
                "quantity_original": 91,
            }
        ]

    def _build_payload_row(template, *, date_text, daypart, menu, regular):
        fields, field_index = order_service._build_sheet_fields_and_indexes(template)
        values = [""] * len(fields)
        values[field_index["date_mmdd"]] = date_text
        values[field_index["daypart"]] = daypart
        values[field_index["menu"]] = menu
        values[field_index["qty.regular_2f"]] = str(regular)
        return values

    def _fake_extract_sheet_rows_from_payload(_payload, template):
        return [
            _build_payload_row(template, date_text="04/20", daypart="朝", menu="Polluted A", regular=999),
            _build_payload_row(template, date_text="", daypart="朝", menu="Polluted B", regular=999),
        ]

    def _fake_extract_first_pass_rows_from_payload(_payload, template):
        return [
            _build_payload_row(template, date_text="04/20", daypart="朝", menu="Unknown A", regular=91),
            _build_payload_row(template, date_text="", daypart="朝", menu="Unknown B", regular=92),
        ]

    monkeypatch.setattr(order_service, "_get_ocr_output_without_legacy_edits", _fake_get_ocr_output_without_legacy_edits)
    monkeypatch.setattr(order_service, "_build_sheet_menu_entries", _fake_build_sheet_menu_entries)
    monkeypatch.setattr(order_service, "_build_rows_from_menu_entries", _fake_build_rows_from_menu_entries)
    monkeypatch.setattr(order_service, "_build_sheet_lines_from_ocr_payload", _fake_build_sheet_lines_from_ocr_payload)
    monkeypatch.setattr(order_service, "_extract_sheet_rows_from_payload", _fake_extract_sheet_rows_from_payload)
    monkeypatch.setattr(order_service, "_extract_first_pass_rows_from_payload", _fake_extract_first_pass_rows_from_payload)

    sheet, error = order_service.get_ocr_sheet(order["id"])

    assert error is None
    assert sheet is not None
    assert sheet["source"] == "weekly_menu+ocr_payload"
    assert (sheet.get("trace") or {}).get("mapped_mode") == "raw_ocr_block"
    qty_idx = sheet["fields"].index("qty.regular_2f")
    assert [row[qty_idx] for row in sheet["rows"]] == ["", ""]
    assert "sheet_quantity_column_unmapped" in (sheet.get("warnings") or [])


def test_get_ocr_sheet_preserves_authoritative_current_sheet_gate_when_blocked(monkeypatch):
    order_service.clear_all()
    payload = IngestEmailPayload(
        message_id="msg-sheet-authoritative-gate-001",
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2026, 1, 8, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2026-01",
    )
    order = order_service.create_order_from_ingest(payload, lines=[])
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "table_rows": [["01/08", "昼", "Menu A", ""]],
            "metrics": {"status": "done"},
        },
    )
    persisted = order_service.persist_sheet_draft(
        order_id=order["id"],
        draft_sheet_json={
            "source": "weekly_menu",
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f", "remarks"],
            "header": ["日付", "区分", "メニュー", "常食2F", "備考"],
            "rows": [["01/08", "昼", "Menu A", "", ""]],
            "row_ids": ["draft-row-1"],
            "warnings": [
                "sheet_payload_mapping_low_confidence",
                "sheet_quantity_column_unmapped",
                "sheet_ocr_review_required",
            ],
            "ui_mode": "sheet",
        },
        draft_state="review_required",
        blockers=["sheet_quantity_column_unmapped"],
        warnings=[
            "sheet_payload_mapping_low_confidence",
            "sheet_quantity_column_unmapped",
            "sheet_ocr_review_required",
        ],
    )
    assert persisted is not None

    authoritative_workflow = {
        "state": "review_required",
        "apply_gate": {
            "can_apply": False,
            "can_confirm": False,
            "blockers": ["sheet_quantity_column_unmapped"],
            "warnings": [
                "quantity_review_required",
                "numeric_trust_low",
                "ocr_review_required",
            ],
            "apply_blockers": ["sheet_quantity_column_unmapped"],
            "confirm_blockers": ["sheet_quantity_column_unmapped"],
            "apply_warnings": [
                "quantity_review_required",
                "numeric_trust_low",
            ],
            "confirm_warnings": [
                "quantity_review_required",
                "numeric_trust_low",
                "ocr_review_required",
            ],
        },
    }

    monkeypatch.setattr(order_service, "_maybe_refresh_semantic_sheet_draft", lambda _order_id, draft: draft)
    monkeypatch.setattr(
        order_service.position_column_mapping_service,
        "payload_uses_ready_position_fallback",
        lambda _payload: True,
    )
    monkeypatch.setattr(
        order_service.position_column_mapping_service,
        "payload_uses_partial_position_fallback",
        lambda _payload: False,
    )
    monkeypatch.setattr(
        order_service.workflow_state_service,
        "project_workflow_state",
        lambda *_args, **_kwargs: authoritative_workflow,
    )
    monkeypatch.setattr(
        order_service,
        "get_order_workflow_state",
        lambda *_args, **_kwargs: authoritative_workflow,
    )

    sheet, error = order_service.get_ocr_sheet(order["id"])

    assert error is None
    assert isinstance(sheet, dict)
    assert sheet["source"] == "weekly_menu"
    assert "sheet_quantity_column_unmapped" in (sheet.get("warnings") or [])
    assert sheet["apply_blockers"] == ["sheet_quantity_column_unmapped"]
    assert sheet["confirm_blockers"] == ["sheet_quantity_column_unmapped"]
    assert sheet["can_apply"] is False
    assert sheet["can_confirm"] is False


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
    assert resolved == "2026-03@2026-03-22~2026-03-28"


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


def test_build_position_menu_entries_from_ocr_payload_replaces_invalid_daypart_tokens_with_nearby_valid_block():
    template = {
        "main_ocr_row_fields": ["date_mmdd", "daypart", "menu", "qty.regular_x"],
        "large_cell_mode": True,
    }
    payload = {
        "table_rows": [
            ["4/05", "朝", "A", "7"],
            ["", "\"", "B", "7"],
            ["", "61", "C", "7"],
            ["4/05", "昼", "D", "7"],
            ["4/07", "&", "E", "7"],
            ["", "", "F", "7"],
            ["", "夕", "G", "7"],
        ]
    }

    entries = order_service._build_position_menu_entries_from_ocr_payload(
        payload=payload,
        template=template,
        received_at=datetime(2026, 4, 5, 9, 0, 0),
    )

    assert [(item["menu_name"], item["daypart_key"]) for item in entries] == [
        ("A", "朝"),
        ("B", "朝"),
        ("C", "朝"),
        ("D", "昼"),
        ("E", "夕"),
        ("F", "夕"),
        ("G", "夕"),
    ]


def test_get_ocr_sheet_weekly_shell_path_does_not_surface_invalid_daypart_tokens():
    order_service.clear_all()
    _seed_monthly_menu_custom_entries(
        month_id="2026-04",
        month_start=date(2026, 4, 1),
        entries=[
            (date(2026, 4, 5), "朝", "A", 0),
            (date(2026, 4, 5), "朝", "B", 1),
            (date(2026, 4, 5), "朝", "C", 2),
            (date(2026, 4, 5), "昼", "D", 3),
            (date(2026, 4, 7), "夕", "E", 4),
            (date(2026, 4, 7), "夕", "F", 5),
            (date(2026, 4, 7), "夕", "G", 6),
        ],
    )
    payload = IngestEmailPayload(
        message_id="msg-sheet-invalid-daypart-ocr-table-001",
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2026, 4, 5, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint=None,
    )
    order = order_service.create_order_from_ingest(payload, lines=[])
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "table_rows": [
                ["04/05", "朝", "A", "7"],
                ["", "\"", "B", "7"],
                ["", "61", "C", "7"],
                ["04/05", "昼", "D", "7"],
                ["04/07", "&", "E", "7"],
                ["", "", "F", "7"],
                ["", "夕", "G", "7"],
            ],
            "date_strings": ["04/05", "04/07"],
            "cell_issues": [
                {
                    "row_index": 2,
                    "column_index": 1,
                    "field": "daypart",
                    "issue_code": "merged_numeric_cell",
                    "severity": "warning",
                    "source": "yomitoku_structured",
                    "text": "61",
                }
            ],
        },
    )

    sheet, error = order_service.get_ocr_sheet(order["id"])

    assert error is None
    assert sheet is not None
    assert sheet["source"] == "weekly_menu+ocr_payload"
    fields = sheet["fields"]
    daypart_idx = fields.index("daypart")
    menu_idx = fields.index("menu")
    rows_by_menu = {row[menu_idx]: row for row in sheet["rows"]}
    assert rows_by_menu["A"][daypart_idx] == "朝"
    assert rows_by_menu["B"][daypart_idx] == "朝"
    assert rows_by_menu["C"][daypart_idx] == "朝"
    assert rows_by_menu["D"][daypart_idx] == "昼"
    assert rows_by_menu["E"][daypart_idx] == "夕"
    assert rows_by_menu["F"][daypart_idx] == "夕"
    assert rows_by_menu["G"][daypart_idx] == "夕"
    assert all(row[daypart_idx] in {"朝", "昼", "夕"} for row in sheet["rows"])


def test_merge_weekly_menu_entries_with_ocr_tail_keeps_weekly_entries_only_for_current_sheet():
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
    ]
    assert [item.get("menu_name") for item in merged] == ["A", "B"]


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
    assert len(sheet["rows"]) == 28

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
    assert dates == {"12/26", "12/27"}


def test_get_ocr_sheet_keeps_late_week_dates_even_when_order_lines_stop_early():
    config_service.reload_configs()
    order_service.clear_all()
    _seed_monthly_menu_custom_entries(
        month_id="2026-04",
        month_start=date(2026, 4, 1),
        entries=[
            (date(2026, 4, 10), "朝", "なすとピーマンのオイスター炒め", 0),
            (date(2026, 4, 10), "朝", "ひじきのごま和え", 1),
            (date(2026, 4, 10), "昼", "鶏肉の味噌炒め", 2),
            (date(2026, 4, 10), "昼", "切干大根煮", 3),
            (date(2026, 4, 10), "昼", "いんげんのおかか和え", 4),
            (date(2026, 4, 10), "夕", "タラのムニエル 添)ｷｬﾍﾞﾂ", 5),
            (date(2026, 4, 10), "夕", "人参の卵とじ", 6),
            (date(2026, 4, 10), "夕", "カリフラワーマリネ", 7),
            (date(2026, 4, 11), "朝", "厚揚げと竹輪の煮物", 0),
            (date(2026, 4, 11), "朝", "ほうれん草のお浸し", 1),
            (date(2026, 4, 11), "昼", "ポークチャップ", 2),
            (date(2026, 4, 11), "昼", "マカロニソテー", 3),
            (date(2026, 4, 11), "昼", "胡瓜とコーンのサラダ", 4),
            (date(2026, 4, 11), "夕", "カレイの照焼き 添)ﾌﾞﾛｯｺﾘｰ", 5),
            (date(2026, 4, 11), "夕", "さつま芋の煮物", 6),
            (date(2026, 4, 11), "夕", "三色ナムル", 7),
        ],
    )
    payload = IngestEmailPayload(
        message_id="msg-weekly-shell-late-week-dates-001",
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2026, 4, 10, 9, 0, 0),
        facility_hint="FAC00005",
        week_hint="2026-04",
    )
    lines = []
    order = order_service.create_order_from_ingest(payload, lines=lines)
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "template_id": "fax_layout_soft_packaging_forbidden_v1",
            "tables": [
                {
                        "page_index": 1,
                        "table_id": "page1_table1",
                        "row_count": 16,
                        "col_count": 10,
                        "rows": [
                            ["4/10\n(金)", "", "物", "", "", "0", "0", "", "", ""],
                            ["", "", "", "謝れか", "小松菜のごま和え", "0", "0", "", "", ""],
                            ["", "", "社", "#A\nAND", "鶏肉の味噌炒め", "58", "2", "", "", ""],
                            ["", "", "", "", "", "58", "2", "", "", ""],
                            ["", "", "", "", "", "58", "2", "", "", ""],
                            ["", "", "", "", "", "0", "0", "", "", ""],
                            ["", "", "", "", "", "0", "0", "", "", ""],
                            ["", "", "", "", "", "0", "0", "", "", ""],
                            ["4/11\n(土)", "", "", "", "厚揚げと竹輪の煮物\nほうれん草のお浸し", "0", "0", "", "", ""],
                            ["", "", "", "", "", "0", "n", "", "", ""],
                            ["", "", "", "", "ポークチャップ\nマカロニソテー\n胡瓜とコーンのサラダ", "0", "0", "", "", ""],
                            ["", "", "", "", "", "0", "0", "", "", ""],
                            ["", "", "", "", "", "0", "0", "", "", ""],
                            ["", "", "", "", "カレイの照焼き\n添)プロッコリー\nさつま芋の煮物", "58\n58", "2", "", "", ""],
                            ["", "", "", "", "", "", "2", "", "", ""],
                            ["", "", "", "", "", "58", "2", "", "", ""],
                        ],
                    }
                ],
            },
    )

    sheet, error = order_service.get_ocr_sheet(order["id"])

    assert error is None
    assert isinstance(sheet, dict)
    fields = list(sheet.get("fields") or [])
    date_idx = fields.index("date_mmdd")
    menu_idx = fields.index("menu")
    dates = [row[date_idx] for row in sheet["rows"] if row and row[date_idx]]
    assert "04/10" in dates
    assert "04/11" in dates
    rows_by_identity = {
        (row[date_idx], row[menu_idx]): row
        for row in sheet["rows"]
        if len(row) > max(date_idx, menu_idx)
    }
    assert ("04/10", "鶏肉の味噌炒め") in rows_by_identity
    assert ("04/11", "厚揚げと竹輪の煮物") in rows_by_identity


def test_apply_payload_quantities_numeric_only_aligns_weekly_shell_for_late_week_rows():
    config_service.reload_configs()
    fields = [
        "date_mmdd",
        "daypart",
        "menu",
        "qty.soft_x",
        "qty.regular_bag_x",
        "qty.no_meat_x",
        "qty.no_fish_x",
        "qty.change_1_x",
        "qty.change_2_x",
        "remarks",
    ]
    rows = [
        {"values": ["04/10", "朝", "なすとピーマンのオイスター炒め", "", "", "", "", "", "", ""]},
        {"values": ["04/10", "朝", "ひじきのごま和え", "", "", "", "", "", "", ""]},
        {"values": ["04/10", "昼", "鶏肉の味噌炒め", "", "", "", "", "", "", ""]},
        {"values": ["04/10", "昼", "切干大根煮", "", "", "", "", "", "", ""]},
        {"values": ["04/10", "昼", "いんげんのおかか和え", "", "", "", "", "", "", ""]},
        {"values": ["04/10", "夕", "タラのムニエル 添)ｷｬﾍﾞﾂ", "", "", "", "", "", "", ""]},
        {"values": ["04/10", "夕", "人参の卵とじ", "", "", "", "", "", "", ""]},
        {"values": ["04/10", "夕", "カリフラワーマリネ", "", "", "", "", "", "", ""]},
        {"values": ["04/11", "朝", "厚揚げと竹輪の煮物", "", "", "", "", "", "", ""]},
        {"values": ["04/11", "朝", "ほうれん草のお浸し", "", "", "", "", "", "", ""]},
        {"values": ["04/11", "昼", "ポークチャップ", "", "", "", "", "", "", ""]},
        {"values": ["04/11", "昼", "マカロニソテー", "", "", "", "", "", "", ""]},
        {"values": ["04/11", "昼", "胡瓜とコーンのサラダ", "", "", "", "", "", "", ""]},
        {"values": ["04/11", "夕", "カレイの照焼き 添)ﾌﾞﾛｯｺﾘｰ", "", "", "", "", "", "", ""]},
        {"values": ["04/11", "夕", "さつま芋の煮物", "", "", "", "", "", "", ""]},
        {"values": ["04/11", "夕", "三色ナムル", "", "", "", "", "", "", ""]},
    ]
    template = (config_service.get_facility_config("FAC00005") or {}).get("fax_template") or {}
    payload_rows = order_service._extract_sheet_rows_from_payload(
        {
            "template_id": "fax_layout_soft_packaging_forbidden_v1",
            "tables": [
                {
                    "page_index": 1,
                    "table_id": "page1_table1",
                    "row_count": 16,
                    "col_count": 10,
                    "rows": [
                        ["4/10\n(金)", "", "物", "", "", "0", "0", "", "", ""],
                        ["", "", "", "謝れか", "小松菜のごま和え", "0", "0", "", "", ""],
                        ["", "", "社", "#A\nAND", "鶏肉の味噌炒め", "58", "2", "", "", ""],
                        ["", "", "", "", "", "58", "2", "", "", ""],
                        ["", "", "", "", "", "58", "2", "", "", ""],
                        ["", "", "", "", "", "0", "0", "", "", ""],
                        ["", "", "", "", "", "0", "0", "", "", ""],
                        ["", "", "", "", "", "0", "0", "", "", ""],
                        ["4/11\n(土)", "", "", "", "厚揚げと竹輪の煮物\nほうれん草のお浸し", "0", "0", "", "", ""],
                        ["", "", "", "", "", "0", "n", "", "", ""],
                        ["", "", "", "", "ポークチャップ\nマカロニソテー\n胡瓜とコーンのサラダ", "0", "0", "", "", ""],
                        ["", "", "", "", "", "0", "0", "", "", ""],
                        ["", "", "", "", "", "0", "0", "", "", ""],
                        ["", "", "", "", "カレイの照焼き\n添)プロッコリー\nさつま芋の煮物", "58\n58", "2", "", "", ""],
                        ["", "", "", "", "", "", "2", "", "", ""],
                        ["", "", "", "", "", "58", "2", "", "", ""],
                    ],
                }
            ],
        },
        template,
    )
    quantity_index = {
        ("soft", "x"): fields.index("qty.soft_x"),
        ("regular_bag", "x"): fields.index("qty.regular_bag_x"),
    }

    stats = order_service._apply_payload_quantities_numeric_only(
        rows=rows,
        fields=fields,
        quantity_index=quantity_index,
        payload_rows=payload_rows,
        payload_unstructured_qty=[],
        allow_heuristics=False,
        enable_daypart_consensus=False,
        overlay_structural_fields_from_sheet_rows=True,
    )

    rows_by_identity = {
        (row["values"][0], row["values"][2]): row["values"]
        for row in rows
    }
    soft_idx = fields.index("qty.soft_x")
    bag_idx = fields.index("qty.regular_bag_x")
    assert stats["row_index"] == 15
    assert stats["partial"] == 1
    assert rows_by_identity[("04/10", "鶏肉の味噌炒め")][soft_idx] == "58"
    assert rows_by_identity[("04/10", "鶏肉の味噌炒め")][bag_idx] == "2"
    assert rows_by_identity[("04/10", "切干大根煮")][soft_idx] == "58"
    assert rows_by_identity[("04/10", "切干大根煮")][bag_idx] == "2"
    assert rows_by_identity[("04/10", "いんげんのおかか和え")][soft_idx] == "58"
    assert rows_by_identity[("04/10", "いんげんのおかか和え")][bag_idx] == "2"
    assert rows_by_identity[("04/11", "カレイの照焼き 添)ﾌﾞﾛｯｺﾘｰ")][soft_idx] == "58"
    assert rows_by_identity[("04/11", "カレイの照焼き 添)ﾌﾞﾛｯｺﾘｰ")][bag_idx] == "2"
    assert rows_by_identity[("04/11", "さつま芋の煮物")][soft_idx] == "58"
    assert rows_by_identity[("04/11", "さつま芋の煮物")][bag_idx] == "2"
    assert rows_by_identity[("04/11", "三色ナムル")][soft_idx] == "58"
    assert rows_by_identity[("04/11", "三色ナムル")][bag_idx] == "2"


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
        assert error is None
        assert isinstance(sheet, dict)
        assert sheet["source"] == "weekly_menu_blocked"
        assert sheet["rows"] == []
        tokens = set(
            list(sheet.get("warnings") or [])
            + list(sheet.get("blockers") or [])
            + list(sheet.get("apply_blockers") or [])
            + list(sheet.get("confirm_blockers") or [])
        )
        assert "sheet_template_field_invalid" in tokens
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


def test_hakodate_projection_uses_target_truth_field_for_remarks_column():
    base_sheet = {
        "fields": [
            "date_mmdd",
            "daypart",
            "menu",
            "qty.regular_2f",
            "qty.regular_3f",
            "qty.soft_2f",
            "qty.soft_3f",
            "qty.mixer_2f",
            "qty.mixer_3f",
            "qty.regular_x",
            "remarks",
        ],
        "rows": [
            ["04/26", "朝", "Menu A", "", "", "", "", "", "", "", ""],
            ["04/26", "朝", "Menu B", "", "", "", "", "", "", "", ""],
            ["04/26", "昼", "Menu C", "", "", "", "", "", "", "", ""],
        ],
    }
    assignment = {
        "target_cells": [
            {
                "target_cell_id": "K13",
                "sheet_cell": "K13",
                "worksheet_row": 13,
                "worksheet_col": 11,
                "semantic_field": "note",
                "metadata": {"truth": {"row_index": 2, "field": "remarks"}},
            }
        ],
        "sheet_output": {
            "cells": {
                "K13": {
                    "sheet_cell": "K13",
                    "worksheet_row": 13,
                    "worksheet_col": 11,
                    "semantic_field": "note",
                    "value_text": "111",
                    "value_normalized": "111",
                    "assignment_confidence": 0.66,
                }
            }
        },
    }

    projected = order_service._apply_hakodate_sheet_output_to_sheet_payload(  # noqa: SLF001
        base_sheet=base_sheet,
        assignment=assignment,
    )

    assert projected["rows"][2][10] == "111"
    assert projected["hakodate_projection_version"] == order_service.HAKODATE_EVIDENCE_PROJECTION_VERSION
    assert projected["hakodate_evidence_projection"]["applied"][0]["field"] == "remarks"


def test_hakodate_projection_does_not_fallback_to_worksheet_position_without_truth():
    base_sheet = {
        "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f", "remarks"],
        "rows": [["04/26", "朝", "Menu A", "", ""]],
    }
    assignment = {
        "target_cells": [
            {
                "target_cell_id": "D3",
                "sheet_cell": "D3",
                "worksheet_row": 3,
                "worksheet_col": 4,
                "semantic_field": "qty.regular_2f",
            }
        ],
        "sheet_output": {
            "cells": {
                "D3": {
                    "sheet_cell": "D3",
                    "worksheet_row": 3,
                    "worksheet_col": 4,
                    "semantic_field": "qty.regular_2f",
                    "value_text": "70",
                    "value_normalized": "70",
                    "assignment_confidence": 0.9,
                }
            }
        },
    }

    projected = order_service._apply_hakodate_sheet_output_to_sheet_payload(  # noqa: SLF001
        base_sheet=base_sheet,
        assignment=assignment,
    )

    assert projected["rows"][0][3] == ""
    projection = projected["hakodate_evidence_projection"]
    assert projection["applied"] == []
    assert projection["skipped"][0]["skip_reason"] == "row_identity_not_found"
    assert "hakodate_sheet_projection_incomplete" in projected["blockers"]


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
    assert sheet["source"] == "edited_sheet_exact"
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
    _seed_monthly_menu_custom_entries(
        month_id="2099-10",
        month_start=date(2099, 10, 1),
        entries=[
            (date(2099, 10, 15), "朝", "Menu A", 0),
            (date(2099, 10, 15), "昼", "Menu B", 1),
        ],
    )
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
    assert updated_1["llm_review"]["baseline_source"] == "current_draft"
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
    assert updated_2["llm_review"]["baseline_source"] == "current_draft"
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
    assert "Current baseline source: current_draft" in captured_prompts[0]["user"]
    assert '"qty.regular_2f": "2"' in captured_prompts[0]["user"]
    assert f"Current baseline revision_id: {applied_draft['id']}" in captured_prompts[1]["user"]
    assert "Current baseline source: current_draft" in captured_prompts[1]["user"]
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
    assert updated["llm_review"]["baseline_source"] == "current_draft"
    assert updated["llm_review"]["needs_more_review"] is True
    assert len(updated["llm_review"]["applied_overwrites"]) == 1
    assert updated["llm_review"]["applied_overwrites"][0]["field"] == "remarks"
    assert updated["llm_review"]["applied_overwrites"][0]["new_text"] == "first-pass"
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


def test_get_ocr_sheet_uses_weekly_shell_and_facility_schema_when_template_columns_drift():
    config_service.reload_configs()
    order_service.clear_all()
    _seed_monthly_menu_custom_entries(
        month_id="2026-04",
        month_start=date(2026, 4, 1),
        entries=[
            (date(2026, 4, 5), "朝", "豚肉の卵とじ", 0),
            (date(2026, 4, 5), "朝", "いんげんのカニ和え", 1),
            (date(2026, 4, 5), "昼", "サワラの西京焼き 添)小松菜", 2),
            (date(2026, 4, 5), "昼", "じゃが芋の煮物", 3),
        ],
    )
    payload = IngestEmailPayload(
        message_id="msg-ocr-sheet-projection-drift-001",
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2026, 4, 5, 9, 0, 0),
        facility_hint="FAC00005",
        week_hint="2026-04",
    )
    order = order_service.create_order_from_ingest(payload, lines=None)
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "template_id": "fax_layout_soft_packaging_forbidden_v1",
            "tables": [
                {
                    "page_index": 1,
                    "table_id": "page1_table1",
                    "row_count": 6,
                    "col_count": 10,
                    "rows": [
                        ["", "日付", "区 分", "", "献立", "##", "44日", "禁食【軟菜】", "", "備考欄"],
                        ["", "", "", "", "", "", "", "肉禁", "魚禁", ""],
                        ["", "4/5\n(日)", "ま", "...", "豚肉の卵とじ", "0", "0", "", "", ""],
                        ["", "", "", "***", "いんげんのカニ和え", "0", "0", "", "", ""],
                        ["", "", "口", "VT", "サワラの西京焼き 添)小松菜", "58", "2", "", "", ""],
                        ["", "", "", "OK", "じゃが芋の煮物", "58", "2", "", "", ""],
                    ],
                }
            ],
        },
    )

    sheet, error = order_service.get_ocr_sheet(order["id"])

    assert error is None
    assert isinstance(sheet, dict)
    assert sheet["source"] == "weekly_menu+ocr_payload"
    assert list(sheet.get("fields") or []) == [
        "date_mmdd",
        "daypart",
        "menu",
        "qty.soft_x",
        "qty.regular_bag_x",
        "qty.no_meat_x",
        "qty.no_fish_x",
        "qty.change_1_x",
        "qty.change_2_x",
        "remarks",
    ]
    assert list(sheet.get("header") or []) == [
        "日付",
        "区分",
        "メニュー",
        "軟菜",
        "袋分け",
        "肉禁",
        "魚禁",
        "変更1",
        "変更2",
        "備考欄",
    ]
    fields = list(sheet.get("fields") or [])
    date_idx = next(idx for idx, field in enumerate(fields) if str(field).startswith("date"))
    daypart_idx = fields.index("daypart")
    menu_idx = fields.index("menu")
    first_rows = list(sheet.get("rows") or [])[:4]
    assert first_rows[0][date_idx] == "04/05"
    assert first_rows[0][daypart_idx] == "朝"
    assert first_rows[0][menu_idx] == "豚肉の卵とじ"
    assert first_rows[0][3] == "0"
    assert first_rows[0][4] == "0"
    assert first_rows[1][daypart_idx] == "朝"
    assert first_rows[1][menu_idx] == "いんげんのカニ和え"
    assert "sheet_structural_projection_corrupted" not in list(sheet.get("warnings") or [])


def test_get_ocr_sheet_uses_monthly_menu_entries_even_when_parent_menu_row_is_missing():
    config_service.reload_configs()
    order_service.clear_all()
    _seed_monthly_menu_custom_entries(
        month_id="2026-04",
        month_start=date(2026, 4, 1),
        entries=[
            (date(2026, 4, 5), "朝", "豚肉の卵とじ", 0),
            (date(2026, 4, 5), "朝", "いんげんのカニ和え", 1),
            (date(2026, 4, 5), "昼", "サワラの西京焼き 添)小松菜", 2),
            (date(2026, 4, 5), "昼", "じゃが芋の煮物", 3),
        ],
    )
    with session_scope() as session:
        session.execute(delete(MonthlyMenu).where(MonthlyMenu.id == "2026-04"))
    payload = IngestEmailPayload(
        message_id="msg-ocr-sheet-missing-menu-parent-row-001",
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2026, 4, 5, 9, 0, 0),
        facility_hint="FAC00005",
        week_hint="2026-04",
    )
    order = order_service.create_order_from_ingest(payload, lines=None)
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "template_id": "fax_layout_soft_packaging_forbidden_v1",
            "tables": [
                {
                    "page_index": 1,
                    "table_id": "page1_table1",
                    "row_count": 6,
                    "col_count": 10,
                    "rows": [
                        ["", "日付", "区 分", "", "献立", "##", "44日", "禁食【軟菜】", "", "備考欄"],
                        ["", "", "", "", "", "", "", "肉禁", "魚禁", ""],
                        ["", "4/5\n(日)", "ま", "...", "豚肉の卵とじ", "0", "0", "", "", ""],
                        ["", "", "", "***", "いんげんのカニ和え", "0", "0", "", "", ""],
                        ["", "", "口", "VT", "サワラの西京焼き 添)小松菜", "58", "2", "", "", ""],
                        ["", "", "", "OK", "じゃが芋の煮物", "58", "2", "", "", ""],
                    ],
                }
            ],
        },
    )

    sheet, error = order_service.get_ocr_sheet(order["id"])

    assert error is None
    assert isinstance(sheet, dict)
    assert sheet["source"] == "weekly_menu+ocr_payload"
    assert "monthly_menu_object_missing" not in (sheet.get("warnings") or [])
    rows = list(sheet.get("rows") or [])
    assert [row[2] for row in rows[:4]] == [
        "豚肉の卵とじ",
        "いんげんのカニ和え",
        "サワラの西京焼き 添)小松菜",
        "じゃが芋の煮物",
    ]
