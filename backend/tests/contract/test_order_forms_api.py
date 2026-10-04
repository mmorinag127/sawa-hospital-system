import pathlib
import sys

from fastapi.testclient import TestClient

from auth_support import hospital_operator  # noqa: F401

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

import src.api.order_forms as order_forms_api  # noqa: E402
from src.main import app  # noqa: E402


def test_order_forms_generate_requires_operator_and_returns_generated_filename(tmp_path, monkeypatch, hospital_operator):
    headers = hospital_operator.headers

    output_path = tmp_path / "order_form_FAC00001_2026-03_fax_layout_regular_forbidden_v1.xlsx"
    output_path.write_bytes(b"test-xlsx")

    monkeypatch.setattr(order_forms_api.order_form_service, "build_order_form_excel", lambda **kwargs: output_path)

    client = TestClient(app)
    unauthorized = client.post("/order-forms/generate", params={"facility_id": "FAC00001", "month_id": "2026-03"})
    assert unauthorized.status_code == 401

    authorized = client.post(
        "/order-forms/generate",
        params={"facility_id": "FAC00001", "month_id": "2026-03"},
        headers=headers,
    )
    assert authorized.status_code == 200
    assert "order_form_FAC00001_2026-03_fax_layout_regular_forbidden_v1.xlsx" in (
        authorized.headers.get("content-disposition") or ""
    )
