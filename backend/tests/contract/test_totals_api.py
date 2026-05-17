import pathlib
import sys
from datetime import date, datetime
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import delete

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.db import session_scope  # noqa: E402
from src.main import app  # noqa: E402
from src.models.facility import Facility, FacilityArea, FacilityConfig  # noqa: E402
from src.models.menu import MonthlyMenuEntry  # noqa: E402
from src.models.order import OrderMenuSnapshot  # noqa: E402
from src.services import facility_service, menu_service, order_service  # noqa: E402
from src.workers.ingest_mail_adapter import IngestEmailPayload  # noqa: E402


def _reset_facilities_from_master() -> None:
    with session_scope() as session:
        session.execute(delete(FacilityConfig))
        session.execute(delete(FacilityArea))
        session.execute(delete(Facility))
    facility_service.list_facilities()


def test_totals_prefers_line_daypart_over_snapshot_daypart():
    order_service.clear_all()
    month_id = f"2026-03-totals-{uuid4().hex[:8]}"
    menu_csv = "menu\n豆腐の煮物\n".encode("utf-8")
    menu_service.create_menu(month_id, menu_csv, "menu.csv")
    menu_item = menu_service.create_item_stub(month_id, "豆腐の煮物")
    menu_service.update_item(
        month_id,
        menu_item["id"],
        {"qty_per_serving": 70, "unit_type": "g", "daypart": "朝食", "category": "主菜"},
    )
    order = order_service.create_order_from_ingest(
        IngestEmailPayload(
            message_id="msg-totals-daypart-001",
            pdf_uri="file://dummy-totals-daypart.pdf",
            received_at=datetime(2026, 3, 24, 9, 0, 0),
            facility_hint="FAC00003",
            week_hint="2026-03",
        ),
        lines=[
            {
                "date": "2026-03-24",
                "daypart": "昼",
                "menu_name": "豆腐の煮物",
                "diet_type": "regular",
                "area_id": "2F",
                "bag_type": "standard",
                "quantity_original": 9,
            }
        ],
    )
    order_service.set_status(order["id"], "確定")
    with session_scope() as session:
        session.add(
            OrderMenuSnapshot(
                id="OMStotals001",
                order_id=order["id"],
                snapshot_json={
                    "version": 1,
                    "generated_at": "2026-03-24T09:00:00",
                    "menu_items": {
                        "豆腐の煮物": {
                            "daypart": "朝食",
                            "category": "主菜",
                            "qty_per_serving": 70.0,
                            "unit_type": "g",
                        }
                    },
                },
            )
        )

    client = TestClient(app)
    res = client.get("/totals?date=2026-03-24")

    assert res.status_code == 200
    row = next(item for item in res.json()["rows"] if item.get("menu_name") == "豆腐の煮物")
    assert row["daypart"] == "昼"


def test_totals_prefers_monthly_menu_entry_category_over_menu_master():
    order_service.clear_all()
    month_id = "2099-03"
    menu_name = f"豆腐の煮物-{uuid4().hex[:6]}"
    menu_csv = f"menu\n{menu_name}\n".encode("utf-8")
    menu_service.create_menu(month_id, menu_csv, "menu.csv")
    menu_item = menu_service.create_item_stub(month_id, menu_name)
    menu_service.update_item(
        month_id,
        menu_item["id"],
        {"qty_per_serving": 70, "unit_type": "g", "daypart": "昼食", "category": "主菜"},
    )
    with session_scope() as session:
        session.add(
            MonthlyMenuEntry(
                id=f"MME{uuid4().hex[:8]}",
                monthly_menu_id=month_id,
                menu_date=date(2099, 3, 24),
                daypart="昼食",
                name=menu_name,
                category="副菜",
                slot_index=2,
                facility_override=None,
            )
        )
    order = order_service.create_order_from_ingest(
        IngestEmailPayload(
            message_id="msg-totals-entry-category-001",
            pdf_uri="file://dummy-totals-entry-category.pdf",
            received_at=datetime(2099, 3, 24, 9, 0, 0),
            facility_hint="FAC00003",
            week_hint=month_id,
        ),
        lines=[
            {
                "date": "2099-03-24",
                "daypart": "昼",
                "menu_name": menu_name,
                "diet_type": "regular",
                "area_id": "2F",
                "bag_type": "standard",
                "quantity_original": 9,
            }
        ],
    )
    order_service.set_status(order["id"], "確定")

    client = TestClient(app)
    res = client.get("/totals?date=2099-03-24")

    assert res.status_code == 200
    row = next(item for item in res.json()["rows"] if item.get("menu_name") == menu_name)
    assert row["menu_category"] == "副菜"


