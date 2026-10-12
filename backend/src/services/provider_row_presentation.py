"""Legacy display formatting only; raw OCR payloads remain authoritative."""

from __future__ import annotations

import re
from typing import Any


_FULLWIDTH_TRANSLATION = str.maketrans(
    {
        "０": "0",
        "１": "1",
        "２": "2",
        "３": "3",
        "４": "4",
        "５": "5",
        "６": "6",
        "７": "7",
        "８": "8",
        "９": "9",
        "／": "/",
        "－": "-",
        "ー": "-",
        "　": " ",
    }
)


def _normalize_digits_text(value: str) -> str:
    normalized = (
        value.translate(_FULLWIDTH_TRANSLATION)
        .replace(",", "")
        .replace("，", "")
        .replace(" ", "")
    )
    if re.fullmatch(r"\d+", normalized):
        return normalized
    return ""


def _normalize_daypart_text(value: str) -> str:
    normalized = value.translate(_FULLWIDTH_TRANSLATION)
    if "朝" in normalized:
        return "朝"
    if "昼" in normalized:
        return "昼"
    if "夕" in normalized or "夜" in normalized:
        return "夕"
    return ""


def _normalize_date_text(value: str) -> str:
    normalized = value.translate(_FULLWIDTH_TRANSLATION).replace(" ", "")
    match = re.search(r"\d{4}[/-](\d{1,2})[/-](\d{1,2})", normalized)
    if match:
        month = int(match.group(1))
        day = int(match.group(2))
        if 1 <= month <= 12 and 1 <= day <= 31:
            return f"{month}/{day}"
        return ""
    match = re.search(r"(\d{1,2})[/-](\d{1,2})", normalized)
    if not match:
        return ""
    month = int(match.group(1))
    day = int(match.group(2))
    if 1 <= month <= 12 and 1 <= day <= 31:
        return f"{month}/{day}"
    return ""


def _normalize_row_index(value: object) -> str:
    if value is None:
        return ""
    text = str(value).translate(_FULLWIDTH_TRANSLATION).strip()
    if not re.fullmatch(r"\d+", text):
        return ""
    return str(int(text))


def _is_qty_field(field: str) -> bool:
    return field.startswith("qty.")


def _is_date_field(field: str) -> bool:
    key = field.lower()
    return key in {"date_mmdd", "date"} or key.startswith("date")


def _is_daypart_field(field: str) -> bool:
    return field in {"daypart", "meal", "time"}


def _is_aux_field(field: str) -> bool:
    return field.startswith("aux.")


def _is_remarks_field(field: str) -> bool:
    return field in {"remarks", "note"}


def _full_table_patch_fields(row_fields: list[str]) -> list[str]:
    return [
        field
        for field in row_fields
        if _is_qty_field(field) or _is_aux_field(field) or _is_remarks_field(field)
    ]


def _row_has_field(row: dict[str, Any], field: str) -> bool:
    if field in row:
        return True
    current: Any = row
    for part in field.split("."):
        if not isinstance(current, dict) or part not in current:
            return False
        current = current.get(part)
    return True


def _normalize_row_cell(field: str, value: object, *, full_table_mode: bool = False) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if _is_qty_field(field):
        return _normalize_digits_text(text)
    if full_table_mode:
        return text
    if _is_daypart_field(field):
        return _normalize_daypart_text(text)
    if _is_date_field(field):
        return _normalize_date_text(text)
    return text


def _quantity_fields(row_fields: list[str]) -> list[str]:
    return [field for field in row_fields if _is_qty_field(field)]


def _normalize_rows(
    rows: object,
    row_fields: list[str],
    *,
    quantity_only_mode: bool,
    full_table_mode: bool,
) -> list[dict[str, str]]:
    if not isinstance(rows, list):
        return []
    normalized: list[dict[str, str]] = []
    qty_fields = _quantity_fields(row_fields)
    for row in rows:
        if not isinstance(row, dict):
            continue
        normalized_row: dict[str, str] = {}
        non_empty = False
        if quantity_only_mode and qty_fields:
            target_fields = qty_fields
        elif full_table_mode:
            target_fields = _full_table_patch_fields(row_fields)
        else:
            target_fields = row_fields
        for field in target_fields:
            if full_table_mode and not _row_has_field(row, field):
                continue
            cell = _normalize_row_cell(field, row.get(field), full_table_mode=full_table_mode)
            normalized_row[field] = cell
            if cell:
                non_empty = True
        if quantity_only_mode or full_table_mode:
            row_index = _normalize_row_index(row.get("row_index"))
            if row_index:
                normalized_row["row_index"] = row_index
                non_empty = True
            elif full_table_mode:
                continue
        if non_empty or (full_table_mode and not quantity_only_mode):
            normalized.append(normalized_row)
    return normalized


def _normalize_payload(
    payload: dict[str, Any],
    *,
    row_fields: list[str],
    quantity_only_mode: bool,
    full_table_mode: bool,
) -> dict[str, Any]:
    normalized = dict(payload)

    raw_name = normalized.get("facility_name")
    facility_name = str(raw_name or "").strip() or None

    raw_dates = normalized.get("date_strings")
    date_strings: list[str] = []
    if isinstance(raw_dates, list):
        for item in raw_dates:
            date_text = _normalize_date_text(str(item))
            if date_text and date_text not in date_strings:
                date_strings.append(date_text)

    normalized["facility_name"] = facility_name
    normalized["date_strings"] = date_strings
    normalized["rows"] = _normalize_rows(
        normalized.get("rows"),
        row_fields,
        quantity_only_mode=quantity_only_mode,
        full_table_mode=full_table_mode,
    )
    if full_table_mode:
        returned_row_indexes: list[int] = []
        for row in normalized["rows"]:
            if not isinstance(row, dict):
                continue
            row_index = _normalize_row_index(row.get("row_index"))
            if not row_index:
                continue
            try:
                returned_row_indexes.append(int(row_index))
            except Exception:
                continue
        if returned_row_indexes:
            normalized["_ocr_returned_row_indexes"] = sorted(set(returned_row_indexes))
    return normalized
