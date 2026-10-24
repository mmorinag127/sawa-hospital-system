from src.services import hakodate_assignment_service
from src.services.hakodate_assignment_service import build_hakodate_assignment
from openpyxl import Workbook
from pathlib import Path
from PIL import Image
import pytest


def _template() -> dict:
    template = {
        "quantity_assignment_strategy": "hakodate",
        "hakodate_header_rows": 1,
        "hakodate_data_row_count": 2,
        "main_ocr_row_fields": [
            "date",
            "daypart",
            "menu_name",
            "qty.regular_x",
            "qty.no_meat_x",
        ],
    }
    signature = hakodate_assignment_service.build_facility_template_signature(template)
    template["hakodate_template_signature"] = signature["hash"]
    template["hakodate_template_signature_components"] = signature["components"]
    return template


def _grid() -> dict:
    return {
        "table_box": [0.0, 0.0, 1.0, 1.0],
        "column_edges": [0.0, 0.2, 0.4, 0.6, 0.8, 1.0],
        "row_edges": [0.0, 0.2, 0.4, 0.6],
        "confidence": 0.9,
    }


def _legacy_grid_pdf_template() -> dict:
    template = _template()
    template["hakodate_alignment_mode"] = "detected_grid"
    return template


def _structure_slot_template(skeleton_rows: list[dict] | None = None) -> dict:
    template = {
        "facility_id": "FAC_TEST",
        "quantity_assignment_strategy": "hakodate",
        "hakodate_alignment_mode": "structure_slot",
        "hakodate_header_rows": 2,
        "main_ocr_row_fields": [
            "date",
            "daypart",
            "aux",
            "menu_name",
            "qty.regular_x",
        ],
    }
    signature = hakodate_assignment_service.build_facility_template_signature(
        template,
        facility_id="FAC_TEST",
        skeleton_rows=skeleton_rows or [{"row_id": "row-a", "menu_name": "献立A"}],
    )
    template["hakodate_template_signature"] = signature["hash"]
    template["hakodate_template_signature_components"] = signature["components"]
    return template


def _structure_slot_worksheet(*, names=("献立A",), merged=False):
    from datetime import date
    from src.services import master_order_form_template_service as master

    template = {"columns": [
        {"index": 0, "role": "date"}, {"index": 1, "role": "daypart"},
        {"index": 3, "role": "menu_name", "header": "献立"},
        {"index": 4, "role": "quantity", "diet_type": "regular", "area_id": "X",
         "header": "常食", "name": "qty.regular_x"},
    ]}
    if merged:
        template["body_merge_policy"] = {"mode": "daypart", "columns": ["qty.regular_x"], "required": True}
    workbook = master.build_facility_template_workbook(
        facility_config={"facility_id": "FAC_TEST", "fax_template": template},
        week_value="2026-04@2026-04-26~2026-04-26",
        week_menu_entries=[{"menu_date": date(2026, 4, 26), "daypart": "朝", "name": name,
                            "slot_index": index, "diet_type": "regular"}
                           for index, name in enumerate(names)],
    )
    return workbook[master.FACILITY_TEMPLATE_SHEET_NAME]


def _merged_structure_slot_worksheet():
    return _structure_slot_worksheet(names=("献立A", "献立B"), merged=True)


def test_template_slot_label_includes_header_super_group_for_area_quantity_columns() -> None:
    label = hakodate_assignment_service._template_slot_label(  # noqa: SLF001
        {
            "role": "quantity",
            "header_super_group": "禁食、魚",
            "header_group": "常食",
            "header": "花",
            "name": "qty.no_fish_regular_2f",
        },
        "qty.no_fish_regular_2f",
    )

    assert label == "禁食、魚 常食花"


def test_structure_source_cannot_recapture_current_facility_when_captured_workbook_is_missing(monkeypatch) -> None:
    from src.services import config_service

    monkeypatch.setattr(config_service, "get_facility_config",
        lambda *_args, **_kwargs: pytest.fail("current facility must not replace the captured workbook"))
    with pytest.raises(ValueError, match="^hakodate_captured_structure_required$"):
        hakodate_assignment_service._source_worksheet_for_structure_template(
            facility_id="FAC00007", week_sheet_name="4月26日～4月30日")
    with pytest.raises(ValueError, match="^hakodate_captured_structure_required$"):
        hakodate_assignment_service._worksheet_for_manifest_structure_template(
            item={}, facility_id="FAC00007", week_sheet_name="4月26日～4月30日")


