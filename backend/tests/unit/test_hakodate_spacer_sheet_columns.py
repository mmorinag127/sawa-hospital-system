from src.services import order_service
from src.services import order_workflow_v2_service


def test_align_hakodate_sheet_payload_preserves_physical_spacer_column() -> None:
    sheet = {
        "fields": ["date_mmdd", "daypart", "menu", "qty.regular_x", "qty.no_meat_x", "remarks"],
        "header": ["日付", "区分", "メニュー", "常食", "肉禁", "備考欄"],
        "rows": [["04/26", "朝", "大豆のトマト煮", "70", "3", ""]],
    }
    target_cells = [
        {"worksheet_col": 5, "semantic_field": "qty.regular_x", "metadata": {"field_label": "常食"}},
        {"worksheet_col": 6, "semantic_field": "post_menu.F", "metadata": {"field_label": "空白列(F)"}},
        {"worksheet_col": 7, "semantic_field": "qty.no_meat_x", "metadata": {"field_label": "肉禁"}},
        {"worksheet_col": 8, "semantic_field": "note", "metadata": {"field_label": "備考欄"}},
    ]

    aligned = order_service._align_hakodate_sheet_payload_to_target_cells(sheet, target_cells)  # noqa: SLF001

    assert aligned["fields"] == [
        "date_mmdd",
        "daypart",
        "menu",
        "qty.regular_x",
        "post_menu.F",
        "qty.no_meat_x",
        "remarks",
    ]
    assert aligned["header"] == ["日付", "区分", "メニュー", "常食", "空白列(F)", "肉禁", "備考欄"]
    assert aligned["rows"] == [["04/26", "朝", "大豆のトマト煮", "70", "", "3", ""]]


def test_hakodate_projection_prefers_physical_semantic_field_over_shifted_truth() -> None:
    field_index = {
        "qty.regular_x": 3,
        "post_menu.F": 4,
        "qty.no_meat_x": 5,
    }
    spacer_cell = {
        "semantic_field": "post_menu.F",
        "metadata": {"truth": {"field": "qty.no_meat_x"}},
    }
    meat_cell = {
        "semantic_field": "qty.no_meat_x",
        "metadata": {"truth": {"field": "qty.no_fish_x"}},
    }

    assert order_service._hakodate_projection_field_for_cell(spacer_cell, field_index=field_index) == "post_menu.F"  # noqa: SLF001
    assert order_service._hakodate_projection_field_for_cell(meat_cell, field_index=field_index) == "qty.no_meat_x"  # noqa: SLF001


def test_workflow_compact_target_cell_map_uses_physical_field_identity() -> None:
    target_cells = [
        {
            "sheet_cell": "F11",
            "target_cell_id": "F11",
            "semantic_field": "post_menu.F",
            "bbox": [10, 20, 30, 40],
            "center": [20, 30],
            "metadata": {"truth": {"row_index": 0, "field": "qty.no_meat_x"}},
        },
        {
            "sheet_cell": "G11",
            "target_cell_id": "G11",
            "semantic_field": "qty.no_meat_x",
            "bbox": [30, 20, 50, 40],
            "center": [40, 30],
            "metadata": {"truth": {"row_index": 0, "field": "qty.no_fish_x"}},
        },
    ]

    compact = order_workflow_v2_service._compact_target_cell_map_for_sheet(  # noqa: SLF001
        target_cells=target_cells,
        fields=["date_mmdd", "daypart", "menu", "qty.regular_x", "post_menu.F", "qty.no_meat_x"],
        row_count=1,
    )

    assert [(item["sheet_cell"], item["field"], item["target_col_index"]) for item in compact] == [
        ("F11", "post_menu.F", 4),
        ("G11", "qty.no_meat_x", 5),
    ]