def test_totals_merges_regular_equivalent_diets():
    order_service.clear_all()
    month_id = f"2026-03-totals-regular-bucket-{uuid4().hex[:8]}"
    menu_csv = "menu\nホイコーロー\n".encode("utf-8")
    menu_service.create_menu(month_id, menu_csv, "menu.csv")
    menu_item = menu_service.create_item_stub(month_id, "ホイコーロー")
    menu_service.update_item(
        month_id,
        menu_item["id"],
        {"qty_per_serving": 100, "unit_type": "g", "daypart": "昼食", "category": "主菜"},
    )
    order = order_service.create_order_from_ingest(
        IngestEmailPayload(
            message_id="msg-totals-regular-bucket-001",
            pdf_uri="file://dummy-totals-regular-bucket.pdf",
            received_at=datetime(2026, 3, 24, 9, 0, 0),
            facility_hint="FAC00004",
            week_hint="2026-03",
        ),
        lines=[
            {
                "date": "2026-03-24",
                "daypart": "昼",
                "menu_name": "ホイコーロー",
                "diet_type": "regular",
                "area_id": "X",
                "bag_type": "standard",
                "quantity_original": 9,
            },
            {
                "date": "2026-03-24",
                "daypart": "昼",
                "menu_name": "ホイコーロー",
                "diet_type": "regular_bag",
                "area_id": "X",
                "bag_type": "standard",
                "quantity_original": 1,
            },
            {
                "date": "2026-03-24",
                "daypart": "昼",
                "menu_name": "ホイコーロー",
                "diet_type": "daycare",
                "area_id": "X",
                "bag_type": "standard",
                "quantity_original": 4,
            },
            {
                "date": "2026-03-24",
                "daypart": "昼",
                "menu_name": "ホイコーロー",
                "diet_type": "staff",
                "area_id": "X",
                "bag_type": "standard",
                "quantity_original": 3,
            },
            {
                "date": "2026-03-24",
                "daypart": "昼",
                "menu_name": "ホイコーロー",
                "diet_type": "regular_1600kcal",
                "area_id": "X",
                "bag_type": "standard",
                "quantity_original": 2,
            },
        ],
    )
    order_service.set_status(order["id"], "確定")

    client = TestClient(app)
    res = client.get("/totals?date=2026-03-24")

    assert res.status_code == 200
    rows = [item for item in res.json()["rows"] if item.get("menu_name") == "ホイコーロー"]
    regular = next(item for item in rows if item.get("diet_type") == "regular")
    assert regular["quantity"] == 19.0
    assert not any(item.get("diet_type") in {"regular_bag", "daycare", "staff", "regular_1600kcal"} for item in rows)


