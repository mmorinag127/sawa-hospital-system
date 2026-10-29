import pathlib
import sys
from datetime import date

import pytest
from fastapi.testclient import TestClient

from auth_support import hospital_operator  # noqa: F401

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

import src.api.menus as menus_api  # noqa: E402
from src.db import session_scope  # noqa: E402
from src.main import app  # noqa: E402
from src.models.menu import MonthlyMenu, MonthlyMenuEntry  # noqa: E402


def test_monthly_menus_get_requires_operator_and_allows_operator(monkeypatch, hospital_operator):
    headers = hospital_operator.headers

    monkeypatch.setattr(
        menus_api.menu_service,
        "get_menu",
        lambda month_id, *, allow_unresolved: {"menu": {"id": month_id}, "items": [], "entries": []} if allow_unresolved else None,
    )

    client = TestClient(app)
    unauthorized = client.get("/monthly-menus/2026-03")
    assert unauthorized.status_code == 401

    authorized = client.get(
        "/monthly-menus/2026-03",
        headers=headers,
    )
    assert authorized.status_code == 200
    assert authorized.json()["menu"]["id"] == "2026-03"


def test_monthly_menus_get_uses_facility_specific_lookup_when_query_is_present(monkeypatch, hospital_operator):
    headers = hospital_operator.headers

    captured: dict[str, str | None] = {}

    def _get_menu_for_facility(month_id: str, facility_id: str | None, *, allow_unresolved):
        assert allow_unresolved is True
        captured["month_id"] = month_id
        captured["facility_id"] = facility_id
        return {"menu": {"id": "2026-03"}, "items": [], "entries": []}

    monkeypatch.setattr(menus_api.menu_service, "get_menu_for_facility", _get_menu_for_facility)

    client = TestClient(app)
    authorized = client.get(
        "/monthly-menus/2026-04?facility_id=FAC00005",
        headers=headers,
    )
    assert authorized.status_code == 200
    assert captured == {"month_id": "2026-04", "facility_id": "FAC00005"}


def test_monthly_menus_latest_requires_operator_and_allows_operator(monkeypatch, hospital_operator):
    headers = hospital_operator.headers

    monkeypatch.setattr(
        menus_api.menu_service,
        "get_latest_menu",
        lambda *, allow_unresolved: {"menu": {"id": "2026-03"}, "items": [], "entries": []} if allow_unresolved else None,
    )

    client = TestClient(app)
    unauthorized = client.get("/monthly-menus/latest")
    assert unauthorized.status_code == 401

    authorized = client.get(
        "/monthly-menus/latest",
        headers=headers,
    )
    assert authorized.status_code == 200
    assert authorized.json()["menu"]["id"] == "2026-03"


def test_monthly_menus_get_returns_synthetic_menu_when_parent_row_is_missing(monkeypatch, hospital_operator):
    headers = hospital_operator.headers

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
        headers=headers,
    )
    assert res.status_code == 200
    payload = res.json()
    assert payload["menu"]["id"] == "2099-12"
    assert payload["menu"]["filename"] is None
    assert payload["menu"]["display_name"] == "2099-12"
    assert payload["entries"][0]["name"] == "Synthetic Menu A"


def test_monthly_menus_get_does_not_substitute_previous_month(monkeypatch, hospital_operator):
    headers = hospital_operator.headers

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
        headers=headers,
    )
    assert res.status_code == 404
    assert res.json()["detail"] == "not found"
    source = client.get("/monthly-menus/2099-03", headers=headers)
    assert source.status_code == 200
    payload = source.json()
    assert payload["menu"]["id"] == "2099-03"
    assert payload["entries"][0]["month_id"] == "2099-03"
    assert payload["entries"][0]["menu_date"] == "2099-04-05"
    assert payload["entries"][0]["name"] == "Covered Menu A"


def test_monthly_menus_get_returns_entries_in_canonical_daypart_order(monkeypatch, hospital_operator):
    headers = hospital_operator.headers

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
        headers=headers,
    )
    assert res.status_code == 200
    payload = res.json()
    assert [(entry["daypart"], entry["name"]) for entry in payload["entries"]] == [
        ("朝食", "朝メニューA"),
        ("朝食", "朝メニューB"),
        ("昼食", "昼メニュー"),
        ("夕食", "夕メニュー"),
    ]