def test_manifest_uses_declared_generated_workbook_without_recapturing_facility(monkeypatch, tmp_path):
    from copy import deepcopy
    from src.services import config_service, master_order_form_template_service as master

    worksheet = _merged_structure_slot_worksheet()
    path = tmp_path / "captured-structure.xlsx"
    worksheet.parent.save(path)
    item = {"template_xlsx": str(path), "template_sheet_name": worksheet.title}
    before = deepcopy(item)
    original = path.read_bytes()
    monkeypatch.setattr(config_service, "get_facility_config",
        lambda *_args, **_kwargs: pytest.fail("captured workbook must not recapture current facility"))
    selected = hakodate_assignment_service._worksheet_for_manifest_structure_template(
        item=item, facility_id="FAC_TEST", week_sheet_name="not-a-selected-sheet")
    assert selected["D11"].value == "献立A"
    assert selected["D12"].value == "献立B"
    assert "E11:E12" in {str(region) for region in selected.merged_cells.ranges}
    assert len(master.validated_body_row_plan(selected)) == 2
    assert item == before and path.read_bytes() == original
    selected.parent.close()
    worksheet.parent.close()


def test_merge_aware_grid_segments_suppress_internal_merged_quantity_edge() -> None:
    worksheet = _merged_structure_slot_worksheet()
    row_edges = [0.0, 0.2, 0.4, 0.6, 0.8]
    column_edges = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]

    regions = hakodate_assignment_service._worksheet_merge_regions_for_grid(  # noqa: SLF001
        worksheet,
        row_edges=row_edges,
        column_edges=column_edges,
        quantity_columns={5},
    )
    regions = [region for region in regions if region["is_quantity"] and region["min_row"] >= 11]
    segments = hakodate_assignment_service._merge_aware_grid_line_segments(  # noqa: SLF001
        row_edges=row_edges,
        column_edges=column_edges,
        merge_regions=regions,
    )

    assert regions == [
        {
            "range": "E11:E12",
            "min_col": 5,
            "min_row": 11,
            "max_col": 5,
            "max_row": 12,
            "row_span": 2,
            "col_span": 1,
            "start_col_index": 4,
            "end_col_index": 4,
            "start_row_index": 2,
            "end_row_index": 3,
            "bbox": [0.8, 0.4, 1.0, 0.8],
            "is_quantity": True,
        }
    ]
    assert {
        (
            item["orientation"],
            tuple(round(value, 3) for value in item["start"]),
            tuple(round(value, 3) for value in item["end"]),
        )
        for item in segments
        if item["orientation"] == "horizontal" and round(item["start"][1], 3) == 0.6
    } == {("horizontal", (0.0, 0.6), (0.8, 0.6))}
    assert any(
        item["orientation"] == "vertical"
        and round(item["start"][0], 3) == 0.8
        and item["start"][1] <= 0.4
        and item["end"][1] >= 0.8
        for item in segments
    )


def test_hakodate_assignment_places_numeric_token_in_quantity_cell() -> None:
    result = build_hakodate_assignment(
        tokens=[
            {"text": "12", "x": 0.7, "y": 0.3, "bbox": [0.68, 0.28, 0.72, 0.32]},
            {"text": "99", "x": 0.5, "y": 0.3, "bbox": [0.48, 0.28, 0.52, 0.32]},
        ],
        grid=_grid(),
        template=_template(),
        skeleton_rows=[
            {"row_id": "row-a", "date": "2026-04-26", "daypart": "朝", "menu_name": "献立A"},
        ],
    )

    assert result["status"] == "auto_assignable"
    assert result["metrics"]["assigned_count"] == 1
    assert result["assignments"][0]["row_id"] == "row-a"
    assert result["assignments"][0]["field"] == "qty.regular_x"
    assert result["assignments"][0]["value_normalized"] == "12"
    assert result["rejected_candidates"][0]["reason"] == "non_quantity_cell"


