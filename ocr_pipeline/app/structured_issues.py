from __future__ import annotations

import re
from typing import Any


_FULLWIDTH_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")
_NUMERIC_CELL_RE = re.compile(r"^[0-9\s\n]+$")


def _normalize_digits_text(value: object) -> str:
    return str(value or "").translate(_FULLWIDTH_DIGITS).strip()


def _coerce_int(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _resolve_header_rows(template: dict[str, Any] | None) -> int:
    if not isinstance(template, dict):
        return 0
    return max(0, _coerce_int(template.get("header_rows"), 0))


def _resolve_max_numeric_value(template: dict[str, Any] | None) -> int | None:
    if not isinstance(template, dict):
        return None
    post = template.get("postprocess")
    if not isinstance(post, dict):
        return None
    raw = post.get("qty_max_value")
    try:
        value = int(raw) if raw is not None else None
    except Exception:
        value = None
    if value is None or value < 0:
        return None
    return value


def _numeric_value(text: str) -> int | None:
    compact = re.sub(r"\s+", "", text)
    if not compact or not _NUMERIC_CELL_RE.fullmatch(compact):
        return None
    try:
        return int(compact.replace("\n", ""))
    except ValueError:
        return None


def detect_table_cell_issues(
    *,
    tables: list[dict[str, Any]] | None,
    template: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if not isinstance(tables, list) or not tables:
        return []

    header_rows = _resolve_header_rows(template)
    max_numeric_value = _resolve_max_numeric_value(template)
    issues: list[dict[str, Any]] = []
    seen: set[tuple[str, int, int, str]] = set()

    for table in tables:
        if not isinstance(table, dict):
            continue
        table_id = str(table.get("table_id") or "").strip()
        page_index = _coerce_int(table.get("page_index"), 0)
        raw_cells = table.get("cells")
        if not isinstance(raw_cells, list):
            continue
        for cell in raw_cells:
            if not isinstance(cell, dict):
                continue
            row_index = _coerce_int(cell.get("row_index"), -1)
            col_index = _coerce_int(cell.get("col_index"), -1)
            if row_index < header_rows or col_index < 0:
                continue
            text = _normalize_digits_text(cell.get("text"))
            numeric_value = _numeric_value(text)
            if numeric_value is None:
                continue
            row_span = max(_coerce_int(cell.get("row_span"), 1), 1)
            col_span = max(_coerce_int(cell.get("col_span"), 1), 1)
            source_row_index = row_index - header_rows

            candidates: list[tuple[str, str]] = []
            if "\n" in text:
                candidates.append(("multiline_numeric_cell", "high"))
            if row_span > 1 or col_span > 1:
                candidates.append(("merged_numeric_cell", "high"))
            if max_numeric_value is not None and numeric_value > max_numeric_value:
                candidates.append(("numeric_outlier", "warning"))

            for issue_code, severity in candidates:
                issue_key = (table_id, source_row_index, col_index, issue_code)
                if issue_key in seen:
                    continue
                seen.add(issue_key)
                issue: dict[str, Any] = {
                    "table_id": table_id,
                    "page_index": page_index,
                    "source_row_index": source_row_index,
                    "column_index": col_index,
                    "issue_code": issue_code,
                    "severity": severity,
                    "source": "yomitoku_structured",
                    "text": text,
                    "value": numeric_value,
                    "row_span": row_span,
                    "col_span": col_span,
                }
                bbox = cell.get("bbox")
                if isinstance(bbox, list) and len(bbox) == 4:
                    issue["bbox"] = [float(item) for item in bbox]
                if issue_code == "numeric_outlier" and max_numeric_value is not None:
                    issue["max_allowed"] = int(max_numeric_value)
                issues.append(issue)

    return issues
