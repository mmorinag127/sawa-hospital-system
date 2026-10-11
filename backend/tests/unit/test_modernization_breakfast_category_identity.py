import pytest

from src.services import output_builder
from tests.helpers.monthly_override_fixture import (
    authored_entries, publish_configuration, publish_sources, produce,
)


def entries(daypart, categories, slots):
    return authored_entries([
        ("2026-06-08", daypart, slot, f"Synthetic menu {index}", category, "regular")
        for index, (category, slot) in enumerate(zip(categories, slots, strict=True))
    ])


@pytest.mark.parametrize("slots", [(1, 2), (4, 11), (8, 21, 35)])
def test_breakfast_uses_physical_order_not_slot_numbers(slots):
    rows = entries("朝食", ["主菜"] + ["副菜"] * (len(slots) - 1), slots)
    resolved = output_builder._captured_monthly_categories(rows)
    assert list(resolved.values()) == [f"副菜{i}" for i in range(1, len(slots) + 1)]


def test_logical_variants_share_one_physical_category():
    rows = entries("朝", ["主菜", "副菜"], [4, 9])
    rows.extend([{**row, "diet_type": diet} for row in list(rows) for diet in ("soft", "mixer")])
    resolved = output_builder._captured_monthly_categories(rows)
    assert len(resolved) == 2
    assert list(resolved.values()) == ["副菜1", "副菜2"]


@pytest.mark.parametrize("daypart, categories, expected", [
    ("朝", ["副菜", "副菜"], ["副菜1", "副菜2"]),
    ("昼", ["主菜", "副菜"], ["主菜", "副菜1"]),
    ("夕", ["主菜", "副菜"], ["主菜", "副菜1"]),
    ("朝", ["主A", "主Ａ", "主食", "副菜2"], ["主A", "主Ａ", "主食", "副菜2"]),
])
def test_other_explicit_semantics_unchanged(daypart, categories, expected):
    assert list(output_builder._captured_monthly_categories(
        entries(daypart, categories, list(range(4, 4 + len(categories))))
    ).values()) == expected


@pytest.mark.parametrize("categories, slots", [
    (["主菜", "副菜1"], [4, 9]),
    (["主菜", "主菜"], [4, 9]),
    (["主菜", "副菜"], [4, 4]),
])
def test_same_categories_across_slots_are_legal_but_same_slot_conflict_stops(categories, slots):
    if slots[0] == slots[1]:
        with pytest.raises(ValueError, match="delivery_monthly_slot_ambiguous"):
            output_builder._captured_monthly_categories(entries("朝", categories, slots))
    else:
        assert list(output_builder._captured_monthly_categories(
            entries("朝", categories, slots)
        ).values()) == ["副菜1", "副菜1"]
    with pytest.raises(ValueError, match="delivery_monthly_slot_ambiguous"):
        output_builder._captured_monthly_categories(entries("朝", ["副菜", "副菜1"], [4, 9]))


def test_missing_monthly_source_stops():
    with pytest.raises(ValueError, match="delivery_monthly_category_required"):
        output_builder._apply_label_meal_slot_categories([{"daypart": "朝", "menu_category": "副菜"}])
    with pytest.raises(ValueError, match="delivery_monthly_slot_required"):
        output_builder._captured_monthly_categories([{"menu_date": "2026-06-08", "daypart": "朝", "category": "主菜"}])


def test_real_sql_capture_producer_to_labels_preserves_quantities(
    monkeypatch, tmp_path, isolated_formal_schema,
):
    configured = publish_configuration(monkeypatch, tmp_path, day="2026-06-08")
    rows = entries("朝", ["主菜", "主菜", "主菜"], [4, 9, 17])
    publish_sources(rows, physical_amount=(1, "g"))
    original = [2, 0, None]
    lines = produce({
        "id": "ORD-breakfast-component", "facility": "FAC00009",
        "week_value": "2026-06@2026-06-08~2026-06-08",
        "lines": [{
            "date": "2026-06-08", "daypart": "朝", "menu_name": row["name"],
            "menu_category": row["category"], "diet_type": "regular", "area_id": "2F",
            "quantity_original": quantity, "quantity_corrected": None, "source_row_index": index,
        } for index, (row, quantity) in enumerate(zip(rows, original, strict=True))],
    }, configured_input=configured)
    assert [line["quantity_original"] for line in lines] == original
    assert [line["quantity_corrected"] for line in lines] == [None, None, None]
    assert [line["menu_category"] for line in lines] == ["副菜1", "副菜1", "副菜1"]
    bags = output_builder._bind_label_area_names([
        {**line, "facility": "FAC00009", "quantity": line["quantity_original"]}
        for line in lines if line["quantity_original"] == 2
    ], configured["facility"])
    labels, _, label_format = output_builder._build_label_rows(bags, {}, None)
    assert label_format == "jp"
    assert [label["メニュー"] for label in labels] == ["副菜①"]
