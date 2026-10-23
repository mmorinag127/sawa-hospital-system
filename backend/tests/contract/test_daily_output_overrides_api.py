import pathlib
import json
import sys
import pytest

from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.main import app  # noqa: E402
from src.services import order_service, output_builder, menu_service  # noqa: E402
from tests.helpers.newnarrowbaggingfixture import (  # noqa: E402
    selected_bagging_order, accepted_bagging_order,
)


def _daily_lines(*, target_date="2026-04-18", daypart="昼",
                 menu_name="筑前煮", diet_type="regular", quantity=9):
    return [{"date": target_date, "daypart": daypart, "menu_name": menu_name,
             "diet_type": diet_type, "area_id": "2F", "bag_type": "standard",
             "quantity_original": quantity}]


@pytest.fixture
def daily_override_sources(monkeypatch, tmp_path, isolated_formal_schema, request):
    """Authored output components, not recognition or real FAX proof."""
    order_service.clear_all()
    # Prepare the complete canonical source set before capturing any order.
    menus = getattr(request, "param", {
        "FAC00003": ["筑前煮", "白身魚のフライ"],
        "FAC00004": ["筑前煮", "ハンバーグ"],
        "FAC00005": ["春巻き"],
        "FAC00006": ["焼き海苔"],
    })
    for facility, names in menus.items():
        lines = [line for name in names for line in _daily_lines(menu_name=name)]
        selected_bagging_order(monkeypatch, tmp_path, lines,
            facility_id=facility, menu_category="副菜",
            menu_qty_per_serving=40, menu_unit_type="g", prepare_only=True)
    path = tmp_path / "authored-facilities.json"
    configured = json.loads(path.read_text())
    for facility in configured["facilities"]:
        facility["versions"][0]["valid_from"] = "2026-04-17"
    path.write_text(json.dumps(configured))
    return monkeypatch, tmp_path


def _create_daily_override_order(
    sources, *, message_id: str, facility_hint: str = "FAC00003",
    target_date: str = "2026-04-18", daypart: str = "昼",
    menu_name: str = "筑前煮", diet_type: str = "regular", quantity: float = 9, week_start=None,
):
    monkeypatch, tmp_path = sources
    order = accepted_bagging_order(monkeypatch, tmp_path,
        _daily_lines(target_date=target_date, daypart=daypart, menu_name=menu_name,
                     diet_type=diet_type, quantity=quantity),
        facility_id=facility_hint, message_id=message_id,
        menu_category="副菜", prepared_sources=True, confirm=True, week_start=week_start)
    return order_service.get_order_by_id(order["id"])


def _original_receipt(client, *, menu_name="筑前煮"):
    response = client.get("/orders/daily-output-overrides",
        params={"date": "2026-04-18", "daypart": "昼", "menu_name": menu_name,
                "menu_category": "副菜1"})
    assert response.status_code == 200
    return response.json()["original_receipt"]


def _create_ambiguous_originals(sources):
    first = _create_daily_override_order(sources, message_id="ORD-AMB-001", quantity=9)
    # Explicit later menu edit: the first accepted publication stays immutable.
    items = menu_service.get_menu_items_for_facility("2026-04", "FAC00003")
    item = next(item for item in items if item["name"] == "筑前煮")
    assert menu_service.update_item("2026-04", item["id"],
        {"qty_per_serving": 50, "unit_type": "g"}) is True
    second = _create_daily_override_order(sources, message_id="ORD-AMB-002", quantity=8,
                                          week_start="2026-04-17")
    for order, amount, people in ((first, 40, 9), (second, 50, 8)):
        lines = output_builder.build_order_lines_for_outputs(order_service.get_order_by_id(order["id"]))
        line = next(line for line in lines if line["menu_name"] == "筑前煮")
        assert line["menu_qty_per_serving"] == amount
        assert line["menu_unit_type"] == "g"
        assert line["quantity_original"] == people
        assert line["menu_qty_per_serving"] * line["quantity_original"] == amount * people
    return first, second


