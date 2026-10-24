from __future__ import annotations

from datetime import date, datetime
from uuid import uuid4
import pytest
from src.db import session_scope
from src.models.menu import MonthlyMenu, MonthlyMenuEntry
from tests.helpers.owned_order import owned_registration
from tests.helpers.owned_output import owned_outputs

from src.services import candidate_resolution_service, order_service, output_builder  # noqa: E402
from src.workers.ingest_mail_adapter import IngestEmailPayload  # noqa: E402


def _template(fields):
    columns = []
    for index, field in enumerate(fields):
        column = {"index": index, "source_index": index, "name": field}
        if field.startswith("qty."):
            diet, area = field[4:].rsplit("_", 1)
            column.update(role="quantity", diet_type=diet, area_id=area.upper())
        else:
            column["role"] = {"date_mmdd": "date", "daypart": "daypart",
                              "menu": "menu_name", "remarks": "note"}[field]
        columns.append(column)
    return {"columns": columns}


def _physical_entries(entries):
    with session_scope() as session:
        for row in entries:
            day = date.fromisoformat(row["menu_date"])
            month = day.strftime("%Y-%m")
            if session.get(MonthlyMenu, month) is None:
                session.add(MonthlyMenu(id=month, month_start=day.replace(day=1)))
                session.flush()
            session.add(MonthlyMenuEntry(id=uuid4().hex, monthly_menu_id=month,
                menu_date=day, name=row["name"], daypart=row["daypart"],
                diet_type="regular", slot_index=row["slot_index"],
                category=row.get("category")))



def test_resolve_sheet_week_id_requires_explicit_choice_despite_cross_month_observed_dates() -> None:
    received_at = datetime(2026, 4, 30, 9, 0, 0)
    payload = {
        "table_raw": """
|日付|区分|献立|常|
|-|-|-|-|
|4/29|朝|A|10|
|4/30|朝|B|10|
|5/1|朝|C|10|
|5/2|朝|D|10|
|5/3|朝|E|10|
|5/4|朝|F|10|
|5/5|朝|G|10|
""".strip()
    }

    resolved = order_service._resolve_sheet_week_id(  # noqa: SLF001
        current_week_id=None,
        received_at=received_at,
        order_lines=[],
        ocr_payload=payload,
        facility_id="FAC00001",
        week_hints=[],
    )

    assert resolved is None  # Observed OCR dates cannot authorize a period.
    selected = order_service._resolve_sheet_week_id(
        current_week_id="2026-04@2026-04-29~2026-05-05",
        received_at=received_at, order_lines=[], ocr_payload=payload,
        facility_id="FAC00001", week_hints=[])
    assert selected == "2026-04@2026-04-29~2026-05-05"


def test_system_managed_week_resolution_keeps_explicit_selected_week() -> None:
    explicit_week = "2026-05@2026-05-01~2026-05-02"

    resolved = order_service._resolve_system_managed_order_week_code(  # noqa: SLF001
        {"week_code": explicit_week, "message_id": "msg-system-week-preserve-001"},
        "2026-04@2026-04-26~2026-05-02",
    )

    assert resolved == explicit_week


def test_collect_sheet_dates_requires_explicit_period_and_actual_rows_not_page_uris() -> None:
    template = _template(["date_mmdd", "daypart", "menu", "qty.regular_x"])
    received = datetime(2026, 4, 30, 9)
    period = "2026-04@2026-04-29~2026-05-02"
    pages = {"pages": [{"page_index": 1, "markdown_uri": "gs://bucket/page-1.md"},
                       {"page_index": 2, "markdown_uri": "gs://bucket/page-2.md"}]}
    assert order_service._collect_sheet_dates_from_payload(
        pages, received, template, explicit_week=period) == []
    rows = [["4/29", "朝", "A", "10"], ["4/30", "朝", "B", "10"],
            ["5/1", "朝", "C", "10"], ["5/2", "朝", "D", "10"]]
    with pytest.raises(ValueError, match="week_hint_required"):
        order_service._collect_sheet_dates_from_payload({"table_rows": rows}, received, template)
    dates = order_service._collect_sheet_dates_from_payload(
        {"table_rows": rows}, received, template, explicit_week=period)
    assert [item.isoformat() for item in dates] == [
        "2026-04-29", "2026-04-30", "2026-05-01", "2026-05-02"]


