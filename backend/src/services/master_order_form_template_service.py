from __future__ import annotations

from copy import copy, deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from openpyxl import load_workbook
from openpyxl.cell.cell import MergedCell
from openpyxl.drawing.spreadsheet_drawing import OneCellAnchor
from openpyxl.drawing.xdr import XDRPositiveSize2D
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.utils.units import pixels_to_EMU
from openpyxl.worksheet.worksheet import Worksheet

from src.services import config_service


DATA_DIR = Path(__file__).resolve().parents[1] / "data"
MASTER_TEMPLATE_PATH = DATA_DIR / "order_form_master_templates" / "master_layout_template.xlsx"
MASTER_SHEET_NAME = "master_layout"
FACILITY_TEMPLATE_SHEET_NAME = "facility_template"
GENERATED_START_COL = 5  # E
MASTER_GENERATED_END_COL = 12  # L in the canonical common source workbook
HEADER_TOP_ROW = 7
HEADER_GROUP_BOTTOM_ROW = 8
HEADER_LEAF_TOP_ROW = 9
HEADER_BOTTOM_ROW = 10
BODY_START_ROW = 11
BODY_END_ROW = 66
PRINT_END_ROW = 69
FACILITY_NAME_RANGE = "A4:E5"
SOURCE_GENERATED_PIXEL_PROFILE = (172, 144, 224, 144, 172, 144, 141, 144)
SOURCE_GENERATED_PIXEL_WIDTH = sum(SOURCE_GENERATED_PIXEL_PROFILE)
DEFAULT_DPI = 144
EMU_PER_PIXEL = 9525


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


def _copy_side(side) -> Side | None:
    if side is None or not getattr(side, "style", None):
        return None
    return copy(side)


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


def _unmerge_generated_area(ws: Worksheet, end_col: int) -> None:
    for merged_range in list(ws.merged_cells.ranges):
        min_col, min_row, max_col, max_row = merged_range.bounds
        if max_row < HEADER_TOP_ROW or min_row > BODY_END_ROW:
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
    if end_col >= MASTER_GENERATED_END_COL:
        return
    ws.delete_cols(end_col + 1, MASTER_GENERATED_END_COL - end_col)


def _normalize_static_right_merges(ws: Worksheet, end_col: int) -> None:
    for row in range(2, 7):
        for merged_range in list(ws.merged_cells.ranges):
            min_col, min_row, max_col, max_row = merged_range.bounds
            if min_row == row and max_row == row and min_col == 7 and max_col == MASTER_GENERATED_END_COL:
                value = ws.cell(row, min_col).value
                alignment = copy(ws.cell(row, min_col).alignment)
                font = copy(ws.cell(row, min_col).font)
                ws.unmerge_cells(str(merged_range))
                ws.merge_cells(start_row=row, start_column=min_col, end_row=row, end_column=end_col)
                cell = ws.cell(row, min_col)
                cell.value = value
                cell.alignment = alignment
                cell.font = font
                break


