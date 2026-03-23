import sys
import pathlib
import base64
from datetime import date, datetime

from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.db import session_scope  # noqa: E402
from src.main import app  # noqa: E402
from src.models.menu import MonthlyMenu, MonthlyMenuEntry  # noqa: E402
from src.services import config_service  # noqa: E402
from src.services import facility_service  # noqa: E402
from src.services import order_service  # noqa: E402
from src.workers.ingest_mail_adapter import IngestEmailPayload  # noqa: E402


def _basic_header(username: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    return {"Authorization": f"Basic {token}"}


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


def _seed_monthly_menu_daypart_order_2099_11() -> None:
    with session_scope() as session:
        session.query(MonthlyMenuEntry).filter(MonthlyMenuEntry.monthly_menu_id == "2099-11").delete()
        session.query(MonthlyMenu).filter(MonthlyMenu.id == "2099-11").delete()
        session.add(
            MonthlyMenu(
                id="2099-11",
                month_start=date(2099, 11, 1),
                filename="seed-2099-11.xlsx",
            )
        )
        session.add_all(
            [
                MonthlyMenuEntry(
                    id="seed-entry-2099-11-15-breakfast-contract",
                    monthly_menu_id="2099-11",
                    menu_date=date(2099, 11, 15),
                    daypart="朝食",
                    name="朝メニュー",
                    slot_index=0,
                ),
                MonthlyMenuEntry(
                    id="seed-entry-2099-11-15-lunch-contract",
                    monthly_menu_id="2099-11",
                    menu_date=date(2099, 11, 15),
                    daypart="昼食",
                    name="昼メニュー",
                    slot_index=0,
                ),
                MonthlyMenuEntry(
                    id="seed-entry-2099-11-15-dinner-contract",
                    monthly_menu_id="2099-11",
                    menu_date=date(2099, 11, 15),
                    daypart="夕食",
                    name="夕メニュー",
                    slot_index=0,
                ),
            ]
        )


def _clear_monthly_menu(month_id: str) -> None:
    with session_scope() as session:
        session.query(MonthlyMenuEntry).filter(MonthlyMenuEntry.monthly_menu_id == month_id).delete()
        session.query(MonthlyMenu).filter(MonthlyMenu.id == month_id).delete()


def _create_seed_order(message_id: str) -> dict:
    _seed_monthly_menu_2026_01()
    payload = IngestEmailPayload(
        message_id=message_id,
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2026, 1, 8, 9, 0, 0),
        facility_hint="FAC00001",
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
        }
    ]
    return order_service.create_order_from_ingest(payload, lines=lines)


def _create_weekly_menu_seed_order_2099_11(message_id: str) -> dict:
    _seed_monthly_menu_daypart_order_2099_11()
    payload = IngestEmailPayload(
        message_id=message_id,
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2099, 11, 15, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2099-11",
    )
    lines = [
        {
            "line_id": "line-contract-lunch-1",
            "date": "2099-11-15",
            "daypart": "昼",
            "menu_name": "昼メニュー",
            "diet_type": "regular",
            "area_id": "2F",
            "bag_type": "standard",
            "quantity_original": 6,
        }
    ]
    return order_service.create_order_from_ingest(payload, lines=lines)


def _create_seed_order_without_facility(message_id: str) -> dict:
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


def test_orders_ocr_sheet_and_history_api_flow():
    order_service.clear_all()
    client = TestClient(app)
    order = _create_seed_order("msg-api-sheet-001")

    sheet_res = client.get(f"/orders/{order['id']}/ocr-sheet")
    assert sheet_res.status_code == 200
    sheet = sheet_res.json()
    assert sheet["order_id"] == order["id"]
    assert isinstance(sheet.get("fields"), list)
    assert isinstance(sheet.get("rows"), list)
    assert sheet.get("legacy_available") is True

    history_before = client.get(f"/orders/{order['id']}/ocr-history")
    assert history_before.status_code == 200
    history_before_json = history_before.json()
    assert history_before_json.get("latest") is None
    assert history_before_json.get("revisions") == []

    apply_payload = {
        "header": ["日付", "区分", "メニュー", "常食2F", "備考"],
        "rows": [["01/08", "昼", "Menu A", "3", "api"]],
        "ui_mode": "sheet",
        "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f", "remarks"],
        "row_ids": ["row-api-1"],
    }
    apply_res = client.post(f"/orders/{order['id']}/ocr-apply", json=apply_payload)
    assert apply_res.status_code == 200

    history_after = client.get(f"/orders/{order['id']}/ocr-history")
    assert history_after.status_code == 200
    history_after_json = history_after.json()
    latest = history_after_json.get("latest")
    assert isinstance(latest, dict)
    assert latest.get("ui_mode") == "sheet"
    assert latest.get("row_ids") == ["row-api-1"]
    assert len(history_after_json.get("revisions") or []) >= 1


