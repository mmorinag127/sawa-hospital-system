from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from openpyxl import load_workbook
from openpyxl.cell.cell import MergedCell
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from src.services import config_service


DATA_DIR = Path(__file__).resolve().parents[1] / "data"
MASTER_TEMPLATE_PATH = DATA_DIR / "order_form_master_templates" / "master_layout_template.xlsx"
MASTER_SHEET_NAME = "master_layout"
GENERATED_START_COL = 5  # E
MASTER_GENERATED_END_COL = 14  # N
HEADER_TOP_ROW = 7
HEADER_BOTTOM_ROW = 8
BODY_START_ROW = 9
BODY_END_ROW = 64


class FacilityTemplateBuildError(ValueError):
    pass


def _style_side(style: str = "thin") -> Side:
    return Side(style=style, color="000000")


def _set_border(cell, *, left=None, right=None, top=None, bottom=None) -> None:
    current = cell.border
    cell.border = Border(
        left=left or current.left,
        right=right or current.right,
        top=top or current.top,
        bottom=bottom or current.bottom,
    )


def _column_label(column: dict[str, Any]) -> str:
    for key in ("header", "display_name", "label", "name"):
        value = str(column.get(key) or "").strip()
        if value:
            return value
    role = str(column.get("role") or "").strip()
    return role or "列"


def _column_name(column: dict[str, Any], fallback_index: int) -> str:
    value = str(column.get("name") or "").strip()
    if value:
        return value
    role = str(column.get("role") or "").strip() or "column"
    return f"{role}_{fallback_index}"


def _normalise_columns(columns: Any) -> list[dict[str, Any]]:
    if not isinstance(columns, list):
        return []
    normalised: list[dict[str, Any]] = []
    for fallback_index, raw in enumerate(columns):
        if not isinstance(raw, dict):
            continue
        column = deepcopy(raw)
        column["index"] = int(column.get("index") if column.get("index") is not None else fallback_index)
        column["role"] = str(column.get("role") or "").strip()
        column["header"] = _column_label(column)
        column["name"] = _column_name(column, fallback_index)
        normalised.append(column)
    return sorted(normalised, key=lambda item: int(item.get("index") or 0))


def _columns_from_facility_config(facility_config: dict[str, Any]) -> list[dict[str, Any]]:
    fax_template = facility_config.get("fax_template") if isinstance(facility_config, dict) else None
    if isinstance(fax_template, dict) and isinstance(fax_template.get("columns"), list):
        return _normalise_columns(fax_template.get("columns"))
    override = facility_config.get("fax_template_override") if isinstance(facility_config, dict) else None
    if isinstance(override, dict) and isinstance(override.get("columns"), list):
        return _normalise_columns(override.get("columns"))
    return []


def _generated_columns(columns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for column in columns:
        role = str(column.get("role") or "").strip()
        if role in {"date", "daypart", "menu_name", "menu", "aux"}:
            continue
        if role in {"quantity", "note", "remarks"} or str(column.get("name") or "").strip() == "remarks":
            result.append(column)
    return result


def _unmerge_generated_header(ws: Worksheet, end_col: int) -> None:
    for merged_range in list(ws.merged_cells.ranges):
        min_col, min_row, max_col, max_row = merged_range.bounds
        if max_row < HEADER_TOP_ROW or min_row > HEADER_BOTTOM_ROW:
            continue
        if max_col < GENERATED_START_COL or min_col > end_col:
            continue
        ws.unmerge_cells(str(merged_range))


def _ensure_generated_capacity(ws: Worksheet, required_count: int) -> int:
    if required_count < 1:
        raise FacilityTemplateBuildError("facility_template_generated_columns_missing")
    capacity = MASTER_GENERATED_END_COL - GENERATED_START_COL + 1
    if required_count > capacity:
        ws.insert_cols(MASTER_GENERATED_END_COL + 1, required_count - capacity)
    return GENERATED_START_COL + required_count - 1


def _trim_unused_generated_columns(ws: Worksheet, end_col: int) -> None:
    if end_col < MASTER_GENERATED_END_COL:
        ws.delete_cols(end_col + 1, MASTER_GENERATED_END_COL - end_col)


def _clear_generated_area(ws: Worksheet, end_col: int) -> None:
    for row in range(HEADER_TOP_ROW, BODY_END_ROW + 1):
        for col in range(GENERATED_START_COL, end_col + 1):
            cell = ws.cell(row, col)
            if isinstance(cell, MergedCell):
                continue
            if row in (HEADER_TOP_ROW, HEADER_BOTTOM_ROW):
                cell.value = None
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)