def _clear_generated_area(ws: Worksheet, end_col: int) -> None:
    for row in range(HEADER_TOP_ROW, BODY_END_ROW + 1):
        for col in range(GENERATED_START_COL, end_col + 1):
            cell = ws.cell(row, col)
            if isinstance(cell, MergedCell):
                continue
            if HEADER_TOP_ROW <= row <= HEADER_BOTTOM_ROW:
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
    last_logical_col = GENERATED_START_COL + len(generated_columns) - 1
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
            ws.merge_cells(
                start_row=HEADER_TOP_ROW,
                start_column=start_col,
                end_row=HEADER_GROUP_BOTTOM_ROW,
                end_column=stop_col,
            )
            ws.cell(HEADER_TOP_ROW, start_col).value = group
            for inner_offset in range(offset, group_end + 1):
                leaf_col = col_by_offset[inner_offset]
                ws.merge_cells(
                    start_row=HEADER_LEAF_TOP_ROW,
                    start_column=leaf_col,
                    end_row=HEADER_BOTTOM_ROW,
                    end_column=leaf_col,
                )
                ws.cell(HEADER_LEAF_TOP_ROW, leaf_col).value = _column_label(
                    generated_columns[inner_offset]
                )
        else:
            col = col_by_offset[offset]
            role = str(column.get("role") or "").strip()
            name = str(column.get("name") or "").strip()
            merge_end_col = end_col if col == last_logical_col and (role in {"note", "remarks"} or name == "remarks") else col
            ws.merge_cells(
                start_row=HEADER_TOP_ROW,
                start_column=col,
                end_row=HEADER_BOTTOM_ROW,
                end_column=merge_end_col,
            )
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
        row_top = _copy_side(ws.cell(row, 4).border.top)
        row_bottom = _copy_side(ws.cell(row, 4).border.bottom)
        for col in range(GENERATED_START_COL, end_col + 1):
            cell = ws.cell(row, col)
            _set_border(cell, left=thin, right=thin, top=row_top, bottom=row_bottom)
        _set_border(ws.cell(row, GENERATED_START_COL), left=thick)
        _set_border(ws.cell(row, end_col), right=thick)
    for col in range(GENERATED_START_COL, end_col + 1):
        _set_border(ws.cell(BODY_END_ROW, col), bottom=thick)


def _generated_widths(generated_columns: list[dict[str, Any]]) -> list[float]:
    count = len(generated_columns)
    if count <= 0:
        return []
    if count == len(SOURCE_GENERATED_PIXEL_PROFILE):
        desired_pixels = list(SOURCE_GENERATED_PIXEL_PROFILE)
    elif count >= 3:
        fixed = list(SOURCE_GENERATED_PIXEL_PROFILE[:2])
        distributed = _distribute_pixels(SOURCE_GENERATED_PIXEL_WIDTH - sum(fixed), count - len(fixed))
        desired_pixels = fixed + distributed
    else:
        desired_pixels = _distribute_pixels(SOURCE_GENERATED_PIXEL_WIDTH, count)
    return [_column_width_from_render_pixels(px) for px in desired_pixels]


def _distribute_pixels(total: int, count: int) -> list[int]:
    if count <= 0:
        return []
    base = total // count
    remainder = total - base * count
    return [base + (1 if index >= count - remainder else 0) for index in range(count)]


def _column_width_to_render_pixels(width: float, *, dpi: int = 144) -> int:
    return max(8, int(round((float(width) * 7.0 + 5.0) * float(dpi) / 96.0)))


def _column_width_from_render_pixels(pixels: int, *, dpi: int = 144) -> float:
    return max(1.0, ((float(pixels) * 96.0 / float(dpi)) - 5.0) / 7.0)


def _row_height_to_render_pixels(height_points: float | None, *, dpi: int = DEFAULT_DPI) -> int:
    points = float(height_points if height_points is not None else 15.0)
    return max(12, int(round(points * float(dpi) / 72.0)))


def _worksheet_positions(ws: Worksheet, *, max_col: int, max_row: int, dpi: int = DEFAULT_DPI) -> tuple[dict[int, int], dict[int, int]]:
    x_positions = {1: 0}
    current_x = 0
    for col in range(1, max_col + 1):
        dim = ws.column_dimensions[get_column_letter(col)]
        current_x += _column_width_to_render_pixels(float(dim.width or 8.43), dpi=dpi)
        x_positions[col + 1] = current_x

    y_positions = {1: 0}
    current_y = 0
    default_row_height = ws.sheet_format.defaultRowHeight
    for row in range(1, max_row + 1):
        dim = ws.row_dimensions[row]
        current_y += _row_height_to_render_pixels(dim.height or default_row_height, dpi=dpi)
        y_positions[row + 1] = current_y
    return x_positions, y_positions


def _anchor_offset_to_render_pixels(value: int | float | None, *, dpi: int = DEFAULT_DPI) -> int:
    return int(round(float(value or 0) / float(EMU_PER_PIXEL) * float(dpi) / 96.0))


