import base64
import importlib
import pathlib
import sys

from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

import src.api.auth as auth_module  # noqa: E402
import src.api.auth_config as auth_config_module  # noqa: E402
import src.api.order_forms as order_forms_api  # noqa: E402
from src.main import app  # noqa: E402


def _basic_header(username: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    return {"Authorization": f"Basic {token}"}


def test_order_forms_generate_requires_operator_and_returns_generated_filename(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "false")
    monkeypatch.setenv("OPERATOR_USER", "operator")
    monkeypatch.setenv("OPERATOR_PASSWORD", "secret")
    importlib.reload(auth_module)
    importlib.reload(auth_config_module)

    output_path = tmp_path / "order_form_FAC00001_2026-03_fax_layout_regular_forbidden_v1.xlsx"
    output_path.write_bytes(b"test-xlsx")

    monkeypatch.setattr(order_forms_api.order_form_service, "build_order_form_excel", lambda **kwargs: output_path)

    client = TestClient(app)
    unauthorized = client.post("/order-forms/generate", params={"facility_id": "FAC00001", "month_id": "2026-03"})
    assert unauthorized.status_code == 401

    authorized = client.post(
        "/order-forms/generate",
        params={"facility_id": "FAC00001", "month_id": "2026-03"},
        headers=_basic_header("operator", "secret"),
    )
    assert authorized.status_code == 200
    assert "order_form_FAC00001_2026-03_fax_layout_regular_forbidden_v1.xlsx" in (
        authorized.headers.get("content-disposition") or ""
    )