def test_totals_can_include_order_refs_for_debug():
    order_service.clear_all()
    month_id = f"2026-03-totals-order-refs-{uuid4().hex[:8]}"
    menu_csv = "menu\nホイコーロー\n".encode("utf-8")
    menu_service.create_menu(month_id, menu_csv, "menu.csv")
    menu_item = menu_service.create_item_stub(month_id, "ホイコーロー")
    menu_service.update_item(
        month_id,
        menu_item["id"],
        {"qty_per_serving": 100, "unit_type": "g", "daypart": "昼食", "category": "主菜"},
    )
    order_a = order_service.create_order_from_ingest(
        IngestEmailPayload(
            message_id=f"msg-totals-order-refs-a-{uuid4().hex[:8]}",
            pdf_uri="file://dummy-totals-order-refs-a.pdf",
            received_at=datetime(2026, 3, 24, 9, 0, 0),
            facility_hint="FAC00004",
            week_hint="2026-03",
        ),
        lines=[
            {
                "date": "2026-03-24",
                "daypart": "昼",
                "menu_name": "ホイコーロー",
                "diet_type": "regular",
                "area_id": "X",
                "bag_type": "standard",
                "quantity_original": 9,
            },
            {
                "date": "2026-03-24",
                "daypart": "昼",
                "menu_name": "ホイコーロー",
                "diet_type": "staff",
                "area_id": "X",
                "bag_type": "standard",
                "quantity_original": 2,
            },
        ],
    )
    order_b = order_service.create_order_from_ingest(
        IngestEmailPayload(
            message_id=f"msg-totals-order-refs-b-{uuid4().hex[:8]}",
            pdf_uri="file://dummy-totals-order-refs-b.pdf",
            received_at=datetime(2026, 3, 24, 9, 0, 0),
            facility_hint="FAC00005",
            week_hint="2026-03",
        ),
        lines=[
            {
                "date": "2026-03-24",
                "daypart": "昼",
                "menu_name": "ホイコーロー",
                "diet_type": "daycare",
                "area_id": "2F",
                "bag_type": "standard",
                "quantity_original": 4,
            },
            {
                "date": "2026-03-24",
                "daypart": "昼",
                "menu_name": "ホイコーロー",
                "diet_type": "regular_bag",
                "area_id": "2F",
                "bag_type": "standard",
                "quantity_original": 1,
            },
        ],
    )
    order_service.set_status(order_a["id"], "確定")
    order_service.set_status(order_b["id"], "確定")

    client = TestClient(app)
    res = client.get("/totals?date=2026-03-24&include_order_refs=true")

    assert res.status_code == 200
    rows = [item for item in res.json()["rows"] if item.get("menu_name") == "ホイコーロー"]
    regular = next(item for item in rows if item.get("diet_type") == "regular")
    assert regular["quantity"] == 16.0
    refs = regular["order_refs"]
    actual = [
        {
            "order_id": item["order_id"],
            "facility_id": item["facility_id"],
            "source_diet_type": item["source_diet_type"],
            "aggregated_diet_type": item["aggregated_diet_type"],
            "area_id": item["area_id"],
            "quantity": item["quantity"],
        }
        for item in refs
    ]
    assert actual == [
        {
            "order_id": order_a["id"],
            "facility_id": "FAC00004",
            "source_diet_type": "regular",
            "aggregated_diet_type": "regular",
            "area_id": "X",
            "quantity": 9.0,
        },
        {
            "order_id": order_a["id"],
            "facility_id": "FAC00004",
            "source_diet_type": "staff",
            "aggregated_diet_type": "regular",
            "area_id": "X",
            "quantity": 2.0,
        },
        {
            "order_id": order_b["id"],
            "facility_id": "FAC00005",
            "source_diet_type": "daycare",
            "aggregated_diet_type": "regular",
            "area_id": "2F",
            "quantity": 4.0,
        },
        {
            "order_id": order_b["id"],
            "facility_id": "FAC00005",
            "source_diet_type": "regular_bag",
            "aggregated_diet_type": "regular",
            "area_id": "2F",
            "quantity": 1.0,
        },
    ]
    assert all("facility_name" in item for item in refs)


def test_totals_splits_main_garnish_menu_and_keeps_facility_refs():
    order_service.clear_all()
    month_id = "2026-03"
    menu_csv = "menu\n鶏唐揚げ 添)ブロッコリー\n".encode("utf-8")
    menu_service.create_menu(month_id, menu_csv, "menu.csv")
    menu_item = menu_service.create_item_stub(month_id, "鶏唐揚げ 添)ブロッコリー")
    menu_service.update_item(
        month_id,
        menu_item["id"],
        {"qty_per_serving": 2, "unit_type": "個", "daypart": "夕食", "category": "主菜"},
    )
    order = order_service.create_order_from_ingest(
        IngestEmailPayload(
            message_id=f"msg-totals-garnish-{uuid4().hex[:8]}",
            pdf_uri="file://dummy-totals-garnish.pdf",
            received_at=datetime(2026, 3, 24, 9, 0, 0),
            facility_hint="FAC00001",
            week_hint=month_id,
        ),
        lines=[
            {
                "date": "2026-03-24",
                "daypart": "夕",
                "menu_name": "鶏唐揚げ 添)ブロッコリー",
                "diet_type": "regular",
                "area_id": "X",
                "bag_type": "standard",
                "quantity_original": 31,
            }
        ],
    )
    order_service.set_status(order["id"], "確定")

    client = TestClient(app)
    res = client.get("/totals?date=2026-03-24&include_order_refs=true")

    assert res.status_code == 200
    rows = res.json()["rows"]
    assert not any(item.get("menu_name") == "鶏唐揚げ 添)ブロッコリー" for item in rows)
    main = next(item for item in rows if item.get("menu_name") == "鶏唐揚げ")
    garnish = next(item for item in rows if item.get("menu_name") == "ブロッコリー")
    assert main["menu_category"] == "主菜"
    assert main["quantity"] == 31.0
    assert garnish["menu_category"] == "添え"
    assert garnish["quantity"] == 31.0
    assert [
        (item["facility_id"], item["source_diet_type"], item["quantity"])
        for item in garnish["order_refs"]
    ] == [("FAC00001", "regular", 31.0)]


