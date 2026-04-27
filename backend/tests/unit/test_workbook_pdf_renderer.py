import pathlib
import sys

from openpyxl import Workbook
from openpyxl.styles import Border, Side

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.services.workbook_pdf_renderer import render_workbook_to_pdf_bytes, render_worksheet_to_image  # noqa: E402


def test_render_workbook_to_pdf_bytes_returns_pdf_payload() -> None:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Sheet1"
    worksheet.print_area = "A1:D6"
    worksheet["A1"] = "施設名"
    worksheet["A2"] = "日付"
    worksheet["B2"] = "区分"
    worksheet["C2"] = "献立"
    worksheet["D2"] = "常食"
    worksheet["A3"] = "4/5"
    worksheet["B3"] = "朝"
    worksheet["C3"] = "サンプル"
    worksheet["D3"] = "12"

    pdf_bytes = render_workbook_to_pdf_bytes(workbook, dpi=144)

    assert pdf_bytes.startswith(b"%PDF")
    assert len(pdf_bytes) > 1000


def test_render_workbook_to_pdf_bytes_clips_merged_range_that_extends_past_print_area() -> None:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Sheet1"
    worksheet.print_area = "A1:M5"
    worksheet.merge_cells("M1:P1")
    worksheet["M1"] = "右にはみ出す結合セル"
    worksheet["A2"] = "ok"

    pdf_bytes = render_workbook_to_pdf_bytes(workbook, dpi=144)

    assert pdf_bytes.startswith(b"%PDF")
    assert len(pdf_bytes) > 1000


def test_render_workbook_to_pdf_bytes_renders_merged_range_with_anchor_outside_print_area() -> None:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Sheet1"
    worksheet.print_area = "B1:D4"
    worksheet.merge_cells("A1:C1")
    worksheet["A1"] = "左にはみ出す結合セル"
    worksheet["B2"] = "ok"

    pdf_bytes = render_workbook_to_pdf_bytes(workbook, dpi=144)

    assert pdf_bytes.startswith(b"%PDF")
    assert len(pdf_bytes) > 1000


def test_render_worksheet_to_image_draws_shared_border_once() -> None:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.print_area = "A1:B1"
    side = Side(style="medium", color="000000")
    worksheet["A1"].border = Border(right=side)
    worksheet["B1"].border = Border(left=side)

    image = render_worksheet_to_image(worksheet, dpi=96)
    y = image.height // 2
    black_columns = [x for x in range(image.width) if image.getpixel((x, y)) == (0, 0, 0)]

    assert len(black_columns) == 2
