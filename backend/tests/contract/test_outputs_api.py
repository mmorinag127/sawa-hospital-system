import pathlib
import sys

from fastapi.testclient import TestClient
from openpyxl import Workbook

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.main import app  # noqa: E402
from src.api import outputs as outputs_api  # noqa: E402
from src.services import output_builder  # noqa: E402


def test_delivery_html_render_columns_hide_revision_columns_and_split_duplicate_areas():
    columns = [
        {"name": "常食1回目", "source": "quantity", "diet_type": "regular", "area_id": "X", "header": "常食1回目"},
        {"name": "常食2回目", "source": "quantity", "diet_type": "change_1", "area_id": "X", "header": "常食2回目"},
        {"name": "常食3回目", "source": "quantity", "diet_type": "change_2", "area_id": "X", "header": "常食3回目"},
        {"name": "変更1", "source": "quantity", "diet_type": "変更1", "area_id": "X", "header": "変更1"},
        {"name": "ミキサー2F", "source": "quantity", "diet_type": "mixer", "area_id": "2F", "header": "ミキサー"},
        {"name": "ミキサー3F", "source": "quantity", "diet_type": "mixer", "area_id": "3F", "header": "ミキサー"},
    ]

    render_columns = outputs_api._build_delivery_render_columns(columns)  # noqa: SLF001
    headers = [column.get("header") for column in render_columns]
    quantity_columns = [column for column in render_columns if column.get("kind") == "quantity"]

    assert headers == ["日付", "区分", "献立区分", "メニュー名", "常食", "ミキサー\n2F", "ミキサー\n3F", "備考欄"]
    assert quantity_columns[0]["source_names"] == ["常食1回目", "常食2回目", "常食3回目", "変更1"]


def test_delivery_html_render_sums_final_regular_column():
    columns = outputs_api._build_delivery_render_columns(  # noqa: SLF001
        [
            {"name": "常食1回目", "source": "quantity", "diet_type": "regular", "area_id": "X", "header": "常食1回目"},
            {"name": "常食2回目", "source": "quantity", "diet_type": "change_1", "area_id": "X", "header": "常食2回目"},
        ]
    )
    regular_column = [column for column in columns if column.get("kind") == "quantity"][0]

    value = outputs_api._delivery_render_cell_value(  # noqa: SLF001
        {"常食1回目": 1.0, "常食2回目": 2.0},
        regular_column,
    )

    assert value == 3


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


def test_weekly_weight_returns_empty_xlsx_when_empty_package_patch_fails(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTH_DISABLED", "true")
    monkeypatch.setattr(outputs_api, "build_weekly_weight_summary_workbook", output_builder.build_weekly_weight_summary_workbook)
    monkeypatch.setattr(output_builder, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(output_builder, "_weekly_weight_collect_rows", lambda target_date, status=None: {})
    monkeypatch.setattr(output_builder, "_patch_weekly_weight_package", lambda path, sheet_title: (_ for _ in ()).throw(KeyError(4)))

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
