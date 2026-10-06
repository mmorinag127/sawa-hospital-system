import csv
import pathlib
import re
import sys
from datetime import datetime

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.services import order_service, order_workflow_v2_service, output_builder  # noqa: E402
from src.services import facility_service  # noqa: E402
from src.db import session_scope  # noqa: E402
from src.models.facility_template_version import FacilityTemplateVersion  # noqa: E402
from src.workers.ingest_mail_adapter import IngestEmailPayload  # noqa: E402

FIELDS = ["date_mmdd", "daypart", "menu", "qty.regular_x", "qty.change_1_x", "qty.change_2_x"]
HEADER = ["日付", "区分", "メニュー", "常食", "変更1", "変更2"]


def _save_canonical_sheet(*, message_id: str, rows: list[list[str]]):
    with session_scope() as session:
        assert facility_service.ensure_facility_materialized(session, "FAC00001") is not None
    order = order_service.create_order_from_ingest(
        IngestEmailPayload(message_id, "file://dummy.pdf", datetime(2025, 12, 23, 10), "FAC00001", "WEK2025W52"),
        lines=None,
    )
    workflow, error = order_workflow_v2_service.confirm_context(
        order_id=order["id"], facility_id="FAC00001", week_start="2025-12-22", week_end="2025-12-28", template_id="大和なでしこ"
    )
    assert error is None
    evidence = order_service.persist_ocr_evidence_run(
        order["id"],
        {
            "status": "done",
            "pages": [{"page_index": 1, "ocr_overlay_uri": "file://ocr.png", "layout_overlay_uri": "file://layout.png"}],
            "table_raw": "|日付|区分|メニュー|常食|変更1|変更2|\n|---|---|---|---|---|---|\n|12/23|朝|Menu A|10|||",
            "template_resolution": {"resolved_template_id": "大和なでしこ", "blocked": False, "blocked_reasons": []},
            "table_box": [0.1, 0.2, 0.9, 0.8], "grid_column_edges": [0.1, 0.2, 0.4, 0.6, 0.75, 0.9], "grid_row_edges": [0.2, 0.4, 0.8],
        },
        producer_version="c2-ci-contract", source="c2-ci-contract",
    )
    assert evidence is not None
    _, error = order_workflow_v2_service.select_ocr_result(order["id"], evidence["id"])
    assert error is None
    saved, error = order_workflow_v2_service.save_sheet(
        order_id=order["id"],
        sheet={"fields": FIELDS, "header": HEADER, "rows": rows, "row_ids": [f"row-{index}" for index in range(len(rows))]},
        edited_by="c2-ci-contract",
    )
    assert error is None
    assert saved["saved_sheet"]["template_version_id"] == workflow["template_version_id"]
    order["template_version_id"] = workflow["template_version_id"]
    return order


def _resolve_menu_name(row: dict) -> str:
    for key in ("menu_name", "商品名１", "商品名1", "メニュー"):
        value = row.get(key)
        if value:
            return str(value).strip()
    return ""


def _resolve_quantity(row: dict) -> float:
    for key in ("quantity", "数量", "", "内容量"):
        value = row.get(key)
        if value is None:
            continue
        match = re.search(r"\d+(?:\.\d+)?", str(value))
        if match:
            return float(match.group(0))
    raise AssertionError(f"quantity not found in row: {row}")


def test_line_corrections_zero_suppression_and_change_column():
    order_service.clear_all()
    order = _save_canonical_sheet(message_id="m1", rows=[
        ["12/23", "朝", "Menu A", "10", "", ""], ["12/23", "朝", "Menu B", "10", "7", ""],
        ["12/23", "朝", "Menu C", "0", "", ""], ["12/23", "朝", "Menu D", "5", "0", ""],
    ])
    outputs = output_builder.build_outputs(order["id"])
    candidate = order_service.build_confirm_materialization_candidate(order["id"])
    assert candidate is not None
    corrected = next(line for line in candidate["lines"] if line["menu_name"] == "Menu B")
    assert corrected["quantity_original"] == 7.0
    with open(outputs["aggregate"], newline="", encoding="cp932", errors="replace") as output_file:
        rows = list(csv.DictReader(output_file))
    quantities = {_resolve_menu_name(row): _resolve_quantity(row) for row in rows}
    assert len(rows) == 2
    assert quantities["Menu A"] == 10.0
    assert quantities["Menu B"] == 7.0
    assert "Menu C" not in quantities
    assert "Menu D" not in quantities
    order_service.update_lines(order["id"], [{
        "line_id": "L-correction", "date": "2025-12-23", "daypart": "朝", "menu_name": "Correction retention",
        "diet_type": "regular", "area_id": "X", "bag_type": "standard", "quantity_original": 10, "quantity_corrected": 7,
    }])
    saved = order_service.get_order_by_id(order["id"])
    assert saved["lines"][0]["quantity_corrected"] == 7


def test_line_corrections_blocks_output_when_canonical_saved_sheet_is_missing(tmp_path, monkeypatch):
    order_service.clear_all()
    order = order_service.create_order_from_ingest(
        IngestEmailPayload("m1-missing", "file://dummy.pdf", datetime(2025, 12, 23, 10), "FAC00001", "WEK2025W52"), lines=None
    )
    monkeypatch.setattr(output_builder, "OUTPUT_DIR", tmp_path)
    with pytest.raises(ValueError, match="saved sheet is missing"):
        output_builder.build_outputs(order["id"])
    assert list(tmp_path.iterdir()) == []


def test_line_corrections_blocks_when_explicit_template_version_is_inactive():
    order_service.clear_all()
    order = _save_canonical_sheet(message_id="m1-version-missing", rows=[["12/23", "朝", "Menu A", "10", "", ""]])
    with session_scope() as session:
        version = session.get(FacilityTemplateVersion, order["template_version_id"])
        assert version is not None
        version.status = "archived"
    try:
        _, error = order_workflow_v2_service.save_sheet(
            order_id=order["id"],
            sheet={"fields": FIELDS, "header": HEADER, "rows": [["12/23", "朝", "Menu A", "10", "", ""]], "row_ids": ["row-0"]},
        )
        assert error == "template_version_mismatch"
    finally:
        with session_scope() as session:
            version = session.get(FacilityTemplateVersion, order["template_version_id"])
            assert version is not None
            version.status = "active"