def test_hakodate_assignment_reviews_boundary_and_duplicate_candidates() -> None:
    result = build_hakodate_assignment(
        tokens=[
            {"text": "10", "x": 0.605, "y": 0.3, "bbox": [0.60, 0.28, 0.61, 0.32]},
            {"text": "11", "x": 0.7, "y": 0.5, "bbox": [0.68, 0.48, 0.72, 0.52]},
            {"text": "12", "x": 0.72, "y": 0.5, "bbox": [0.70, 0.48, 0.74, 0.52]},
        ],
        grid=_grid(),
        template=_template(),
        skeleton_rows=[{"row_id": "row-a"}, {"row_id": "row-b"}],
    )

    reasons = {item.get("reason") for item in result["review_candidates"]}
    assert result["status"] == "review_required"
    assert "near_cell_boundary" in reasons
    assert "duplicate_quantity_candidates" in reasons
    assert result["metrics"]["review_count"] == 3


def test_hakodate_assignment_blocks_when_strategy_is_legacy() -> None:
    template = _template()
    template["quantity_assignment_strategy"] = "legacy"

    result = build_hakodate_assignment(
        tokens=[{"text": "12", "x": 0.7, "y": 0.3, "bbox": [0.68, 0.28, 0.72, 0.32]}],
        grid=_grid(),
        template=template,
    )

    assert result["status"] == "blocked"
    assert "hakodate_strategy_not_enabled" in result["blockers"]


def test_hakodate_assignment_blocks_when_template_signature_is_missing() -> None:
    template = _template()
    template.pop("hakodate_template_signature", None)
    template.pop("hakodate_template_signature_components", None)

    result = build_hakodate_assignment(
        tokens=[{"text": "12", "x": 0.7, "y": 0.3, "bbox": [0.68, 0.28, 0.72, 0.32]}],
        grid=_grid(),
        template=template,
    )

    assert result["status"] == "blocked"
    assert "hakodate_template_signature_missing" in result["blockers"]


def test_hakodate_assignment_blocks_when_template_signature_is_stale() -> None:
    template = _template()
    template["main_ocr_row_fields"].append("qty.mixer_x")

    result = build_hakodate_assignment(
        tokens=[{"text": "12", "x": 0.7, "y": 0.3, "bbox": [0.68, 0.28, 0.72, 0.32]}],
        grid=_grid(),
        template=template,
    )

    assert result["status"] == "blocked"
    assert "template_stale_due_to_facility_category_change" in result["blockers"]


def test_hakodate_template_regeneration_candidate_summarizes_quantity_diff() -> None:
    template = _template()
    template["main_ocr_row_fields"].append("qty.mixer_x")

    candidate = hakodate_assignment_service.build_facility_template_regeneration_candidate(template)

    assert candidate["status"] == "candidate_ready"
    assert candidate["reason"] == "template_stale_due_to_facility_category_change"
    assert candidate["diff"]["quantity_fields_added"] == ["qty.mixer_x"]
    assert candidate["candidate_template"]["hakodate_template_signature"] == candidate["candidate_signature"]


def test_hakodate_assignment_from_pdf_blocks_detected_grid_mode_before_token_ocr(monkeypatch) -> None:
    monkeypatch.setattr(
        hakodate_assignment_service,
        "detect_table_grid",
        lambda _pdf_bytes, _template: (_ for _ in ()).throw(AssertionError("detected grid mode must not run")),
    )

    def raise_unavailable(_pdf_bytes, _template):
        raise AssertionError("detected grid mode must not run token OCR")

    monkeypatch.setattr(
        hakodate_assignment_service,
        "_extract_tesseract_tokens",
        raise_unavailable,
    )

    result = hakodate_assignment_service.build_hakodate_assignment_from_pdf(
        pdf_bytes=b"%PDF-1.4",
        template=_legacy_grid_pdf_template(),
        strategy="hakodate",
    )

    assert result["status"] == "blocked"
    assert "hakodate_structure_slot_required" in result["blockers"]
    assert "hakodate_detected_grid_token_assignment_disabled" in result["warnings"]


