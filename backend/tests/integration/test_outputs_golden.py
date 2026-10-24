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
from tests.helpers.newnarrowbaggingfixture import accepted_bagging_order, authored_tabular_invoice

FIXTURES_DIR = pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "outputs"


def _read_csv(path: pathlib.Path, encoding: str) -> list[dict]:
    with path.open("r", newline="", encoding=encoding) as handle:
        return list(csv.DictReader(handle))


def _sort_rows(rows: list[dict], keys: list[str]) -> list[dict]:
    return sorted(rows, key=lambda row: tuple(row.get(key, "") for key in keys))


def test_outputs_match_authored_golden_values(monkeypatch, tmp_path, isolated_formal_schema):
    """Explicit accepted source; archived AM/Main golden files are not current truth."""
    lines = [
        {"date": "2025-01-06", "daypart": "朝", "menu_name": "Menu A",
         "diet_type": "regular", "area_id": "ARE0001", "bag_type": "standard", "quantity_original": 10},
        {"date": "2025-01-06", "daypart": "朝", "menu_name": "Menu A",
         "diet_type": "diabetic", "area_id": "ARE0002", "bag_type": "diet", "quantity_original": 5},
    ]
    invoice = authored_tabular_invoice(tmp_path, quantity_columns=[
        {"name": "常食", "source": "quantity", "diet_type": "regular", "area_id": "ARE0001"},
        {"name": "糖尿", "source": "quantity", "diet_type": "diabetic", "area_id": "ARE0002"},
    ])
    order = accepted_bagging_order(monkeypatch, tmp_path, lines, facility_id="FAC00001",
        message_id="golden-001", menu_qty_per_serving=100, menu_unit_type="g", menu_temp_type="hot",
        area_names={"ARE0001": "ARE0001", "ARE0002": "ARE0002"}, invoice_template=invoice, confirm=True)
    outputs = build_outputs(order["id"])
    common = {"呼び出し番号": "", "発行枚数": "1", "賞味期限": "2025年1月6日",
        "メニュー": "副菜①", "温・冷": "温菜", "商品名１": "Menu A", "商品名２": "", "内容詳細": "100g"}
    expected_labels = [
        {**common, "時間": "朝ARE0001", "内容量": "1000g", "": "10人前"},
        {**common, "時間": "朝ARE0002", "内容量": "500g", "": "5人前"},
    ]
    actual_labels = _read_csv(pathlib.Path(outputs["labels"]), encoding="cp932")
    assert _sort_rows(actual_labels, ["時間", "内容量"]) == _sort_rows(expected_labels, ["時間", "内容量"])
    actual_agg = _read_csv(pathlib.Path(outputs["aggregate"]), encoding="cp932")
    assert actual_agg == [{**common, "発行枚数": "", "時間": "朝", "内容量": "1500g", "": "15人前"}]
    actual_delivery = pd.read_excel(outputs["delivery_note"])
    # Both authored quantity columns must survive; the retired workbook omitted the second.
    expected_delivery = pd.DataFrame([{
        "日付": pd.Timestamp("2025-01-06"), "区分": "朝", "献立": "副① Menu A", "常食": 10, "糖尿": 5}])
    assert_frame_equal(actual_delivery, expected_delivery, check_dtype=False)
