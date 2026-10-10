"""Weekly primary-slot component contracts, not HTTP/native/stg proof.

Only negative writer tests inject collector results and a forbidden shell spy.
Real accepted-producer/HTTP coverage remains in test_modernization_real_weekly_weight.
"""

from copy import deepcopy
from datetime import date, timedelta

import pytest

from src.services import output_builder as output


DAY = date(2026, 3, 2)


def component_line(**identity):
    return {
        "date": DAY,
        "daypart": "朝",
        "menu_category": "主菜",
        "quantity_original": 7,
        "quantity_corrected": 0,
        "other_quantities": [7, 0, None, ""],
        "amount": 700,
        "unit": "g",
        "diet_type": "regular",
        "extra": {"null": None, "empty": "", "raw": [0, 7]},
        **identity,
    }


@pytest.mark.parametrize("daypart,category,expected", [
    ("朝", "主", "副①"),
    ("朝食", "主菜", "副①"),
    ("昼", "主", "主Ａ"),
    ("昼食", "主菜", "主Ａ"),
    ("夕", "主", "主"),
    ("夕食", "主菜", "主"),
    # Explicit side aliases in the weekly normalizer; full forms also occur in
    # the shared delivery-category vocabulary. No substring inference is used.
    *[("朝", alias, "副①") for alias in ("副1", "副①", "副一", "副菜1", "副菜①", "副菜一")],
    *[("夕", alias, "副②") for alias in ("副2", "副②", "副二", "副菜2", "副菜②", "副菜二")],
    *[("昼", alias, "主Ａ") for alias in ("主A", "主Ａ", "主菜A", "主菜Ａ")],
])
def test_primary_alias_resolves_without_mutating_raw_line(daypart, category, expected):
    line = component_line(daypart=daypart, menu_category=category)
    before = deepcopy(line)
    assert output._weekly_weight_slot_for_line(line) == expected
    assert line == before
    assert line["quantity_corrected"] == 0
    assert line["other_quantities"] == [7, 0, None, ""]


@pytest.mark.parametrize("identity,missing_key,error", [
    ({}, "menu_category", "weekly_weight_category_unresolved"),
    ({"menu_category": None}, None, "weekly_weight_category_unresolved"),
    ({"menu_category": ""}, None, "weekly_weight_category_unresolved"),
    ({"menu_category": " "}, None, "weekly_weight_category_unresolved"),
    ({"menu_category": "unknown"}, None, "weekly_weight_category_unresolved"),
    ({"menu_category": "unknown主菜A"}, None, "weekly_weight_category_unresolved"),
    ({"menu_category": "副③"}, None, "weekly_weight_category_unresolved"),
    ({}, "daypart", "weekly_weight_daypart_unresolved"),
    ({"daypart": None}, None, "weekly_weight_daypart_unresolved"),
    ({"daypart": ""}, None, "weekly_weight_daypart_unresolved"),
    ({"daypart": "朝unknown"}, None, "weekly_weight_daypart_unresolved"),
    ({"menu_category": "主Ａ"}, None, "weekly_weight_slot_unsupported"),
    ({"daypart": "夕", "menu_category": "主Ａ"}, None, "weekly_weight_slot_unsupported"),
])
def test_invalid_primary_or_meal_never_uses_secondary_fields(identity, missing_key, error):
    line = component_line(**identity)
    if missing_key:
        del line[missing_key]
    for key in ("slot_label", "category", "menu_slot", "_menu_slot_label"):
        line[key] = "副①"
    before = deepcopy(line)
    with pytest.raises(ValueError, match=f"^{error}$"):
        output._weekly_weight_slot_for_line(line)
    assert line == before


@pytest.mark.parametrize("fault", ["outside-week", "unknown-slot", "unknown-meal", "duplicate-layout"])
def test_writer_rejects_unconsumable_keys_before_shell_or_files(monkeypatch, tmp_path, fault):
    key = (DAY, "朝", "副①")
    if fault == "outside-week":
        key = (DAY + timedelta(days=7), "朝", "副①")
    elif fault == "unknown-slot":
        key = (DAY, "朝", "副③")
    elif fault == "unknown-meal":
        key = (DAY, "unknown", "副①")
    rows = {key: {"regular_menu": "component", "regular_quantity": 7, "regular_amounts": {"g": 700}}}
    rows_before = deepcopy(rows)
    layout_before = deepcopy(output._WEEKLY_WEIGHT_DAYPART_SLOTS)
    if fault == "duplicate-layout":
        layout = deepcopy(layout_before)
        layout[0][1].append("副①")
        monkeypatch.setattr(output, "_WEEKLY_WEIGHT_DAYPART_SLOTS", layout)
    collect_calls = []
    shell_calls = []

    def faulty_collect(target_date, *, status=None):
        collect_calls.append((target_date, status))
        return rows

    def forbidden_shell(*args, **kwargs):
        shell_calls.append((args, kwargs))
        raise AssertionError("invalid collected identity must stop before workbook generation")

    monkeypatch.setattr(output, "_weekly_weight_collect_rows", faulty_collect)
    monkeypatch.setattr(output, "_build_weekly_weight_workbook_shell", forbidden_shell)
    monkeypatch.setattr(output, "OUTPUT_DIR", tmp_path)
    files_before = {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    with pytest.raises(ValueError, match="^weekly_weight_rows_unconsumed$"):
        output.build_weekly_weight_summary_workbook(DAY, status="confirmed")
    assert collect_calls == [(DAY, "confirmed")]
    assert shell_calls == []
    assert rows == rows_before
    assert {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()} == files_before
