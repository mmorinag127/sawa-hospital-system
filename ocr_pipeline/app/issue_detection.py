from __future__ import annotations

import re
from typing import Any


_FULLWIDTH_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")
_DIGITS_ONLY_RE = re.compile(r"^[0-9\s\n]+$")
_FORBIDDEN_HEADER_TOKENS = ("禁食", "肉禁", "魚禁")


def _normalize_text(value: object) -> str:
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


def _numeric_value(text: str) -> int | None:
    compact = re.sub(r"\s+", "", text)
    if not compact or not _DIGITS_ONLY_RE.fullmatch(compact):
        return None
    try:
        return int(compact)
    except ValueError:
        return None


def _header_tokens(table: dict[str, Any]) -> list[str]:
    tokens: list[str] = []
    rows = table.get("rows")
    if isinstance(rows, list):
        for row in rows[:2]:
            if not isinstance(row, list):
                continue
            for value in row:
                token = _normalize_text(value)
                if token:
                    tokens.append(token)
    if tokens:
        return tokens
    raw_cells = table.get("cells")
    if not isinstance(raw_cells, list):
        return []
    for cell in raw_cells:
        if not isinstance(cell, dict):
            continue
        if _coerce_int(cell.get("row_index"), 99) > 1:
            continue
        token = _normalize_text(cell.get("text"))
        if token:
            tokens.append(token)
    return tokens


def _build_issue(
    *,
    issue_code: str,
    reason: str,
    severity: str,
    table_id: str,
    page_index: int,
    row_index: int,
    col_index: int,
    row_span: int,
    col_span: int,
    text: str | None = None,
    bbox: list[float] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    issue: dict[str, Any] = {
        "table_id": table_id,
        "page_index": page_index,
        "row_index": row_index,
        "col_index": col_index,
        "source_row_index": row_index,
        "column_index": col_index,
        "issue_code": issue_code,
        "reason": reason,
        "severity": severity,
        "source": "yomitoku_structured",
        "row_span": row_span,
        "col_span": col_span,
    }
    if text:
        issue["text"] = text
    if isinstance(bbox, list) and len(bbox) == 4:
        issue["bbox"] = [float(item) for item in bbox]
    if isinstance(extra, dict):
        issue.update(extra)
    return issue


def merge_cell_issues(*issue_groups: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[tuple[str, int, str, int, int, str]] = set()
    for issue_group in issue_groups:
        if not isinstance(issue_group, list):
            continue
        for issue in issue_group:
            if not isinstance(issue, dict):
                continue
            key = (
                str(issue.get("source") or "").strip(),
                _coerce_int(issue.get("page_index"), 0),
                str(issue.get("table_id") or "").strip(),
                _coerce_int(issue.get("row_index"), _coerce_int(issue.get("source_row_index"), -1)),
                _coerce_int(issue.get("col_index"), _coerce_int(issue.get("column_index"), -1)),
                str(issue.get("issue_code") or "").strip(),
            )
            if key in seen:
                continue
            seen.add(key)
            merged.append(dict(issue))
    return merged


def detect_table_cell_issues(
    *,
    tables: list[dict[str, Any]] | None,
    template: dict[str, Any] | None = None,
    template_id: str | None = None,
) -> list[dict[str, Any]]:
    if not isinstance(tables, list) or not tables:
        return []

    header_rows = _resolve_header_rows(template)
    normalized_template_id = str(template_id or "").strip().lower()
    issues: list[dict[str, Any]] = []

    for table in tables:
        if not isinstance(table, dict):
            continue
        table_id = str(table.get("table_id") or "").strip()
        page_index = _coerce_int(table.get("page_index"), 0)
        raw_cells = table.get("cells")
        if isinstance(raw_cells, list):
            for cell in raw_cells:
                if not isinstance(cell, dict):
                    continue
                row_index = _coerce_int(cell.get("row_index"), -1)
                col_index = _coerce_int(cell.get("col_index"), -1)
                if row_index < header_rows or col_index < 0:
                    continue
                text = _normalize_text(cell.get("text"))
                if _numeric_value(text) is None:
                    continue
                row_span = max(_coerce_int(cell.get("row_span"), 1), 1)
                col_span = max(_coerce_int(cell.get("col_span"), 1), 1)
                source_row_index = max(row_index - header_rows, 0)
                bbox = cell.get("bbox") if isinstance(cell.get("bbox"), list) else None
                if "\n" in text:
                    issues.append(
                        _build_issue(
                            issue_code="multiline_numeric_cell",
                            reason="cell_contains_multiline_numeric_text",
                            severity="high",
                            table_id=table_id,
                            page_index=page_index,
                            row_index=row_index,
                            col_index=col_index,
                            row_span=row_span,
                            col_span=col_span,
                            text=text,
                            bbox=bbox,
                            extra={"source_row_index": source_row_index},
                        )
                    )
                if row_span > 1 or col_span > 1:
                    issues.append(
                        _build_issue(
                            issue_code="merged_numeric_cell",
                            reason="numeric_cell_spans_multiple_rows_or_columns",
                            severity="high",
                            table_id=table_id,
                            page_index=page_index,
                            row_index=row_index,
                            col_index=col_index,
                            row_span=row_span,
                            col_span=col_span,
                            text=text,
                            bbox=bbox,
                            extra={"source_row_index": source_row_index},
                        )
                    )

        if "floor" in normalized_template_id:
            header_tokens = _header_tokens(table)
            if any(token in "".join(header_tokens) for token in _FORBIDDEN_HEADER_TOKENS):
                issues.append(
                    _build_issue(
                        issue_code="header_template_mismatch",
                        reason="floor_template_contains_forbidden_headers",
                        severity="warning",
                        table_id=table_id,
                        page_index=page_index,
                        row_index=0,
                        col_index=-1,
                        row_span=1,
                        col_span=1,
                        bbox=table.get("bbox") if isinstance(table.get("bbox"), list) else None,
                        extra={
                            "matched_template_id": template_id,
                            "header_tokens": header_tokens[:12],
                        },
                    )
                )

    return merge_cell_issues(issues)