def _find_daily_bag_diet_group(payload: dict, *, menu_name: str, diet_type: str):
    for group in payload.get("groups") or []:
        if str(group.get("menu_name") or "").strip() != menu_name:
            continue
        for diet_group in group.get("diet_groups") or []:
            if str(diet_group.get("diet_type") or "").strip() == diet_type:
                return diet_group
    raise AssertionError(f"diet group not found for {menu_name}/{diet_type}")


def test_daily_output_override_updates_daily_bags_and_order_bags(monkeypatch, daily_override_sources):
    monkeypatch.setenv("AUTH_DISABLED", "true")
    order = _create_daily_override_order(daily_override_sources, message_id="msg-daily-override-001")

    client = TestClient(app)

    editor_res = client.get(
        "/orders/daily-output-overrides",
        params={
            "menu_category": "副菜1",
            "date": "2026-04-18",
            "daypart": "昼",
            "menu_name": "筑前煮",
        },
    )
    assert editor_res.status_code == 200
    editor_rows = editor_res.json().get("rows") or []
    assert len(editor_rows) == 1
    row = editor_rows[0]
    assert row.get("facility_id") == "FAC00003"
    assert row.get("diet_type") == "regular"
    assert row.get("override") is None

    save_res = client.post(
        "/orders/daily-output-overrides/upsert",
        json={
            "menu_category": "副菜1",
            "original_receipt": _original_receipt(client),
            "date": "2026-04-18",
            "facility_id": "FAC00003",
            "menu_name": "筑前煮",
            "diet_type": "regular",
            "daypart": "昼",
            "unit_type": "g",
            "qty_per_serving": 40,
            "note": "この日だけ 40g",
        },
    )
    assert save_res.status_code == 200
    save_payload = save_res.json()
    assert save_payload.get("override", {}).get("qty_per_serving") == 40
    assert order["id"] in (save_payload.get("affected_order_ids") or [])

    output_lines = output_builder.build_order_lines_for_outputs(order_service.get_order_by_id(order["id"]))
    matching_line = next(
        line
        for line in output_lines
        if str(line.get("date") or "") == "2026-04-18"
        and str(line.get("daypart") or "") == "昼"
        and str(line.get("menu_name") or "") == "筑前煮"
        and str(line.get("diet_type") or "") == "regular"
    )
    assert matching_line.get("menu_qty_per_serving") == 40
    assert matching_line.get("menu_unit_type") == "g"
    assert matching_line.get("actual_amount") == 360

    editor_after_res = client.get(
        "/orders/daily-output-overrides",
        params={
            "menu_category": "副菜1",
            "date": "2026-04-18",
            "daypart": "昼",
            "menu_name": "筑前煮",
        },
    )
    assert editor_after_res.status_code == 200
    row_after = (editor_after_res.json().get("rows") or [])[0]
    assert row_after.get("override", {}).get("qty_per_serving") == 40
    assert row_after.get("override", {}).get("unit_type") == "g"

    daily_bags_res = client.get(
        "/orders/daily-bags",
        params={"date": "2026-04-18"},
    )
    assert daily_bags_res.status_code == 200
    daily_group = _find_daily_bag_diet_group(daily_bags_res.json(), menu_name="筑前煮", diet_type="regular")
    assert daily_group.get("calculation_basis_label") == "40g/人"
    assert daily_group.get("total_amount_label") == "360g"

    order_bags_res = client.get(f"/orders/{order['id']}/bags")
    assert order_bags_res.status_code == 200
    order_bags_payload = order_bags_res.json()
    assert order_bags_payload.get("generated") is True
    applied = order_bags_payload.get("applied_portion_overrides") or []
    assert len(applied) == 1
    assert applied[0].get("menu_name") == "筑前煮"
    assert applied[0].get("qty_per_serving") == 40
    assert applied[0].get("unit_type") == "g"

    override_id = save_payload.get("override", {}).get("id")
    delete_res = client.request("DELETE", f"/orders/daily-output-overrides/{override_id}",
        json={"original_receipt": _original_receipt(client)})
    assert delete_res.status_code == 200
    _original_receipt(client)
    order_bags_after_delete = client.get(f"/orders/{order['id']}/bags")
    assert order_bags_after_delete.status_code == 200
    assert (order_bags_after_delete.json().get("applied_portion_overrides") or []) == []