def _write_header(ws: Worksheet, generated_columns: list[dict[str, Any]], end_col: int) -> None:
    header_fill = PatternFill(fill_type="solid", start_color="F2F2F2", end_color="F2F2F2")
    thick = _style_side("thick")
    thin = _style_side("thin")

    col_by_offset = {
        offset: GENERATED_START_COL + offset
        for offset, _column in enumerate(generated_columns)
    }
    offset = 0
    while offset < len(generated_columns):
        column = generated_columns[offset]
        group = str(column.get("header_group") or "").strip()
        group_end = offset
        if group:
            while (
                group_end + 1 < len(generated_columns)
                and str(generated_columns[group_end + 1].get("header_group") or "").strip() == group
            ):
                group_end += 1

        if group and group_end > offset:
            start_col = col_by_offset[offset]
            stop_col = col_by_offset[group_end]
            ws.merge_cells(start_row=HEADER_TOP_ROW, start_column=start_col, end_row=HEADER_TOP_ROW, end_column=stop_col)
            ws.cell(HEADER_TOP_ROW, start_col).value = group
            for inner_offset in range(offset, group_end + 1):
                ws.cell(HEADER_BOTTOM_ROW, col_by_offset[inner_offset]).value = _column_label(generated_columns[inner_offset])
        else:
            col = col_by_offset[offset]
            ws.merge_cells(start_row=HEADER_TOP_ROW, start_column=col, end_row=HEADER_BOTTOM_ROW, end_column=col)
            ws.cell(HEADER_TOP_ROW, col).value = _column_label(column)
        offset = group_end + 1

    for row in range(HEADER_TOP_ROW, HEADER_BOTTOM_ROW + 1):
        for col in range(GENERATED_START_COL, end_col + 1):
            cell = ws.cell(row, col)
            cell.fill = header_fill
            cell.font = Font(name="Yu Gothic", bold=True, size=10)
            _set_border(cell, left=thin, right=thin, top=thin, bottom=thin)

    for col in range(GENERATED_START_COL, end_col + 1):
        _set_border(ws.cell(HEADER_TOP_ROW, col), top=thick)
        _set_border(ws.cell(HEADER_BOTTOM_ROW, col), bottom=thick)
    for row in range(HEADER_TOP_ROW, HEADER_BOTTOM_ROW + 1):
        _set_border(ws.cell(row, GENERATED_START_COL), left=thick)
        _set_border(ws.cell(row, end_col), right=thick)


def _write_body_grid(ws: Worksheet, end_col: int) -> None:
    thick = _style_side("thick")
    thin = _style_side("thin")
    for row in range(BODY_START_ROW, BODY_END_ROW + 1):
        for col in range(GENERATED_START_COL, end_col + 1):
            cell = ws.cell(row, col)
            _set_border(cell, left=thin, right=thin)
        _set_border(ws.cell(row, GENERATED_START_COL), left=thick)
        _set_border(ws.cell(row, end_col), right=thick)
    for col in range(GENERATED_START_COL, end_col + 1):
        _set_border(ws.cell(BODY_END_ROW, col), bottom=thick)


