from modernization_input_support import registration_environment, authored_ingest_master, register_ingest, seed_saved_evidence, sheet_ingest_payload  # noqa: F401
from modernization_input_support import seed_sheet_monthly_menu

import csv
import pathlib
import re
import sys
from datetime import datetime

import pytest

pytestmark = pytest.mark.usefixtures("authored_ingest_master")

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.services import order_service, order_workflow_v2_service, output_builder  # noqa: E402
from src.services import facility_service  # noqa: E402
from src.services import config_service  # noqa: E402
from src.db import session_scope  # noqa: E402
from src.models.facility_template_version import FacilityTemplateVersion  # noqa: E402
from src.workers.ingest_mail_adapter import IngestEmailPayload  # noqa: E402

FIELDS = ["date_mmdd", "daypart", "menu", "qty.regular_x", "qty.change_1_x", "qty.change_2_x"]
HEADER = ["日付", "区分", "メニュー", "常食", "変更1", "変更2"]


def _authored_sheet(rows):
    template = config_service.get_facility_config("FAC00001")["fax_template"]
    fields = template["main_ocr_row_fields"]
    assert set(FIELDS) <= set(fields)
    expanded = []
    for row in rows:
        values = dict(zip(FIELDS, row, strict=True))
        next_row = [values[field] if field in values else "" for field in fields]
        assert [next_row[fields.index(field)] for field in FIELDS] == row
        expanded.append(next_row)
    headers = dict(zip(FIELDS, HEADER, strict=True))
    headers.update({column["name"]: column["header"] for column in template["columns"] if column["name"] not in headers})
    return {"fields": fields, "header": [headers[field] for field in fields], "rows": expanded,
            "row_ids": [f"row-{index}" for index in range(len(rows))]}


def _save_canonical_sheet(*, message_id: str, rows: list[list[str]]):
    seed_sheet_monthly_menu(rows)
    with session_scope() as session:
        assert facility_service.ensure_facility_materialized(session, "FAC00001") is not None
    order = register_ingest(
        sheet_ingest_payload(message_id, rows),
        lines=None,
    )
    current, error = order_workflow_v2_service.get_workflow_readonly(order["id"])
    assert error is None
    workflow, error = order_workflow_v2_service.confirm_context(
        order_id=order["id"], facility_id="FAC00001", week_start="2025-12-22", week_end="2025-12-28", template_id="大和なでしこ", expected_revision=current["revision"]
    )
    assert error is None
    evidence = seed_saved_evidence(
        order["id"],
        workflow["template_version_id"],
        {
            "status": "done",
            "pages": [{"page_index": 1, "ocr_overlay_uri": "file://ocr.png", "layout_overlay_uri": "file://layout.png"}],
            "rows": [dict(zip(FIELDS, row, strict=True)) for row in rows],
            "template_resolution": {"resolved_template_id": "大和なでしこ", "blocked": False, "blocked_reasons": []},
            "table_box": [0.1, 0.2, 0.9, 0.8], "grid_column_edges": [0.1, 0.2, 0.4, 0.6, 0.75, 0.9], "grid_row_edges": [0.2, 0.4, 0.8],
        },
        producer_version="c2-ci-contract", source="c2-ci-contract",
    )
    assert evidence is not None
    selected, error = order_workflow_v2_service.select_ocr_result(order["id"], evidence["id"], expected_revision=workflow["revision"])
    assert error is None
    saved, error = order_workflow_v2_service.save_sheet(
        order_id=order["id"],
        sheet=_authored_sheet(rows),
        edited_by="c2-ci-contract",
        expected_revision=selected["revision"],
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
    current, error = order_workflow_v2_service.get_workflow_readonly(order["id"])
    assert error is None
    _, error = order_workflow_v2_service.run_bagging(order["id"], expected_revision=current["revision"])
    assert error is None, error
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
    seed_sheet_monthly_menu([["12/23", "朝", "Correction retention"]], slot_start=4)
    order_service.update_lines(order["id"], [{
        "line_id": "L-correction", "date": "2025-12-23", "daypart": "朝", "menu_name": "Correction retention",
        "diet_type": "regular", "area_id": "X", "bag_type": "standard", "quantity_original": 10, "quantity_corrected": 7,
    }])
    saved = order_service.get_order_by_id(order["id"])
    assert saved["lines"][0]["quantity_corrected"] == 7


def test_line_corrections_blocks_output_when_canonical_saved_sheet_is_missing(tmp_path, monkeypatch):
    order_service.clear_all()
    order = register_ingest(
        IngestEmailPayload("m1-missing", "file://dummy.pdf", datetime(2025, 12, 23, 10), "FAC00001", "2025-12@2025-12-22~2025-12-28"), lines=None
    )
    monkeypatch.setattr(output_builder, "OUTPUT_DIR", tmp_path)
    before = {path.name for path in tmp_path.iterdir()}
    with pytest.raises(ValueError, match="output original ownership input missing"):
        output_builder.build_outputs(order["id"])
    assert {path.name for path in tmp_path.iterdir()} == before


def test_line_corrections_blocks_when_explicit_template_version_is_inactive():
    order_service.clear_all()
    order = _save_canonical_sheet(message_id="m1-version-missing", rows=[["12/23", "朝", "Menu A", "10", "", ""]])
    current, error = order_workflow_v2_service.get_workflow_readonly(order["id"])
    assert error is None
    with session_scope() as session:
        version = session.get(FacilityTemplateVersion, order["template_version_id"])
        assert version is not None
        archived_version_id = version.id
        original_status = version.status
        version.status = "archived"
    try:
        saved, error = order_workflow_v2_service.save_sheet(
            order_id=order["id"],
            sheet=_authored_sheet([["12/23", "朝", "Menu A", "10", "", ""]]),
            expected_revision=current["revision"],
        )
        assert error is None
        assert saved["saved_sheet"]["template_version_id"] == archived_version_id
    finally:
        with session_scope() as session:
            version = session.get(FacilityTemplateVersion, order["template_version_id"])
            assert version is not None
            version.status = original_status