def test_orders_ocr_sheet_save_api_flow():
    order_service.clear_all()
    client = TestClient(app)
    order = _create_seed_order("msg-api-sheet-save-001")

    save_payload = {
        "header": ["日付", "区分", "メニュー", "常食2F", "備考"],
        "rows": [["01/08", "昼", "Menu A", "8", "exact-save"]],
        "ui_mode": "sheet",
        "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f", "remarks"],
        "row_ids": ["row-api-save-1"],
    }
    save_res = client.post(f"/orders/{order['id']}/ocr-sheet-save", json=save_payload)
    assert save_res.status_code == 200
    payload = save_res.json()
    revision = payload.get("revision")
    assert isinstance(revision, dict)
    assert revision.get("sheet_save_only") is True
    assert revision.get("sheet_save_mode") == "exact"
    assert revision.get("row_ids") == ["row-api-save-1"]
    assert revision.get("rows") == [["01/08", "昼", "Menu A", "8", "exact-save"]]

    history_res = client.get(f"/orders/{order['id']}/ocr-history")
    assert history_res.status_code == 200
    history_payload = history_res.json()
    latest = history_payload.get("latest")
    assert isinstance(latest, dict)
    assert latest.get("sheet_save_only") is True
    assert latest.get("rows") == [["01/08", "昼", "Menu A", "8", "exact-save"]]


def test_orders_ocr_history_falls_back_to_latest_evidence_run_when_revision_history_empty():
    order_service.clear_all()
    client = TestClient(app)
    order = _create_seed_order("msg-api-history-evidence-001")
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "status": "done",
            "pages": [
                {
                    "page_index": 1,
                    "tables": [
                        {
                            "table_id": "p1_t1",
                            "rows": [["日付", "区分", "メニュー", "常食2F"], ["01/08", "昼", "Menu A", "3"]],
                        }
                    ],
                    "ocr_overlay_uri": "gs://dummy/overlay.png",
                }
            ],
            "table_raw": "|日付|区分|メニュー|常食2F|\n|---|---|---|---|\n|01/08|昼|Menu A|3|",
            "template_resolution": {"resolved_template_id": "fax_layout_regular_soft_mixer_forbidden_v1"},
            "table_box": [0.1, 0.1, 0.9, 0.9],
            "grid_column_edges": [0.1, 0.5, 0.9],
            "grid_row_edges": [0.1, 0.5, 0.9],
        },
    )

    history_res = client.get(f"/orders/{order['id']}/ocr-history")
    assert history_res.status_code == 200
    history_payload = history_res.json()
    latest = history_payload.get("latest")
    assert isinstance(latest, dict)
    assert latest.get("ui_mode") == "evidence"
    assert str(latest.get("revision_id") or "").startswith("OEV")
    assert latest.get("sheet_save_mode") == "evidence_run"
    assert len(history_payload.get("revisions") or []) == 1
    assert isinstance(history_payload.get("raw_output"), dict)