def test_hakodate_assignment_from_pdf_blocks_detected_grid_template_fallback_path(monkeypatch) -> None:
    monkeypatch.setattr(
        hakodate_assignment_service,
        "detect_table_grid",
        lambda _pdf_bytes, _template: None,
    )
    monkeypatch.setattr(
        hakodate_assignment_service,
        "_extract_tesseract_tokens",
        lambda _pdf_bytes, _template: [
            {"text": "12", "x": 0.7, "y": 0.5, "bbox": [0.68, 0.48, 0.72, 0.52]},
        ],
    )
    template = _legacy_grid_pdf_template()
    template["table_box"] = [0.0, 0.0, 1.0, 1.0]

    result = hakodate_assignment_service.build_hakodate_assignment_from_pdf(
        pdf_bytes=b"%PDF-1.4",
        template=template,
        strategy="hakodate",
        skeleton_rows=[{"row_id": "row-a"}, {"row_id": "row-b"}],
    )

    assert result["status"] == "blocked"
    assert result["assignments"] == []
    assert result["review_candidates"] == []
    assert "hakodate_structure_slot_required" in result["blockers"]
    assert "hakodate_detected_grid_token_assignment_disabled" in result["warnings"]


def test_hakodate_assignment_from_pdf_blocks_detected_grid_unmatched_edges_path(monkeypatch) -> None:
    monkeypatch.setattr(
        hakodate_assignment_service,
        "detect_table_grid",
        lambda _pdf_bytes, _template: {
            "table_box": [0.0, 0.0, 1.0, 1.0],
            "column_edges": [0.0, 0.5, 1.0],
            "row_edges": [0.0, 0.5, 1.0],
            "confidence": 0.8,
        },
    )
    monkeypatch.setattr(
        hakodate_assignment_service,
        "_extract_tesseract_tokens",
        lambda _pdf_bytes, _template: [
            {"text": "12", "x": 0.7, "y": 0.5, "bbox": [0.68, 0.48, 0.72, 0.52]},
        ],
    )

    result = hakodate_assignment_service.build_hakodate_assignment_from_pdf(
        pdf_bytes=b"%PDF-1.4",
        template=_legacy_grid_pdf_template(),
        strategy="hakodate",
        skeleton_rows=[{"row_id": "row-a"}, {"row_id": "row-b"}],
    )

    assert result["status"] == "blocked"
    assert result["assignments"] == []
    assert result["review_candidates"] == []
    assert "hakodate_structure_slot_required" in result["blockers"]


def test_hakodate_assignment_from_pdf_blocks_detected_grid_template_matched_edges_path(monkeypatch) -> None:
    monkeypatch.setattr(
        hakodate_assignment_service,
        "detect_table_grid",
        lambda _pdf_bytes, _template: {
            "table_box": [0.0, 0.0, 1.0, 1.0],
            "column_edges": [0.0, 0.2, 0.4, 0.6, 0.61, 0.8, 1.0],
            "row_edges": [0.0, 0.33, 0.66, 0.67, 1.0],
            "confidence": 0.8,
        },
    )
    monkeypatch.setattr(
        hakodate_assignment_service,
        "_extract_tesseract_tokens",
        lambda _pdf_bytes, _template: [
            {"text": "12", "x": 0.7, "y": 0.5, "bbox": [0.68, 0.48, 0.72, 0.52]},
        ],
    )

    result = hakodate_assignment_service.build_hakodate_assignment_from_pdf(
        pdf_bytes=b"%PDF-1.4",
        template=_legacy_grid_pdf_template(),
        strategy="hakodate",
        skeleton_rows=[{"row_id": "row-a"}, {"row_id": "row-b"}],
    )

    assert result["status"] == "blocked"
    assert result["assignments"] == []
    assert "hakodate_structure_slot_required" in result["blockers"]


