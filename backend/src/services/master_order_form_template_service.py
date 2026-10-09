from __future__ import annotations

from copy import copy, deepcopy
from datetime import UTC, date, datetime
import hashlib
import json
from pathlib import Path
from typing import Any

from openpyxl import load_workbook
from openpyxl.cell.cell import MergedCell
from openpyxl.drawing.spreadsheet_drawing import OneCellAnchor
from openpyxl.drawing.xdr import XDRPositiveSize2D
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.utils.units import pixels_to_EMU
from openpyxl.worksheet.print_settings import PrintArea
from openpyxl.worksheet.worksheet import Worksheet

from src.services import config_service, menu_service, sheet_week_service


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
BODY_END_ROW = 67
PRINT_END_ROW = 69
FACILITY_NAME_RANGE = "A4:E5"
SOURCE_GENERATED_PIXEL_PROFILE = (172, 144, 224, 144, 172, 144, 141, 144)
SOURCE_GENERATED_PIXEL_WIDTH = sum(SOURCE_GENERATED_PIXEL_PROFILE)
DEFAULT_DPI = 144
EMU_PER_PIXEL = 9525
DAYPART_ORDER = {"朝": 0, "昼": 1, "夕": 2}


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
    return []


def _generated_columns(columns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    menu_index = next(
        (
            int(column.get("index") or 0)
            for column in columns
            if str(column.get("role") or "").strip() in {"menu_name", "menu"}
        ),
        2,
    )
    result: list[dict[str, Any]] = []
    for column in columns:
        role = str(column.get("role") or "").strip()
        try:
            column_index = int(column.get("index") or 0)
        except Exception:
            column_index = 0
        if role in {"date", "daypart", "menu_name", "menu"}:
            continue
        if role == "aux" and column_index <= menu_index:
            continue
        if role == "aux" and column_index > menu_index:
            result.append(column)
            continue
        if role in {"quantity", "note", "remarks"} or str(column.get("name") or "").strip() == "remarks":
            result.append(column)
    return result


def _unmerge_generated_area(ws: Worksheet, end_col: int, body_end_row: int = BODY_END_ROW) -> None:
    for merged_range in list(ws.merged_cells.ranges):
        min_col, min_row, max_col, max_row = merged_range.bounds
        if max_row < HEADER_TOP_ROW or min_row > body_end_row:
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
    min_static_col = 7
    if end_col < min_static_col:
        return
    for row in range(2, 7):
        for merged_range in list(ws.merged_cells.ranges):
            min_col, min_row, max_col, max_row = merged_range.bounds
            if min_row == row and max_row == row and min_col == min_static_col and max_col == MASTER_GENERATED_END_COL:
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


def _clear_generated_area(ws: Worksheet, end_col: int, body_end_row: int = BODY_END_ROW) -> None:
    for row in range(HEADER_TOP_ROW, body_end_row + 1):
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
        super_group = str(column.get("header_super_group") or "").strip()
        if super_group:
            super_end = offset
            while (
                super_end + 1 < len(generated_columns)
                and str(generated_columns[super_end + 1].get("header_super_group") or "").strip() == super_group
            ):
                super_end += 1
            start_col = col_by_offset[offset]
            stop_col = col_by_offset[super_end]
            ws.merge_cells(
                start_row=HEADER_TOP_ROW,
                start_column=start_col,
                end_row=HEADER_TOP_ROW,
                end_column=stop_col,
            )
            ws.cell(HEADER_TOP_ROW, start_col).value = super_group

            inner_offset = offset
            while inner_offset <= super_end:
                inner_column = generated_columns[inner_offset]
                group = str(inner_column.get("header_group") or "").strip()
                group_end = inner_offset
                if group:
                    while (
                        group_end + 1 <= super_end
                        and str(generated_columns[group_end + 1].get("header_group") or "").strip() == group
                    ):
                        group_end += 1
                start_inner_col = col_by_offset[inner_offset]
                stop_inner_col = col_by_offset[group_end]
                if group and group_end > inner_offset:
                    ws.merge_cells(
                        start_row=HEADER_GROUP_BOTTOM_ROW,
                        start_column=start_inner_col,
                        end_row=HEADER_GROUP_BOTTOM_ROW,
                        end_column=stop_inner_col,
                    )
                    ws.cell(HEADER_GROUP_BOTTOM_ROW, start_inner_col).value = group
                    for leaf_offset in range(inner_offset, group_end + 1):
                        leaf_col = col_by_offset[leaf_offset]
                        ws.merge_cells(
                            start_row=HEADER_LEAF_TOP_ROW,
                            start_column=leaf_col,
                            end_row=HEADER_BOTTOM_ROW,
                            end_column=leaf_col,
                        )
                        ws.cell(HEADER_LEAF_TOP_ROW, leaf_col).value = _column_label(
                            generated_columns[leaf_offset]
                        )
                else:
                    ws.merge_cells(
                        start_row=HEADER_GROUP_BOTTOM_ROW,
                        start_column=start_inner_col,
                        end_row=HEADER_BOTTOM_ROW,
                        end_column=start_inner_col,
                    )
                    ws.cell(HEADER_GROUP_BOTTOM_ROW, start_inner_col).value = _column_label(inner_column)
                inner_offset = group_end + 1
            offset = super_end + 1
            continue

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


def _write_body_grid(ws: Worksheet, end_col: int, body_end_row: int = BODY_END_ROW) -> None:
    thick = _style_side("thick")
    thin = _style_side("thin")
    for row in range(BODY_START_ROW, body_end_row + 1):
        row_top = _copy_side(ws.cell(row, 4).border.top)
        row_bottom = _copy_side(ws.cell(row, 4).border.bottom)
        for col in range(GENERATED_START_COL, end_col + 1):
            cell = ws.cell(row, col)
            _set_border(cell, left=thin, right=thin, top=row_top, bottom=row_bottom)
        _set_border(ws.cell(row, GENERATED_START_COL), left=thick)
        _set_border(ws.cell(row, end_col), right=thick)
    for col in range(GENERATED_START_COL, end_col + 1):
        _set_border(ws.cell(body_end_row, col), bottom=thick)


def _week_sheet_name_from_week_value(week_value: object | None) -> str | None:
    _month_id, start_date, end_date = sheet_week_service.parse_sheet_week_value(week_value)
    if not isinstance(start_date, date) or not isinstance(end_date, date):
        return None
    return f"{start_date.month}月{start_date.day}日～{end_date.month}月{end_date.day}日"


def _body_merge_policy_from_config(facility_config: dict[str, Any]) -> dict[str, Any]:
    fax_template = facility_config.get("fax_template") if isinstance(facility_config, dict) else None
    for source in (fax_template, facility_config):
        if not isinstance(source, dict):
            continue
        policy = source.get("body_merge_policy")
        if isinstance(policy, dict):
            return deepcopy(policy)
    return {}


def _body_merge_mode(value: object) -> str:
    mode = str(value or "").strip().lower()
    if mode in {"daypart", "meal", "meal_period"}:
        return "daypart"
    if mode in {"", "none", "false", "off"}:
        return ""
    raise FacilityTemplateBuildError(f"unsupported_body_merge_mode:{mode}")


def _body_merge_target_names(policy: dict[str, Any]) -> set[str]:
    raw_columns = policy.get("columns")
    if raw_columns is None:
        raw_columns = policy.get("target_columns")
    if raw_columns is None:
        raw_columns = policy.get("target_names")
    if raw_columns is None:
        raw_columns = policy.get("quantity_names")
    if isinstance(raw_columns, str):
        raw_columns = [raw_columns]
    if not isinstance(raw_columns, list):
        return set()
    return {str(item or "").strip() for item in raw_columns if str(item or "").strip()}


def _configured_body_merge_columns(
    *,
    facility_config: dict[str, Any],
    generated_columns: list[dict[str, Any]],
) -> list[tuple[int, dict[str, Any], str]]:
    policy = _body_merge_policy_from_config(facility_config)
    policy_mode = _body_merge_mode(policy.get("mode"))
    target_names = _body_merge_target_names(policy)
    if policy_mode and not target_names:
        raise FacilityTemplateBuildError("facility_template_body_merge_columns_missing")

    selected: list[tuple[int, dict[str, Any], str]] = []
    for offset, column in enumerate(generated_columns):
        column_mode = _body_merge_mode(column.get("body_merge") or column.get("body_merge_mode"))
        name_tokens = {
            str(column.get("name") or "").strip(),
            str(column.get("header") or "").strip(),
            str(column.get("diet_type") or "").strip(),
        }
        mode = column_mode
        if not mode and policy_mode and target_names.intersection(name_tokens):
            mode = policy_mode
        if not mode:
            continue
        if mode != "daypart":
            raise FacilityTemplateBuildError(f"unsupported_body_merge_mode:{mode}")
        role = str(column.get("role") or "").strip()
        if role != "quantity":
            raise FacilityTemplateBuildError("facility_template_body_merge_non_quantity_column")
        selected.append((GENERATED_START_COL + offset, column, mode))
    if bool(policy.get("required", False)) and not selected:
        raise FacilityTemplateBuildError("facility_template_body_merge_columns_missing")
    return selected


def _standard_daypart_body_spans() -> list[tuple[int, int, str]]:
    spans: list[tuple[int, int, str]] = []
    row = BODY_START_ROW
    while row <= BODY_END_ROW:
        for label, size in (("朝", 2), ("昼", 3), ("夕", 3)):
            end_row = row + size - 1
            if end_row > BODY_END_ROW:
                return spans
            spans.append((row, end_row, label))
            row = end_row + 1
    return spans


def _apply_configured_body_merges(
    ws: Worksheet,
    *,
    facility_config: dict[str, Any],
    generated_columns: list[dict[str, Any]],
    end_col: int,
    body_spans: list[tuple[int, int, str]] | None = None,
) -> list[dict[str, Any]]:
    selected_columns = _configured_body_merge_columns(
        facility_config=facility_config,
        generated_columns=generated_columns,
    )
    if not selected_columns:
        return []

    applied: list[dict[str, Any]] = []
    spans = _standard_daypart_body_spans() if body_spans is None else body_spans
    for target_col, column, mode in selected_columns:
        if target_col < GENERATED_START_COL or target_col > end_col:
            raise FacilityTemplateBuildError("facility_template_body_merge_target_out_of_range")
        for min_row, max_row, daypart in spans:
            ws.merge_cells(
                start_row=min_row,
                start_column=target_col,
                end_row=max_row,
                end_column=target_col,
            )
            applied.append(
                {
                    "source": "facility_config",
                    "mode": mode,
                    "daypart": daypart,
                    "target_col": int(target_col),
                    "column_name": str(column.get("name") or ""),
                    "column_header": str(column.get("header") or ""),
                    "min_row": int(min_row),
                    "max_row": int(max_row),
                    "target_range": f"{get_column_letter(target_col)}{min_row}:{get_column_letter(target_col)}{max_row}",
                }
            )
    return applied


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


def _cell_value_for_digest(value: object) -> object:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value


def _side_for_digest(side: Any) -> dict[str, Any]:
    return {
        "style": getattr(side, "style", None),
        "color": getattr(getattr(side, "color", None), "rgb", None),
    }


def _cell_style_for_digest(cell: Any) -> dict[str, Any]:
    alignment = cell.alignment
    font = cell.font
    fill = cell.fill
    border = cell.border
    return {
        "alignment": {
            "horizontal": alignment.horizontal,
            "vertical": alignment.vertical,
            "wrap_text": alignment.wrap_text,
            "shrink_to_fit": alignment.shrink_to_fit,
            "text_rotation": alignment.textRotation,
        },
        "font": {
            "name": font.name,
            "size": font.sz,
            "bold": font.bold,
            "italic": font.italic,
            "color": getattr(getattr(font, "color", None), "rgb", None),
        },
        "fill": {
            "fill_type": fill.fill_type,
            "fgColor": getattr(fill.fgColor, "rgb", None),
            "bgColor": getattr(fill.bgColor, "rgb", None),
        },
        "border": {
            "left": _side_for_digest(border.left),
            "right": _side_for_digest(border.right),
            "top": _side_for_digest(border.top),
            "bottom": _side_for_digest(border.bottom),
        },
        "number_format": cell.number_format,
    }


def _worksheet_digest_payload(ws: Worksheet, *, max_row: int, max_col: int) -> dict[str, Any]:
    cells: list[dict[str, Any]] = []
    for row in range(1, max_row + 1):
        for col in range(1, max_col + 1):
            cell = ws.cell(row=row, column=col)
            if isinstance(cell, MergedCell):
                continue
            cells.append(
                {
                    "row": row,
                    "col": col,
                    "value": _cell_value_for_digest(cell.value),
                    "style": _cell_style_for_digest(cell),
                }
            )
    images: list[dict[str, Any]] = []
    for image in getattr(ws, "_images", []) or []:
        anchor = getattr(image, "anchor", None)
        marker = getattr(anchor, "_from", None)
        images.append(
            {
                "row": getattr(marker, "row", None),
                "col": getattr(marker, "col", None),
                "rowOff": getattr(marker, "rowOff", None),
                "colOff": getattr(marker, "colOff", None),
                "width": getattr(image, "width", None),
                "height": getattr(image, "height", None),
            }
        )
    return {
        "title": ws.title,
        "print_area": str(ws.print_area or ""),
        "max_row": max_row,
        "max_col": max_col,
        "merged_ranges": sorted(str(item) for item in ws.merged_cells.ranges),
        "rows": [
            {
                "row": row,
                "height": ws.row_dimensions[row].height,
                "hidden": ws.row_dimensions[row].hidden,
            }
            for row in range(1, max_row + 1)
        ],
        "columns": [
            {
                "col": col,
                "letter": get_column_letter(col),
                "width": ws.column_dimensions[get_column_letter(col)].width,
                "hidden": ws.column_dimensions[get_column_letter(col)].hidden,
            }
            for col in range(1, max_col + 1)
        ],
        "images": sorted(images, key=lambda item: (item.get("row") or 0, item.get("col") or 0)),
        "cells": cells,
    }


def _stable_digest(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_facility_template_diagnostics(
    *,
    facility_config: dict[str, Any],
    week_value: object | None = None,
    week_menu_entries: Any | None = None,
    master_template_path: Path | str = MASTER_TEMPLATE_PATH,
) -> dict[str, Any]:
    master_path = Path(master_template_path)
    workbook = build_facility_template_workbook(
        facility_config=facility_config,
        week_value=week_value,
        week_menu_entries=week_menu_entries,
        master_template_path=master_path,
    )
    worksheet = workbook[FACILITY_TEMPLATE_SHEET_NAME]
    schema = workbook["generated_template_schema"]
    end_col = worksheet.max_column
    schema_rows = [
        [_cell_value_for_digest(value) for value in row]
        for row in schema.iter_rows(values_only=True)
    ]
    schema_rows_for_digest = [
        ([row[0], "<generated_at_utc>", *row[2:]] if row and row[0] == "generated_at_utc" else row)
        for row in schema_rows
    ]
    facility_payload = _worksheet_digest_payload(
        worksheet,
        max_row=next(row[1] for row in schema_rows if row[0] == "print_end_row"),
        max_col=end_col,
    )
    canonical_payload = {
        "facility_template": facility_payload,
        "generated_template_schema": schema_rows_for_digest,
    }
    return {
        "master_template_sha256": _file_sha256(master_path),
        "master_template_name": master_path.name,
        "facility_id": str(facility_config.get("facility_id") or facility_config.get("id") or "").strip(),
        "week_value": str(week_value or ""),
        "facility_template_canonical_digest": _stable_digest(canonical_payload),
        "facility_template_payload_digest": _stable_digest(facility_payload),
        "schema_digest": _stable_digest(schema_rows_for_digest),
        "generated_end_col": end_col,
        "generated_end_letter": get_column_letter(end_col),
        "schema": schema_rows,
    }


def _parse_menu_date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except Exception:
        return None


def _normalize_week_menu_entries(*, entries: Any, week_value: object) -> list[dict[str, Any]]:
    month_id, start_date, end_date = sheet_week_service.parse_sheet_week_value(week_value)
    if not month_id or not isinstance(start_date, date) or not isinstance(end_date, date):
        raise FacilityTemplateBuildError("week_menu_week_invalid")
    if not isinstance(entries, list):
        raise FacilityTemplateBuildError("week_menu_entries_invalid")
    normalized: list[dict[str, Any]] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise FacilityTemplateBuildError("week_menu_entry_invalid")
        menu_date = _parse_menu_date(entry.get("menu_date"))
        daypart = str(entry.get("daypart") or "").strip()
        menu_name = str(entry.get("name") or "").strip()
        if "diet_variants" in entry and not menu_name:
            raise ValueError("physical_menu_common_name_unresolved")
        if not isinstance(menu_date, date) or not daypart or not menu_name:
            raise FacilityTemplateBuildError("week_menu_entry_identity_unresolved")
        if not (start_date <= menu_date <= end_date):
            continue
        normalized.append(
            {
                **entry,
                "_menu_date_obj": menu_date,
                "_source_index": index,
            }
        )
    return sorted(
        normalized,
        key=lambda item: (
            item["_menu_date_obj"],
            DAYPART_ORDER.get(str(item.get("daypart") or "").strip(), 99),
            int(item.get("sort_order") or item.get("display_order") or item.get("order") or item["_source_index"]),
            int(item["_source_index"]),
        ),
    )


def _collect_week_menu_entries(*, facility_id: str, week_value: object) -> list[dict[str, Any]]:
    month_id, start_date, end_date = sheet_week_service.parse_sheet_week_value(week_value)
    if not month_id or not isinstance(start_date, date) or not isinstance(end_date, date):
        return []
    payload = menu_service.get_menu_for_facility(month_id, facility_id)
    entries = payload.get("entries") if isinstance(payload, dict) else None
    return _normalize_week_menu_entries(entries=entries, week_value=week_value)


def _weekday_label(menu_date: date) -> str:
    labels = ["月", "火", "水", "木", "金", "土", "日"]
    return labels[menu_date.weekday()]


def _clear_body_identity_values(ws: Worksheet) -> None:
    for row in range(BODY_START_ROW, BODY_END_ROW + 1):
        for col in range(1, 5):
            cell = ws.cell(row=row, column=col)
            if isinstance(cell, MergedCell):
                continue
            cell.value = None


def _write_cell_if_writable(ws: Worksheet, *, row: int, column: int, value: object) -> None:
    cell = ws.cell(row=row, column=column)
    if isinstance(cell, MergedCell):
        return
    cell.value = value


def _split_week_menu_identity_entries(entries: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, str]], int]:
    for entry in entries:
        if not isinstance(entry.get("_menu_date_obj"), date):
            raise FacilityTemplateBuildError("week_menu_entry_identity_unresolved")
    return list(entries), [], len(entries)