def test_orders_week_options_and_save_api_flow():
    order_service.clear_all()
    client = TestClient(app)
    order = _create_seed_order("msg-api-week-001")

    options_res = client.get(f"/orders/{order['id']}/week-options")
    assert options_res.status_code == 200
    options = options_res.json().get("options") or []
    assert any(str(item.get("week_id") or "").startswith("2026-01@") for item in options)
    selected = next(item for item in options if str(item.get("week_id") or "").startswith("2026-01@"))
    assert str(selected.get("label") or "").startswith("2026-01 (")
    assert selected.get("date_from") == "2026-01-08"
    assert selected.get("date_to") == "2026-01-08"

    save_res = client.post(f"/orders/{order['id']}/week", json={"week": selected.get("week_id")})
    assert save_res.status_code == 200
    assert save_res.json().get("updated") is True

    order_res = client.get(f"/orders/{order['id']}")
    assert order_res.status_code == 200
    assert order_res.json().get("week") == "2026-01"
    assert order_res.json().get("week_value") == selected.get("week_id")
    assert order_res.json().get("week_label") == selected.get("label")


def test_orders_week_options_include_future_calendar_ranges_without_menu():
    order_service.clear_all()
    _clear_monthly_menu("2026-03")
    client = TestClient(app)
    payload = IngestEmailPayload(
        message_id="msg-api-week-future-001",
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2026, 3, 11, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2026-03",
    )
    order = order_service.create_order_from_ingest(
        payload,
        lines=[
            {
                "date": "2026-03-22",
                "daypart": "昼",
                "menu_name": "Future Menu",
                "diet_type": "regular",
                "area_id": "2F",
                "bag_type": "standard",
                "quantity_original": 3,
            }
        ],
    )

    res = client.get(f"/orders/{order['id']}/week-options")
    assert res.status_code == 200
    options = res.json().get("options") or []
    target = next((item for item in options if item.get("week_id") == "2026-03@2026-03-22~2026-03-28"), None)
    assert isinstance(target, dict)
    assert target.get("date_from") == "2026-03-22"
    assert target.get("date_to") == "2026-03-28"
    assert target.get("selected") is True


def test_orders_week_save_api_rejects_invalid_week():
    order_service.clear_all()
    client = TestClient(app)
    order = _create_seed_order("msg-api-week-invalid-001")

    res = client.post(f"/orders/{order['id']}/week", json={"week": "2026-01@2026-01-08~2026-02-01"})
    assert res.status_code == 400
    assert res.json().get("detail") == "week invalid"


def test_order_detail_promotes_plain_month_week_to_selected_weekly_range():
    order_service.clear_all()
    client = TestClient(app)
    order = _create_seed_order("msg-api-week-display-001")

    save_res = client.post(f"/orders/{order['id']}/week", json={"week": "2026-01"})
    assert save_res.status_code == 200

    order_res = client.get(f"/orders/{order['id']}")
    assert order_res.status_code == 200
    payload = order_res.json()
    assert payload.get("week") == "2026-01"
    assert payload.get("week_value") == "2026-01@2026-01-08~2026-01-08"
    assert payload.get("week_label") == "2026-01 (01/08-01/08)"


def test_orders_facility_template_columns_save_api_flow():
    order_service.clear_all()
    client = TestClient(app)
    order = _create_seed_order("msg-api-facility-template-001")
    previous_config = config_service.get_facility_config(order["facility"]) or {}

    try:
        columns = [
            {"index": 0, "role": "date", "header": "日付", "name": "date"},
            {
                "index": 1,
                "role": "quantity",
                "header": "常食2F",
                "name": "qty.regular_2f",
                "diet_type": "regular",
                "area_id": "2F",
            },
        ]
        save_res = client.put(f"/orders/{order['id']}/facility-template-columns", json={"columns": columns})
        assert save_res.status_code == 200
        payload = save_res.json()
        assert payload.get("updated") is True
        resolved_columns = (((payload.get("resolved_config") or {}).get("fax_template") or {}).get("columns") or [])
        quantity_column = next(item for item in resolved_columns if item.get("index") == 1)
        assert quantity_column.get("header") == "常食2F"
        assert quantity_column.get("diet_type") == "regular"
        assert quantity_column.get("area_id") == "2F"

        facility_res = client.get(f"/facilities/{order['facility']}")
        assert facility_res.status_code == 200
        facility_columns = (
            (((facility_res.json().get("resolved_config") or {}).get("fax_template") or {}).get("columns"))
            or []
        )
        assert any(
            item.get("index") == 1
            and item.get("header") == "常食2F"
            and item.get("diet_type") == "regular"
            and item.get("area_id") == "2F"
            for item in facility_columns
        )
    finally:
        assert facility_service.update_config(order["facility"], previous_config)


