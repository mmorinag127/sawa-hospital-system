import csv
import pathlib
import sys

import pandas as pd
from pandas.testing import assert_frame_equal

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.db import session_scope
from src.models.menu import MonthlyMenu, MonthlyMenuItem
from src.services import menu_service, order_service
from src.services.output_builder import build_outputs
from src.workers.ingest_mail_adapter import IngestEmailPayload

FIXTURES_DIR = pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "outputs"


def _read_csv(path: pathlib.Path, encoding: str) -> list[dict]:
    with path.open("r", newline="", encoding=encoding) as handle:
        return list(csv.DictReader(handle))


def _sort_rows(rows: list[dict], keys: list[str]) -> list[dict]:
    return sorted(rows, key=lambda row: tuple(row.get(key, "") for key in keys))


def test_outputs_match_golden_files():
    order_service.clear_all()
    with session_scope() as session:
        session.query(MonthlyMenuItem).delete()
        session.query(MonthlyMenu).delete()

    week_id = "2025-01"
    item = menu_service.create_item_stub(week_id, "Menu A")
    menu_service.update_item(
        week_id,
        item["id"],
        {
            "unit_type": "g",
            "qty_per_serving": 100,
            "temp_type": "hot",
            "daypart": "AM",
            "category": "Main",
        },
    )

    payload = IngestEmailPayload(
        message_id="golden-001",
        pdf_uri="file:///tmp/golden.pdf",
        received_at=pd.Timestamp("2025-01-05T12:00:00"),
        facility_hint="FAC00001",
        week_hint=week_id,
    )
    lines = [
        {
            "line_id": "1",
            "date": "2025-01-06",
            "daypart": None,
            "menu_name": "Menu A",
            "diet_type": "regular",
            "area_id": "ARE0001",
            "bag_type": "standard",
            "quantity_original": 10,
        },
        {
            "line_id": "2",
            "date": "2025-01-06",
            "daypart": None,
            "menu_name": "Menu A",
            "diet_type": "diabetic",
            "area_id": "ARE0002",
            "bag_type": "diet",
            "quantity_original": 5,
        },
    ]
    order = order_service.create_order_from_ingest(payload, lines=lines)
    outputs = build_outputs(order["id"])

    actual_labels = _read_csv(pathlib.Path(outputs["labels"]), encoding="cp932")
    expected_labels = _read_csv(FIXTURES_DIR / "labels_expected.csv", encoding="cp932")
    assert _sort_rows(actual_labels, ["product_name", "details", "quantity"]) == _sort_rows(
        expected_labels, ["product_name", "details", "quantity"]
    )

    actual_agg = _read_csv(pathlib.Path(outputs["aggregate"]), encoding="utf-8")
    expected_agg = _read_csv(FIXTURES_DIR / "aggregate_expected.csv", encoding="utf-8")
    assert _sort_rows(actual_agg, ["diet_type", "area_id", "bag_type"]) == _sort_rows(
        expected_agg, ["diet_type", "area_id", "bag_type"]
    )

    actual_delivery = pd.read_excel(outputs["delivery_note"])
    expected_delivery = pd.read_excel(FIXTURES_DIR / "delivery_expected.xlsx")
    assert_frame_equal(actual_delivery, expected_delivery, check_dtype=False, check_like=True)