def _set_generated_widths(ws: Worksheet, generated_columns: list[dict[str, Any]], end_col: int) -> None:
    note_col = None
    for offset, column in enumerate(generated_columns):
        role = str(column.get("role") or "").strip()
        name = str(column.get("name") or "").strip()
        if role in {"note", "remarks"} or name == "remarks":
            note_col = GENERATED_START_COL + offset
            break

    base_width = 10.0
    if note_col is not None and len(generated_columns) > 1:
        base_width = 8.8
    for col in range(GENERATED_START_COL, end_col + 1):
        ws.column_dimensions[get_column_letter(col)].width = 14.0 if col == note_col else base_width


def _append_schema_sheet(wb, *, facility_config: dict[str, Any], generated_columns: list[dict[str, Any]], end_col: int) -> None:
    if "generated_template_schema" in wb.sheetnames:
        del wb["generated_template_schema"]
    schema = wb.create_sheet("generated_template_schema")
    schema.sheet_state = "hidden"
    rows = [
        ["key", "value"],
        ["generated_at_utc", datetime.now(UTC).isoformat()],
        ["source", "master_layout_template"],
        ["facility_id", str(facility_config.get("facility_id") or facility_config.get("id") or "")],
        ["facility_name", str(facility_config.get("facility_name") or facility_config.get("name") or "")],
        ["generated_start_col", GENERATED_START_COL],
        ["generated_end_col", end_col],
        ["generated_column_count", len(generated_columns)],
    ]
    for row in rows:
        schema.append(row)
    schema.append([])
    schema.append(["index", "role", "header", "header_group", "name", "diet_type", "area_id", "source_index"])
    for offset, column in enumerate(generated_columns):
        schema.append(
            [
                offset,
                column.get("role"),
                column.get("header"),
                column.get("header_group"),
                column.get("name"),
                column.get("diet_type"),
                column.get("area_id"),
                column.get("source_index"),
            ]
        )


def build_facility_template_workbook(
    *,
    facility_config: dict[str, Any],
    master_template_path: Path | str = MASTER_TEMPLATE_PATH,
):
    master_path = Path(master_template_path)
    if not master_path.exists():
        raise FacilityTemplateBuildError(f"master_template_not_found:{master_path}")
    columns = _columns_from_facility_config(facility_config)
    generated_columns = _generated_columns(columns)
    if not generated_columns:
        raise FacilityTemplateBuildError("facility_template_generated_columns_missing")

    wb = load_workbook(master_path)
    if MASTER_SHEET_NAME not in wb.sheetnames:
        raise FacilityTemplateBuildError(f"master_sheet_not_found:{MASTER_SHEET_NAME}")
    ws = wb[MASTER_SHEET_NAME]
    ws.title = "facility_template"

    facility_name = str(facility_config.get("facility_name") or facility_config.get("name") or "").strip()
    if facility_name:
        ws["A4"].value = facility_name

    end_col = _ensure_generated_capacity(ws, len(generated_columns))
    _unmerge_generated_header(ws, end_col)
    _trim_unused_generated_columns(ws, end_col)
    _clear_generated_area(ws, end_col)
    _write_header(ws, generated_columns, end_col)
    _write_body_grid(ws, end_col)
    _set_generated_widths(ws, generated_columns, end_col)
    ws.print_area = f"A1:{get_column_letter(end_col)}64"
    _append_schema_sheet(wb, facility_config=facility_config, generated_columns=generated_columns, end_col=end_col)
    return wb


def build_facility_template_xlsx(
    *,
    facility_config: dict[str, Any],
    output_path: Path | str,
    master_template_path: Path | str = MASTER_TEMPLATE_PATH,
) -> Path:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    wb = build_facility_template_workbook(
        facility_config=facility_config,
        master_template_path=master_template_path,
    )
    wb.save(output)
    return output


def build_facility_template_xlsx_for_facility(
    *,
    facility_id: str,
    output_path: Path | str,
    master_template_path: Path | str = MASTER_TEMPLATE_PATH,
) -> Path:
    facility_config = config_service.get_facility_config(facility_id)
    if not facility_config:
        raise FacilityTemplateBuildError(f"facility_not_found:{facility_id}")
    return build_facility_template_xlsx(
        facility_config=facility_config,
        output_path=output_path,
        master_template_path=master_template_path,
    )