def test_orders_facility_template_columns_allows_operator_auth(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "false")
    monkeypatch.setenv("ADMIN_USER", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "admin-secret")
    monkeypatch.setenv("OPERATOR_USER", "operator")
    monkeypatch.setenv("OPERATOR_PASSWORD", "operator-secret")

    order_service.clear_all()
    client = TestClient(app)
    order = _create_seed_order("msg-api-facility-template-admin-001")
    columns = [
        {"index": 0, "role": "date", "header": "日付", "name": "date"},
        {
            "index": 1,
            "role": "quantity",
            "header": "常食2F",
            "name": "qty.regular_2f",
            "diet_type": "regular",
            "area_id": "2F",
        },
    ]

    operator_res = client.put(
        f"/orders/{order['id']}/facility-template-columns",
        json={"columns": columns},
        headers=_basic_header("operator", "operator-secret"),
    )
    assert operator_res.status_code == 200

    admin_res = client.put(
        f"/orders/{order['id']}/facility-template-columns",
        json={"columns": columns},
        headers=_basic_header("admin", "admin-secret"),
    )
    assert admin_res.status_code == 200


def test_orders_facility_template_columns_save_updates_ocr_sheet_header():
    order_service.clear_all()
    client = TestClient(app)
    order = _create_seed_order("msg-api-facility-template-sheet-001")
    previous_config = config_service.get_facility_config(order["facility"]) or {}

    try:
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

        save_res = client.put(f"/orders/{order['id']}/facility-template-columns", json={"columns": columns})
        assert save_res.status_code == 200

        sheet_res = client.get(f"/orders/{order['id']}/ocr-sheet")
        assert sheet_res.status_code == 200
        sheet = sheet_res.json()
        field_idx = sheet["fields"].index("qty.regular_2f")
        assert sheet["header"][field_idx] == "新常食2F"
    finally:
        assert facility_service.update_config(order["facility"], previous_config)


def test_orders_facility_template_columns_save_canonicalizes_invalid_quantity_tokens():
    order_service.clear_all()
    client = TestClient(app)
    order = _create_seed_order("msg-api-facility-template-canonical-001")
    previous_config = config_service.get_facility_config(order["facility"]) or {}

    try:
        columns = [
            {"index": 0, "role": "date", "header": "日付"},
            {"index": 1, "role": "daypart", "header": "区分"},
            {"index": 2, "role": "menu_name", "header": "メニュー"},
            {"index": 3, "role": "quantity", "header": "常食", "diet_type": "regular", "area_id": "regular"},
            {"index": 4, "role": "quantity", "header": "None", "diet_type": "regular", "area_id": "none"},
            {"index": 5, "role": "quantity", "header": "肉禁", "diet_type": "reglur", "area_id": "niku-kin"},
            {"index": 6, "role": "quantity", "header": "魚禁", "diet_type": "reglur", "area_id": "sakana-kin"},
            {"index": 7, "role": "quantity", "header": "変更1", "diet_type": "regular", "area_id": "henkou1"},
            {"index": 8, "role": "quantity", "header": "変更2", "diet_type": "regular", "area_id": "henkou2"},
            {"index": 9, "role": "note", "header": "備考"},
        ]

        save_res = client.put(f"/orders/{order['id']}/facility-template-columns", json={"columns": columns})
        assert save_res.status_code == 200

        sheet_res = client.get(f"/orders/{order['id']}/ocr-sheet")
        assert sheet_res.status_code == 200
        sheet = sheet_res.json()
        assert sheet["header"][3:9] == ["常食", "None", "肉禁", "魚禁", "変更1", "変更2"]
        assert sheet["fields"][3:9] == [
            "qty.regular_x",
            "qty.unknown_x",
            "qty.no_meat_x",
            "qty.no_fish_x",
            "qty.change_1_x",
            "qty.change_2_x",
        ]
    finally:
        assert facility_service.update_config(order["facility"], previous_config)


