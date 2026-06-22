import base64
import importlib
import pathlib
import sys
from datetime import date

from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

import src.api.auth as auth_module  # noqa: E402
import src.api.auth_config as auth_config_module  # noqa: E402
import src.api.menus as menus_api  # noqa: E402
from src.db import session_scope  # noqa: E402
from src.main import app  # noqa: E402
from src.models.menu import MonthlyMenu, MonthlyMenuEntry  # noqa: E402


def _basic_header(username: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    return {"Authorization": f"Basic {token}"}


def test_monthly_menus_get_requires_operator_and_allows_operator(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "false")
    monkeypatch.setenv("OPERATOR_USER", "operator")
    monkeypatch.setenv("OPERATOR_PASSWORD", "secret")
    importlib.reload(auth_module)
    importlib.reload(auth_config_module)

    monkeypatch.setattr(
        menus_api.menu_service,
        "get_menu",
        lambda month_id: {"menu": {"id": month_id}, "items": [], "entries": []},
    )

    client = TestClient(app)
    unauthorized = client.get("/monthly-menus/2026-03")
    assert unauthorized.status_code == 401

    authorized = client.get(
        "/monthly-menus/2026-03",
        headers=_basic_header("operator", "secret"),
    )
    assert authorized.status_code == 200
    assert authorized.json()["menu"]["id"] == "2026-03"


def test_monthly_menus_get_uses_facility_specific_lookup_when_query_is_present(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "false")
    monkeypatch.setenv("OPERATOR_USER", "operator")
    monkeypatch.setenv("OPERATOR_PASSWORD", "secret")
    importlib.reload(auth_module)
    importlib.reload(auth_config_module)

    captured: dict[str, str | None] = {}

    def _get_menu_for_facility(month_id: str, facility_id: str | None):
        captured["month_id"] = month_id
        captured["facility_id"] = facility_id
        return {"menu": {"id": "2026-03"}, "items": [], "entries": []}

    monkeypatch.setattr(menus_api.menu_service, "get_menu_for_facility", _get_menu_for_facility)

    client = TestClient(app)
    authorized = client.get(
        "/monthly-menus/2026-04?facility_id=FAC00005",
        headers=_basic_header("operator", "secret"),
    )
    assert authorized.status_code == 200
    assert captured == {"month_id": "2026-04", "facility_id": "FAC00005"}


def test_monthly_menus_latest_requires_operator_and_allows_operator(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "false")
    monkeypatch.setenv("OPERATOR_USER", "operator")
    monkeypatch.setenv("OPERATOR_PASSWORD", "secret")
    importlib.reload(auth_module)
    importlib.reload(auth_config_module)

    monkeypatch.setattr(
        menus_api.menu_service,
        "get_latest_menu",
        lambda: {"menu": {"id": "2026-03"}, "items": [], "entries": []},
    )

    client = TestClient(app)
    unauthorized = client.get("/monthly-menus/latest")
    assert unauthorized.status_code == 401

    authorized = client.get(
        "/monthly-menus/latest",
        headers=_basic_header("operator", "secret"),
    )
    assert authorized.status_code == 200
    assert authorized.json()["menu"]["id"] == "2026-03"


def test_monthly_menus_get_returns_synthetic_menu_when_parent_row_is_missing(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "false")
    monkeypatch.setenv("OPERATOR_USER", "operator")
    monkeypatch.setenv("OPERATOR_PASSWORD", "secret")
    importlib.reload(auth_module)
    importlib.reload(auth_config_module)

    with session_scope() as session:
        session.query(MonthlyMenuEntry).filter(MonthlyMenuEntry.monthly_menu_id == "2099-12").delete()
        session.query(MonthlyMenu).filter(MonthlyMenu.id == "2099-12").delete()
        session.add(
            MonthlyMenuEntry(
                id="menu-entry-parent-missing-1",
                monthly_menu_id="2099-12",
                menu_date=date(2099, 12, 15),
                daypart="朝",
                name="Synthetic Menu A",
                slot_index=0,
            )
        )

    client = TestClient(app)
    res = client.get(
        "/monthly-menus/2099-12",
        headers=_basic_header("operator", "secret"),
    )
    assert res.status_code == 200
    payload = res.json()
    assert payload["menu"]["id"] == "2099-12"
    assert payload["menu"]["filename"] is None
    assert payload["menu"]["display_name"] == "2099-12"
    assert payload["entries"][0]["name"] == "Synthetic Menu A"


def test_monthly_menus_get_resolves_previous_month_when_entries_cover_requested_month(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "false")
    monkeypatch.setenv("OPERATOR_USER", "operator")
    monkeypatch.setenv("OPERATOR_PASSWORD", "secret")
    importlib.reload(auth_module)
    importlib.reload(auth_config_module)

    with session_scope() as session:
        session.query(MonthlyMenuEntry).filter(
            MonthlyMenuEntry.monthly_menu_id.in_(["2099-03", "2099-04"])
        ).delete()
        session.query(MonthlyMenu).filter(MonthlyMenu.id.in_(["2099-03", "2099-04"])).delete()
        session.add(
            MonthlyMenu(
                id="2099-03",
                month_start=date(2099, 3, 1),
                filename="seed-2099-03.xlsx",
            )
        )
        session.add(
            MonthlyMenuEntry(
                id="menu-entry-covered-month-1",
                monthly_menu_id="2099-03",
                menu_date=date(2099, 4, 5),
                daypart="朝",
                name="Covered Menu A",
                slot_index=0,
            )
        )

    client = TestClient(app)
    res = client.get(
        "/monthly-menus/2099-04",
        headers=_basic_header("operator", "secret"),
    )
    assert res.status_code == 200
    payload = res.json()
    assert payload["menu"]["id"] == "2099-04"
    assert payload["menu"]["requested_month_id"] == "2099-04"
    assert payload["menu"]["source_month_id"] == "2099-03"
    assert payload["entries"][0]["month_id"] == "2099-04"
    assert payload["entries"][0]["requested_month_id"] == "2099-04"
    assert payload["entries"][0]["source_month_id"] == "2099-03"
    assert payload["entries"][0]["menu_date"] == "2099-04-05"
    assert payload["entries"][0]["name"] == "Covered Menu A"


def test_monthly_menus_get_returns_entries_in_canonical_daypart_order(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "false")
    monkeypatch.setenv("OPERATOR_USER", "operator")
    monkeypatch.setenv("OPERATOR_PASSWORD", "secret")
    importlib.reload(auth_module)
    importlib.reload(auth_config_module)

    with session_scope() as session:
        session.query(MonthlyMenuEntry).filter(MonthlyMenuEntry.monthly_menu_id == "2099-06").delete()
        session.query(MonthlyMenu).filter(MonthlyMenu.id == "2099-06").delete()
        session.add(MonthlyMenu(id="2099-06", month_start=date(2099, 6, 1), filename="seed-2099-06.xlsx"))
        session.add_all(
            [
                MonthlyMenuEntry(
                    id="menu-entry-order-1",
                    monthly_menu_id="2099-06",
                    menu_date=date(2099, 6, 5),
                    daypart="夕",
                    name="夕メニュー",
                    slot_index=0,
                ),
                MonthlyMenuEntry(
                    id="menu-entry-order-2",
                    monthly_menu_id="2099-06",
                    menu_date=date(2099, 6, 5),
                    daypart="昼",
                    name="昼メニュー",
                    slot_index=0,
                ),
                MonthlyMenuEntry(
                    id="menu-entry-order-3",
                    monthly_menu_id="2099-06",
                    menu_date=date(2099, 6, 5),
                    daypart="朝",
                    name="朝メニューA",
                    slot_index=0,
                ),
                MonthlyMenuEntry(
                    id="menu-entry-order-4",
                    monthly_menu_id="2099-06",
                    menu_date=date(2099, 6, 5),
                    daypart="朝",
                    name="朝メニューB",
                    slot_index=1,
                ),
            ]
        )

    client = TestClient(app)
    res = client.get(
        "/monthly-menus/2099-06",
        headers=_basic_header("operator", "secret"),
    )
    assert res.status_code == 200
    payload = res.json()
    assert [(entry["daypart"], entry["name"]) for entry in payload["entries"]] == [
        ("朝食", "朝メニューA"),
        ("朝食", "朝メニューB"),
        ("昼食", "昼メニュー"),
        ("夕食", "夕メニュー"),
    ]


def test_monthly_menus_upload_allows_operator(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "false")
    monkeypatch.setenv("OPERATOR_USER", "operator")
    monkeypatch.setenv("OPERATOR_PASSWORD", "secret")
    importlib.reload(auth_module)
    importlib.reload(auth_config_module)

    captured: dict[str, object] = {}

    def _create_menu(month_id, file_bytes, filename, sheet_name=None, **kwargs):
        captured["month_id"] = month_id
        captured["filename"] = filename
        captured["sheet_name"] = sheet_name
        captured["scope_override"] = kwargs.get("scope_override")
        captured["require_menu_master_review"] = kwargs.get("require_menu_master_review")
        return {"id": month_id}, False, 12

    monkeypatch.setattr(menus_api.menu_service, "create_menu", _create_menu)
    monkeypatch.setattr(
        menus_api,
        "save_monthly_menu_upload",
        lambda **_kwargs: type(
            "Archived",
            (),
            {
                "file_uri": "/tmp/monthly-menu.xlsx",
                "content_sha256": "abc123",
            },
        )(),
    )

    client = TestClient(app)
    res = client.post(
        "/monthly-menus",
        params={
            "month_id": "2026-03",
            "scope_type": "tag",
            "scope_value": "ikebukuro",
        },
        files={"file": ("menu.xlsx", b"dummy", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        headers=_basic_header("operator", "secret"),
    )
    assert res.status_code == 200
    assert res.json()["created"] is True
    assert res.json()["item_count"] == 12
    assert captured["scope_override"] == "TAG:ikebukuro"
    assert captured["require_menu_master_review"] is True


def test_monthly_menus_upload_passes_review_resolutions(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "false")
    monkeypatch.setenv("OPERATOR_USER", "operator")
    monkeypatch.setenv("OPERATOR_PASSWORD", "secret")
    importlib.reload(auth_module)
    importlib.reload(auth_config_module)

    captured: dict[str, object] = {}

    def _create_menu(month_id, file_bytes, filename, sheet_name=None, **kwargs):
        captured["month_id"] = month_id
        captured["menu_master_resolutions"] = kwargs.get("menu_master_resolutions")
        captured["require_menu_master_review"] = kwargs.get("require_menu_master_review")
        return {"id": month_id}, False, 2

    monkeypatch.setattr(menus_api.menu_service, "create_menu", _create_menu)
    monkeypatch.setattr(
        menus_api,
        "save_monthly_menu_upload",
        lambda **_kwargs: type(
            "Archived",
            (),
            {
                "file_uri": "/tmp/monthly-menu.xlsx",
                "content_sha256": "abc123",
            },
        )(),
    )

    client = TestClient(app)
    res = client.post(
        "/monthly-menus",
        params={"month_id": "2026-03"},
        data={
            "review_resolutions": '[{"source_name":"白身魚のフライ","action":"create","unit_type":"count","qty_per_serving":1}]'
        },
        files={"file": ("menu.xlsx", b"dummy", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        headers=_basic_header("operator", "secret"),
    )
    assert res.status_code == 200
    assert captured["menu_master_resolutions"] == [
        {
            "source_name": "白身魚のフライ",
            "action": "create",
            "unit_type": "count",
            "qty_per_serving": 1,
        }
    ]
    assert captured["require_menu_master_review"] is True


def test_menu_master_resolution_index_accepts_issue_key_and_normalized_name():
    indexed = menus_api.menu_service._index_menu_master_resolutions(  # noqa: SLF001
        [
            {
                "issue_key": "白身魚フライ",
                "source_name": "白身魚フライ 添)キャベツ",
                "name": "白身魚フライ",
                "action": "create",
                "unit_type": "count",
                "qty_per_serving": 1,
            }
        ]
    )

    assert indexed["白身魚フライ"]["action"] == "create"
    assert indexed["白身魚フライ 添)キャベツ"]["action"] == "create"
    assert indexed[menus_api.menu_service._normalize_menu_name("白身魚フライ 添)キャベツ")]["action"] == "create"  # noqa: SLF001


def test_monthly_menus_upload_returns_review_required_payload(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "false")
    monkeypatch.setenv("OPERATOR_USER", "operator")
    monkeypatch.setenv("OPERATOR_PASSWORD", "secret")
    importlib.reload(auth_module)
    importlib.reload(auth_config_module)

    def _create_menu(*_args, **_kwargs):
        raise menus_api.menu_service.MenuMasterResolutionRequired(
            [
                {
                    "source_name": "白身魚のフライ",
                    "reason": "candidate_review_required",
                    "candidates": [{"id": "MNU0001", "name": "白身魚フライ"}],
                }
            ]
        )

    monkeypatch.setattr(menus_api.menu_service, "create_menu", _create_menu)
    monkeypatch.setattr(
        menus_api,
        "save_monthly_menu_upload",
        lambda **_kwargs: type(
            "Archived",
            (),
            {
                "file_uri": "/tmp/monthly-menu.xlsx",
                "content_sha256": "abc123",
            },
        )(),
    )

    client = TestClient(app)
    res = client.post(
        "/monthly-menus",
        params={"month_id": "2026-03"},
        files={"file": ("menu.xlsx", b"dummy", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        headers=_basic_header("operator", "secret"),
    )
    assert res.status_code == 409
    assert res.json()["detail"]["code"] == "menu_master_review_required"
    assert res.json()["detail"]["issues"][0]["source_name"] == "白身魚のフライ"


def test_monthly_menus_upload_history_allows_operator(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "false")
    monkeypatch.setenv("OPERATOR_USER", "operator")
    monkeypatch.setenv("OPERATOR_PASSWORD", "secret")
    importlib.reload(auth_module)
    importlib.reload(auth_config_module)

    monkeypatch.setattr(
        menus_api.menu_service,
        "list_menu_uploads",
        lambda month_id: [
            {
                "id": "AUD1",
                "month_id": month_id,
                "filename": "menu.xlsx",
                "download_available": True,
            }
        ],
    )

    client = TestClient(app)
    res = client.get(
        "/monthly-menus/2026-03/uploads",
        headers=_basic_header("operator", "secret"),
    )
    assert res.status_code == 200
    assert res.json()["items"][0]["id"] == "AUD1"


def test_monthly_menus_scope_options_allows_operator(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "false")
    monkeypatch.setenv("OPERATOR_USER", "operator")
    monkeypatch.setenv("OPERATOR_PASSWORD", "secret")
    importlib.reload(auth_module)
    importlib.reload(auth_config_module)

    monkeypatch.setattr(
        menus_api.menu_service,
        "list_menu_scope_options",
        lambda: {
            "facilities": [{"id": "FAC00008", "name": "池袋"}],
            "tags": [{"value": "special-group", "facility_count": 1}],
        },
    )

    client = TestClient(app)
    res = client.get(
        "/monthly-menus/scope-options",
        headers=_basic_header("operator", "secret"),
    )
    assert res.status_code == 200
    assert res.json()["facilities"][0]["id"] == "FAC00008"
    assert res.json()["tags"][0]["value"] == "special-group"


def test_monthly_menus_upload_download_allows_operator(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "false")
    monkeypatch.setenv("OPERATOR_USER", "operator")
    monkeypatch.setenv("OPERATOR_PASSWORD", "secret")
    importlib.reload(auth_module)
    importlib.reload(auth_config_module)

    monkeypatch.setattr(
        menus_api.menu_service,
        "get_menu_upload_download",
        lambda month_id, upload_id: {
            "filename": "menu.xlsx",
            "media_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "bytes": b"dummy",
            "download_available": True,
        },
    )

    client = TestClient(app)
    res = client.get(
        "/monthly-menus/2026-03/uploads/AUD1/download",
        headers=_basic_header("operator", "secret"),
    )
    assert res.status_code == 200
    assert res.content == b"dummy"


def test_monthly_menus_update_item_allows_operator(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "false")
    monkeypatch.setenv("OPERATOR_USER", "operator")
    monkeypatch.setenv("OPERATOR_PASSWORD", "secret")
    importlib.reload(auth_module)
    importlib.reload(auth_config_module)

    monkeypatch.setattr(menus_api.menu_service, "update_item_status", lambda *_args, **_kwargs: "updated")

    client = TestClient(app)
    res = client.put(
        "/monthly-menus/2026-03/items/item-1",
        json={"name": "Menu A"},
        headers=_basic_header("operator", "secret"),
    )
    assert res.status_code == 200
    assert res.json()["updated"] is True


def test_monthly_menus_download_supports_non_ascii_filename(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "false")
    monkeypatch.setenv("OPERATOR_USER", "operator")
    monkeypatch.setenv("OPERATOR_PASSWORD", "secret")
    importlib.reload(auth_module)
    importlib.reload(auth_config_module)

    monkeypatch.setattr(
        menus_api.menu_service,
        "get_menu_upload_download",
        lambda month_id, upload_id: {
            "filename": "献立表(月間)2026.4月.xlsm",
            "media_type": "application/vnd.ms-excel.sheet.macroEnabled.12",
            "bytes": b"dummy",
            "download_available": True,
        },
    )

    client = TestClient(app)
    res = client.get(
        "/monthly-menus/2026-03/uploads/AUD-JP/download",
        headers=_basic_header("operator", "secret"),
    )
    assert res.status_code == 200
    assert res.content == b"dummy"
    disposition = res.headers["content-disposition"]
    assert 'filename="2026.4.xlsm"' in disposition
    assert "filename*=UTF-8''%E7%8C%AE%E7%AB%8B%E8%A1%A8%28%E6%9C%88%E9%96%93%292026.4%E6%9C%88.xlsm" in disposition


def test_monthly_menus_upsert_entry_exceptions_allows_operator(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "false")
    monkeypatch.setenv("OPERATOR_USER", "operator")
    monkeypatch.setenv("OPERATOR_PASSWORD", "secret")
    importlib.reload(auth_module)
    importlib.reload(auth_config_module)

    captured: dict[str, object] = {}

    def _upsert(month_id: str, entry_id: str, body: dict):
        captured["month_id"] = month_id
        captured["entry_id"] = entry_id
        captured["body"] = body
        return {
            "updated": True,
            "entry_id": entry_id,
            "facility_ids": body["facility_ids"],
            "entries": [{"id": "MME_EXCEPTION", "facility_override": "FAC00003", "name": "鮭の塩焼き"}],
            "items": [{"id": "MMI_EXCEPTION", "facility_override": "FAC00003", "name": "鮭の塩焼き"}],
        }

    monkeypatch.setattr(menus_api.menu_service, "upsert_entry_exceptions", _upsert)

    client = TestClient(app)
    res = client.post(
        "/monthly-menus/2026-03/entries/MME1/exceptions",
        json={
            "facility_ids": ["FAC00003"],
            "name": "鮭の塩焼き",
            "unit_type": "cut",
            "qty_per_serving": 1,
        },
        headers=_basic_header("operator", "secret"),
    )
    assert res.status_code == 200
    assert res.json()["updated"] is True
    assert captured["month_id"] == "2026-03"
    assert captured["entry_id"] == "MME1"
    assert captured["body"] == {
        "facility_ids": ["FAC00003"],
        "name": "鮭の塩焼き",
        "unit_type": "cut",
        "qty_per_serving": 1,
    }


def test_monthly_menus_resolve_master_check_allows_operator(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "false")
    monkeypatch.setenv("OPERATOR_USER", "operator")
    monkeypatch.setenv("OPERATOR_PASSWORD", "secret")
    importlib.reload(auth_module)
    importlib.reload(auth_config_module)

    captured: dict[str, object] = {}

    def _resolve(month_id, item_id, body):
        captured["month_id"] = month_id
        captured["item_id"] = item_id
        captured["body"] = body
        return {"resolved": True, "mode": "update", "menu_master_id": "MNU1"}

    monkeypatch.setattr(menus_api.menu_service, "resolve_menu_master_check", _resolve)

    client = TestClient(app)
    res = client.post(
        "/monthly-menus/2026-03/master-checks/item-1/resolve",
        json={"action": "month_only", "unit_type": "cut", "qty_per_serving": 2, "category": "主菜"},
        headers=_basic_header("operator", "secret"),
    )
    assert res.status_code == 200
    assert res.json()["resolved"] is True
    assert captured["month_id"] == "2026-03"
    assert captured["item_id"] == "item-1"
    assert captured["body"] == {"action": "month_only", "unit_type": "cut", "qty_per_serving": 2, "category": "主菜"}


def test_monthly_menus_condiments_allows_operator(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "false")
    monkeypatch.setenv("OPERATOR_USER", "operator")
    monkeypatch.setenv("OPERATOR_PASSWORD", "secret")
    importlib.reload(auth_module)
    importlib.reload(auth_config_module)

    monkeypatch.setattr(
        menus_api.menu_service,
        "import_condiments",
        lambda **_kwargs: {"items": 5},
    )

    client = TestClient(app)
    res = client.post(
        "/monthly-menus/condiments",
        files={"file": ("condiments.xlsx", b"dummy", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        headers=_basic_header("operator", "secret"),
    )
    assert res.status_code == 200
    assert res.json()["items"] == 5