def _freeze_images_to_master_render_size(ws: Worksheet) -> None:
    """Make top-area images independent from generated quantity-column widths."""
    images = list(getattr(ws, "_images", []))
    if not images:
        return
    x_positions, y_positions = _worksheet_positions(
        ws,
        max_col=MASTER_GENERATED_END_COL,
        max_row=BODY_END_ROW,
    )
    for image in images:
        anchor = getattr(image, "anchor", None)
        start = getattr(anchor, "_from", None)
        end = getattr(anchor, "to", None)
        if start is None or end is None:
            continue
        start_col = int(getattr(start, "col", 0)) + 1
        start_row = int(getattr(start, "row", 0)) + 1
        end_col = int(getattr(end, "col", 0)) + 1
        end_row = int(getattr(end, "row", 0)) + 1
        if start_col not in x_positions or end_col not in x_positions or start_row not in y_positions or end_row not in y_positions:
            continue
        start_x = x_positions[start_col] + _anchor_offset_to_render_pixels(getattr(start, "colOff", 0))
        start_y = y_positions[start_row] + _anchor_offset_to_render_pixels(getattr(start, "rowOff", 0))
        end_x = x_positions[end_col] + _anchor_offset_to_render_pixels(getattr(end, "colOff", 0))
        end_y = y_positions[end_row] + _anchor_offset_to_render_pixels(getattr(end, "rowOff", 0))
        target_width = max(1, int(round((end_x - start_x) * 96.0 / float(DEFAULT_DPI))))
        target_height = max(1, int(round((end_y - start_y) * 96.0 / float(DEFAULT_DPI))))
        image.width = target_width
        image.height = target_height
        image.anchor = OneCellAnchor(
            _from=deepcopy(start),
            ext=XDRPositiveSize2D(
                cx=pixels_to_EMU(target_width),
                cy=pixels_to_EMU(target_height),
            ),
        )


def _set_generated_widths(ws: Worksheet, generated_columns: list[dict[str, Any]], end_col: int) -> None:
    widths = _generated_widths(generated_columns)
    expected_count = end_col - GENERATED_START_COL + 1
    if len(widths) != expected_count:
        raise FacilityTemplateBuildError(
            f"generated_width_count_mismatch:{len(widths)}!={expected_count}"
        )
    for offset, _width in enumerate(widths):
        col = GENERATED_START_COL + offset
        ws.column_dimensions[get_column_letter(col)].width = widths[offset]


def _write_facility_name(ws: Worksheet, facility_name: str) -> None:
    text = str(facility_name or "").strip()
    if not text:
        return
    for merged_range in list(ws.merged_cells.ranges):
        if str(merged_range) == FACILITY_NAME_RANGE:
            break
    else:
        ws.merge_cells(FACILITY_NAME_RANGE)
    cell = ws["A4"]
    cell.value = text
    cell.font = Font(name="Yu Gothic", bold=True, size=18)
    cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=False, shrink_to_fit=True)


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
        ["source_generated_pixel_width", SOURCE_GENERATED_PIXEL_WIDTH],
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
    ws.title = FACILITY_TEMPLATE_SHEET_NAME
    _freeze_images_to_master_render_size(ws)

    facility_name = str(facility_config.get("facility_name") or facility_config.get("name") or "").strip()
    _write_facility_name(ws, facility_name)

    end_col = _ensure_generated_capacity(ws, len(generated_columns))
    _normalize_static_right_merges(ws, end_col)
    _unmerge_generated_area(ws, max(end_col, MASTER_GENERATED_END_COL))
    _trim_unused_generated_columns(ws, end_col)
    _clear_generated_area(ws, end_col)
    _write_header(ws, generated_columns, end_col)
    _write_body_grid(ws, end_col)
    _set_generated_widths(ws, generated_columns, end_col)
    ws.print_area = f"A1:{get_column_letter(end_col)}{PRINT_END_ROW}"
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
