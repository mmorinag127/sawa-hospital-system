from __future__ import annotations

from datetime import date, datetime
from io import BytesIO
from pathlib import Path
from typing import Iterable

from openpyxl import load_workbook
from openpyxl.cell.cell import MergedCell
from openpyxl.utils import get_column_letter, range_boundaries
from PIL import Image, ImageColor, ImageDraw, ImageFont


_DEFAULT_DPI = 144
_DEFAULT_MARGIN_PX = 12
_EMU_PER_PIXEL = 9525
_DEFAULT_FONT_PATHS = [
    "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
    "/Library/Fonts/Arial Unicode.ttf",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
]


def _sheet_print_range(worksheet) -> tuple[int, int, int, int]:
    raw = str(worksheet.print_area or "").strip()
    if raw:
        area_ref = raw.split("!", 1)[-1].replace("$", "").split(",", 1)[0]
        min_col, min_row, max_col, max_row = range_boundaries(area_ref)
        return min_col, min_row, max_col, max_row
    return 1, 1, max(worksheet.max_column, 1), max(worksheet.max_row, 1)


def _column_width_to_pixels(width: float | None, *, dpi: int) -> int:
    excel_width = float(width if width is not None else 8.43)
    base_pixels = max(8.0, excel_width * 7.0 + 5.0)
    return max(8, int(round(base_pixels * float(dpi) / 96.0)))


def _row_height_to_pixels(height_points: float | None, *, dpi: int) -> int:
    points = float(height_points if height_points is not None else 15.0)
    return max(12, int(round(points * float(dpi) / 72.0)))


def _line_width(side_style: str | None) -> int:
    style = str(side_style or "").strip().lower()
    if not style:
        return 0
    if style in {"hair"}:
        return 1
    if style in {"medium", "mediumdashed", "mediumdashdot", "mediumdashdotdot"}:
        return 2
    if style in {"thick", "double"}:
        return 3
    return 1


def _color_to_rgb(color, *, default: tuple[int, int, int]) -> tuple[int, int, int]:
    value = getattr(color, "rgb", None) or getattr(color, "value", None)
    if isinstance(value, str):
        token = value.strip()
        if len(token) == 8:
            token = token[2:]
        if len(token) == 6:
            try:
                return ImageColor.getrgb(f"#{token}")
            except ValueError:
                pass
    return default


def _load_font(size_px: int, *, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    requested = max(10, int(size_px))
    for path in _DEFAULT_FONT_PATHS:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, requested)
            except OSError:
                continue
    return ImageFont.load_default()


def _cell_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return f"{value.month}/{value.day}"
    if isinstance(value, date):
        return f"{value.month}/{value.day}"
    return str(value)