def _resize_week_body(ws: Worksheet, body_end_row: int) -> None:
    """Resize only the generated copy; openpyxl does not shift merges/dimensions."""
    delta = body_end_row - BODY_END_ROW
    footer_merges = []
    for region in list(ws.merged_cells.ranges):
        if region.max_row < BODY_START_ROW:
            continue
        if region.min_row < BODY_START_ROW or region.min_row <= BODY_END_ROW < region.max_row:
            raise FacilityTemplateBuildError("week_menu_body_merge_boundary_invalid")
        if region.min_row > BODY_END_ROW:
            footer_merges.append(region.bounds)
        ws.unmerge_cells(str(region))
    body_styles = [copy(ws.cell(BODY_START_ROW, col)._style) for col in range(1, ws.max_column + 1)]
    body_dimension = copy(ws.row_dimensions[BODY_START_ROW])
    footer_dimensions = {
        row: copy(dimension) for row, dimension in ws.row_dimensions.items() if row > BODY_END_ROW
    }
    if delta > 0:
        ws.insert_rows(BODY_END_ROW + 1, delta)
    elif delta < 0:
        ws.delete_rows(body_end_row + 1, -delta)
    for row in list(ws.row_dimensions):
        if row >= BODY_START_ROW:
            del ws.row_dimensions[row]
    for row in range(BODY_START_ROW, body_end_row + 1):
        dimension = copy(body_dimension)
        dimension.index = row
        ws.row_dimensions[row] = dimension
        for col, style in enumerate(body_styles, 1):
            cell = ws.cell(row, col)
            cell._style = copy(style)
            cell.value = None
            cell.border = Border(left=_style_side(), right=_style_side(), top=_style_side(), bottom=_style_side())
        _set_border(ws.cell(row, 1), left=_style_side("thick"))
    for col in range(1, ws.max_column + 1):
        _set_border(ws.cell(BODY_START_ROW, col), top=_style_side("thick"))
    for row, dimension in footer_dimensions.items():
        dimension.index = row + delta
        ws.row_dimensions[row + delta] = dimension
    for min_col, min_row, max_col, max_row in footer_merges:
        ws.merge_cells(start_row=min_row + delta, start_column=min_col, end_row=max_row + delta, end_column=max_col)


