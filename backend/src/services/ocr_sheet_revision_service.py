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
    selected_id = str(edited.get("selected_revision_id") or "").strip()
    selected_evidence_id = str(edited.get("base_evidence_run_id") or "").strip()
    if not selected_id or not selected_evidence_id:
        return None
    execution = payload.get("_ocr_execution")
    if isinstance(execution, dict) and execution.get("evidence_run_id") != selected_evidence_id:
        return None
    for revision in candidates:
        if revision.get("revision_id") != selected_id or revision.get("base_evidence_run_id") != selected_evidence_id:
            continue
        if revision.get("workflow_job_id") or revision.get("draft_from_reparse_reject") or revision.get("auto_apply_blocked"):
            return None
        if str(revision.get("ui_mode") or "").strip().lower() != "sheet":
            continue
        if not isinstance(revision.get("rows"), list):
            continue
        if exact_only:
            is_exact = str(revision.get("sheet_save_mode") or "").strip().lower() == "exact"
            if not is_exact:
                continue
        return revision
    return None


def build_sheet_payload_from_revision(
    *,
    order_id: str,
    revision: dict[str, Any],
    field_label: FieldLabeler,
    field_value_to_str: FieldValueFormatter,
) -> dict[str, Any] | None:
    if revision.get("workflow_job_id") or revision.get("draft_from_reparse_reject") or revision.get("auto_apply_blocked"):
        return None
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