@pytest.mark.parametrize("daily_override_sources", [{
    "FAC00003": ["白身魚のフライ"], "FAC00004": ["ハンバーグ"],
    "FAC00005": ["春巻き"], "FAC00006": ["焼き海苔"],
}], indirect=True)
def test_daily_output_override_normalizes_non_gram_units(monkeypatch, daily_override_sources):
    monkeypatch.setenv("AUTH_DISABLED", "true")
    cut_order = _create_daily_override_order(
        daily_override_sources,
        message_id="msg-daily-override-cut-001",
        menu_name="白身魚のフライ",
        quantity=4,
    )
    count_order = _create_daily_override_order(
        daily_override_sources,
        message_id="msg-daily-override-count-001",
        facility_hint="FAC00004",
        menu_name="ハンバーグ",
        quantity=3,
    )
    stick_order = _create_daily_override_order(
        daily_override_sources,
        message_id="msg-daily-override-stick-001",
        facility_hint="FAC00005",
        menu_name="春巻き",
        quantity=5,
    )
    sheet_order = _create_daily_override_order(
        daily_override_sources,
        message_id="msg-daily-override-sheet-001",
        facility_hint="FAC00006",
        menu_name="焼き海苔",
        quantity=6,
    )

    client = TestClient(app)

    cut_res = client.post(
        "/orders/daily-output-overrides/upsert",
        json={
            "menu_category": "副菜1",
            "original_receipt": _original_receipt(client, menu_name="白身魚のフライ"),
            "date": "2026-04-18",
            "facility_id": "FAC00003",
            "menu_name": "白身魚のフライ",
            "diet_type": "regular",
            "daypart": "昼",
            "unit_type": "切れ",
            "qty_per_serving": 2,
            "note": "魚は2切",
        },
    )
    assert cut_res.status_code == 200, cut_res.text
    _original_receipt(client, menu_name="白身魚のフライ")
    assert cut_res.json().get("override", {}).get("unit_type") == "切"

    count_res = client.post(
        "/orders/daily-output-overrides/upsert",
        json={
            "menu_category": "副菜1",
            "original_receipt": _original_receipt(client, menu_name="ハンバーグ"),
            "date": "2026-04-18",
            "facility_id": "FAC00004",
            "menu_name": "ハンバーグ",
            "diet_type": "regular",
            "daypart": "昼",
            "unit_type": "count",
            "qty_per_serving": 1,
            "note": "ハンバーグは1個",
        },
    )
    assert count_res.status_code == 200
    _original_receipt(client, menu_name="ハンバーグ")
    assert count_res.json().get("override", {}).get("unit_type") == "個"

    stick_res = client.post(
        "/orders/daily-output-overrides/upsert",
        json={
            "menu_category": "副菜1",
            "original_receipt": _original_receipt(client, menu_name="春巻き"),
            "date": "2026-04-18",
            "facility_id": "FAC00005",
            "menu_name": "春巻き",
            "diet_type": "regular",
            "daypart": "昼",
            "unit_type": "本",
            "qty_per_serving": 2,
            "note": "春巻きは2本",
        },
    )
    assert stick_res.status_code == 200
    _original_receipt(client, menu_name="春巻き")
    assert stick_res.json().get("override", {}).get("unit_type") == "本"

    sheet_res = client.post(
        "/orders/daily-output-overrides/upsert",
        json={
            "menu_category": "副菜1",
            "original_receipt": _original_receipt(client, menu_name="焼き海苔"),
            "date": "2026-04-18",
            "facility_id": "FAC00006",
            "menu_name": "焼き海苔",
            "diet_type": "regular",
            "daypart": "昼",
            "unit_type": "枚",
            "qty_per_serving": 3,
            "note": "焼き海苔は3枚",
        },
    )
    assert sheet_res.status_code == 200
    _original_receipt(client, menu_name="焼き海苔")
    assert sheet_res.json().get("override", {}).get("unit_type") == "枚"

    cut_lines = output_builder.build_order_lines_for_outputs(order_service.get_order_by_id(cut_order["id"]))
    cut_line = next(line for line in cut_lines if str(line.get("menu_name") or "") == "白身魚のフライ")
    assert cut_line.get("menu_unit_type") == "切"
    assert cut_line.get("actual_amount") == 8

    count_lines = output_builder.build_order_lines_for_outputs(order_service.get_order_by_id(count_order["id"]))
    count_line = next(line for line in count_lines if str(line.get("menu_name") or "") == "ハンバーグ")
    assert count_line.get("menu_unit_type") == "個"
    assert count_line.get("actual_amount") == 3

    stick_lines = output_builder.build_order_lines_for_outputs(order_service.get_order_by_id(stick_order["id"]))
    stick_line = next(line for line in stick_lines if str(line.get("menu_name") or "") == "春巻き")
    assert stick_line.get("menu_unit_type") == "本"
    assert stick_line.get("actual_amount") == 10

    sheet_lines = output_builder.build_order_lines_for_outputs(order_service.get_order_by_id(sheet_order["id"]))
    sheet_line = next(line for line in sheet_lines if str(line.get("menu_name") or "") == "焼き海苔")
    assert sheet_line.get("menu_unit_type") == "枚"
    assert sheet_line.get("actual_amount") == 18

    daily_bags_res = client.get("/orders/daily-bags", params={"date": "2026-04-18"})
    assert daily_bags_res.status_code == 200
    cut_group = _find_daily_bag_diet_group(daily_bags_res.json(), menu_name="白身魚のフライ", diet_type="regular")
    assert cut_group.get("calculation_basis_label") == "2切/人"
    assert cut_group.get("total_amount_label") == "8切"
    count_group = _find_daily_bag_diet_group(daily_bags_res.json(), menu_name="ハンバーグ", diet_type="regular")
    assert count_group.get("calculation_basis_label") == "1個/人"
    assert count_group.get("total_amount_label") == "3個"
    stick_group = _find_daily_bag_diet_group(daily_bags_res.json(), menu_name="春巻き", diet_type="regular")
    assert stick_group.get("calculation_basis_label") == "2本/人"
    assert stick_group.get("total_amount_label") == "10本"
    sheet_group = _find_daily_bag_diet_group(daily_bags_res.json(), menu_name="焼き海苔", diet_type="regular")
    assert sheet_group.get("calculation_basis_label") == "3枚/人"
    assert sheet_group.get("total_amount_label") == "18枚"