def test_orders_activity_history_api_flow():
    order_service.clear_all()
    client = TestClient(app)
    order = _create_seed_order("msg-api-history-001")

    update_payload = {
        "lines": [
            {
                "line_id": "line-1",
                "date": "2026-01-08",
                "daypart": "昼",
                "menu_name": "Menu A",
                "diet_type": "regular",
                "area_id": "2F",
                "bag_type": "standard",
                "quantity_original": 5,
            }
        ]
    }
    update_res = client.put(f"/orders/{order['id']}/lines", json=update_payload)
    assert update_res.status_code == 200

    history_res = client.get(f"/orders/{order['id']}/history")
    assert history_res.status_code == 200
    payload = history_res.json()
    assert payload.get("order_id") == order["id"]
    items = payload.get("items") or []
    assert isinstance(items, list)
    assert any(item.get("action") == "order_lines_update" for item in items)


def test_orders_ocr_sheet_and_apply_api_without_facility():
    order_service.clear_all()
    client = TestClient(app)
    order = _create_seed_order_without_facility("msg-api-sheet-no-fac-001")

    sheet_res = client.get(f"/orders/{order['id']}/ocr-sheet")
    assert sheet_res.status_code == 400
    assert sheet_res.json().get("detail") == "facility_missing"

    apply_payload = {
        "header": ["日付", "区分", "メニュー", "常食2F", "備考"],
        "rows": [["02/15", "朝", "Menu B", "6", "api"]],
        "ui_mode": "sheet",
        "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f", "remarks"],
        "row_ids": ["row-api-no-fac-1"],
    }
    apply_res = client.post(f"/orders/{order['id']}/ocr-apply", json=apply_payload)
    assert apply_res.status_code == 400
    assert apply_res.json().get("detail") == "facility missing"


def test_orders_ocr_review_api_passes_provider_and_prompt(monkeypatch):
    order_service.clear_all()
    client = TestClient(app)
    order = _create_seed_order("msg-api-review-001")
    captured: dict[str, str | None] = {}

    def _fake_review(
        order_id: str,
        *,
        provider: str | None = None,
        prompt: str | None = None,
        pdf_variant: str | None = None,
    ):
        captured["order_id"] = order_id
        captured["provider"] = provider
        captured["prompt"] = prompt
        captured["pdf_variant"] = pdf_variant
        return {
            "id": order_id,
            "llm_review": {
                "provider": provider,
                "needs_more_review": True,
                "issues": [
                    {
                        "row_id": "row-1",
                        "field": "qty.regular_2f",
                        "issue_code": "review_required",
                    }
                ],
            },
        }, None

    monkeypatch.setattr(order_service, "review_ocr_table_with_llm", _fake_review)

    review_res = client.post(
        f"/orders/{order['id']}/ocr-review",
        json={
            "ocr_provider": "gemini",
            "ocr_prompt": "verify baseline against pdf",
            "pdf_variant": "corrected",
        },
    )

    assert review_res.status_code == 200
    assert captured == {
        "order_id": order["id"],
        "provider": "gemini",
        "prompt": "verify baseline against pdf",
        "pdf_variant": "corrected",
    }
    payload = review_res.json()
    assert payload["id"] == order["id"]
    assert payload["llm_review"]["provider"] == "gemini"
    assert payload["llm_review"]["needs_more_review"] is True

def test_orders_ocr_review_api_maps_errors(monkeypatch):
    client = TestClient(app)

    monkeypatch.setattr(
        order_service,
        "review_ocr_table_with_llm",
        lambda _order_id, *, provider=None, prompt=None, pdf_variant=None: (None, "order_not_found"),
    )
    not_found = client.post("/orders/ORDmissing/ocr-review", json={})
    assert not_found.status_code == 404
    assert not_found.json().get("detail") == "order not found"

    monkeypatch.setattr(
        order_service,
        "review_ocr_table_with_llm",
        lambda _order_id, *, provider=None, prompt=None, pdf_variant=None: (None, "ocr_payload_missing"),
    )
    bad_request = client.post("/orders/ORDdummy/ocr-review", json={})
    assert bad_request.status_code == 400
    assert bad_request.json().get("detail") == "ocr_payload_missing"

    invalid_provider = client.post("/orders/ORDdummy/ocr-review", json={"ocr_provider": "pipeline"})
    assert invalid_provider.status_code == 400
    assert invalid_provider.json().get("detail") == "ocr_provider must be one of openai|gemini"

    invalid_pdf_variant = client.post("/orders/ORDdummy/ocr-review", json={"pdf_variant": "auto"})
    assert invalid_pdf_variant.status_code == 400
    assert invalid_pdf_variant.json().get("detail") == "pdf_variant must be one of raw|corrected"


