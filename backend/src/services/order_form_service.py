from __future__ import annotations

from datetime import datetime
from pathlib import Path
import os
import re

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from src.services import config_service, menu_service


_MONTH_ID_RE = re.compile(r"^\d{4}-\d{2}$")
_OUTPUT_DIR = Path(os.getenv("ORDER_FORM_OUTPUT_DIR", "/tmp/order-form-outputs"))
_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def list_order_form_patterns() -> list[dict]:
    return config_service.get_order_form_patterns()


def _normalize_month_id(month_id: str) -> str:
    value = str(month_id or "").strip()
    if not _MONTH_ID_RE.match(value):
        raise ValueError("month_id must be YYYY-MM")
    return value


def _resolve_pattern(facility: dict, pattern_id: str | None) -> dict:
    if pattern_id:
        pattern = config_service.get_order_form_pattern(pattern_id)
        if pattern:
            return pattern
    facility_pattern = facility.get("order_form_pattern_id")
    if isinstance(facility_pattern, str) and facility_pattern.strip():
        pattern = config_service.get_order_form_pattern(facility_pattern.strip())
        if pattern:
            return pattern
    patterns = config_service.get_order_form_patterns()
    if patterns:
        return dict(patterns[0])
    return {"pattern_id": "PATTERN_A", "label": "標準A", "marker_cells": []}


def _resolve_facility(facility_id: str) -> dict:
    facility = config_service.get_facility_by_id(facility_id)
    if not facility:
        raise ValueError("facility not found")
    return facility


def _collect_menu_entries(month_id: str) -> list[dict]:
    payload = menu_service.get_menu(month_id)
    if not payload:
        raise ValueError("monthly menu not found")
    entries = payload.get("entries")
    if not isinstance(entries, list) or not entries:
        raise ValueError("monthly menu entries not found")
    normalized: list[dict] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        menu_date = entry.get("menu_date")
        daypart = entry.get("daypart")
        name = entry.get("name")
        if not menu_date or not daypart or not name:
            continue
        normalized.append(entry)
    if not normalized:
        raise ValueError("no usable menu entries")
    return normalized


def build_order_form_excel(
    *,
    facility_id: str,
    month_id: str,
    pattern_id: str | None = None,
) -> Path:
    normalized_month = _normalize_month_id(month_id)
    facility = _resolve_facility(facility_id)
    entries = _collect_menu_entries(normalized_month)
    pattern = _resolve_pattern(facility, pattern_id)

    wb = Workbook()
    ws = wb.active
    ws.title = "注文書"

    ws["A1"] = "注文書（自動生成）"
    ws["A1"].font = Font(size=16, bold=True)
    ws["A2"] = "施設ID"
    ws["B2"] = facility_id
    ws["A3"] = "施設名"
    ws["B3"] = str(facility.get("facility_name") or facility.get("name") or "")
    ws["A4"] = "対象月"
    ws["B4"] = normalized_month
    ws["A5"] = "パターン"
    ws["B5"] = str(pattern.get("pattern_id") or "")
    ws["C5"] = str(pattern.get("label") or "")

    headers = ["日付", "区分", "カテゴリ", "献立", "数量(記入)", "備考"]
    header_fill = PatternFill(start_color="E9EEF5", end_color="E9EEF5", fill_type="solid")
    for col_idx, name in enumerate(headers, start=1):
        cell = ws.cell(row=7, column=col_idx, value=name)
        cell.font = Font(bold=True)
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center")

    row_idx = 8
    for entry in entries:
        ws.cell(row=row_idx, column=1, value=str(entry.get("menu_date") or ""))
        ws.cell(row=row_idx, column=2, value=str(entry.get("daypart") or ""))
        ws.cell(row=row_idx, column=3, value=str(entry.get("category") or ""))
        ws.cell(row=row_idx, column=4, value=str(entry.get("name") or ""))
        ws.cell(row=row_idx, column=5, value=None)
        ws.cell(row=row_idx, column=6, value=None)
        row_idx += 1

    width_map = {1: 12, 2: 10, 3: 12, 4: 40, 5: 16, 6: 20}
    for col_idx, width in width_map.items():
        ws.column_dimensions[get_column_letter(col_idx)].width = width
    ws.freeze_panes = "A8"

    marker_cells = pattern.get("marker_cells")
    if isinstance(marker_cells, list):
        for cell_ref in marker_cells:
            if not isinstance(cell_ref, str) or not cell_ref.strip():
                continue
            ref = cell_ref.strip().upper()
            try:
                marker = ws[ref]
            except Exception:
                continue
            marker.value = "■"
            marker.font = Font(color="CC0000", bold=True)
            marker.alignment = Alignment(horizontal="center", vertical="center")

    meta = wb.create_sheet("設定")
    meta.append(["key", "value"])
    meta.append(["generated_at", datetime.utcnow().isoformat()])
    meta.append(["facility_id", facility_id])
    meta.append(["facility_name", str(facility.get("facility_name") or facility.get("name") or "")])
    meta.append(["month_id", normalized_month])
    meta.append(["pattern_id", str(pattern.get("pattern_id") or "")])
    meta.append(["pattern_label", str(pattern.get("label") or "")])
    meta.append(["entry_count", len(entries)])

    file_pattern = str(pattern.get("pattern_id") or "PATTERN_A")
    stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    output = _OUTPUT_DIR / f"order_form_{facility_id}_{normalized_month}_{file_pattern}_{stamp}.xlsx"
    wb.save(output)
    return output