def test_daily_output_override_bulk_updates_all_facilities(monkeypatch, daily_override_sources):
    monkeypatch.setenv("AUTH_DISABLED", "true")
    order_a = _create_daily_override_order(daily_override_sources, message_id="msg-daily-override-bulk-001", facility_hint="FAC00003", quantity=9)
    order_b = _create_daily_override_order(daily_override_sources, message_id="msg-daily-override-bulk-002", facility_hint="FAC00004", quantity=4)

    client = TestClient(app)

    bulk_res = client.post(
        "/orders/daily-output-overrides/upsert-bulk",
        json={
            "menu_category": "副菜1",
            "original_receipt": _original_receipt(client),
            "date": "2026-04-18",
            "menu_name": "筑前煮",
            "daypart": "昼",
            "unit_type": "cut",
            "qty_per_serving": 2,
            "note": "全施設2切",
        },
    )
    assert bulk_res.status_code == 200
    payload = bulk_res.json()
    assert payload.get("updated_count") == 2
    assert order_a["id"] in (payload.get("affected_order_ids") or [])
    assert order_b["id"] in (payload.get("affected_order_ids") or [])
    assert {item.get("facility_id") for item in payload.get("overrides") or []} == {"FAC00003", "FAC00004"}
    assert {item.get("unit_type") for item in payload.get("overrides") or []} == {"切"}

    editor_res = client.get(
        "/orders/daily-output-overrides",
        params={
            "menu_category": "副菜1",
            "date": "2026-04-18",
            "daypart": "昼",
            "menu_name": "筑前煮",
        },
    )
    assert editor_res.status_code == 200
    rows = editor_res.json().get("rows") or []
    assert len(rows) == 2
    assert {row.get("override", {}).get("unit_type") for row in rows} == {"切"}

    daily_bags_res = client.get("/orders/daily-bags", params={"date": "2026-04-18"})
    assert daily_bags_res.status_code == 200
    diet_group = _find_daily_bag_diet_group(daily_bags_res.json(), menu_name="筑前煮", diet_type="regular")
    assert diet_group.get("calculation_basis_label") == "2切/人"
    assert diet_group.get("total_amount_label") == "26切"