def test_structure_slot_assignment_ocr_each_quantity_cell_without_token_ocr(monkeypatch) -> None:
    skeleton_rows = [{"row_id": "row-a", "date": "2026-04-26", "daypart": "朝", "menu_name": "献立A"}]
    structure_grid = {
        "table_box": [0.0, 0.0, 1.0, 1.0],
        "column_edges": [0.0, 0.2, 0.4, 0.6, 0.8, 1.0],
        "row_edges": [0.0, 0.2, 0.4, 0.6],
        "confidence": 1.0,
        "column_edges_source": "structure_workbook_geometry",
        "row_edges_source": "structure_workbook_geometry",
    }
    monkeypatch.setattr(
        hakodate_assignment_service,
        "_structure_grid_for_facility_template",
        lambda **_kwargs: (structure_grid, _structure_slot_worksheet()),
    )
    monkeypatch.setattr(
        hakodate_assignment_service,
        "detect_table_grid",
        lambda _pdf_bytes, _template: {
            "table_box": [0.1, 0.1, 0.9, 0.7],
            "column_edges": [0.1, 0.23, 0.38, 0.56, 0.73, 0.9],
            "row_edges": [0.1, 0.24, 0.45, 0.7],
            "confidence": 0.86,
        },
    )

    def fail_token_ocr(_pdf_bytes, _template):
        raise AssertionError("structure_slot must not use whole-table token OCR")

    monkeypatch.setattr(hakodate_assignment_service, "_extract_tesseract_tokens", fail_token_ocr)
    monkeypatch.setattr(
        hakodate_assignment_service,
        "_render_pdf_page_image",
        lambda _pdf_bytes, _template: Image.new("RGB", (1000, 1000), "white"),
    )

    seen_cell_boxes = []

    def fake_cell_ocr(**kwargs):
        seen_cell_boxes.append(kwargs["fax_cell_bbox"])
        return hakodate_assignment_service.HakodateCellOcrResult(text="12", normalized="12")

    monkeypatch.setattr(hakodate_assignment_service, "_ocr_quantity_cell", fake_cell_ocr)

    result = hakodate_assignment_service.build_structure_slot_assignment_from_pdf(
        pdf_bytes=b"%PDF-1.4",
        template=_structure_slot_template(skeleton_rows),
        strategy="hakodate",
        skeleton_rows=skeleton_rows,
    )

    assert result["status"] == "auto_assignable"
    assert len(seen_cell_boxes) == 1
    assert result["assignments"][0]["row_id"] == "row-a"
    assert result["assignments"][0]["field"] == "qty.regular_x"
    assert result["assignments"][0]["sheet_cell"] == "E11"
    assert result["assignments"][0]["ocr_scope"] == "cell_crop"
    assert result["assignments"][0]["ocr_engine_bbox_used_for_assignment"] is False
    assert result["assignments"][0]["fax_cell_bbox"] == seen_cell_boxes[0]
    assert result["metrics"]["ocr_cell_count"] == 1
    assert result["metrics"]["merged_quantity_cell_count"] == 0
    assert result["grid"]["actual_structure_slot_grid"]["merged_quantity_cells"] == []


def test_structure_slot_assignment_uses_merged_quantity_bbox_for_each_spanned_row(monkeypatch, tmp_path) -> None:
    skeleton_rows = [
        {"row_id": "row-a", "date": "2026-04-26", "daypart": "朝", "menu_name": "献立A"},
        {"row_id": "row-b", "date": "2026-04-26", "daypart": "朝", "menu_name": "献立B"},
    ]
    structure_grid = {
        "table_box": [0.0, 0.0, 1.0, 1.0],
        "column_edges": [0.0, 0.2, 0.4, 0.6, 0.8, 1.0],
        "row_edges": [0.0, 0.2, 0.4, 0.6, 0.8],
        "confidence": 1.0,
        "column_edges_source": "structure_workbook_geometry",
        "row_edges_source": "structure_workbook_geometry",
    }
    monkeypatch.setattr(
        hakodate_assignment_service,
        "_structure_grid_for_facility_template",
        lambda **_kwargs: (structure_grid, _merged_structure_slot_worksheet()),
    )
    monkeypatch.setattr(
        hakodate_assignment_service,
        "detect_table_grid",
        lambda _pdf_bytes, _template: {
            "table_box": [0.1, 0.1, 0.9, 0.7],
            "column_edges": [0.1, 0.23, 0.38, 0.56, 0.73, 0.9],
            "row_edges": [0.1, 0.24, 0.45, 0.58, 0.7],
            "confidence": 0.86,
        },
    )
    monkeypatch.setattr(
        hakodate_assignment_service,
        "_render_pdf_page_image",
        lambda _pdf_bytes, _template: Image.new("RGB", (1000, 1000), "white"),
    )

    seen_cell_boxes = []

    def fake_cell_ocr(**kwargs):
        seen_cell_boxes.append(kwargs["fax_cell_bbox"])
        return hakodate_assignment_service.HakodateCellOcrResult(text="20", normalized="20")

    monkeypatch.setattr(hakodate_assignment_service, "_ocr_quantity_cell", fake_cell_ocr)
    template = _structure_slot_template(skeleton_rows)
    template["hakodate_debug_output_dir"] = str(tmp_path)

    result = hakodate_assignment_service.build_structure_slot_assignment_from_pdf(
        pdf_bytes=b"%PDF-1.4",
        template=template,
        strategy="hakodate",
        skeleton_rows=skeleton_rows,
    )

    assert result["status"] == "auto_assignable"
    assert [item["row_id"] for item in result["assignments"]] == ["row-a", "row-b"]
    assert [item["sheet_cell"] for item in result["assignments"]] == ["E11", "E12"]
    assert seen_cell_boxes == [[0.73, 0.45, 0.9, 0.7], [0.73, 0.45, 0.9, 0.7]]
    assert {item["value_normalized"] for item in result["assignments"]} == {"20"}
    assert result["assignments"][0]["merged_cell"]["range"] == "E11:E12"
    assert result["assignments"][1]["merged_cell"]["row_span"] == 2
    assert result["metrics"]["merged_quantity_cell_count"] == 1
    assert result["grid"]["actual_structure_slot_grid"]["merged_quantity_cells"][0]["range"] == "E11:E12"
    assert Path(result["artifacts"]["overlay_image_path"]).exists()