def test_totals_reflects_confirmed_lines_rebuilt_from_latest_draft():
    order_service.clear_all()
    _reset_facilities_from_master()
    month_id = f"2026-03-totals-confirm-draft-{uuid4().hex[:8]}"
    menu_csv = "menu\nホイコーロー\n".encode("utf-8")
    menu_service.create_menu(month_id, menu_csv, "menu.csv")
    menu_item = menu_service.create_item_stub(month_id, "ホイコーロー")
    menu_service.update_item(
        month_id,
        menu_item["id"],
        {"qty_per_serving": 100, "unit_type": "g", "daypart": "昼食", "category": "主菜"},
    )
    order = order_service.create_order_from_ingest(
        IngestEmailPayload(
            message_id=f"msg-totals-confirm-draft-{uuid4().hex[:8]}",
            pdf_uri="file://dummy-totals-confirm-draft.pdf",
            received_at=datetime(2026, 3, 24, 9, 0, 0),
            facility_hint="FAC00014",
            week_hint="2026-03",
        ),
        lines=[
            {
                "date": "2026-03-24",
                "daypart": "昼",
                "menu_name": "ホイコーロー",
                "diet_type": "regular",
                "area_id": "X",
                "bag_type": "standard",
                "quantity_original": 2,
            }
        ],
    )
    saved = order_service.persist_sheet_draft(
        order_id=order["id"],
        draft_sheet_json={
            "order_id": order["id"],
            "source": "draft_sheet",
            "fields": [
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
            ],
            "header": ["日付", "区分", "メニュー", "常食", "職員", "肉禁", "魚禁", "ゴマアレルギー", "変更1", "備考欄"],
            "rows": [["03/24", "昼", "ホイコーロー", "102", "2", "2", "1", "", "", ""]],
            "row_ids": ["draft-row-1"],
        },
        draft_state="draft_ready",
        blockers=[],
        warnings=[],
    )
    assert saved is not None

    confirmed = order_service.confirm_order(order["id"])
    assert confirmed is not None

    client = TestClient(app)
    res = client.get("/totals?date=2026-03-24&include_order_refs=true")

    assert res.status_code == 200
    rows = [item for item in res.json()["rows"] if item.get("menu_name") == "ホイコーロー"]
    regular = next(item for item in rows if item.get("diet_type") == "regular")
    forbidden = next(item for item in rows if item.get("diet_type") == "forbidden")
    assert regular["quantity"] == 104.0
    assert forbidden["quantity"] == 3.0
    assert {
        (ref["source_diet_type"], ref["aggregated_diet_type"], ref["quantity"])
        for ref in regular["order_refs"]
    } == {
        ("regular", "regular", 102.0),
        ("staff", "regular", 2.0),
    }


