import pathlib
import sys
from datetime import datetime

from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.main import app  # noqa: E402
from src.services import order_service, output_builder  # noqa: E402
from src.workers.ingest_mail_adapter import IngestEmailPayload  # noqa: E402


def _create_daily_override_order(
    *,
    message_id: str,
    facility_hint: str = "FAC00003",
    target_date: str = "2026-04-18",
    daypart: str = "昼",
    menu_name: str = "筑前煮",
    diet_type: str = "regular",
    quantity: float = 9,
):
    payload = IngestEmailPayload(
        message_id=message_id,
        pdf_uri=f"file://{message_id}.pdf",
        received_at=datetime(2026, 4, 18, 9, 0, 0),
        facility_hint=facility_hint,
        week_hint="2026-04",
    )
    lines = [
        {
            "date": target_date,
            "daypart": daypart,
            "menu_name": menu_name,
            "diet_type": diet_type,
            "area_id": "2F",
            "bag_type": "standard",
            "quantity_original": quantity,
        }
    ]
    return order_service.create_order_from_ingest(payload, lines=lines)


def _find_daily_bag_diet_group(payload: dict, *, menu_name: str, diet_type: str):
    for group in payload.get("groups") or []:
        if str(group.get("menu_name") or "").strip() != menu_name:
            continue
        for diet_group in group.get("diet_groups") or []:
            if str(diet_group.get("diet_type") or "").strip() == diet_type:
                return diet_group
    raise AssertionError(f"diet group not found for {menu_name}/{diet_type}")


def test_daily_output_override_updates_daily_bags_and_order_bags(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "true")
    order_service.clear_all()
    order = _create_daily_override_order(message_id="msg-daily-override-001")

    client = TestClient(app)

    editor_res = client.get(
        "/orders/daily-output-overrides",
        params={
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
    delete_res = client.delete(f"/orders/daily-output-overrides/{override_id}")
    assert delete_res.status_code == 200
    order_bags_after_delete = client.get(f"/orders/{order['id']}/bags")
    assert order_bags_after_delete.status_code == 200
    assert (order_bags_after_delete.json().get("applied_portion_overrides") or []) == []


def test_daily_output_override_normalizes_non_gram_units(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "true")
    order_service.clear_all()
    cut_order = _create_daily_override_order(
        message_id="msg-daily-override-cut-001",
        menu_name="白身魚のフライ",
        quantity=4,
    )
    count_order = _create_daily_override_order(
        message_id="msg-daily-override-count-001",
        facility_hint="FAC00004",
        menu_name="ハンバーグ",
        quantity=3,
    )
    stick_order = _create_daily_override_order(
        message_id="msg-daily-override-stick-001",
        facility_hint="FAC00005",
        menu_name="春巻き",
        quantity=5,
    )
    sheet_order = _create_daily_override_order(
        message_id="msg-daily-override-sheet-001",
        facility_hint="FAC00006",
        menu_name="焼き海苔",
        quantity=6,
    )

    client = TestClient(app)

    cut_res = client.post(
        "/orders/daily-output-overrides/upsert",
        json={
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
    assert cut_res.status_code == 200
    assert cut_res.json().get("override", {}).get("unit_type") == "切"

    count_res = client.post(
        "/orders/daily-output-overrides/upsert",
        json={
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
    assert count_res.json().get("override", {}).get("unit_type") == "個"

    stick_res = client.post(
        "/orders/daily-output-overrides/upsert",
        json={
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
    assert stick_res.json().get("override", {}).get("unit_type") == "本"

    sheet_res = client.post(
        "/orders/daily-output-overrides/upsert",
        json={
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


def test_daily_output_override_bulk_updates_all_facilities(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "true")
    order_service.clear_all()
    order_a = _create_daily_override_order(message_id="msg-daily-override-bulk-001", facility_hint="FAC00003", quantity=9)
    order_b = _create_daily_override_order(message_id="msg-daily-override-bulk-002", facility_hint="FAC00004", quantity=4)

    client = TestClient(app)

    bulk_res = client.post(
        "/orders/daily-output-overrides/upsert-bulk",
        json={
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


def test_daily_output_override_bulk_requires_intervention_when_rows_are_ambiguous(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "true")
    order_service.clear_all()

    def _fake_list_daily_output_override_editor_rows(target_date, *, daypart, menu_name, menu_category=None):
        assert str(target_date) == "2026-04-18"
        return {
            "date": "2026-04-18",
            "daypart": daypart,
            "menu_name": menu_name,
            "menu_category": menu_category or "",
            "rows": [
                {
                    "facility_id": "FAC00003",
                    "facility_label": "施設A (FAC00003)",
                    "diet_type": "regular",
                    "requires_intervention": True,
                    "current_variants": [
                        {"basis_label": "40g/人", "order_ids": ["ORD-A"]},
                        {"basis_label": "50g/人", "order_ids": ["ORD-B"]},
                    ],
                }
            ],
        }

    monkeypatch.setattr(order_service, "list_daily_output_override_editor_rows", _fake_list_daily_output_override_editor_rows)

    client = TestClient(app)
    res = client.post(
        "/orders/daily-output-overrides/upsert-bulk",
        json={
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


def test_daily_output_override_requires_acknowledgement_for_ambiguous_rows(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "true")
    order_service.clear_all()
    fake_orders = [
        {"id": "ORD-AMB-001"},
        {"id": "ORD-AMB-002"},
    ]

    def _fake_list_orders_by_line_date(target_date, facility_id=None, status=None):
        assert str(target_date) == "2026-04-18"
        return fake_orders

    def _fake_get_order_by_id(order_id: str):
        return {
            "id": order_id,
            "facility": "FAC00003",
            "week": "2026-04",
            "week_value": "2026-04",
            "lines": [],
        }

    def _fake_build_order_lines_for_outputs(order_payload: dict):
        order_id = str(order_payload.get("id") or "")
        qty_per_serving = 40.0 if order_id == "ORD-AMB-001" else 50.0
        quantity = 9.0 if order_id == "ORD-AMB-001" else 8.0
        return [
            {
                "date": "2026-04-18",
                "daypart": "昼",
                "menu_name": "筑前煮",
                "diet_type": "regular",
                "area_id": "2F",
                "bag_type": "standard",
                "quantity_original": quantity,
                "quantity_corrected": quantity,
                "menu_qty_per_serving": qty_per_serving,
                "menu_unit_type": "g",
                "actual_amount": quantity * qty_per_serving,
                "actual_unit_type": "g",
                "menu_category": "副菜",
            }
        ]

    monkeypatch.setattr(order_service, "list_orders_by_line_date", _fake_list_orders_by_line_date)
    monkeypatch.setattr(order_service, "get_order_by_id", _fake_get_order_by_id)
    monkeypatch.setattr(order_service, "_rebuild_bags_for_override_scope", lambda target_date, facility_id: ["ORD-AMB-001", "ORD-AMB-002"])
    monkeypatch.setattr(output_builder, "build_order_lines_for_outputs", _fake_build_order_lines_for_outputs)

    client = TestClient(app)

    editor_res = client.get(
        "/orders/daily-output-overrides",
        params={
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
    assert acknowledged_res.json().get("override", {}).get("qty_per_serving") == 45