def test_column_slots_detect_diabetes_quantity_from_template_header() -> None:
    from src.services.master_order_form_template_service import MASTER_SHEET_NAME

    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = MASTER_SHEET_NAME
    worksheet.cell(row=7, column=1, value="日付")
    worksheet.cell(row=7, column=2, value="区分")
    worksheet.cell(row=7, column=4, value="献立")
    worksheet.cell(row=7, column=5, value="常食")
    worksheet.cell(row=7, column=6, value="糖尿")

    slots = hakodate_assignment_service._column_slots_from_worksheet(  # noqa: SLF001
        worksheet,
        col_count=6,
    )

    assert slots[4]["role"] == "quantity"
    assert slots[4]["slot_name"] == "qty.regular_x"
    assert slots[5]["role"] == "quantity"
    assert slots[5]["slot_name"] == "qty.diabetes_x"


def test_column_slots_do_not_treat_logical_template_index_as_physical_column() -> None:
    from src.services.master_order_form_template_service import MASTER_SHEET_NAME

    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = MASTER_SHEET_NAME
    worksheet.cell(row=7, column=1, value="日付")
    worksheet.cell(row=7, column=2, value="区分")
    worksheet.cell(row=7, column=3, value="区分")
    worksheet.cell(row=7, column=4, value="献立")
    worksheet.cell(row=7, column=5, value="常食")
    worksheet.cell(row=7, column=7, value="肉禁")
    worksheet.cell(row=7, column=8, value="魚禁")
    worksheet.cell(row=7, column=9, value="変更1")
    worksheet.cell(row=7, column=10, value="変更2")
    worksheet.cell(row=7, column=11, value="備考欄")
    worksheet.cell(row=7, column=12, value="備考欄")
    template = {
        "columns": [
            {"index": 0, "role": "date", "header": "日付", "name": "date_mmdd"},
            {"index": 1, "role": "daypart", "header": "区分", "name": "daypart"},
            {"index": 2, "role": "menu_name", "header": "メニュー", "name": "menu"},
            {"index": 3, "role": "quantity", "header": "常食", "name": "qty.regular_x"},
            {"index": 4, "role": "quantity", "header": "-", "name": "qty.placeholder_x"},
            {"index": 5, "role": "quantity", "header": "肉禁", "name": "qty.no_meat_x"},
            {"index": 6, "role": "quantity", "header": "魚禁", "name": "qty.no_fish_x"},
            {"index": 7, "role": "quantity", "header": "変更1", "name": "qty.change_1_x"},
            {"index": 8, "role": "quantity", "header": "変更2", "name": "qty.change_2_x"},
            {"index": 9, "role": "note", "header": "備考", "name": "remarks"},
        ],
    }

    slots = hakodate_assignment_service._column_slots_from_worksheet(  # noqa: SLF001
        worksheet,
        col_count=12,
        template=template,
    )
    by_col = {slot["worksheet_col_index"]: slot for slot in slots}

    assert by_col[4]["slot_name"] == "menu_name"
    assert by_col[5]["slot_name"] == "qty.regular_x"
    assert by_col[6]["slot_name"] == "qty.placeholder_x"
    assert by_col[7]["slot_name"] == "qty.no_meat_x"
    assert by_col[8]["slot_name"] == "qty.no_fish_x"
    assert by_col[11]["slot_name"] == "note"
    assert by_col[12]["slot_name"] == "note"