def test_totals_merges_forbidden_diets():
    order_service.clear_all()
    month_id = f"2026-03-totals-forbidden-bucket-{uuid4().hex[:8]}"
    menu_csv = "menu\n豆腐の煮物\n".encode("utf-8")
    menu_service.create_menu(month_id, menu_csv, "menu.csv")
    menu_item = menu_service.create_item_stub(month_id, "豆腐の煮物")
    menu_service.update_item(
        month_id,
        menu_item["id"],
        {"qty_per_serving": 40, "unit_type": "g", "daypart": "昼食", "category": "副菜"},
    )
    order = order_service.create_order_from_ingest(
        IngestEmailPayload(
            message_id="msg-totals-forbidden-bucket-001",
            pdf_uri="file://dummy-totals-forbidden-bucket.pdf",
            received_at=datetime(2026, 3, 24, 9, 0, 0),
            facility_hint="FAC00014",
            week_hint="2026-03",
        ),
        lines=[
            {
                "date": "2026-03-24",
                "daypart": "昼",
                "menu_name": "豆腐の煮物",
                "diet_type": "no_meat",
                "area_id": "X",
                "bag_type": "standard",
                "quantity_original": 2,
            },
            {
                "date": "2026-03-24",
                "daypart": "昼",
                "menu_name": "豆腐の煮物",
                "diet_type": "no_fish",
                "area_id": "X",
                "bag_type": "standard",
                "quantity_original": 1,
            },
            {
                "date": "2026-03-24",
                "daypart": "昼",
                "menu_name": "豆腐の煮物",
                "diet_type": "forbidden_other",
                "area_id": "X",
                "bag_type": "standard",
                "quantity_original": 3,
            },
            {
                "date": "2026-03-24",
                "daypart": "昼",
                "menu_name": "豆腐の煮物",
                "diet_type": "no_fried",
                "area_id": "X",
                "bag_type": "standard",
                "quantity_original": 4,
            },
        ],
    )
    order_service.set_status(order["id"], "確定")

    client = TestClient(app)
    res = client.get("/totals?date=2026-03-24")

    assert res.status_code == 200
    rows = [item for item in res.json()["rows"] if item.get("menu_name") == "豆腐の煮物"]
    forbidden = next(item for item in rows if item.get("diet_type") == "forbidden")
    assert forbidden["quantity"] == 10.0
    assert not any(item.get("diet_type") in {"no_meat", "no_fish", "forbidden_other", "no_fried"} for item in rows)


def test_totals_reflects_confirmed_semantic_sheet_materialization_for_multi_column_facility():
    order_service.clear_all()
    _reset_facilities_from_master()
    month_id = f"2026-03-totals-confirm-materialization-{uuid4().hex[:8]}"
    menu_csv = "menu\nホイコーロー\n".encode("utf-8")
    menu_service.create_menu(month_id, menu_csv, "menu.csv")
    menu_item = menu_service.create_item_stub(month_id, "ホイコーロー")
    menu_service.update_item(
        month_id,
        menu_item["id"],
        {"qty_per_serving": 100, "unit_type": "g", "daypart": "昼食", "category": "主菜"},
    )
    order = order_service.create_order_from_ingest(
        IngestEmailPayload(
            message_id=f"msg-totals-confirm-materialization-{uuid4().hex[:8]}",
            pdf_uri="file://dummy-totals-confirm-materialization.pdf",
            received_at=datetime(2026, 3, 24, 9, 0, 0),
            facility_hint="FAC00014",
            week_hint="2026-03",
        ),
        lines=[
            {
                "date": "2026-03-24",
                "daypart": "昼",
                "menu_name": "ホイコーロー",
                "diet_type": "regular",
                "area_id": "X",
                "bag_type": "standard",
                "quantity_original": 1,
            }
        ],
    )

    saved, error = order_service.save_ocr_sheet_exact(
        order["id"],
        header=["日付", "区分", "メニュー", "常食", "職員", "肉禁", "魚禁", "ゴマアレルギー", "変更1", "備考欄"],
        rows=[["03/24", "昼", "ホイコーロー", "102", "2", "2", "1", "", "", ""]],
        fields=[
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
        ],
        row_ids=["draft-row-1"],
        ui_mode="sheet",
    )

    assert error is None
    assert saved is not None
    confirmed = order_service.confirm_order(order["id"])
    assert confirmed is not None

    client = TestClient(app)
    res = client.get("/totals?date=2026-03-24&include_order_refs=true")

    assert res.status_code == 200
    rows = [item for item in res.json()["rows"] if item.get("menu_name") == "ホイコーロー"]
    regular = next(item for item in rows if item.get("diet_type") == "regular")
    forbidden = next(item for item in rows if item.get("diet_type") == "forbidden")
    assert regular["quantity"] == 104.0
    assert forbidden["quantity"] == 3.0
    assert [
        (item["source_diet_type"], item["aggregated_diet_type"], item["quantity"])
        for item in regular["order_refs"]
    ] == [
        ("regular", "regular", 102.0),
        ("staff", "regular", 2.0),
    ]