def test_build_position_menu_entries_merges_adjacent_month_payloads(isolated_formal_schema) -> None:
    def _authored_menu_for_month(month_id: str, _facility_id: str | None):
        if month_id == "2026-04":
            return {
                "entries": [
                    {"name": "A", "menu_date": "2026-04-29", "daypart": "朝", "slot_index": 0},
                    {"name": "B", "menu_date": "2026-04-30", "daypart": "朝", "slot_index": 0},
                ]
            }
        if month_id == "2026-05":
            return {
                "entries": [
                    {"name": "C", "menu_date": "2026-05-01", "daypart": "朝", "slot_index": 0},
                    {"name": "D", "menu_date": "2026-05-02", "daypart": "朝", "slot_index": 0},
                ]
            }
        return None

    _physical_entries(_authored_menu_for_month("2026-04", None)["entries"])
    _physical_entries(_authored_menu_for_month("2026-05", None)["entries"])

    entries = order_service._build_position_menu_entries_safe(  # noqa: SLF001
        "2026-04@2026-04-29~2026-05-02",
        "FAC00001",
    )

    assert [entry["menu_name"] for entry in entries] == ["A", "B", "C", "D"]
    assert [entry["menu_date"].isoformat() for entry in entries] == [
        "2026-04-29",
        "2026-04-30",
        "2026-05-01",
        "2026-05-02",
    ]


def test_build_position_menu_entries_keeps_single_event_lunch_fourth_slot(isolated_formal_schema) -> None:
    entries_payload = []
    order_index = 0
    for day in range(20, 27):
        for daypart, count in (("朝食", 2), ("昼食", 3), ("夕食", 3)):
            for slot_index in range(count):
                entries_payload.append(
                    {
                        "name": f"{day}-{daypart}-{slot_index}",
                        "menu_date": f"2026-09-{day:02d}",
                        "daypart": daypart,
                        "slot_index": slot_index,
                        "category": "副菜" if slot_index else "主菜",
                        "order": order_index,
                    }
                )
                order_index += 1
    entries_payload.append(
        {
            "name": "9月21日昼4品目",
            "menu_date": "2026-09-21",
            "daypart": "昼食",
            "slot_index": 3,
            "category": "副菜",
            "order": order_index,
        }
    )

    _physical_entries(entries_payload)

    entries = order_service._build_position_menu_entries(  # noqa: SLF001
        "2026-09@2026-09-20~2026-09-26",
        "FAC00009",
    )

    event_lunch = [
        entry
        for entry in entries
        if entry["menu_date"] == date(2026, 9, 21) and entry["daypart_key"] == "昼"
    ]
    assert len(entries) == 57
    assert [entry["slot_index"] for entry in event_lunch] == [0, 1, 2, 3]
    assert event_lunch[-1]["menu_name"] == "9月21日昼4品目"


