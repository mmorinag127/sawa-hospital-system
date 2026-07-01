from __future__ import annotations

from collections.abc import Callable
import hashlib
import json
from typing import Any


FieldLabeler = Callable[[str], str]
FieldValueFormatter = Callable[[object], str]


def sanitize_revision_rows(
    *,
    rows_payload: object,
    fields: list[str],
    field_value_to_str: FieldValueFormatter,
) -> list[list[str]]:
    sanitized: list[list[str]] = []
    if not isinstance(rows_payload, list):
        return sanitized
    for row in rows_payload:
        if isinstance(row, dict):
            sanitized.append([field_value_to_str(row.get(field)) for field in fields])
            continue
        if isinstance(row, list):
            current = [field_value_to_str(cell) for cell in row[: len(fields)]]
            if len(current) < len(fields):
                current.extend([""] * (len(fields) - len(current)))
            sanitized.append(current)
    return sanitized


def normalize_sheet_revision_snapshot(
    *,
    fields: object,
    header: object,
    rows_payload: object,
    row_ids: object,
    field_label: FieldLabeler,
    field_value_to_str: FieldValueFormatter,
) -> dict[str, Any]:
    normalized_fields = [str(field).strip() for field in fields] if isinstance(fields, list) else []
    input_rows = rows_payload if isinstance(rows_payload, list) else []
    max_width = max(
        (len(row) for row in input_rows if isinstance(row, list)),
        default=0,
    )
    if isinstance(header, list):
        max_width = max(max_width, len(header))
    if not normalized_fields:
        normalized_fields = [f"col{idx + 1}" for idx in range(max(max_width, 1))]
    elif len(normalized_fields) < max_width:
        normalized_fields.extend(
            [f"col{idx + 1}" for idx in range(len(normalized_fields), max_width)]
        )

    normalized_rows = sanitize_revision_rows(
        rows_payload=rows_payload,
        fields=normalized_fields,
        field_value_to_str=field_value_to_str,
    )
    normalized_header = (
        [str(cell or "").strip() for cell in header]
        if isinstance(header, list)
        else [field_label(field) for field in normalized_fields]
    )
    if len(normalized_header) < len(normalized_fields):
        normalized_header.extend(
            [field_label(field) for field in normalized_fields[len(normalized_header) :]]
        )

    normalized_row_ids = (
        [str(item).strip() for item in row_ids if str(item).strip()]
        if isinstance(row_ids, list)
        else []
    )
    if len(normalized_row_ids) < len(normalized_rows):
        normalized_row_ids.extend(
            [f"row-{idx + 1}" for idx in range(len(normalized_row_ids), len(normalized_rows))]
        )

    return {
        "fields": normalized_fields,
        "header": normalized_header,
        "rows": normalized_rows,
        "row_ids": normalized_row_ids[: len(normalized_rows)],
    }