def test_daily_output_override_bulk_requires_intervention_when_rows_are_ambiguous(monkeypatch, daily_override_sources):
    monkeypatch.setenv("AUTH_DISABLED", "true")
    _create_ambiguous_originals(daily_override_sources)
    client = TestClient(app)
    target_date = _original_receipt(client)["date"]
    assert str(target_date) == "2026-04-18"

    res = client.post(
        "/orders/daily-output-overrides/upsert-bulk",
        json={
            "menu_category": "副菜1",
            "original_receipt": _original_receipt(client),
            "date": "2026-04-18",
            "menu_name": "筑前煮",
            "daypart": "昼",
            "unit_type": "g",
            "qty_per_serving": 40,
        },
    )
    assert res.status_code == 409
    detail = res.json().get("detail") or {}
    assert detail.get("code") == "daily_output_override_bulk_requires_intervention"
    assert len(detail.get("rows") or []) == 1


def test_daily_output_override_requires_acknowledgement_for_ambiguous_rows(monkeypatch, daily_override_sources):
    monkeypatch.setenv("AUTH_DISABLED", "true")
    _create_ambiguous_originals(daily_override_sources)
    client = TestClient(app)
    target_date = _original_receipt(client)["date"]
    assert str(target_date) == "2026-04-18"

    editor_res = client.get(
        "/orders/daily-output-overrides",
        params={
            "menu_category": "副菜1",
            "date": "2026-04-18",
            "daypart": "昼",
            "menu_name": "筑前煮",
        },
    )
    assert editor_res.status_code == 200
    editor_rows = editor_res.json().get("rows") or []
    assert len(editor_rows) == 1
    row = editor_rows[0]
    assert row.get("requires_intervention") is True
    assert len(row.get("current_variants") or []) == 2

    blocked_res = client.post(
        "/orders/daily-output-overrides/upsert",
        json={
            "menu_category": "副菜1",
            "original_receipt": _original_receipt(client),
            "date": "2026-04-18",
            "facility_id": "FAC00003",
            "menu_name": "筑前煮",
            "diet_type": "regular",
            "daypart": "昼",
            "unit_type": "g",
            "qty_per_serving": 45,
            "note": "operator decision required",
        },
    )
    assert blocked_res.status_code == 409
    detail = blocked_res.json().get("detail") or {}
    assert detail.get("code") == "daily_output_override_ambiguous"
    assert len(detail.get("candidates") or []) == 2

    acknowledged_res = client.post(
        "/orders/daily-output-overrides/upsert",
        json={
            "menu_category": "副菜1",
            "original_receipt": _original_receipt(client),
            "date": "2026-04-18",
            "facility_id": "FAC00003",
            "menu_name": "筑前煮",
            "diet_type": "regular",
            "daypart": "昼",
            "unit_type": "g",
            "qty_per_serving": 45,
            "note": "operator decision required",
            "acknowledge_ambiguous": True,
        },
    )
    assert acknowledged_res.status_code == 200
    _original_receipt(client)
    assert acknowledged_res.json().get("override", {}).get("qty_per_serving") == 45
