import pathlib
import sys

import pytest
from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.api import outputs as outputs_api  # noqa: E402
from src.main import app  # noqa: E402


@pytest.mark.parametrize("bundle_type", ["labels", "labels_csv", "delivery", "both"])
def test_daily_bundle_headers_keep_empty_orders_separate(monkeypatch, tmp_path, bundle_type):
    monkeypatch.setenv("AUTH_DISABLED", "true")
    output_path = tmp_path / "daily-output.xlsx"
    output_path.write_bytes(b"output")
    monkeypatch.setattr(
        outputs_api,
        "build_daily_output_bundle",
        lambda *_args, **_kwargs: (
            output_path,
            {
                "bundle_type": bundle_type,
                "total_orders": 3,
                "success_orders": 1,
                "empty_orders": 1,
                "error_orders": 1,
                "file_format": "xlsx",
            },
        ),
    )

    response = TestClient(app).get(
        "/outputs/daily-bundle",
        params={"date": "2026-03-22", "bundle_type": bundle_type},
    )

    assert response.status_code == 200
    assert response.headers["x-daily-bundle-total-orders"] == "3"
    assert response.headers["x-daily-bundle-success-orders"] == "1"
    assert response.headers["x-daily-bundle-empty-orders"] == "1"
    assert response.headers["x-daily-bundle-error-orders"] == "1"
