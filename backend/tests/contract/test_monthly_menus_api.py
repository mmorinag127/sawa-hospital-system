import base64
import importlib
import pathlib
import sys

from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

import src.api.auth as auth_module  # noqa: E402
import src.api.auth_config as auth_config_module  # noqa: E402
import src.api.menus as menus_api  # noqa: E402
from src.main import app  # noqa: E402


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
