import pathlib
import sys

from fastapi.testclient import TestClient
from openpyxl import Workbook

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.main import app  # noqa: E402
from src.api import outputs as outputs_api  # noqa: E402


def test_daily_bundle_returns_xlsx(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTH_DISABLED", "true")
    workbook_path = tmp_path / "daily_outputs_2026-03-22_labels.xlsx"
    workbook = Workbook()
    workbook.active.title = "そよかぜ"
    workbook.save(workbook_path)

    monkeypatch.setattr(
        outputs_api,
        "build_daily_output_bundle",
        lambda target_date, bundle_type="both", status="確定", include_weight_workbook=False: (
            workbook_path,
            {
                "bundle_type": bundle_type,
                "total_orders": 1,
                "success_orders": 1,
                "error_orders": 0,
            },
        ),
    )

    client = TestClient(app)
    response = client.get(
        "/outputs/daily-bundle",
        params={"date": "2026-03-22", "bundle_type": "labels"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert "daily_outputs_2026-03-22_labels.xlsx" in response.headers["content-disposition"]


def test_daily_bundle_with_weight_returns_zip(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTH_DISABLED", "true")
    zip_path = tmp_path / "daily_outputs_2026-03-22_labels.zip"
    zip_path.write_bytes(b"zip")

    monkeypatch.setattr(
        outputs_api,
        "build_daily_output_bundle",
        lambda target_date, bundle_type="both", status=None, include_weight_workbook=False: (
            zip_path,
            {
                "bundle_type": bundle_type,
                "total_orders": 1,
                "success_orders": 1,
                "error_orders": 0,
                "file_format": "zip",
            },
        ),
    )

    client = TestClient(app)
    response = client.get(
        "/outputs/daily-bundle",
        params={"date": "2026-03-22", "bundle_type": "labels", "include_weight_workbook": "true"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/zip")
    assert "daily_outputs_2026-03-22_labels.zip" in response.headers["content-disposition"]


def test_daily_bundle_returns_400_when_no_rows(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "true")
    monkeypatch.setattr(
        outputs_api,
        "build_daily_output_bundle",
        lambda target_date, bundle_type="both", status=None, include_weight_workbook=False: (_ for _ in ()).throw(
            ValueError("対象日の出力対象がありません")
        ),
    )

    client = TestClient(app)
    response = client.get(
        "/outputs/daily-bundle",
        params={"date": "2026-03-22", "bundle_type": "labels"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "対象日の出力対象がありません"
