from __future__ import annotations

from datetime import date, datetime

from src.services import candidate_resolution_service, order_service, output_builder  # noqa: E402
from src.workers.ingest_mail_adapter import IngestEmailPayload  # noqa: E402


def test_resolve_sheet_week_id_prefers_explicit_cross_month_range_from_observed_dates() -> None:
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

    assert resolved == "2026-04@2026-04-29~2026-05-05"


def test_system_managed_week_resolution_keeps_explicit_selected_week() -> None:
    explicit_week = "2026-05@2026-05-01~2026-05-02"

    resolved = order_service._resolve_system_managed_order_week_code(  # noqa: SLF001
        {"week_code": explicit_week, "message_id": "msg-system-week-preserve-001"},
        "2026-04@2026-04-26~2026-05-02",
    )

    assert resolved == explicit_week


def test_collect_sheet_dates_from_payload_reads_page_markdown_uri_blocks(monkeypatch) -> None:
    markdown_by_uri = {
        "gs://bucket/page-1.md": """
|日付|区分|献立|常|
|-|-|-|-|
|4/29|朝|A|10|
|4/30|朝|B|10|
""".strip(),
        "gs://bucket/page-2.md": """
|日付|区分|献立|常|
|-|-|-|-|
|5/1|朝|C|10|
|5/2|朝|D|10|
""".strip(),
    }

    monkeypatch.setattr(
        order_service,
        "load_bytes_from_uri",
        lambda uri: markdown_by_uri[str(uri)].encode("utf-8"),
    )

    dates = order_service._collect_sheet_dates_from_payload(  # noqa: SLF001
        {
            "pages": [
                {"page_index": 1, "markdown_uri": "gs://bucket/page-1.md"},
                {"page_index": 2, "markdown_uri": "gs://bucket/page-2.md"},
            ]
        },
        datetime(2026, 4, 30, 9, 0, 0),
    )

    assert [item.isoformat() for item in dates] == [
        "2026-04-29",
        "2026-04-30",
        "2026-05-01",
        "2026-05-02",
    ]


def test_build_position_menu_entries_merges_adjacent_month_payloads(monkeypatch) -> None:
    def _fake_get_menu_for_facility(month_id: str, _facility_id: str | None):
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

    monkeypatch.setattr(order_service.menu_service, "get_menu_for_facility", _fake_get_menu_for_facility)

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


def test_build_position_menu_entries_keeps_single_event_lunch_fourth_slot(monkeypatch) -> None:
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

    monkeypatch.setattr(order_service, "_sheet_week_month_ids", lambda _week_id: ["2026-09"])
    monkeypatch.setattr(
        order_service,
        "_load_menu_payloads_for_week",
        lambda *_args, **_kwargs: [("2026-09", {"entries": entries_payload})],
    )

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


def test_build_sheet_menu_entries_rescues_missing_cross_month_dates_from_ocr_payload(monkeypatch) -> None:
    def _fake_get_menu_for_facility(month_id: str, _facility_id: str | None):
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

    monkeypatch.setattr(order_service.menu_service, "get_menu_for_facility", _fake_get_menu_for_facility)
    monkeypatch.setattr(
        order_service,
        "_build_position_menu_entries_from_ocr_payload",
        lambda **_kwargs: [
            {
                "menu_name": "A",
                "menu_date": datetime(2026, 4, 29).date(),
                "daypart_key": "朝",
                "slot_index": 0,
                "order": 0,
                "source_order": 0,
            },
            {
                "menu_name": "B",
                "menu_date": datetime(2026, 4, 30).date(),
                "daypart_key": "朝",
                "slot_index": 0,
                "order": 1,
                "source_order": 1,
            },
            {
                "menu_name": "C",
                "menu_date": datetime(2026, 5, 1).date(),
                "daypart_key": "朝",
                "slot_index": 0,
                "order": 2,
                "source_order": 2,
            },
            {
                "menu_name": "D",
                "menu_date": datetime(2026, 5, 2).date(),
                "daypart_key": "朝",
                "slot_index": 0,
                "order": 3,
                "source_order": 3,
            },
        ],
    )

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
    assert [entry["menu_name"] for entry in entries] == ["A", "B", "C", "D"]
    assert [entry["menu_date"].isoformat() for entry in entries] == [
        "2026-04-29",
        "2026-04-30",
        "2026-05-01",
        "2026-05-02",
    ]


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
    quantity_index = order_service._build_sheet_quantity_index(fields)  # noqa: SLF001
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
    )

    assert stats["exact"] >= 3
    assert rows[2]["values"][3:6] == ["104", "2", ""]
    assert rows[3]["values"][3:6] == ["102", "2", "2"]