def _week_body_spans(entries: list[dict[str, Any]], *, by_daypart: bool) -> list[tuple[int, int, Any]]:
    spans: list[tuple[int, int, Any]] = []
    for offset, entry in enumerate(entries):
        key = (entry["_menu_date_obj"], str(entry["daypart"]).strip()) if by_daypart else entry["_menu_date_obj"]
        row = BODY_START_ROW + offset
        if spans and spans[-1][2] == key:
            start, _end, _key = spans[-1]
            spans[-1] = (start, row, key)
        else:
            spans.append((row, row, key))
    return spans


def _write_week_menu_identity(ws: Worksheet, entries: list[dict[str, Any]]) -> dict[str, Any]:
    physical_entries, overflow_entries, source_rows = _split_week_menu_identity_entries(entries)
    if not physical_entries:
        raise FacilityTemplateBuildError("week_menu_body_entries_missing")
    body_end_row = BODY_START_ROW + len(physical_entries) - 1
    _resize_week_body(ws, body_end_row)
    day_spans = _week_body_spans(physical_entries, by_daypart=False)
    meal_spans = _week_body_spans(physical_entries, by_daypart=True)
    for start, end, menu_date in day_spans:
        if start == end:
            ws.cell(start, 1).value = f"{menu_date.isoformat()} ({_weekday_label(menu_date)})"
        else:
            ws.merge_cells(start_row=start, start_column=1, end_row=end - 1, end_column=1)
            ws.cell(start, 1).value = menu_date
            ws.cell(end, 1).value = _weekday_label(menu_date)
    for start, end, (_menu_date, daypart) in meal_spans:
        ws.merge_cells(start_row=start, start_column=2, end_row=end, end_column=2)
        ws.cell(start, 2).value = daypart
    row_plan = []
    for offset, entry in enumerate(physical_entries):
        row = BODY_START_ROW + offset
        ws.cell(row, 3).value = str(entry.get("category") or "").strip()
        ws.cell(row, 4).value = str(entry["name"]).strip()
        row_plan.append({
            "worksheet_row": row,
            "source_index": entry["_source_index"],
            "date": entry["_menu_date_obj"].isoformat(),
            "daypart": str(entry["daypart"]).strip(),
            "category": str(entry.get("category") or "").strip(),
            "name": str(entry["name"]).strip(),
        })
    for col in range(1, 5):
        _set_border(ws.cell(body_end_row, col), bottom=_style_side("thick"))
    return {
        "written_rows": len(physical_entries),
        "source_rows": source_rows,
        "overflow_rows": len(overflow_entries),
        "overflow_entries": overflow_entries,
        "body_end_row": body_end_row,
        "body_spans": [(start, end, key[1]) for start, end, key in meal_spans],
        "physical_row_plan": row_plan,
    }