def test_structure_slot_assignment_blocks_when_detected_table_box_is_invalid(monkeypatch) -> None:
    skeleton_rows = [{"row_id": "row-a", "date": "2026-04-26", "daypart": "朝", "menu_name": "献立A"}]
    structure_grid = {
        "table_box": [0.0, 0.0, 1.0, 1.0],
        "column_edges": [0.0, 0.2, 0.4, 0.6, 0.8, 1.0],
        "row_edges": [0.0, 0.2, 0.4, 0.6],
        "confidence": 1.0,
        "column_edges_source": "structure_workbook_geometry",
        "row_edges_source": "structure_workbook_geometry",
    }
    monkeypatch.setattr(
        hakodate_assignment_service,
        "_structure_grid_for_facility_template",
        lambda **_kwargs: (structure_grid, _structure_slot_worksheet()),
    )
    monkeypatch.setattr(
        hakodate_assignment_service,
        "detect_table_grid",
        lambda _pdf_bytes, _template: {
            "table_box": [],
            "column_edges": [0.1, 0.5, 0.9],
            "row_edges": [0.1, 0.7],
            "confidence": 0.86,
        },
    )
    monkeypatch.setattr(
        hakodate_assignment_service,
        "_extract_tesseract_tokens",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("no token fallback")),
    )
    monkeypatch.setattr(
        hakodate_assignment_service,
        "_ocr_quantity_cell",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("invalid table box must block before OCR")),
    )
    monkeypatch.setattr(
        hakodate_assignment_service,
        "_render_pdf_page_image",
        lambda _pdf_bytes, _template: Image.new("RGB", (1000, 1000), "white"),
    )

    result = hakodate_assignment_service.build_structure_slot_assignment_from_pdf(
        pdf_bytes=b"%PDF-1.4",
        template=_structure_slot_template(skeleton_rows),
        strategy="hakodate",
        skeleton_rows=skeleton_rows,
    )

    assert result["status"] == "blocked"
    assert result["assignments"] == []
    assert "structure_alignment_box_missing" in result["blockers"]


@pytest.mark.parametrize("case_name,facility_id", [
    ("fac14_stg_corrected", "FAC00014"),
    ("fac14_fax000364233", "FAC00014"),
    ("fac01_degraded_document2", "FAC00001"),
    ("fac02_sibling_stg_corrected", "FAC00002"),
    ("fac07_sibling_stg_corrected", "FAC00007"),
])
def test_legacy_structure_cases_require_captured_workbook_before_reading_or_recognizing_pdf(
    monkeypatch, case_name, facility_id,
):
    from copy import deepcopy
    from src.services import config_service

    def forbidden(*_args, **_kwargs):
        pytest.fail("unowned legacy structure must stop before source reread or recognition")

    monkeypatch.setattr(config_service, "get_facility_config", forbidden)
    monkeypatch.setattr(hakodate_assignment_service, "detect_table_grid", forbidden)
    monkeypatch.setattr(hakodate_assignment_service, "_ocr_quantity_cell", forbidden)
    monkeypatch.setattr(hakodate_assignment_service, "_render_pdf_page_image", forbidden)
    skeleton_rows = [{"row_id": case_name, "date": "2026-04-26", "daypart": "朝", "menu_name": "Synthetic menu"}]
    template = _structure_slot_template(skeleton_rows)
    template["facility_id"] = facility_id
    signature = hakodate_assignment_service.build_facility_template_signature(
        template, facility_id=facility_id, skeleton_rows=skeleton_rows)
    template["hakodate_template_signature"] = signature["hash"]
    template["hakodate_template_signature_components"] = signature["components"]
    before = deepcopy((template, skeleton_rows))

    result = hakodate_assignment_service.build_structure_slot_assignment_from_pdf(
        pdf_bytes=b"", template=template, strategy="hakodate", skeleton_rows=skeleton_rows)

    assert result["status"] == "blocked"
    assert result["blockers"] == ["hakodate_captured_structure_required"]
    assert "structure_template_unavailable" in result["warnings"]
    assert result["grid"] is None
    assert result["assignments"] == []
    assert result["metrics"]["assigned_count"] == result["metrics"]["review_count"] == 0
    assert "artifacts" not in result
    assert (template, skeleton_rows) == before