def test_orders_apply_patch_candidate_api_flow(monkeypatch):
    client = TestClient(app)
    captured: dict[str, str | None] = {}

    def _fake_apply(order_id: str, *, candidate_id: str | None = None, applied_by: str | None = None):
        captured["order_id"] = order_id
        captured["candidate_id"] = candidate_id
        captured["applied_by"] = applied_by
        return ({
            "candidate": {"id": "OPCtest", "candidate_state": "applied"},
            "draft": {"id": "ODRtest", "latest_patch_candidate_id": "OPCtest"},
        }, None)

    monkeypatch.setattr(order_service, "apply_patch_candidate_to_draft", _fake_apply)

    res = client.post(
        "/orders/ORDpatch/draft-sheet/apply-patch-candidate",
        json={"candidate_id": "OPCtest"},
    )

    assert res.status_code == 200
    assert captured == {
        "order_id": "ORDpatch",
        "candidate_id": "OPCtest",
        "applied_by": "operator",
    }
    payload = res.json()
    assert payload["candidate"]["id"] == "OPCtest"
    assert payload["candidate"]["candidate_state"] == "applied"
    assert payload["draft"]["latest_patch_candidate_id"] == "OPCtest"


def test_orders_apply_patch_candidate_api_returns_not_found(monkeypatch):
    client = TestClient(app)

    monkeypatch.setattr(
        order_service,
        "apply_patch_candidate_to_draft",
        lambda order_id, *, candidate_id=None, applied_by=None: (None, "patch_candidate_not_found"),
    )

    res = client.post("/orders/ORDmissing/draft-sheet/apply-patch-candidate", json={"candidate_id": "OPCmissing"})

    assert res.status_code == 404
    assert res.json().get("detail") == "patch candidate not found"


def test_orders_ocr_sheet_api_returns_template_validation_error():
    order_service.clear_all()
    client = TestClient(app)
    order = _create_seed_order("msg-api-sheet-invalid-template-001")
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
        sheet_res = client.get(f"/orders/{order['id']}/ocr-sheet")
        assert sheet_res.status_code == 400
        assert sheet_res.json().get("detail") == "sheet_template_field_invalid"
    finally:
        config_service.get_facility_config = original_get


def test_orders_ocr_sheet_api_stays_evidence_only_even_when_lines_change():
    order_service.clear_all()
    client = TestClient(app)
    order = _create_weekly_menu_seed_order_2099_11("msg-api-sheet-priority-001")
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "table_rows": [
                ["11/15", "昼", "OCRノイズ", "99", "", "", "", "", "", ""],
            ]
        },
    )

    first_sheet_res = client.get(f"/orders/{order['id']}/ocr-sheet")
    assert first_sheet_res.status_code == 200
    first_sheet = first_sheet_res.json()
    assert str(first_sheet.get("source") or "").startswith("weekly_menu")
    fields = first_sheet.get("fields") or []
    qty_idx = next(
        idx
        for idx, field in enumerate(fields)
        if field in {"qty.regular_2f", "qty.regular_x"}
    )
    daypart_idx = fields.index("daypart")
    menu_idx = fields.index("menu")
    date_idx = next((idx for idx, field in enumerate(fields) if field.startswith("date")), 0)
    first_lunch = next(
        row
        for row in (first_sheet.get("rows") or [])
        if row[date_idx] == "11/15" and row[daypart_idx] == "昼" and row[menu_idx] == "昼メニュー"
    )
    assert first_lunch[qty_idx] == ""

    update_payload = {
        "lines": [
            {
                "line_id": "line-contract-lunch-updated",
                "date": "2099-11-15",
                "daypart": "昼",
                "menu_name": "昼メニュー",
                "diet_type": "regular",
                "area_id": "2F",
                "bag_type": "standard",
                "quantity_original": 20,
            }
        ]
    }
    update_res = client.put(f"/orders/{order['id']}/lines", json=update_payload)
    assert update_res.status_code == 200
    assert update_res.json().get("updated") is True

    second_sheet_res = client.get(f"/orders/{order['id']}/ocr-sheet")
    assert second_sheet_res.status_code == 200
    second_sheet = second_sheet_res.json()
    assert str(second_sheet.get("source") or "").startswith("weekly_menu")
    second_lunch = next(
        row
        for row in (second_sheet.get("rows") or [])
        if row[date_idx] == "11/15" and row[daypart_idx] == "昼" and row[menu_idx] == "昼メニュー"
    )
    assert second_lunch[qty_idx] == ""


