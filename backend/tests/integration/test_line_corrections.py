import sys
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

import csv
from src.services import order_service, output_builder  # noqa: E402


def test_line_corrections_zero_suppression_and_change_column():
    order_service.clear_all()
    payload = type(
        "obj",
        (),
        {
            "message_id": "m1",
            "pdf_uri": "file://dummy.pdf",
            "received_at": "2025-12-23T10:00:00",
            "facility_hint": "FAC001",
            "week_hint": "WEK2025W52",
        },
    )
    order = order_service.create_order_from_ingest(payload=payload)
    lines = [
        {
            "line_id": "L1",
            "date": "2025-12-23",
            "daypart": "朝",
            "menu_name": "Menu A",
            "diet_type": "regular",
            "area_id": "ARE0001",
            "bag_type": "standard",
            "quantity_original": 10,
        },
        {
            "line_id": "L2",
            "date": "2025-12-23",
            "daypart": "朝",
            "menu_name": "Menu B",
            "diet_type": "regular",
            "area_id": "ARE0001",
            "bag_type": "standard",
            "quantity_original": 10,
            "quantity_corrected": 7,
        },
        {
            "line_id": "L3",
            "date": "2025-12-23",
            "daypart": "朝",
            "menu_name": "Menu C",
            "diet_type": "regular",
            "area_id": "ARE0001",
            "bag_type": "standard",
            "quantity_original": 0,
        },
        {
            "line_id": "L4",
            "date": "2025-12-23",
            "daypart": "朝",
            "menu_name": "Menu D",
            "diet_type": "regular",
            "area_id": "ARE0001",
            "bag_type": "standard",
            "quantity_original": 5,
            "quantity_corrected": 0,
        },
    ]
    order_service.update_lines(order["id"], lines)
    saved = order_service.get_order_by_id(order["id"])
    assert saved["lines"][1]["quantity_corrected"] == 7
    outputs = output_builder.build_outputs(order["id"])
    with open(outputs["aggregate"], newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 2
    quantities = {row["menu_name"]: float(row["quantity"]) for row in rows}
    assert quantities["Menu A"] == 10.0
    assert quantities["Menu B"] == 7.0
