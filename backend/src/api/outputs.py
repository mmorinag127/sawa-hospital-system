import csv
from datetime import date as dt_date
from html import escape
from pathlib import Path

import pandas as pd
from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import FileResponse, HTMLResponse
from loguru import logger
from openpyxl import load_workbook
from openpyxl.styles import PatternFill

from src.services.output_builder import (
    build_output_preview,
    build_delivery_preview,
    build_daily_output_bundle,
    build_weekly_weight_summary_workbook,
)
from src.services import order_form_service
from src.api.auth import require_role

router = APIRouter()

_PREVIEW_LIMIT_DEFAULT = 10


def _output_file_for_type(order_id: str, output_type: str) -> tuple[Path, str]:
    if output_type == "labels":
        outputs = build_output_preview(order_id, "labels")
        return Path(outputs["labels"]), "ラベルCSV"
    if output_type == "delivery":
        outputs = build_output_preview(order_id, "delivery")
        return Path(outputs["delivery_note"]), "納品書Excel"
    if output_type == "aggregate":
        outputs = build_output_preview(order_id, "aggregate")
        return Path(outputs["aggregate"]), "総量CSV"
    if output_type == "order_form_saved_sheet":
        return order_form_service.build_saved_sheet_order_form_excel(order_id=order_id), "FAX読取シートExcel"
    raise ValueError("invalid output type")


def _cell_color(fill: PatternFill) -> str:
    color = getattr(fill, "fgColor", None)
    rgb = str(getattr(color, "rgb", "") or "")
    if len(rgb) == 8:
        return f"#{rgb[-6:]}"
    return ""


def _cell_css(cell) -> str:
    styles: list[str] = []
    if cell.font and cell.font.bold:
        styles.append("font-weight:700")
    if cell.alignment:
        if cell.alignment.horizontal:
            styles.append(f"text-align:{cell.alignment.horizontal}")
        if cell.alignment.vertical:
            styles.append(f"vertical-align:{cell.alignment.vertical}")
        if cell.alignment.wrap_text:
            styles.append("white-space:pre-wrap")
    color = _cell_color(cell.fill)
    if color and color != "#000000":
        styles.append(f"background:{color}")
    border_styles = []
    for side_name in ("top", "right", "bottom", "left"):
        side = getattr(cell.border, side_name)
        if getattr(side, "style", None):
            border_styles.append(side_name)
    if border_styles:
        styles.append("border:1px solid #1f2933")
    return ";".join(styles)


def _render_xlsx_preview(path: Path, title: str) -> str:
    workbook = load_workbook(path, data_only=True)
    sheet_html: list[str] = []
    for worksheet in workbook.worksheets:
        if worksheet.sheet_state != "visible":
            continue
        merged_lookup: dict[tuple[int, int], tuple[int, int]] = {}
        covered: set[tuple[int, int]] = set()
        for merged in worksheet.merged_cells.ranges:
            rowspan = merged.max_row - merged.min_row + 1
            colspan = merged.max_col - merged.min_col + 1
            merged_lookup[(merged.min_row, merged.min_col)] = (rowspan, colspan)
            for row_idx in range(merged.min_row, merged.max_row + 1):
                for col_idx in range(merged.min_col, merged.max_col + 1):
                    if (row_idx, col_idx) != (merged.min_row, merged.min_col):
                        covered.add((row_idx, col_idx))
        rows_html: list[str] = []
        for row_idx in range(1, min(worksheet.max_row, 90) + 1):
            cells_html: list[str] = []
            for col_idx in range(1, min(worksheet.max_column, 20) + 1):
                if (row_idx, col_idx) in covered:
                    continue
                cell = worksheet.cell(row=row_idx, column=col_idx)
                attrs = []
                rowspan, colspan = merged_lookup.get((row_idx, col_idx), (1, 1))
                if rowspan > 1:
                    attrs.append(f'rowspan="{rowspan}"')
                if colspan > 1:
                    attrs.append(f'colspan="{colspan}"')
                style = _cell_css(cell)
                if style:
                    attrs.append(f'style="{escape(style)}"')
                value = cell.value
                text = value.strftime("%Y-%m-%d") if hasattr(value, "strftime") else str(value or "")
                cells_html.append(f"<td {' '.join(attrs)}>{escape(text)}</td>")
            rows_html.append(f"<tr>{''.join(cells_html)}</tr>")
        sheet_html.append(
            f"<section><h2>{escape(worksheet.title)}</h2><div class=\"sheet-wrap\"><table>{''.join(rows_html)}</table></div></section>"
        )
    return _wrap_preview_html(title, "".join(sheet_html))