def test_orders_ocr_sheet_api_returns_sheet_with_warning_when_quantity_mapping_unmapped():
    order_service.clear_all()
    client = TestClient(app)
    _seed_monthly_menu_daypart_order_2099_11()
    payload = IngestEmailPayload(
        message_id="msg-api-sheet-unmapped-warning-001",
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2099, 11, 15, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2099-11",
    )
    order = order_service.create_order_from_ingest(
        payload,
        lines=[
            {
                "line_id": "line-contract-unmapped-1",
                "date": "2099-11-15",
                "daypart": "朝",
                "menu_name": "朝メニュー",
                "diet_type": "regular",
                "area_id": None,
                "bag_type": "standard",
                "quantity_original": 11,
            }
        ],
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
        sheet_res = client.get(f"/orders/{order['id']}/ocr-sheet")
        assert sheet_res.status_code == 200
        sheet = sheet_res.json()
        assert str(sheet.get("source") or "").startswith("weekly_menu")
        assert sheet.get("can_apply") is True
        assert sheet.get("apply_blockers") in ([], None)
    finally:
        config_service.get_facility_config = original_get


def test_orders_ocr_sheet_api_maps_sheet_errors_to_400(monkeypatch):
    client = TestClient(app)
    monkeypatch.setattr(order_service, "get_ocr_sheet", lambda _order_id: (None, "sheet_week_dates_incomplete"))
    res_week = client.get("/orders/ORDdummy/ocr-sheet")
    assert res_week.status_code == 400
    assert res_week.json().get("detail") == "sheet_week_dates_incomplete"

    monkeypatch.setattr(order_service, "get_ocr_sheet", lambda _order_id: (None, "sheet_quantity_column_unmapped"))
    res_qty = client.get("/orders/ORDdummy/ocr-sheet")
    assert res_qty.status_code == 400
    assert res_qty.json().get("detail") == "sheet_quantity_column_unmapped"

    monkeypatch.setattr(order_service, "get_ocr_sheet", lambda _order_id: (None, "sheet_canonical_mismatch"))
    res_canonical = client.get("/orders/ORDdummy/ocr-sheet")
    assert res_canonical.status_code == 400
    assert res_canonical.json().get("detail") == "sheet_canonical_mismatch"

    monkeypatch.setattr(order_service, "get_ocr_sheet", lambda _order_id: (None, "sheet_suspicious_blank_row"))
    res_blank = client.get("/orders/ORDdummy/ocr-sheet")
    assert res_blank.status_code == 400
    assert res_blank.json().get("detail") == "sheet_suspicious_blank_row"


def test_orders_ocr_sheet_api_includes_trace():
    order_service.clear_all()
    client = TestClient(app)
    order = _create_weekly_menu_seed_order_2099_11("msg-api-sheet-trace-001")
    sheet_res = client.get(f"/orders/{order['id']}/ocr-sheet")
    assert sheet_res.status_code == 200
    sheet = sheet_res.json()
    trace = sheet.get("trace")
    assert isinstance(trace, dict)
    trace_rows = trace.get("rows")
    assert isinstance(trace_rows, list)
    assert len(trace_rows) == len(sheet.get("rows") or [])
