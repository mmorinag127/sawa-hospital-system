import pathlib
import sys
import csv
from datetime import datetime
from uuid import uuid4

from openpyxl import Workbook, load_workbook
from sqlalchemy import delete

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.db import session_scope
from src.models.menu import MonthlyMenu, MonthlyMenuItem
from src.services import config_service, facility_service, menu_service, order_service
from src.services.output_builder import build_outputs, rebuild_bags
from src.workers.ingest_mail_adapter import IngestEmailPayload


def _reset_orders_and_menus() -> None:
    config_service.reload_configs()
    order_service.clear_all()
    with session_scope() as session:
        session.execute(delete(MonthlyMenuItem))
        session.execute(delete(MonthlyMenu))


def test_condiment_bags_are_merged_across_diet_and_area():
    _reset_orders_and_menus()

    payload = IngestEmailPayload(
        message_id="bag-condiment-001",
        pdf_uri="file:///tmp/bag-condiment.pdf",
        received_at=datetime(2025, 1, 5, 12, 0, 0),
        facility_hint="FAC00001",
        week_hint="2025-01",
    )
    lines = [
        {
            "line_id": "1",
            "date": "2025-01-06",
            "daypart": "朝",
            "menu_name": "ソース",
            "diet_type": "regular",
            "area_id": "2F",
            "bag_type": "condiment",
            "quantity_original": 3,
        },
        {
            "line_id": "2",
            "date": "2025-01-06",
            "daypart": "朝",
            "menu_name": "ソース",
            "diet_type": "soft",
            "area_id": "3F",
            "bag_type": "condiment",
            "quantity_original": 4,
        },
    ]
    order = order_service.create_order_from_ingest(payload, lines=lines)

    rebuilt = rebuild_bags(order["id"])
    condiments = [bag for bag in rebuilt["bags"] if bag.get("bag_type") == "condiment"]

    assert len(condiments) == 1
    assert condiments[0]["menu_name"] == "ソース"
    assert condiments[0]["quantity"] == 7.0
    assert condiments[0]["diet_type"] is None
    assert condiments[0]["area_id"] is None


def test_delivery_slot_template_writes_menu_display_and_clears_unassigned(tmp_path):
    _reset_orders_and_menus()

    week_id = "2025-02"
    menu_am = menu_service.create_item_stub(week_id, "メニュー朝")
    menu_service.update_item(week_id, menu_am["id"], {"category": "主A"})
    menu_pm = menu_service.create_item_stub(week_id, "メニュー夕")
    menu_service.update_item(week_id, menu_pm["id"], {"category": "主A"})

    template_path = tmp_path / "delivery_slot_template.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "Template"
    ws.cell(row=1, column=1, value="日付")
    ws.cell(row=1, column=2, value="区分")
    ws.cell(row=1, column=3, value="献立")
    ws.cell(row=1, column=4, value="常食2F")

    ws.cell(row=2, column=2, value="朝")
    ws.cell(row=2, column=3, value="主A")
    ws.cell(row=2, column=4, value=999)

    ws.cell(row=3, column=3, value="副①")
    ws.cell(row=3, column=4, value=999)

    ws.cell(row=4, column=2, value="夕")
    ws.cell(row=4, column=3, value="主A")
    ws.cell(row=4, column=4, value=999)

    wb.save(template_path)

    slot_area_id = f"ARE_SLOT_{uuid4().hex[:8]}"
    facility = facility_service.create_facility(
        "Slot Writer Facility",
        [{"id": slot_area_id, "name": "2F"}],
    )
    updated = facility_service.update_config(
        facility["id"],
        {
            "invoice_template": {
                "template_uri": template_path.as_uri(),
                "sheet_name": "Template",
                "include_menu_name": False,
                "columns": [
                    {"name": "日付", "source": "date", "column_index": 1},
                    {"name": "区分", "source": "daypart", "column_index": 2},
                    {"name": "献立", "source": "menu_display", "column_index": 3},
                    {
                        "name": "常食2F",
                        "source": "quantity",
                        "diet_type": "regular",
                        "area_id": "2F",
                        "column_index": 4,
                    },
                ],
            }
        },
    )
    assert updated

    payload = IngestEmailPayload(
        message_id="delivery-slot-001",
        pdf_uri="file:///tmp/delivery-slot.pdf",
        received_at=datetime(2025, 2, 20, 9, 0, 0),
        facility_hint=facility["id"],
        week_hint=week_id,
    )
    lines = [
        {
            "line_id": "1",
            "date": "2025-02-20",
            "daypart": "朝",
            "menu_name": "メニュー朝",
            "diet_type": "regular",
            "area_id": "2F",
            "bag_type": "standard",
            "quantity_original": 5,
        },
        {
            "line_id": "2",
            "date": "2025-02-20",
            "daypart": "夕",
            "menu_name": "メニュー夕",
            "diet_type": "regular",
            "area_id": "2F",
            "bag_type": "standard",
            "quantity_original": 7,
        },
    ]
    order = order_service.create_order_from_ingest(payload, lines=lines)

    outputs = build_outputs(order["id"])
    delivery_wb = load_workbook(outputs["delivery_note"])
    ws_out = delivery_wb["2025-02-20"]

    assert ws_out.cell(row=2, column=3).value == "主A メニュー朝"
    assert ws_out.cell(row=2, column=4).value == 5
    assert ws_out.cell(row=4, column=3).value == "主A メニュー夕"
    assert ws_out.cell(row=4, column=4).value == 7
    assert ws_out.cell(row=3, column=3).value in ("", None)
    assert ws_out.cell(row=3, column=4).value in ("", None)


def test_ikebukuro_label_expiry_is_plus_three_months_and_content_amount_fallbacks():
    _reset_orders_and_menus()

    payload = IngestEmailPayload(
        message_id="label-ikebukuro-001",
        pdf_uri="file:///tmp/label-ikebukuro.pdf",
        received_at=datetime(2025, 1, 5, 12, 0, 0),
        facility_hint="FAC00005",
        week_hint="2025-01",
    )
    lines = [
        {
            "line_id": "1",
            "date": "2025-01-06",
            "daypart": "朝",
            "menu_name": "テストメニュー",
            "diet_type": "regular",
            "area_id": "2F",
            "bag_type": "standard",
            "quantity_original": 2,
        }
    ]
    order = order_service.create_order_from_ingest(payload, lines=lines)
    outputs = build_outputs(order["id"])

    with pathlib.Path(outputs["labels"]).open("r", newline="", encoding="cp932") as handle:
        rows = list(csv.DictReader(handle))

    assert len(rows) == 1
    assert rows[0]["賞味期限"] == "2025年4月6日"
    assert rows[0]["内容量"] == "2人前"
    assert rows[0]["実量"] in ("", None)