def _apply_week_menu_identity(
    ws: Worksheet,
    *,
    facility_config: dict[str, Any],
    week_value: object | None,
    week_menu_entries: Any | None = None,
) -> dict[str, Any]:
    if not week_value:
        return {"written_rows": 0, "source_rows": 0, "overflow_rows": 0, "overflow_entries": []}
    facility_id = str(facility_config.get("facility_id") or facility_config.get("id") or "").strip()
    if not facility_id:
        raise FacilityTemplateBuildError("week_menu_facility_missing")
    entries = (
        _normalize_week_menu_entries(entries=week_menu_entries, week_value=week_value)
        if week_menu_entries is not None
        else _collect_week_menu_entries(facility_id=facility_id, week_value=week_value)
    )
    if not entries:
        raise FacilityTemplateBuildError("week_menu_entries_missing")
    return _write_week_menu_identity(ws, entries)


def validated_body_row_plan(ws: Worksheet, *, allow_static: bool = False) -> list[dict[str, Any]] | None:
    """Validate generated physical identity before consumers map geometry to rows."""
    wb = ws.parent
    if "generated_template_schema" not in wb.sheetnames:
        if allow_static and ws.title == MASTER_SHEET_NAME:
            return None
        raise FacilityTemplateBuildError("generated_body_schema_missing")
    schema = {
        row[0]: row[1] for row in wb["generated_template_schema"].iter_rows(values_only=True)
        if len(row) >= 2 and isinstance(row[0], str)
    }
    if not schema.get("week_value"):
        if allow_static:
            return None
        raise FacilityTemplateBuildError("generated_body_week_missing")
    if not schema.get("week_menu_rows"):
        raise FacilityTemplateBuildError("generated_body_entries_missing")
    try:
        plan = json.loads(schema["week_menu_physical_row_plan"])
    except (KeyError, TypeError, ValueError) as exc:
        raise FacilityTemplateBuildError("generated_body_row_plan_invalid") from exc
    end_row = schema.get("body_end_row")
    end_col = schema.get("generated_end_col")
    try:
        print_ranges = PrintArea.from_string(ws.print_area).ranges
        print_bounds = [area.bounds for area in print_ranges]
    except (TypeError, ValueError) as exc:
        raise FacilityTemplateBuildError("generated_body_print_area_invalid") from exc
    if (not isinstance(plan, list) or not plan or type(end_row) is not int
            or type(end_col) is not int or end_col != ws.max_column
            or schema.get("week_menu_rows") != len(plan)
            or schema.get("week_menu_source_rows") != len(plan)
            or schema.get("week_menu_overflow_rows") != 0
            or end_row != BODY_START_ROW + len(plan) - 1
            or schema.get("print_end_row") != PRINT_END_ROW + end_row - BODY_END_ROW
            or print_bounds != [(1, 1, end_col, schema.get("print_end_row"))]):
        raise FacilityTemplateBuildError("generated_body_row_plan_changed")
    merged_values = {}
    for region in ws.merged_cells.ranges:
        if region.min_row < BODY_START_ROW or region.max_row > end_row:
            if region.min_row <= end_row and region.max_row >= BODY_START_ROW:
                raise FacilityTemplateBuildError("generated_body_merge_boundary_invalid")
            continue
        value = ws.cell(region.min_row, region.min_col).value
        for row in range(region.min_row, region.max_row + 1):
            for col in range(region.min_col, region.max_col + 1):
                merged_values[(row, col)] = value
    source_indexes = set()
    effective_date = None
    for offset, entry in enumerate(plan):
        row = BODY_START_ROW + offset
        if (not isinstance(entry, dict) or entry.get("worksheet_row") != row
                or type(entry.get("source_index")) is not int or entry["source_index"] in source_indexes):
            raise FacilityTemplateBuildError("generated_body_row_identity_invalid")
        source_indexes.add(entry["source_index"])
        date_value = merged_values.get((row, 1), ws.cell(row, 1).value)
        menu_date = _parse_menu_date(date_value)
        if menu_date is not None:
            effective_date = menu_date
        elif effective_date is not None and date_value == _weekday_label(effective_date):
            menu_date = effective_date
        daypart = merged_values.get((row, 2), ws.cell(row, 2).value)
        if (menu_date is None or menu_date.isoformat() != entry.get("date")
                or daypart != entry.get("daypart") or not daypart
                or str(ws.cell(row, 3).value or "") != entry.get("category")
                or ws.cell(row, 4).value != entry.get("name") or not entry.get("name")):
            raise FacilityTemplateBuildError("generated_body_row_identity_changed")
    for col in range(1, end_col + 1):
        if getattr(ws.cell(end_row, col).border.bottom, "style", None) != "thick":
            raise FacilityTemplateBuildError("generated_body_end_border_changed")
    try:
        details = json.loads(schema["configured_body_merged_range_details"])
    except (KeyError, TypeError, ValueError) as exc:
        raise FacilityTemplateBuildError("generated_body_merge_plan_invalid") from exc
    if not isinstance(details, list) or len(details) != schema.get("configured_body_merged_ranges"):
        raise FacilityTemplateBuildError("generated_body_merge_plan_invalid")
    meal_spans = set()
    start = BODY_START_ROW
    for offset, entry in enumerate(plan):
        if offset + 1 == len(plan) or (entry["date"], entry["daypart"]) != (plan[offset + 1]["date"], plan[offset + 1]["daypart"]):
            meal_spans.add((start, BODY_START_ROW + offset, entry["daypart"]))
            start = BODY_START_ROW + offset + 1
    declared_ranges = set()
    spans_by_column: dict[int, set[tuple[int, int, str]]] = {}
    for detail in details:
        if not isinstance(detail, dict):
            raise FacilityTemplateBuildError("generated_body_merge_plan_invalid")
        col, first, last = detail.get("target_col"), detail.get("min_row"), detail.get("max_row")
        span = (first, last, detail.get("daypart"))
        if (type(col) is not int or not GENERATED_START_COL <= col <= end_col
                or type(first) is not int or type(last) is not int
                or not isinstance(detail.get("daypart"), str) or span not in meal_spans):
            raise FacilityTemplateBuildError("generated_body_merge_plan_changed")
        target = f"{get_column_letter(col)}{first}:{get_column_letter(col)}{last}"
        if detail.get("target_range") != target:
            raise FacilityTemplateBuildError("generated_body_merge_plan_changed")
        declared_ranges.add((col, first, col, last))
        spans_by_column.setdefault(col, set()).add(span)
    actual_ranges = {
        region.bounds for region in ws.merged_cells.ranges
        if region.min_col >= GENERATED_START_COL and BODY_START_ROW <= region.min_row <= end_row
    }
    if actual_ranges != declared_ranges or any(spans != meal_spans for spans in spans_by_column.values()):
        raise FacilityTemplateBuildError("generated_body_merge_plan_changed")
    return plan