def test_monthly_menus_upload_allows_operator(monkeypatch, hospital_operator):
    headers = hospital_operator.headers

    captured: dict[str, object] = {}

    def _save_import(month_id, file_bytes, archived, sheet_name=None, **kwargs):
        captured["month_id"] = month_id
        captured["filename"] = archived.original_filename
        captured["sheet_name"] = sheet_name
        captured["scope_override"] = kwargs.get("scope_override")
        return {"id": "saved-12", "month_id": month_id, "state": "review", "revision": "saved-read-12", "review": {"item_count": 12}}

    monkeypatch.setattr(menus_api.menu_service, "save_menu_import", _save_import)
    monkeypatch.setattr(
        menus_api,
        "save_monthly_menu_upload",
        lambda **_kwargs: type(
            "Archived",
            (),
            {
                "file_uri": "/tmp/monthly-menu.xlsx",
                "content_sha256": "abc123",
                "original_filename": "menu.xlsx",
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
        headers=headers,
    )
    assert res.status_code == 200
    assert res.json()["state"] == "review"
    assert "created" not in res.json()
    assert res.json()["review"]["item_count"] == 12
    assert captured["scope_override"] == "TAG:ikebukuro"
    def _apply(month_id, import_id, expected, resolutions, *, apply=False):
        assert (month_id, import_id, expected, resolutions, apply) == ("2026-03", "saved-12", res.json()["revision"], None, True)
        return {"created": True, "item_count": 12}
    monkeypatch.setattr(menus_api.menu_service, "review_menu_import", _apply)
    own = client.post("/monthly-menus/2026-03/imports/saved-12/apply", headers={**headers, "If-Match": res.json()["revision"]})
    assert own.status_code == 200
    assert own.json()["created"] is True
    assert own.json()["item_count"] == 12


def test_monthly_menu_archive_failure_stops_before_saving_or_applying(monkeypatch, hospital_operator):
    def fail_archive(**_kwargs):
        raise RuntimeError("archive unavailable")
    def unexpected(*_args, **_kwargs):
        pytest.fail("archive failure must not save a receipt or apply business rows")
    monkeypatch.setattr(menus_api, "save_monthly_menu_upload", fail_archive)
    monkeypatch.setattr(menus_api.menu_service, "save_menu_import", unexpected)
    monkeypatch.setattr(menus_api.menu_service, "create_menu", unexpected)
    response = TestClient(app).post("/monthly-menus", params={"month_id": "2026-03"},
        files={"file": ("menu.xlsx", b"original", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        headers=hospital_operator.headers)
    assert response.status_code == 503
    assert "no menu applied" in response.json()["detail"]


def test_monthly_menus_upload_passes_review_resolutions(monkeypatch, hospital_operator):
    headers = hospital_operator.headers

    captured: dict[str, object] = {}

    def _review(month_id, import_id, expected, resolutions, *, input_expected=None):
        captured["month_id"] = month_id
        captured["menu_master_resolutions"] = resolutions
        assert expected == "receipt-read-2"
        assert input_expected == "input-read-2"
        return {"id": import_id, "revision": "receipt-own-3", "review": {"item_count": 2}}

    monkeypatch.setattr(menus_api.menu_service, "review_menu_import", _review)
    monkeypatch.setattr(menus_api.menu_service, "read_menu_import", lambda *_args: {"revision": "receipt-read-2", "current_input_revision": "input-read-2"})
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
    read = client.get("/monthly-menus/2026-03/imports/saved-2", headers=headers).json()
    res = client.post(
        "/monthly-menus/2026-03/imports/saved-2/review",
        json={
            "resolutions": [{"source_name": "白身魚のフライ", "action": "create", "unit_type": "count", "qty_per_serving": 1}],
            "input_revision": read["current_input_revision"],
        },
        headers={**headers, "If-Match": read["revision"]},
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
    assert res.json()["review"]["item_count"] == 2


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


def test_monthly_menus_upload_returns_review_required_payload(monkeypatch, hospital_operator):
    headers = hospital_operator.headers

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

    monkeypatch.setattr(menus_api.menu_service, "review_menu_import", _create_menu)
    monkeypatch.setattr(menus_api.menu_service, "read_menu_import", lambda *_args: {"revision": "review-read"})
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
    read = client.get("/monthly-menus/2026-03/imports/saved-unresolved", headers=headers).json()
    res = client.post(
        "/monthly-menus/2026-03/imports/saved-unresolved/apply",
        headers={**headers, "If-Match": read["revision"]},
    )
    assert res.status_code == 409
    assert res.json()["detail"]["code"] == "menu_master_review_required"
    assert res.json()["detail"]["issues"][0]["source_name"] == "白身魚のフライ"


def test_monthly_menus_upload_history_allows_operator(monkeypatch, hospital_operator):
    headers = hospital_operator.headers

    monkeypatch.setattr(
        menus_api.menu_service,
        "list_menu_uploads",
        lambda month_id, *, before_id=None: {"items": [
            {
                "id": "AUD1",
                "month_id": month_id,
                "filename": "menu.xlsx",
                "download_available": True,
            }
        ], "next_before_id": None},
    )

    client = TestClient(app)
    res = client.get(
        "/monthly-menus/2026-03/uploads",
        headers=headers,
    )
    assert res.status_code == 200
    assert res.json()["items"][0]["id"] == "AUD1"


def test_monthly_menus_scope_options_allows_operator(monkeypatch, hospital_operator):
    headers = hospital_operator.headers

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
        headers=headers,
    )
    assert res.status_code == 200
    assert res.json()["facilities"][0]["id"] == "FAC00008"
    assert res.json()["tags"][0]["value"] == "special-group"


def test_monthly_menus_upload_download_allows_operator(monkeypatch, hospital_operator):
    headers = hospital_operator.headers

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
        headers=headers,
    )
    assert res.status_code == 200
    assert res.content == b"dummy"


def test_monthly_menus_update_item_allows_operator(monkeypatch, hospital_operator):
    headers = hospital_operator.headers

    monkeypatch.setattr(menus_api.menu_service, "get_monthly_editor_snapshot", lambda _month: {"revision": "item-read"})
    def _command(month, item, body, expected, *, action):
        assert expected == "item-read" and action == "item"
        return {"updated": True}
    monkeypatch.setattr(menus_api.menu_service, "monthly_editor_command", _command)

    client = TestClient(app)
    read = client.get("/monthly-menus/2026-03?editor=true", headers=headers).json()
    res = client.put(
        "/monthly-menus/2026-03/items/item-1",
        json={"name": "Menu A"},
        headers={**headers, "If-Match": read["revision"]},
    )
    assert res.status_code == 200
    assert res.json()["updated"] is True


def test_monthly_menus_download_supports_non_ascii_filename(monkeypatch, hospital_operator):
    headers = hospital_operator.headers

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
        headers=headers,
    )
    assert res.status_code == 200
    assert res.content == b"dummy"
    disposition = res.headers["content-disposition"]
    assert 'filename="2026.4.xlsm"' in disposition
    assert "filename*=UTF-8''%E7%8C%AE%E7%AB%8B%E8%A1%A8%28%E6%9C%88%E9%96%93%292026.4%E6%9C%88.xlsm" in disposition


def test_monthly_menus_upsert_entry_exceptions_allows_operator(monkeypatch, hospital_operator):
    headers = hospital_operator.headers

    captured: dict[str, object] = {}

    def _upsert(month_id: str, entry_id: str, body: dict, expected, *, action):
        assert expected == "exceptions-read" and action == "exceptions"
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

    monkeypatch.setattr(menus_api.menu_service, "monthly_editor_command", _upsert)
    monkeypatch.setattr(menus_api.menu_service, "get_monthly_editor_snapshot", lambda _month: {"revision": "exceptions-read"})

    client = TestClient(app)
    read = client.get("/monthly-menus/2026-03?editor=true", headers=headers).json()
    res = client.post(
        "/monthly-menus/2026-03/entries/MME1/exceptions",
        json={
            "facility_ids": ["FAC00003"],
            "name": "鮭の塩焼き",
            "unit_type": "cut",
            "qty_per_serving": 1,
        },
        headers={**headers, "If-Match": read["revision"]},
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


def test_monthly_menus_resolve_master_check_allows_operator(monkeypatch, hospital_operator):
    headers = hospital_operator.headers

    captured: dict[str, object] = {}

    def _resolve(month_id, item_id, body, expected, *, action):
        assert expected == "master-read" and action == "master"
        captured["month_id"] = month_id
        captured["item_id"] = item_id
        captured["body"] = body
        return {"resolved": True, "mode": "update", "menu_master_id": "MNU1"}

    monkeypatch.setattr(menus_api.menu_service, "monthly_editor_command", _resolve)
    monkeypatch.setattr(menus_api.menu_service, "get_monthly_editor_snapshot", lambda _month: {"revision": "master-read"})

    client = TestClient(app)
    read = client.get("/monthly-menus/2026-03?editor=true", headers=headers).json()
    res = client.post(
        "/monthly-menus/2026-03/master-checks/item-1/resolve",
        json={"action": "month_only", "unit_type": "cut", "qty_per_serving": 2, "category": "主菜"},
        headers={**headers, "If-Match": read["revision"]},
    )
    assert res.status_code == 200
    assert res.json()["resolved"] is True
    assert captured["month_id"] == "2026-03"
    assert captured["item_id"] == "item-1"
    assert captured["body"] == {"action": "month_only", "unit_type": "cut", "qty_per_serving": 2, "category": "主菜"}


def test_monthly_menus_condiments_allows_operator(monkeypatch, hospital_operator):
    headers = hospital_operator.headers

    monkeypatch.setattr(
        menus_api.menu_service,
        "import_condiments",
        lambda **_kwargs: {"items": 5},
    )

    client = TestClient(app)
    res = client.post(
        "/monthly-menus/condiments",
        files={"file": ("condiments.xlsx", b"dummy", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        headers=headers,
    )
    assert res.status_code == 200
    assert res.json()["items"] == 5