def test_totals_reflect_confirmed_lines_materialized_from_semantic_draft_sheet():
    order_service.clear_all()
    _reset_facilities_from_master()
    month_id = f"2026-03-totals-confirm-materialization-{uuid4().hex[:8]}"
    menu_csv = "menu\nホイコーロー\n".encode("utf-8")
    menu_service.create_menu(month_id, menu_csv, "menu.csv")
    menu_item = menu_service.create_item_stub(month_id, "ホイコーロー")
    menu_service.update_item(
        month_id,
        menu_item["id"],
        {"qty_per_serving": 100, "unit_type": "g", "daypart": "昼食", "category": "主菜"},
    )

    order_a = order_service.create_order_from_ingest(
        IngestEmailPayload(
            message_id=f"msg-totals-confirm-materialization-a-{uuid4().hex[:8]}",
            pdf_uri="file://dummy-totals-confirm-materialization-a.pdf",
            received_at=datetime(2026, 3, 24, 9, 0, 0),
            facility_hint="FAC00004",
            week_hint="2026-03",
        ),
        lines=[],
    )
    saved_a, error_a = order_service.save_ocr_sheet_exact(
        order_a["id"],
        header=["日付", "区分", "メニュー", "常食", "通所", "職員", "肉禁", "魚禁", "揚げ物禁", "変更1", "備考欄"],
        rows=[["03/24", "昼", "ホイコーロー", "65", "45", "", "2", "", "", "", ""]],
        fields=[
            "date_mmdd",
            "daypart",
            "menu",
            "qty.regular_x",
            "qty.daycare_x",
            "qty.staff_x",
            "qty.no_meat_x",
            "qty.no_fish_x",
            "qty.no_fried_x",
            "qty.change_1_x",
            "remarks",
        ],
        row_ids=["draft-row-a"],
        ui_mode="sheet",
    )
    assert error_a is None
    assert saved_a is not None
    assert order_service.confirm_order(order_a["id"]) is not None

    order_b = order_service.create_order_from_ingest(
        IngestEmailPayload(
            message_id=f"msg-totals-confirm-materialization-b-{uuid4().hex[:8]}",
            pdf_uri="file://dummy-totals-confirm-materialization-b.pdf",
            received_at=datetime(2026, 3, 24, 9, 0, 0),
            facility_hint="FAC00014",
            week_hint="2026-03",
        ),
        lines=[],
    )
    saved_b, error_b = order_service.save_ocr_sheet_exact(
        order_b["id"],
        header=["日付", "区分", "メニュー", "常食", "職員", "肉禁", "魚禁", "ゴマアレルギー", "変更1", "備考欄"],
        rows=[["03/24", "昼", "ホイコーロー", "102", "2", "2", "1", "", "", ""]],
        fields=[
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
        ],
        row_ids=["draft-row-b"],
        ui_mode="sheet",
    )
    assert error_b is None
    assert saved_b is not None
    assert order_service.confirm_order(order_b["id"]) is not None

    client = TestClient(app)
    res = client.get("/totals?date=2026-03-24&include_order_refs=true")

    assert res.status_code == 200
    rows = [item for item in res.json()["rows"] if item.get("menu_name") == "ホイコーロー"]
    regular = next(item for item in rows if item.get("diet_type") == "regular")
    assert regular["quantity"] == 214.0
    assert sorted(
        [
            {
                "order_id": item["order_id"],
                "facility_id": item["facility_id"],
                "source_diet_type": item["source_diet_type"],
                "aggregated_diet_type": item["aggregated_diet_type"],
                "quantity": item["quantity"],
            }
            for item in regular["order_refs"]
        ],
        key=lambda item: (item["order_id"], item["source_diet_type"], item["quantity"]),
    ) == sorted(
        [
            {
                "order_id": order_a["id"],
                "facility_id": "FAC00004",
                "source_diet_type": "regular",
                "aggregated_diet_type": "regular",
                "quantity": 65.0,
            },
            {
                "order_id": order_a["id"],
                "facility_id": "FAC00004",
                "source_diet_type": "daycare",
                "aggregated_diet_type": "regular",
                "quantity": 45.0,
            },
            {
                "order_id": order_b["id"],
                "facility_id": "FAC00014",
                "source_diet_type": "regular",
                "aggregated_diet_type": "regular",
                "quantity": 102.0,
            },
            {
                "order_id": order_b["id"],
                "facility_id": "FAC00014",
                "source_diet_type": "staff",
                "aggregated_diet_type": "regular",
                "quantity": 2.0,
            },
        ],
        key=lambda item: (item["order_id"], item["source_diet_type"], item["quantity"]),
    )
