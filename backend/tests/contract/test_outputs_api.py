import pathlib
import sys

from fastapi.testclient import TestClient
from openpyxl import Workbook

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.main import app  # noqa: E402
from src.api import outputs as outputs_api  # noqa: E402
from src.services import output_builder  # noqa: E402


def test_daily_bundle_returns_xlsx(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTH_DISABLED", "true")
    workbook_path = tmp_path / "daily_outputs_2026-03-22_labels.xlsx"
    workbook = Workbook()
    workbook.active.title = "そよかぜ"
    workbook.save(workbook_path)

    monkeypatch.setattr(
        outputs_api,
        "build_daily_output_bundle",
        lambda target_date, bundle_type="both", status="確定": (
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


def test_weekly_weight_returns_xlsx(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTH_DISABLED", "true")
    workbook_path = tmp_path / "May 23-29 2026 Weight.xlsx"
    workbook = Workbook()
    workbook.active.title = "May 23-29 2026"
    workbook.save(workbook_path)

    monkeypatch.setattr(
        outputs_api,
        "build_weekly_weight_summary_workbook",
        lambda target_date, status=None: workbook_path,
    )

    client = TestClient(app)
    response = client.get(
        "/outputs/weekly-weight",
        params={"date": "2026-05-24"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert "May%2023-29%202026%20Weight.xlsx" in response.headers["content-disposition"]


def test_weekly_weight_returns_empty_xlsx_when_no_rows(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTH_DISABLED", "true")
    monkeypatch.setattr(outputs_api, "build_weekly_weight_summary_workbook", output_builder.build_weekly_weight_summary_workbook)
    monkeypatch.setattr(output_builder, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(output_builder, "_weekly_weight_collect_rows", lambda target_date, status=None: {})

    client = TestClient(app)
    response = client.get(
        "/outputs/weekly-weight",
        params={"date": "2026-05-24"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


def test_daily_bundle_returns_400_when_no_rows(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "true")
    monkeypatch.setattr(
        outputs_api,
        "build_daily_output_bundle",
        lambda target_date, bundle_type="both", status=None: (_ for _ in ()).throw(
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