def validated_body_end_row(ws: Worksheet, *, allow_static: bool = False) -> int:
    plan = validated_body_row_plan(ws, allow_static=allow_static)
    return plan[-1]["worksheet_row"] if plan is not None else BODY_END_ROW


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
    schema.append(["index", "role", "header", "header_group", "name", "diet_type", "area_id", "source_index", "body_merge"])
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
                column.get("body_merge") or column.get("body_merge_mode"),
            ]
        )


def build_facility_template_workbook(
    *,
    facility_config: dict[str, Any],
    master_template_path: Path | str = MASTER_TEMPLATE_PATH,
    week_value: object | None = None,
    week_menu_entries: Any | None = None,
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
    week_menu_identity_result = _apply_week_menu_identity(
        ws,
        facility_config=facility_config,
        week_value=week_value,
        week_menu_entries=week_menu_entries,
    )
    body_end_row = week_menu_identity_result.get("body_end_row", BODY_END_ROW)
    print_end_row = PRINT_END_ROW + body_end_row - BODY_END_ROW

    end_col = _ensure_generated_capacity(ws, len(generated_columns))
    _normalize_static_right_merges(ws, end_col)
    _unmerge_generated_area(ws, max(end_col, MASTER_GENERATED_END_COL), body_end_row)
    _trim_unused_generated_columns(ws, end_col)
    _clear_generated_area(ws, end_col, body_end_row)
    _write_header(ws, generated_columns, end_col)
    _write_body_grid(ws, end_col, body_end_row)
    applied_body_merges = _apply_configured_body_merges(
        ws,
        facility_config=facility_config,
        generated_columns=generated_columns,
        end_col=end_col,
        body_spans=week_menu_identity_result.get("body_spans"),
    )
    _set_generated_widths(ws, generated_columns, end_col)
    ws.print_area = f"A1:{get_column_letter(end_col)}{print_end_row}"
    _append_schema_sheet(wb, facility_config=facility_config, generated_columns=generated_columns, end_col=end_col)
    wb["generated_template_schema"].append(["week_value", str(week_value or "")])
    wb["generated_template_schema"].append(["body_end_row", body_end_row])
    wb["generated_template_schema"].append(["print_end_row", print_end_row])
    wb["generated_template_schema"].append(["week_menu_physical_row_plan", json.dumps(week_menu_identity_result.get("physical_row_plan") or [], ensure_ascii=False)])
    wb["generated_template_schema"].append(["week_menu_rows", int(week_menu_identity_result.get("written_rows") or 0)])
    wb["generated_template_schema"].append(["week_menu_source_rows", int(week_menu_identity_result.get("source_rows") or 0)])
    wb["generated_template_schema"].append(["week_menu_overflow_rows", int(week_menu_identity_result.get("overflow_rows") or 0)])
    wb["generated_template_schema"].append(
        [
            "week_menu_overflow_entries",
            json.dumps(week_menu_identity_result.get("overflow_entries") or [], ensure_ascii=False, sort_keys=True),
        ]
    )
    wb["generated_template_schema"].append(["configured_body_merged_ranges", len(applied_body_merges)])
    wb["generated_template_schema"].append(
        ["configured_body_merged_range_details", json.dumps(applied_body_merges, ensure_ascii=False, sort_keys=True)]
    )
    if week_value:
        validated_body_row_plan(ws)
    return wb


def build_facility_template_xlsx(
    *,
    facility_config: dict[str, Any],
    output_path: Path | str,
    master_template_path: Path | str = MASTER_TEMPLATE_PATH,
    week_value: object | None = None,
    week_menu_entries: Any | None = None,
) -> Path:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    wb = build_facility_template_workbook(
        facility_config=facility_config,
        master_template_path=master_template_path,
        week_value=week_value,
        week_menu_entries=week_menu_entries,
    )
    wb.save(output)
    return output


def build_facility_template_xlsx_for_facility(
    *,
    facility_id: str,
    output_path: Path | str,
    master_template_path: Path | str = MASTER_TEMPLATE_PATH,
    week_value: object | None = None,
    week_menu_entries: Any | None = None,
) -> Path:
    facility_config = config_service.get_facility_config(facility_id)
    if not facility_config:
        raise FacilityTemplateBuildError(f"facility_not_found:{facility_id}")
    return build_facility_template_xlsx(
        facility_config=facility_config,
        output_path=output_path,
        master_template_path=master_template_path,
        week_value=week_value,
        week_menu_entries=week_menu_entries,
    )