def test_build_sheet_menu_entries_does_not_rescue_missing_cross_month_dates_from_ocr_payload(isolated_formal_schema) -> None:
    def _authored_menu_for_month(month_id: str, _facility_id: str | None):
        if month_id == "2026-04":
            return {
                "entries": [
                    {"name": "A", "menu_date": "2026-04-29", "daypart": "朝", "slot_index": 0},
                    {"name": "B", "menu_date": "2026-04-30", "daypart": "朝", "slot_index": 0},
                ]
            }
        if month_id == "2026-05":
            return {"entries": []}
        return None

    _physical_entries(_authored_menu_for_month("2026-04", None)["entries"])
    _physical_entries(_authored_menu_for_month("2026-05", None)["entries"])
    template = {
        "columns": [
            {"field": "date_mmdd", "header": "日付"},
            {"field": "daypart", "header": "区分"},
            {"field": "menu", "header": "メニュー"},
            {"field": "qty.regular_x", "header": "常食"},
        ]
    }
    payload = {
        "table_raw": """
|日付|区分|献立|常|
|-|-|-|-|
|4/29|朝|A|10|
|4/30|朝|B|10|
|5/1|朝|C|10|
|5/2|朝|D|10|
""".strip()
    }

    entries, source = order_service._build_sheet_menu_entries(  # noqa: SLF001
        week_id="2026-04@2026-04-29~2026-05-02",
        facility_id="FAC00001",
        order_lines=[],
        ocr_payload=payload,
        template=template,
        received_at=datetime(2026, 4, 30, 9, 0, 0),
    )

    assert source == "weekly_menu"
    assert [entry["menu_name"] for entry in entries] == ["A", "B"]
    assert [entry["menu_date"].isoformat() for entry in entries] == ["2026-04-29", "2026-04-30"]
    # May OCR rows are diagnostic only; no missing canonical entries are invented.
    assert all(entry["menu_date"].month == 4 for entry in entries)


