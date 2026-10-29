"""Pure OCR audit response parsing, independent of order/database operations."""
import re
from typing import Any


def parse_audit_int(value: object) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or not re.fullmatch(r"-?\d+", text):
        return None
    try:
        return int(text)
    except Exception:
        return None


def parse_audit_confidence(value: object) -> float:
    if value is None:
        return 0.0
    text = str(value).strip()
    if not text:
        return 0.0
    try:
        parsed = float(text)
    except Exception:
        return 0.0
    if parsed < 0.0:
        return 0.0
    if parsed > 1.0:
        return 1.0
    return parsed


def normalize_issue_code(value: object) -> str:
    normalized = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return re.sub(r"[^a-z0-9_]+", "", normalized)


def parse_issues(*, rows: list[list[str]], fields: list[str]) -> list[dict[str, Any]]:
    if not rows or not fields:
        return []
    field_index = {field: index for index, field in enumerate(fields)}
    allowed = {
        "row_count_shortfall", "week_scope_mismatch", "date_anchor_drift",
        "mirrored_sibling_columns", "column_swap", "invalid_numeric_spike",
        "all_quantity_blank", "unexpected_dense_fill", "overextended_span", "missing_blank_anchor_rows",
    }
    issues = []
    for row in rows:
        if not isinstance(row, list):
            continue

        def value(name, default=""):
            index = field_index.get(name)
            return row[index] if index is not None and index < len(row) else default

        code = normalize_issue_code(value("issue_code"))
        severity = str(value("severity")).strip().lower()
        evidence = str(value("evidence")).strip()
        reason = str(value("reason")).strip()
        row_index = parse_audit_int(value("row_index", None))
        column_index = parse_audit_int(value("column_index", None))
        confidence = parse_audit_confidence(value("confidence", None))
        if code and code not in allowed and not reason and not evidence:
            continue
        if not code and not reason:
            continue
        issue = {"issue_code": code or "unknown_issue", "severity": severity or "medium",
                 "confidence": round(confidence, 4), "evidence": evidence, "reason": reason}
        if row_index is not None:
            issue["row_index"] = int(row_index)
        if column_index is not None:
            issue["column_index"] = int(column_index)
        issues.append(issue)
    return issues