def _text_bbox(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> tuple[int, int]:
    if not text:
        return 0, 0
    bbox = draw.multiline_textbbox((0, 0), text, font=font, spacing=2)
    return int(bbox[2] - bbox[0]), int(bbox[3] - bbox[1])


def _draw_cell_text(
    draw: ImageDraw.ImageDraw,
    *,
    rect: tuple[int, int, int, int],
    text: str,
    font: ImageFont.ImageFont,
    font_color: tuple[int, int, int],
    horizontal: str,
    vertical: str,
) -> None:
    if not text:
        return
    x0, y0, x1, y1 = rect
    text_w, text_h = _text_bbox(draw, text, font)
    if horizontal == "center":
        text_x = x0 + max(4, ((x1 - x0) - text_w) // 2)
    elif horizontal == "right":
        text_x = max(x0 + 4, x1 - text_w - 4)
    else:
        text_x = x0 + 4
    if vertical == "top":
        text_y = y0 + 3
    elif vertical == "bottom":
        text_y = max(y0 + 3, y1 - text_h - 3)
    else:
        text_y = y0 + max(3, ((y1 - y0) - text_h) // 2)
    draw.multiline_text((text_x, text_y), text, fill=font_color, font=font, spacing=2)


def _merged_rectangles(
    worksheet,
    *,
    min_col: int,
    min_row: int,
    max_col: int,
    max_row: int,
) -> dict[tuple[int, int], dict[str, int]]:
    merged: dict[tuple[int, int], dict[str, int]] = {}
    for merged_range in worksheet.merged_cells.ranges:
        start_col, start_row, end_col, end_row = range_boundaries(str(merged_range))
        if end_col < min_col or start_col > max_col or end_row < min_row or start_row > max_row:
            continue
        clipped_start_col = max(start_col, min_col)
        clipped_start_row = max(start_row, min_row)
        clipped_end_col = min(end_col, max_col)
        clipped_end_row = min(end_row, max_row)
        for row in range(clipped_start_row, clipped_end_row + 1):
            for col in range(clipped_start_col, clipped_end_col + 1):
                merged[(row, col)] = {
                    "anchor_row": start_row,
                    "anchor_col": start_col,
                    "start_row": clipped_start_row,
                    "start_col": clipped_start_col,
                    "end_row": clipped_end_row,
                    "end_col": clipped_end_col,
                }
    return merged


def render_worksheet_to_image(
    worksheet,
    *,
    dpi: int = _DEFAULT_DPI,
    margin_px: int = _DEFAULT_MARGIN_PX,
) -> Image.Image:
    min_col, min_row, max_col, max_row = _sheet_print_range(worksheet)
    col_widths = {}
    row_heights = {}
    for col in range(min_col, max_col + 1):
        dim = worksheet.column_dimensions[get_column_letter(col)]
        col_widths[col] = _column_width_to_pixels(dim.width, dpi=dpi)
    default_row_height = worksheet.sheet_format.defaultRowHeight
    for row in range(min_row, max_row + 1):
        dim = worksheet.row_dimensions[row]
        row_heights[row] = _row_height_to_pixels(dim.height or default_row_height, dpi=dpi)

    x_positions = {min_col: margin_px}
    current_x = margin_px
    for col in range(min_col, max_col + 1):
        current_x += col_widths[col]
        x_positions[col + 1] = current_x
    y_positions = {min_row: margin_px}
    current_y = margin_px
    for row in range(min_row, max_row + 1):
        current_y += row_heights[row]
        y_positions[row + 1] = current_y

    image_width = current_x + margin_px
    image_height = current_y + margin_px
    canvas = Image.new("RGB", (image_width, image_height), "white")
    draw = ImageDraw.Draw(canvas)

    merged = _merged_rectangles(
        worksheet,
        min_col=min_col,
        min_row=min_row,
        max_col=max_col,
        max_row=max_row,
    )

    for row in range(min_row, max_row + 1):
        for col in range(min_col, max_col + 1):
            merge_rect = merged.get((row, col))
            if merge_rect:
                if (row, col) != (merge_rect["start_row"], merge_rect["start_col"]):
                    continue
                cell = worksheet.cell(row=merge_rect["anchor_row"], column=merge_rect["anchor_col"])
                end_row = merge_rect["end_row"]
                end_col = merge_rect["end_col"]
            else:
                cell = worksheet.cell(row=row, column=col)
                if isinstance(cell, MergedCell):
                    continue
                end_row = row
                end_col = col
            rect = (
                x_positions[col],
                y_positions[row],
                x_positions[end_col + 1],
                y_positions[end_row + 1],
            )
            fill = cell.fill
            if getattr(fill, "fill_type", None) == "solid":
                fill_rgb = _color_to_rgb(fill.fgColor, default=(255, 255, 255))
                draw.rectangle(rect, fill=fill_rgb)

            border = cell.border
            left_width = _line_width(getattr(border.left, "style", None))
            right_width = _line_width(getattr(border.right, "style", None))
            top_width = _line_width(getattr(border.top, "style", None))
            bottom_width = _line_width(getattr(border.bottom, "style", None))
            if left_width:
                left_color = _color_to_rgb(border.left.color, default=(0, 0, 0))
                for offset in range(left_width):
                    draw.line(
                        [(rect[0] + offset, rect[1]), (rect[0] + offset, rect[3])],
                        fill=left_color,
                        width=1,
                    )
            if right_width:
                right_color = _color_to_rgb(border.right.color, default=(0, 0, 0))
                for offset in range(right_width):
                    draw.line(
                        [(rect[2] - 1 - offset, rect[1]), (rect[2] - 1 - offset, rect[3])],
                        fill=right_color,
                        width=1,
                    )
            if top_width:
                top_color = _color_to_rgb(border.top.color, default=(0, 0, 0))
                for offset in range(top_width):
                    draw.line(
                        [(rect[0], rect[1] + offset), (rect[2], rect[1] + offset)],
                        fill=top_color,
                        width=1,
                    )
            if bottom_width:
                bottom_color = _color_to_rgb(border.bottom.color, default=(0, 0, 0))
                for offset in range(bottom_width):
                    draw.line(
                        [(rect[0], rect[3] - 1 - offset), (rect[2], rect[3] - 1 - offset)],
                        fill=bottom_color,
                        width=1,
                    )

            text = _cell_text(cell.value).strip()
            if text:
                font_size_px = int(round(float(cell.font.sz or 11.0) * float(dpi) / 72.0))
                font = _load_font(font_size_px, bold=bool(getattr(cell.font, "bold", False)))
                font_color = _color_to_rgb(getattr(cell.font, "color", None), default=(0, 0, 0))
                alignment = cell.alignment
                horizontal = str(getattr(alignment, "horizontal", "") or "left").strip().lower()
                vertical = str(getattr(alignment, "vertical", "") or "center").strip().lower()
                _draw_cell_text(
                    draw,
                    rect=rect,
                    text=text,
                    font=font,
                    font_color=font_color,
                    horizontal=horizontal,
                    vertical=vertical,
                )

    for image in getattr(worksheet, "_images", []):
        anchor = getattr(image, "anchor", None)
        start = getattr(anchor, "_from", None)
        if start is None:
            continue
        start_col = int(getattr(start, "col", 0)) + 1
        start_row = int(getattr(start, "row", 0)) + 1
        if start_col < min_col or start_row < min_row or start_col > (max_col + 1) or start_row > (max_row + 1):
            continue
        try:
            blob = image._data()
        except Exception:
            continue
        try:
            pasted = Image.open(BytesIO(blob)).convert("RGBA")
        except Exception:
            continue
        offset_x = int(round(float(getattr(start, "colOff", 0) or 0) / float(_EMU_PER_PIXEL) * float(dpi) / 96.0))
        offset_y = int(round(float(getattr(start, "rowOff", 0) or 0) / float(_EMU_PER_PIXEL) * float(dpi) / 96.0))
        target_w = max(1, int(round(float(getattr(image, "width", pasted.width)) * float(dpi) / 96.0)))
        target_h = max(1, int(round(float(getattr(image, "height", pasted.height)) * float(dpi) / 96.0)))
        if (target_w, target_h) != pasted.size:
            pasted = pasted.resize((target_w, target_h))
        x = x_positions.get(start_col, margin_px) + offset_x
        y = y_positions.get(start_row, margin_px) + offset_y
        canvas.paste(pasted, (x, y), pasted)

    return canvas


def render_workbook_to_pdf_bytes(
    workbook,
    *,
    sheet_name: str | None = None,
    dpi: int = _DEFAULT_DPI,
) -> bytes:
    target_sheet = sheet_name or workbook.sheetnames[0]
    image = render_worksheet_to_image(workbook[target_sheet], dpi=dpi)
    buffer = BytesIO()
    image.save(buffer, format="PDF", resolution=float(dpi))
    return buffer.getvalue()


def render_workbook_path_to_pdf(
    workbook_path: Path | str,
    *,
    output_path: Path | str,
    sheet_name: str | None = None,
    dpi: int = _DEFAULT_DPI,
) -> Path:
    workbook = load_workbook(Path(workbook_path))
    pdf_bytes = render_workbook_to_pdf_bytes(workbook, sheet_name=sheet_name, dpi=dpi)
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(pdf_bytes)
    return target


def render_workbooks_to_pdf_bytes(
    workbooks: Iterable,
    *,
    sheet_name: str | None = None,
    dpi: int = _DEFAULT_DPI,
) -> bytes:
    images = [
        render_worksheet_to_image(workbook[sheet_name or workbook.sheetnames[0]], dpi=dpi).convert("RGB")
        for workbook in workbooks
    ]
    if not images:
        return b""
    buffer = BytesIO()
    images[0].save(buffer, format="PDF", save_all=True, append_images=images[1:], resolution=float(dpi))
    return buffer.getvalue()