def test_apply_payload_quantities_numeric_only_prefers_identity_over_row_index_for_page2_rows() -> None:
    fields = [
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
    template = _template(fields)
    quantity_index = order_service._build_sheet_quantity_index(fields, template=template)  # noqa: SLF001
    rows = [
        {
            "values": ["04/29", "朝", "A", "", "", "", "", "", "", ""],
            "identity": order_service._sheet_row_identity(date(2026, 4, 29), "朝", "A"),  # noqa: SLF001
        },
        {
            "values": ["04/30", "朝", "B", "", "", "", "", "", "", ""],
            "identity": order_service._sheet_row_identity(date(2026, 4, 30), "朝", "B"),  # noqa: SLF001
        },
        {
            "values": ["05/01", "朝", "竹輪の卵炒め", "", "", "", "", "", "", ""],
            "identity": order_service._sheet_row_identity(date(2026, 5, 1), "朝", "竹輪の卵炒め"),  # noqa: SLF001
        },
        {
            "values": ["05/02", "朝", "里芋のそぼろ煮", "", "", "", "", "", "", ""],
            "identity": order_service._sheet_row_identity(date(2026, 5, 2), "朝", "里芋のそぼろ煮"),  # noqa: SLF001
        },
    ]
    payload_rows = [
        ["4/29", "朝", "A", "101", "2", "", "", "", "", ""],
        ["4/30", "朝", "B", "103", "2", "", "", "", "", ""],
        ["5/1", "朝", "竹輪の卵炒め", "104", "2", "", "", "", "", ""],
        ["5/2", "朝", "里芋のそぼろ煮", "102", "2", "2", "", "", "", ""],
    ]

    stats = order_service._apply_payload_quantities_numeric_only(  # noqa: SLF001
        rows=rows,
        fields=fields,
        quantity_index=quantity_index,
        payload_rows=payload_rows,
        allow_heuristics=False,
        enable_daypart_consensus=False,
        explicit_quantity_pairs=[(3, 3), (4, 4), (5, 5)],
        overlay_structural_fields_from_sheet_rows=True,
        template=template,
    )

    assert stats["exact"] >= 3
    assert rows[2]["values"][3:6] == ["104", "2", ""]
    assert rows[3]["values"][3:6] == ["102", "2", "2"]


def test_candidate_resolution_does_not_resolve_ocr_dates_without_explicit_choice() -> None:
    resolution = candidate_resolution_service.build_week_resolution(
        current_week=None,
        received_at=datetime(2026, 4, 30, 9, 0, 0),
        payload={"table_raw": "4/29 4/30 5/1 5/2 5/3 5/4 5/5"},
        facility_id="FAC00001",
    )

    assert resolution["resolved_value"] is None
    assert resolution["requires_user_choice"] is True
    assert resolution["blocked"] is True
    assert resolution["blocked_reasons"] == ["week_choice_required"]
    assert {"2026-04@2026-04-26~2026-04-30", "2026-05@2026-05-01~2026-05-02"} <= {
        item["value"] for item in resolution["candidates"]
    }


def test_candidate_resolution_preserves_explicit_current_week_range() -> None:
    resolution = candidate_resolution_service.build_week_resolution(
        current_week="2026-04@2026-04-26~2026-04-30",
        received_at=datetime(2026, 4, 30, 9, 0, 0),
        payload={"table_raw": "4/26 4/27 4/28 4/29 4/30 5/1 5/2"},
        facility_id="FAC00001",
    )

    assert resolution["resolved_value"] == "2026-04@2026-04-26~2026-04-30"
    assert resolution["requires_user_choice"] is False
    assert resolution["blocked"] is False


def test_set_week_preserves_short_explicit_exception_range_exact(owned_registration) -> None:
    order_service.clear_all()
    order = owned_registration(
        **dict(
            message_id="msg-auto-week-coerce-001",
            received_at=datetime(2026, 4, 14, 7, 0, 0),
            facility_id="FAC00007",
            effective_date="2026-04-26",
            week="2026-04@2026-04-26~2026-05-02",
        ),
        lines=[],
    )

    result = order_service.set_week(order["id"], "2026-05@2026-05-01~2026-05-02")

    assert result is True
    refreshed = order_service.get_order_by_id(order["id"])
    assert refreshed is not None
    assert refreshed.get("week_value") == "2026-05@2026-05-01~2026-05-02"
    assert refreshed.get("persisted_week_value") == "2026-05@2026-05-01~2026-05-02"


def test_set_week_preserves_short_explicit_range_for_split_page_orders(owned_registration) -> None:
    order_service.clear_all()
    order = owned_registration(
        **dict(
            message_id="upload:sha256:split-case:split:group-abc:1of2",
            received_at=datetime(2026, 4, 14, 7, 0, 0),
            facility_id="FAC00007",
            effective_date="2026-05-01",
            week="2026-05@2026-05-01~2026-05-02",
        ),
        lines=[],
    )

    result = order_service.set_week(order["id"], "2026-05@2026-05-01~2026-05-02")

    assert result is True
    refreshed = order_service.get_order_by_id(order["id"])
    assert refreshed is not None
    assert refreshed.get("week_value") == "2026-05@2026-05-01~2026-05-02"
    assert refreshed.get("persisted_week_value") == "2026-05@2026-05-01~2026-05-02"


def test_cross_month_output_keeps_accepted_second_month_identity_after_menu_edit(owned_outputs):
    payload = IngestEmailPayload(message_id="owned-cross-month-output",
        pdf_uri="component:authored", received_at=datetime(2026, 4, 30),
        facility_hint="FAC00001", week_hint="2026-04@2026-04-29~2026-05-02")
    order = owned_outputs.create_order_from_ingest(payload, lines=[{
        "date": "2026-05-01", "daypart": "朝", "menu_name": "B",
        "diet_type": "regular", "area_id": "X", "bag_type": "standard",
        "quantity_original": 1}],
        recipe={"qty_per_serving": 1, "unit_type": "g", "category": "副菜"}, physical_slot=0)
    with session_scope() as session:
        entry = session.query(MonthlyMenuEntry).filter_by(name="B",
            menu_date=date(2026, 5, 1), facility_override="FAC00001").one()
        entry.daypart = "昼"
    lines = output_builder.build_order_lines_for_outputs(order)
    assert len(lines) == 1
    assert lines[0]["daypart"] == "朝"
    assert lines[0]["menu_category"] == "副菜1"
    assert str(lines[0]["date"]) == "2026-05-01"
