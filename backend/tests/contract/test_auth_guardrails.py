import base64
import importlib
import pathlib
import sys

from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

import src.api.auth as auth_module  # noqa: E402
import src.api.auth_config as auth_config_module  # noqa: E402
from src.main import app  # noqa: E402


def _basic_header(username: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    return {"Authorization": f"Basic {token}"}


def test_auth_default_fails_closed_when_env_missing(monkeypatch):
    monkeypatch.delenv("AUTH_DISABLED", raising=False)
    monkeypatch.delenv("ADMIN_USER", raising=False)
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    monkeypatch.delenv("OPERATOR_USER", raising=False)
    monkeypatch.delenv("OPERATOR_PASSWORD", raising=False)
    importlib.reload(auth_module)
    importlib.reload(auth_config_module)

    client = TestClient(app)
    res = client.get("/orders")
    assert res.status_code == 401


def test_auth_can_be_explicitly_disabled_in_tests(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "true")
    importlib.reload(auth_module)
    importlib.reload(auth_config_module)

    client = TestClient(app)
    res = client.get("/auth/config")
    assert res.status_code == 200
    assert res.json()["auth_disabled"] is True


def test_auth_enables_basic_operator_when_env_present(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "false")
    monkeypatch.setenv("OPERATOR_USER", "operator")
    monkeypatch.setenv("OPERATOR_PASSWORD", "secret")
    importlib.reload(auth_module)
    importlib.reload(auth_config_module)

    client = TestClient(app)
    res = client.get("/orders", headers=_basic_header("operator", "secret"))
    assert res.status_code == 200


def test_operator_basic_cannot_access_admin_route(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "false")
    monkeypatch.setenv("ADMIN_USER", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "admin-secret")
    monkeypatch.setenv("OPERATOR_USER", "operator")
    monkeypatch.setenv("OPERATOR_PASSWORD", "operator-secret")
    importlib.reload(auth_module)
    importlib.reload(auth_config_module)

    client = TestClient(app)
    res = client.get("/users", headers=_basic_header("operator", "operator-secret"))
    assert res.status_code == 403