def sheet_digest(
    *,
    fields: object,
    header: object,
    rows_payload: object,
    row_ids: object,
    field_label: FieldLabeler,
    field_value_to_str: FieldValueFormatter,
) -> str:
    snapshot = normalize_sheet_revision_snapshot(
        fields=fields,
        header=header,
        rows_payload=rows_payload,
        row_ids=row_ids,
        field_label=field_label,
        field_value_to_str=field_value_to_str,
    )
    payload = json.dumps(snapshot, ensure_ascii=True, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def select_edited_sheet_revision(
    payload: dict[str, Any] | None,
    *,
    exact_only: bool = False,
) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    edited = payload.get("_edited_ocr")
    if not isinstance(edited, dict):
        return None
    candidates: list[dict[str, Any]] = []
    revisions = edited.get("revisions")
    if isinstance(revisions, list):
        candidates.extend(item for item in revisions if isinstance(item, dict))
    latest = edited.get("latest")
    if isinstance(latest, dict):
        candidates.append(latest)
    for revision in reversed(candidates):
        if str(revision.get("ui_mode") or "").strip().lower() != "sheet":
            continue
        if not isinstance(revision.get("rows"), list):
            continue
        if exact_only:
            is_exact = bool(revision.get("sheet_save_only")) or (
                str(revision.get("sheet_save_mode") or "").strip().lower() == "exact"
            )
            if not is_exact:
                continue
        return revision
    return None


def _is_applied_reparse_revision(revision: dict[str, Any] | None) -> bool:
    if not isinstance(revision, dict):
        return False
    return (
        str(revision.get("sheet_save_mode") or "").strip().lower() == "applied"
        and bool(revision.get("reparse_applied"))
    )


def _overlay_applied_reparse_quantity_cells(
    *,
    base_snapshot: dict[str, Any],
    revision_snapshot: dict[str, Any],
) -> dict[str, Any] | None:
    base_fields = [str(field).strip() for field in (base_snapshot.get("fields") or []) if str(field).strip()]
    revision_fields = [
        str(field).strip()
        for field in (revision_snapshot.get("fields") or [])
        if str(field).strip()
    ]
    if not base_fields or not revision_fields:
        return None

    quantity_fields = [
        field
        for field in base_fields
        if field.startswith("qty.") and field in revision_fields
    ]
    if not quantity_fields:
        return None

    base_rows = base_snapshot.get("rows") if isinstance(base_snapshot.get("rows"), list) else []
    revision_rows = revision_snapshot.get("rows") if isinstance(revision_snapshot.get("rows"), list) else []
    if not base_rows or not revision_rows:
        return None

    base_index_by_field = {field: idx for idx, field in enumerate(base_fields)}
    revision_index_by_field = {field: idx for idx, field in enumerate(revision_fields)}
    revision_rows_by_id = {
        row_id: revision_rows[idx]
        for idx, row_id in enumerate(revision_snapshot.get("row_ids") or [])
        if isinstance(row_id, str) and row_id.strip() and idx < len(revision_rows)
    }

    merged_rows: list[list[str]] = []
    base_row_ids = base_snapshot.get("row_ids") if isinstance(base_snapshot.get("row_ids"), list) else []
    for row_idx, base_row in enumerate(base_rows):
        if not isinstance(base_row, list):
            continue
        merged_row = list(base_row)
        base_row_id = (
            str(base_row_ids[row_idx]).strip()
            if row_idx < len(base_row_ids) and str(base_row_ids[row_idx]).strip()
            else ""
        )
        revision_row = revision_rows_by_id.get(base_row_id)
        if revision_row is None and row_idx < len(revision_rows) and isinstance(revision_rows[row_idx], list):
            revision_row = revision_rows[row_idx]
        if isinstance(revision_row, list):
            for field in quantity_fields:
                base_idx = base_index_by_field[field]
                revision_idx = revision_index_by_field[field]
                if revision_idx >= len(revision_row):
                    continue
                revision_value = str(revision_row[revision_idx] or "").strip()
                if revision_value == "":
                    continue
                while len(merged_row) <= base_idx:
                    merged_row.append("")
                merged_row[base_idx] = revision_value
        merged_rows.append(merged_row)

    return {
        "fields": list(base_snapshot.get("fields") or []),
        "header": list(base_snapshot.get("header") or []),
        "rows": merged_rows,
        "row_ids": list(base_snapshot.get("row_ids") or [])[: len(merged_rows)],
    }


def build_sheet_payload_from_revision(
    *,
    order_id: str,
    revision: dict[str, Any],
    field_label: FieldLabeler,
    field_value_to_str: FieldValueFormatter,
) -> dict[str, Any] | None:
    snapshot = normalize_sheet_revision_snapshot(
        fields=revision.get("fields"),
        header=revision.get("header"),
        rows_payload=revision.get("rows"),
        row_ids=revision.get("row_ids"),
        field_label=field_label,
        field_value_to_str=field_value_to_str,
    )
    if not snapshot["rows"]:
        return None
    payload = {"order_id": order_id}
    payload["order_id"] = order_id
    payload["fields"] = snapshot["fields"]
    payload["header"] = snapshot["header"]
    payload["rows"] = snapshot["rows"]
    payload["row_ids"] = snapshot["row_ids"]
    if not isinstance(payload.get("source"), str) or not str(payload.get("source")).strip():
        payload["source"] = "edited_sheet"
    return payload