def test_candidate_resolution_resolves_calendar_week_without_user_choice() -> None:
    resolution = candidate_resolution_service.build_week_resolution(
        current_week=None,
        received_at=datetime(2026, 4, 30, 9, 0, 0),
        payload={"table_raw": "4/29 4/30 5/1 5/2 5/3 5/4 5/5"},
        facility_id="FAC00001",
    )

    assert resolution["resolved_value"] == "2026-04@2026-04-26~2026-05-02"
    assert resolution["requires_user_choice"] is False
    assert resolution["blocked"] is False


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


def test_set_week_preserves_short_explicit_exception_range_exact() -> None:
    order_service.clear_all()
    order = order_service.create_order_from_ingest(
        IngestEmailPayload(
            message_id="msg-auto-week-coerce-001",
            pdf_uri="gs://bucket/auto-week-coerce.pdf",
            received_at=datetime(2026, 4, 14, 7, 0, 0),
            facility_hint="FAC00007",
            week_hint="2026-04@2026-04-26~2026-05-02",
            source_kind="manual_upload",
        ),
        lines=[],
    )

    result = order_service.set_week(order["id"], "2026-05@2026-05-01~2026-05-02")

    assert result is True
    refreshed = order_service.get_order_by_id(order["id"])
    assert refreshed is not None
    assert refreshed.get("week_value") == "2026-05@2026-05-01~2026-05-02"
    assert refreshed.get("persisted_week_value") == "2026-05@2026-05-01~2026-05-02"


def test_set_week_preserves_short_explicit_range_for_split_page_orders() -> None:
    order_service.clear_all()
    order = order_service.create_order_from_ingest(
        IngestEmailPayload(
            message_id="upload:sha256:split-case:split:group-abc:1of2",
            pdf_uri="gs://bucket/split-auto-week.pdf",
            received_at=datetime(2026, 4, 14, 7, 0, 0),
            facility_hint="FAC00007",
            week_hint="2026-05@2026-05-01~2026-05-02",
            source_kind="manual_upload",
        ),
        lines=[],
    )

    result = order_service.set_week(order["id"], "2026-05@2026-05-01~2026-05-02")

    assert result is True
    refreshed = order_service.get_order_by_id(order["id"])
    assert refreshed is not None
    assert refreshed.get("week_value") == "2026-05@2026-05-01~2026-05-02"
    assert refreshed.get("persisted_week_value") == "2026-05@2026-05-01~2026-05-02"


def test_output_builder_applies_menu_entry_override_from_second_month(monkeypatch) -> None:
    def _fake_get_menu_entries_for_facility(month_id: str, _facility_id: str | None):
        if month_id == "2026-04":
            return [{"name": "A", "menu_date": "2026-04-29", "daypart": "朝", "category": "main"}]
        if month_id == "2026-05":
            return [{"name": "B", "menu_date": "2026-05-01", "daypart": "昼", "category": "side"}]
        return []

    monkeypatch.setattr(output_builder.config_service, "get_facility_config", lambda _facility_id: None)
    monkeypatch.setattr(order_service.menu_service, "get_menu_entries_for_facility", _fake_get_menu_entries_for_facility)
    monkeypatch.setattr(output_builder, "get_order_menu_snapshot", lambda _order_id: {})

    lines = output_builder.build_order_lines_for_outputs(
        {
            "id": "ORD-CROSS",
            "facility": "FAC00001",
            "week_value": "2026-04@2026-04-29~2026-05-02",
            "lines": [
                {
                    "date": "2026-05-01",
                    "daypart": "朝",
                    "menu_name": "B",
                    "diet_type": "regular",
                    "area_id": "X",
                    "quantity_original": 1,
                    "quantity_corrected": 1,
                }
            ],
        }
    )

    assert len(lines) == 1
    assert lines[0]["daypart"] == "昼"
    assert lines[0]["menu_category"] == "side"