def _render_csv_preview(path: Path, title: str, encoding: str) -> str:
    with open(path, newline="", encoding=encoding, errors="replace") as f:
        reader = csv.reader(f)
        rows = list(reader)
    body = "".join(
        "<tr>" + "".join(f"<td>{escape(str(cell))}</td>" for cell in row) + "</tr>"
        for row in rows[:500]
    )
    return _wrap_preview_html(title, f"<section><h2>{escape(title)}</h2><div class=\"sheet-wrap\"><table>{body}</table></div></section>")


def _wrap_preview_html(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8" />
  <title>{escape(title)} プレビュー</title>
  <style>
    body {{ background:#f3f1ea; color:#1f2a2a; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; margin:0; padding:20px; }}
    h1 {{ font-size:18px; margin:0 0 16px; }}
    h2 {{ font-size:14px; margin:18px 0 8px; }}
    .sheet-wrap {{ background:white; border:1px solid #d8d2c4; overflow:auto; max-height:80vh; }}
    table {{ border-collapse:collapse; background:white; }}
    td {{ border:1px solid #d7d7d7; font-size:12px; min-width:56px; padding:4px 6px; white-space:pre; }}
  </style>
</head>
<body>
  <h1>{escape(title)} プレビュー</h1>
  {body}
</body>
</html>"""


def _preview_csv(path: str, encoding: str, limit: int) -> dict:
    with open(path, newline="", encoding=encoding, errors="replace") as f:
        reader = csv.reader(f)
        header = next(reader, [])
        rows: list[list[str]] = []
        for idx, row in enumerate(reader):
            if idx >= limit:
                break
            rows.append([str(cell) for cell in row])
    return {"headers": header, "rows": rows}


def _normalize_cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and pd.isna(value):
        return ""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _preview_excel(path: str, limit: int) -> dict:
    df = pd.read_excel(path)
    if limit > 0:
        df = df.head(limit)
    headers = [str(col) for col in df.columns]
    rows: list[list[str]] = []
    for _, row in df.iterrows():
        rows.append([_normalize_cell(row.get(col)) for col in df.columns])
    return {"headers": headers, "rows": rows}


def _parse_iso_date(value: str) -> dt_date:
    try:
        return dt_date.fromisoformat(value)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD") from exc


@router.get("/labels", dependencies=[Depends(require_role("operator"))])
def download_labels(order_id: str):
    outputs = build_output_preview(order_id, "labels")
    path = outputs["labels"]
    logger.info("Output download", order_id=order_id, output="labels", path=path)
    return FileResponse(path, media_type="text/csv", filename=f"{order_id}_labels.csv")


@router.get("/delivery-notes", dependencies=[Depends(require_role("operator"))])
def download_delivery(order_id: str):
    outputs = build_output_preview(order_id, "delivery")
    path = outputs["delivery_note"]
    logger.info("Output download", order_id=order_id, output="delivery", path=path)
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=f"{order_id}_delivery.xlsx",
    )


@router.get("/order-form-saved-sheet", dependencies=[Depends(require_role("operator"))])
def download_order_form_saved_sheet(order_id: str):
    try:
        path = order_form_service.build_saved_sheet_order_form_excel(order_id=order_id)
    except ValueError as exc:
        detail = str(exc)
        if detail == "order not found":
            raise HTTPException(status_code=404, detail=detail) from exc
        raise HTTPException(status_code=400, detail=detail) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"order form saved sheet build failed: {exc}") from exc
    logger.info("Output download", order_id=order_id, output="order_form_saved_sheet", path=path)
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=path.name,
    )


@router.get("/manufacturing-aggregate", dependencies=[Depends(require_role("operator"))])
def download_aggregate(order_id: str):
    outputs = build_output_preview(order_id, "aggregate")
    path = outputs["aggregate"]
    logger.info("Output download", order_id=order_id, output="aggregate", path=path)
    return FileResponse(path, media_type="text/csv", filename=f"{order_id}_aggregate.csv")


@router.get("/preview", dependencies=[Depends(require_role("operator"))])
def preview_output(order_id: str, type: str, limit: int = _PREVIEW_LIMIT_DEFAULT):
    limit = max(1, min(limit, _PREVIEW_LIMIT_DEFAULT))
    if type == "labels":
        outputs = build_output_preview(order_id, "labels")
        payload = _preview_csv(outputs["labels"], "cp932", limit)
    elif type == "delivery":
        preview = build_delivery_preview(order_id)
        headers = preview.get("headers", [])
        rows = preview.get("rows", [])
        payload = {
            "headers": headers,
            "rows": rows[:limit],
            "ocr_entry_count": preview.get("ocr_entry_count"),
        }
    elif type == "aggregate":
        outputs = build_output_preview(order_id, "aggregate")
        payload = _preview_csv(outputs["aggregate"], "cp932", limit)
    else:
        raise HTTPException(status_code=400, detail="invalid output type")
    return {"type": type, **payload}


@router.get("/file-preview", response_class=HTMLResponse, dependencies=[Depends(require_role("operator"))])
def preview_output_file(order_id: str, type: str):
    try:
        path, title = _output_file_for_type(order_id, type)
        if type in {"labels", "aggregate"}:
            html = _render_csv_preview(path, title, "cp932")
        else:
            html = _render_xlsx_preview(path, title)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"failed to render output preview: {exc}") from exc
    return HTMLResponse(html)


@router.get("/daily-bundle", dependencies=[Depends(require_role("operator"))])
def download_daily_bundle(
    date: str,
    bundle_type: str = "both",
    status: str | None = None,
):
    target_date = _parse_iso_date(date)
    try:
        bundle_path, summary = build_daily_output_bundle(
            target_date,
            bundle_type=bundle_type,
            status=status,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"bundle build failed: {exc}") from exc
    headers = {
        "X-Daily-Bundle-Total-Orders": str(summary.get("total_orders", 0)),
        "X-Daily-Bundle-Success-Orders": str(summary.get("success_orders", 0)),
        "X-Daily-Bundle-Error-Orders": str(summary.get("error_orders", 0)),
        "X-Daily-Bundle-Type": str(summary.get("bundle_type", bundle_type)),
    }
    file_format = str(summary.get("file_format") or "xlsx")
    filename = f"daily_outputs_{target_date.isoformat()}_{summary.get('bundle_type', bundle_type)}.{file_format}"
    media_type = (
        "application/zip"
        if file_format == "zip"
        else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    return FileResponse(
        str(bundle_path),
        media_type=media_type,
        filename=filename,
        headers=headers,
    )


@router.get("/weekly-weight", dependencies=[Depends(require_role("operator"))])
def download_weekly_weight(date: str, status: str | None = None):
    target_date = _parse_iso_date(date)
    try:
        weight_path = build_weekly_weight_summary_workbook(target_date, status=status)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"weekly weight build failed: {exc}") from exc
    if weight_path is None:
        raise HTTPException(status_code=400, detail="対象週の重量表出力対象がありません")
    return FileResponse(
        str(weight_path),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=weight_path.name,
    )
