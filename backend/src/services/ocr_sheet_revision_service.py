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


def build_sheet_payload_from_revision(
    *,
    order_id: str,
    revision: dict[str, Any],
    fallback_sheet: dict[str, Any] | None = None,
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
    payload = dict(fallback_sheet) if isinstance(fallback_sheet, dict) else {"order_id": order_id}
    payload["order_id"] = order_id
    if isinstance(fallback_sheet, dict):
        base_snapshot = normalize_sheet_revision_snapshot(
            fields=fallback_sheet.get("fields"),
            header=fallback_sheet.get("header"),
            rows_payload=fallback_sheet.get("rows"),
            row_ids=fallback_sheet.get("row_ids"),
            field_label=field_label,
            field_value_to_str=field_value_to_str,
        )
        if base_snapshot["rows"]:
            if (
                snapshot["fields"] == base_snapshot["fields"]
                and snapshot["header"] == base_snapshot["header"]
            ):
                payload["fields"] = snapshot["fields"]
                payload["header"] = snapshot["header"]
                payload["rows"] = snapshot["rows"]
                payload["row_ids"] = snapshot["row_ids"]
                if not isinstance(payload.get("source"), str) or not str(payload.get("source")).strip():
                    payload["source"] = "edited_sheet"
                return payload
            revision_rows_by_id = {
                row_id: snapshot["rows"][idx]
                for idx, row_id in enumerate(snapshot["row_ids"])
                if row_id and idx < len(snapshot["rows"])
            }
            rebased_rows: list[list[str]] = []
            for row_idx, base_row in enumerate(base_snapshot["rows"]):
                base_row_id = (
                    base_snapshot["row_ids"][row_idx]
                    if row_idx < len(base_snapshot["row_ids"])
                    else ""
                )
                revision_row = revision_rows_by_id.get(base_row_id)
                if revision_row is None and row_idx < len(snapshot["rows"]):
                    revision_row = snapshot["rows"][row_idx]
                merged_row = list(base_row)
                if revision_row is not None:
                    for col_idx in range(min(len(merged_row), len(revision_row))):
                        merged_row[col_idx] = revision_row[col_idx]
                rebased_rows.append(merged_row)
            payload["fields"] = base_snapshot["fields"]
            payload["header"] = base_snapshot["header"]
            payload["rows"] = rebased_rows
            payload["row_ids"] = base_snapshot["row_ids"][: len(rebased_rows)]
        else:
            payload["fields"] = snapshot["fields"]
            payload["header"] = snapshot["header"]
            payload["rows"] = snapshot["rows"]
            payload["row_ids"] = snapshot["row_ids"]
    else:
        payload["fields"] = snapshot["fields"]
        payload["header"] = snapshot["header"]
        payload["rows"] = snapshot["rows"]
        payload["row_ids"] = snapshot["row_ids"]
    if not isinstance(payload.get("source"), str) or not str(payload.get("source")).strip():
        payload["source"] = "edited_sheet"
    return payload
