from __future__ import annotations

import json
import os
import re
from statistics import median
from typing import Any

from src.services import gemini_ocr_service, hakodate_ocr_evidence_service

_STRUCTURAL_FIELDS = {
    "date",
    "date_mmdd",
    "daypart",
    "menu",
    "menu_name",
    "menu_category",
    "note",
    "notes",
    "remarks",
}
_MAX_LLM_ROWS = 80
_MAX_LLM_EVIDENCE = 240
_MAX_LLM_WARNINGS = 80
_MAX_PAYLOAD_WALK_ITEMS = 5000
_MAX_CONTEXT_ITEMS = 240

_EVIDENCE_VALUE_KEYS = (
    "value",
    "value_normalized",
    "value_text",
    "normalized_value",
    "raw_text",
    "text",
    "recognized_text",
    "ocr_text",
)
_EVIDENCE_ROW_KEYS = (
    "target_row_index",
    "row_index",
    "source_row_index",
    "sheet_row_index",
)
_EVIDENCE_COL_KEYS = (
    "target_col_index",
    "col_index",
    "source_col_index",
    "sheet_col_index",
)


def _normalize_text(value: object) -> str:
    return str(value or "").strip()


def _normalize_numeric_text(value: object) -> str:
    text = _normalize_text(value).translate(str.maketrans("０１２３４５６７８９．－", "0123456789.-"))
    text = re.sub(r"[^\d.\-]", "", text)
    if text.count(".") > 1:
        head, *tail = text.split(".")
        text = head + "." + "".join(tail)
    return text


def _numeric_value(value: object) -> float | None:
    text = _normalize_numeric_text(value)
    if not text or text in {"-", ".", "-."}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _numeric_display(value: object) -> str:
    numeric = _numeric_value(value)
    if numeric is None:
        return _normalize_text(value)
    if numeric.is_integer():
        return str(int(numeric))
    return str(numeric)


def _digit_groups(value: object) -> list[str]:
    text = _normalize_text(value).translate(str.maketrans("０１２３４５６７８９", "0123456789"))
    return [match.group(0) for match in re.finditer(r"\d+(?:\.\d+)?", text)]


def _is_structural_field(field: object) -> bool:
    normalized = _normalize_text(field).lower()
    if normalized in _STRUCTURAL_FIELDS:
        return True
    return normalized.startswith("date") or normalized.startswith("daypart") or normalized.startswith("menu")


def _coerce_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if re.fullmatch(r"-?\d+", text):
            return int(text)
    return None


def _first_int(mapping: dict[str, Any], keys: tuple[str, ...]) -> int | None:
    for key in keys:
        value = _coerce_int(mapping.get(key))
        if value is not None:
            return value
    return None


def _first_text(mapping: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        text = _normalize_text(mapping.get(key))
        if text:
            return text
    return ""


def _sheet_field(value: object) -> str:
    token = _normalize_text(value)
    return "remarks" if token == "note" else token


def _sheet_dimensions(sheet: dict[str, Any]) -> tuple[list[str], list[str], list[list[str]]]:
    raw_rows = sheet.get("rows") if isinstance(sheet, dict) else None
    raw_row_items = raw_rows if isinstance(raw_rows, list) else []
    dict_row_keys: list[str] = []
    for row in raw_row_items:
        if not isinstance(row, dict):
            continue
        for key in row.keys():
            text = str(key or "").strip()
            if text and text not in dict_row_keys:
                dict_row_keys.append(text)
    configured_fields = [
        str(item or "").strip()
        for item in (sheet.get("fields") if isinstance(sheet.get("fields"), list) else [])
    ]
    if not configured_fields and dict_row_keys:
        preferred = [key for key in ("date", "daypart", "menu", "menu_name", "menu_category") if key in dict_row_keys]
        configured_fields = preferred + [key for key in dict_row_keys if key not in preferred]
    width = max(
        len(configured_fields),
        len(sheet.get("header") or []) if isinstance(sheet.get("header"), list) else 0,
        *(len(row) for row in raw_row_items if isinstance(row, list)),
        1,
    )
    fields = [
        str(configured_fields[idx] if idx < len(configured_fields) else f"col{idx + 1}")
        for idx in range(width)
    ]
    header = [
        str((sheet.get("header") or [])[idx] if isinstance(sheet.get("header"), list) and idx < len(sheet.get("header") or []) else fields[idx])
        for idx in range(width)
    ]
    rows: list[list[str]] = []
    for row in raw_row_items:
        if isinstance(row, list):
            rows.append([str(row[idx] if idx < len(row) else "") for idx in range(width)])
        elif isinstance(row, dict):
            rows.append([str(row.get(fields[idx]) or "") for idx in range(width)])
    normalized_rows = [
        [str(row[idx] if idx < len(row) else "") for idx in range(width)]
        for row in rows
    ]
    return fields, header, normalized_rows


def _row_context(sheet: dict[str, Any]) -> list[dict[str, Any]]:
    fields, header, rows = _sheet_dimensions(sheet)
    contexts: list[dict[str, Any]] = []
    for row_index, row in enumerate(rows):
        values = {fields[idx]: row[idx] for idx in range(len(fields))}
        quantities = []
        for col_index, field in enumerate(fields):
            if _is_structural_field(field):
                continue
            value = row[col_index] if col_index < len(row) else ""
            numeric = _numeric_value(value)
            quantities.append(
                {
                    "col_index": col_index,
                    "field": field,
                    "label": header[col_index],
                    "value": value,
                    "numeric": numeric,
                }
            )
        contexts.append(
            {
                "row_index": row_index,
                "date": values.get("date") or values.get("date_mmdd") or "",
                "daypart": values.get("daypart") or "",
                "menu": values.get("menu") or values.get("menu_name") or "",
                "quantities": quantities,
            }
        )
    return contexts


def _ocr_items_from_sheet(sheet: dict[str, Any]) -> list[dict[str, Any]]:
    items = sheet.get("ocr_numeric_cell_items")
    if not isinstance(items, list):
        return []
    normalized: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        row_index = item.get("target_row_index")
        col_index = item.get("target_col_index")
        if not isinstance(row_index, int) or not isinstance(col_index, int):
            continue
        value = _normalize_text(item.get("value"))
        if not value:
            continue
        normalized.append(
            {
                "target_row_index": row_index,
                "target_col_index": col_index,
                "value": value,
                "numeric": _numeric_value(value),
                "classification": _normalize_text(item.get("classification")),
                "confidence_tier": _normalize_text(item.get("confidence_tier")),
                "placement_basis": _normalize_text(item.get("placement_basis")),
                "source": "sheet.ocr_numeric_cell_items",
            }
        )
    return normalized


def _hakodate_target_cells_from_payload(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    candidates = (
        ((payload.get("hakodate_preprocessing") or {}).get("target_cell_map")),
        ((payload.get("hakodate_preprocessing") or {}).get("target_cells")),
        payload.get("hakodate_target_cell_map"),
        payload.get("target_cell_map"),
    )
    for candidate in candidates:
        if isinstance(candidate, list):
            return [item for item in candidate if isinstance(item, dict)]
    return []


def _hakodate_evidence_records_from_payload(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    raw_records: Any = None
    hakodate_evidence = payload.get("hakodate_ocr_evidence")
    if isinstance(hakodate_evidence, dict) and isinstance(hakodate_evidence.get("records"), list):
        raw_records = hakodate_evidence.get("records")
    elif isinstance(payload.get("hakodate_ocr_evidence_records"), list):
        raw_records = payload.get("hakodate_ocr_evidence_records")
    if not isinstance(raw_records, list):
        return []
    return [item for item in raw_records if isinstance(item, dict)]


def _row_identity_map(sheet: dict[str, Any]) -> dict[tuple[str, str, str], int]:
    mapped: dict[tuple[str, str, str], int] = {}
    for context in _row_context(sheet):
        key = (
            _normalize_text(context.get("date")),
            _normalize_text(context.get("daypart")),
            _normalize_text(context.get("menu")),
        )
        if all(key) and key not in mapped:
            mapped[key] = int(context.get("row_index") or 0)
    return mapped


def _target_sheet_indexes(
    *,
    target: dict[str, Any],
    sheet: dict[str, Any],
    fields: list[str],
) -> tuple[int | None, int | None, dict[str, Any]]:
    field_index = {_sheet_field(field): idx for idx, field in enumerate(fields) if _normalize_text(field)}
    row_by_identity = _row_identity_map(sheet)
    logical_targets = target.get("logical_targets") if isinstance(target.get("logical_targets"), list) else []
    candidates = [target, *[item for item in logical_targets if isinstance(item, dict)]]
    for candidate in candidates:
        row_index = _first_int(candidate, _EVIDENCE_ROW_KEYS)
        field = _sheet_field(
            _first_text(
                candidate,
                ("semantic_field", "field", "slot_name"),
            )
        )
        col_index = field_index.get(field)
        if row_index is None:
            key = (
                _first_text(candidate, ("date", "date_key")),
                _first_text(candidate, ("daypart", "daypart_key")),
                _first_text(candidate, ("menu_name", "menu", "menu_key")),
            )
            row_index = row_by_identity.get(key)
        if row_index is not None and col_index is not None:
            return row_index, col_index, candidate
    return None, None, {}


def _ocr_items_from_hakodate_assignment(
    *,
    sheet: dict[str, Any],
    evidence_payload: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    fields, _header, rows = _sheet_dimensions(sheet)
    targets = _hakodate_target_cells_from_payload(evidence_payload)
    records = _hakodate_evidence_records_from_payload(evidence_payload)
    if not targets or not records:
        return []
    assignment = hakodate_ocr_evidence_service.assign_evidence_to_target_cells(
        evidence_records=records,
        target_cells=targets,
    )
    assignments = assignment.get("assignments") if isinstance(assignment, dict) else None
    if not isinstance(assignments, list):
        return []
    target_by_id = {
        str(target.get("target_cell_id") or target.get("sheet_cell") or ""): target
        for target in targets
        if isinstance(target, dict)
    }
    normalized: list[dict[str, Any]] = []
    for item in assignments:
        if not isinstance(item, dict):
            continue
        value = _normalize_text(item.get("assigned_value"))
        if not value:
            continue
        target = target_by_id.get(str(item.get("target_cell_id") or ""))
        if not isinstance(target, dict):
            continue
        row_index, col_index, logical_target = _target_sheet_indexes(
            target=target,
            sheet=sheet,
            fields=fields,
        )
        if row_index is None or col_index is None:
            continue
        if not (0 <= row_index < len(rows) and 0 <= col_index < len(fields)):
            continue
        confidence = item.get("assignment_confidence")
        normalized.append(
            {
                "target_row_index": row_index,
                "target_col_index": col_index,
                "value": value,
                "numeric": _numeric_value(value),
                "classification": "accepted" if _numeric_value(value) is not None else _normalize_text(item.get("assignment_state")),
                "confidence_tier": "high" if isinstance(confidence, (int, float)) and float(confidence) >= 0.9 else "medium",
                "placement_basis": "hakodate_assignment",
                "source": "evidence_payload.hakodate_assignment",
                "evidence_ids": list(item.get("evidence_ids") or []),
                "date": logical_target.get("date"),
                "daypart": logical_target.get("daypart"),
                "menu": logical_target.get("menu_name") or logical_target.get("menu"),
            }
        )
    return normalized


def _ocr_items_from_payload_coordinates(evidence_payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(evidence_payload, dict):
        return []
    normalized: list[dict[str, Any]] = []
    walked = 0

    def walk(node: Any, path: str) -> None:
        nonlocal walked
        if walked >= _MAX_PAYLOAD_WALK_ITEMS:
            return
        walked += 1
        if isinstance(node, dict):
            row_index = _first_int(node, _EVIDENCE_ROW_KEYS)
            col_index = _first_int(node, _EVIDENCE_COL_KEYS)
            value = _first_text(node, _EVIDENCE_VALUE_KEYS)
            if row_index is not None and col_index is not None and value and _numeric_value(value) is not None:
                normalized.append(
                    {
                        "target_row_index": row_index,
                        "target_col_index": col_index,
                        "value": value,
                        "numeric": _numeric_value(value),
                        "classification": _normalize_text(node.get("classification")) or _normalize_text(node.get("assignment_state")),
                        "confidence_tier": _normalize_text(node.get("confidence_tier")),
                        "placement_basis": _normalize_text(node.get("placement_basis")) or path,
                        "source": f"evidence_payload{path}",
                    }
                )
            for key, value_item in list(node.items()):
                if walked >= _MAX_PAYLOAD_WALK_ITEMS:
                    return
                if key in {"pages", "overlay_image_base64", "image", "pdf_bytes"}:
                    continue
                walk(value_item, f"{path}.{key}")
        elif isinstance(node, list):
            for index, item in enumerate(node[:_MAX_CONTEXT_ITEMS]):
                if walked >= _MAX_PAYLOAD_WALK_ITEMS:
                    return
                walk(item, f"{path}[{index}]")

    walk(evidence_payload, "")
    return normalized


def _combined_ocr_items(
    *,
    sheet: dict[str, Any],
    evidence_payload: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    combined: list[dict[str, Any]] = []
    seen: set[tuple[int, int, str, str]] = set()
    for item in (
        _ocr_items_from_sheet(sheet)
        + _ocr_items_from_hakodate_assignment(sheet=sheet, evidence_payload=evidence_payload)
        + _ocr_items_from_payload_coordinates(evidence_payload)
    ):
        try:
            row_index = int(item.get("target_row_index"))
            col_index = int(item.get("target_col_index"))
        except (TypeError, ValueError):
            continue
        value = _normalize_text(item.get("value"))
        if not value:
            continue
        key = (row_index, col_index, value, _normalize_text(item.get("source")))
        if key in seen:
            continue
        seen.add(key)
        combined.append({**item, "target_row_index": row_index, "target_col_index": col_index, "value": value, "numeric": _numeric_value(value)})
    return combined


def _ocr_item_map(
    sheet: dict[str, Any],
    evidence_payload: dict[str, Any] | None = None,
) -> dict[tuple[int, int], list[dict[str, Any]]]:
    mapped: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for item in _combined_ocr_items(sheet=sheet, evidence_payload=evidence_payload):
        mapped.setdefault((int(item["target_row_index"]), int(item["target_col_index"])), []).append(item)
    return mapped


def _ocr_sheet_comparison(
    *,
    sheet: dict[str, Any],
    evidence_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    fields, header, rows = _sheet_dimensions(sheet)
    contexts = _row_context(sheet)
    evidence_by_cell = _ocr_item_map(sheet, evidence_payload)
    coordinates: set[tuple[int, int]] = set(evidence_by_cell.keys())
    for row_index, row in enumerate(rows):
        for col_index, field in enumerate(fields):
            if _is_structural_field(field):
                continue
            if _normalize_text(row[col_index] if col_index < len(row) else ""):
                coordinates.add((row_index, col_index))

    summary = {
        "comparison_count": 0,
        "match_count": 0,
        "mismatch_count": 0,
        "ocr_only_count": 0,
        "sheet_only_count": 0,
    }
    items: list[dict[str, Any]] = []
    for row_index, col_index in sorted(coordinates):
        if row_index < 0 or col_index < 0 or row_index >= len(rows) or col_index >= len(fields):
            continue
        if _is_structural_field(fields[col_index]):
            continue
        context = contexts[row_index] if row_index < len(contexts) else {}
        sheet_value = _normalize_text(rows[row_index][col_index] if col_index < len(rows[row_index]) else "")
        sheet_numeric = _numeric_value(sheet_value)
        ocr_items = evidence_by_cell.get((row_index, col_index), [])
        ocr_values = list(dict.fromkeys(_numeric_display(item.get("value")) for item in ocr_items if _normalize_text(item.get("value"))))
        ocr_numeric_values = [float(item["numeric"]) for item in ocr_items if item.get("numeric") is not None]
        best_ocr_numeric = ocr_numeric_values[0] if ocr_numeric_values else None
        if sheet_numeric is None and best_ocr_numeric is not None:
            status = "ocr_only"
            summary["ocr_only_count"] += 1
        elif sheet_numeric is not None and best_ocr_numeric is None:
            status = "sheet_only"
            summary["sheet_only_count"] += 1
        elif sheet_numeric is not None and best_ocr_numeric is not None and abs(float(sheet_numeric) - float(best_ocr_numeric)) <= 0.0001:
            status = "match"
            summary["match_count"] += 1
        elif sheet_numeric is not None and best_ocr_numeric is not None:
            status = "mismatch"
            summary["mismatch_count"] += 1
        else:
            status = "blank"
        summary["comparison_count"] += 1
        items.append(
            {
                "row_index": row_index,
                "col_index": col_index,
                "field": fields[col_index],
                "label": header[col_index] if col_index < len(header) else fields[col_index],
                "date": context.get("date"),
                "daypart": context.get("daypart"),
                "menu": context.get("menu"),
                "sheet_value": sheet_value,
                "ocr_values": ocr_values[:5],
                "status": status,
                "ocr_sources": list(dict.fromkeys(_normalize_text(item.get("source")) for item in ocr_items if _normalize_text(item.get("source"))))[:5],
            }
        )
    return {"summary": summary, "items": items[:_MAX_CONTEXT_ITEMS]}


def _make_patch(
    *,
    row_index: int,
    col_index: int,
    fields: list[str],
    header: list[str],
    current_value: object,
    suggested_value: object,
    reason: str,
    confidence: str,
    evidence: str,
    alternatives: list[str] | None = None,
    source: str = "rule",
) -> dict[str, Any]:
    return {
        "row_index": row_index,
        "col_index": col_index,
        "field": fields[col_index] if col_index < len(fields) else f"col{col_index + 1}",
        "label": header[col_index] if col_index < len(header) else f"col{col_index + 1}",
        "current_value": _normalize_text(current_value),
        "suggested_value": _normalize_text(suggested_value),
        "confidence": confidence,
        "reason": reason,
        "evidence": evidence,
        "alternatives": alternatives or [],
        "source": source,
    }


def _rule_based_auto_edit_patches(
    sheet: dict[str, Any],
    evidence_payload: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    fields, header, rows = _sheet_dimensions(sheet)
    evidence_by_cell = _ocr_item_map(sheet, evidence_payload)
    patches: list[dict[str, Any]] = []
    seen: set[tuple[int, int, str]] = set()

    for (row_index, col_index), items in sorted(evidence_by_cell.items()):
        if row_index >= len(rows) or col_index >= len(fields) or _is_structural_field(fields[col_index]):
            continue
        current = rows[row_index][col_index]
        current_numeric = _numeric_value(current)
        numeric_items = [item for item in items if item.get("numeric") is not None]
        if not numeric_items:
            continue
        numeric_items.sort(
            key=lambda item: (
                0 if item.get("classification") == "accepted" else 1 if item.get("classification") == "deterministic_candidate" else 2,
                0 if item.get("confidence_tier") == "high" else 1 if item.get("confidence_tier") == "medium" else 2,
            )
        )
        best = numeric_items[0]
        suggested = _numeric_display(best["value"])
        groups = _digit_groups(current)
        correction_mark_like = len(groups) > 1 and bool(re.search(r"[^\d０-９.\s]", _normalize_text(current)))
        if (current_numeric is None and _normalize_text(current)) or correction_mark_like:
            alternatives = list(dict.fromkeys(groups + [suggested]))
            patch = _make_patch(
                row_index=row_index,
                col_index=col_index,
                fields=fields,
                header=header,
                current_value=current,
                suggested_value=suggested if suggested in alternatives else (groups[-1] if groups else suggested),
                confidence="medium",
                reason="non_numeric_or_corrected_mark_cell",
                evidence=f"OCR候補 {suggested}。セル文字列に斜線/訂正を含む可能性があります。",
                alternatives=alternatives,
            )
        elif current_numeric is None:
            patch = _make_patch(
                row_index=row_index,
                col_index=col_index,
                fields=fields,
                header=header,
                current_value=current,
                suggested_value=suggested,
                confidence="medium" if best.get("classification") != "accepted" else "high",
                reason="blank_cell_has_ocr_number",
                evidence=f"OCR候補 {suggested} ({best.get('classification') or '-'}/{best.get('confidence_tier') or '-'})",
                alternatives=[_numeric_display(item["value"]) for item in numeric_items[:5]],
            )
        elif best.get("numeric") is not None and abs(float(current_numeric) - float(best["numeric"])) > 0.0001:
            alternatives = list(dict.fromkeys([_numeric_display(item["value"]) for item in numeric_items[:5]] + [_numeric_display(current)]))
            patch = _make_patch(
                row_index=row_index,
                col_index=col_index,
                fields=fields,
                header=header,
                current_value=current,
                suggested_value=suggested,
                confidence="medium" if best.get("classification") != "accepted" else "high",
                reason="sheet_value_differs_from_ocr",
                evidence=f"シート {_numeric_display(current)} / OCR {suggested}",
                alternatives=alternatives,
            )
        else:
            continue
        key = (patch["row_index"], patch["col_index"], patch["suggested_value"])
        if key not in seen:
            seen.add(key)
            patches.append(patch)
    return patches


def _sheet_context_for_llm(
    sheet: dict[str, Any],
    patches: list[dict[str, Any]],
    warnings: list[dict[str, Any]] | None = None,
    evidence_payload: dict[str, Any] | None = None,
    computed_context: dict[str, Any] | None = None,
    include_ocr_sheet_comparison: bool = True,
    include_ocr_context: bool = True,
) -> dict[str, Any]:
    fields, header, _rows = _sheet_dimensions(sheet)
    payload = {
        "fields": fields,
        "header": header,
        "rows": _row_context(sheet)[:_MAX_LLM_ROWS],
        "ocr_numeric_cell_items": (
            _combined_ocr_items(sheet=sheet, evidence_payload=evidence_payload)[:_MAX_LLM_EVIDENCE]
            if include_ocr_context
            else []
        ),
        "target_cell_map": [
            {
                "target_cell_id": item.get("target_cell_id") or item.get("sheet_cell"),
                "sheet_cell": item.get("sheet_cell"),
                "worksheet_row": item.get("worksheet_row"),
                "worksheet_col": item.get("worksheet_col"),
                "bbox": item.get("bbox"),
                "field": item.get("field") or item.get("semantic_field"),
                "field_label": item.get("field_label") or item.get("label"),
                "logical_targets": item.get("logical_targets"),
            }
            for item in (_hakodate_target_cells_from_payload(evidence_payload)[:_MAX_LLM_EVIDENCE] if include_ocr_context else [])
        ],
        "computed_context": computed_context or {},
        "rule_patches": patches[:_MAX_LLM_WARNINGS],
        "rule_warnings": (warnings or [])[:_MAX_LLM_WARNINGS],
    }
    if include_ocr_sheet_comparison:
        payload["ocr_sheet_comparison"] = _ocr_sheet_comparison(sheet=sheet, evidence_payload=evidence_payload)
    return payload


def _strip_json_fence(text: str) -> str:
    raw = text.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z0-9_-]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)
    return raw.strip()


def _extract_workflow_llm_json_payload(text: str, *, array_key: str | None = None) -> dict[str, Any]:
    raw = _strip_json_fence(text)
    starts = [index for index in (raw.find("{"), raw.find("[")) if index >= 0]
    if not starts:
        raise ValueError("Workflow LLM response does not contain JSON")
    decoder = json.JSONDecoder()
    parsed, _end_index = decoder.raw_decode(raw[min(starts) :])
    if isinstance(parsed, dict):
        return parsed
    if isinstance(parsed, list):
        return {array_key or "items": parsed}
    raise ValueError("Workflow LLM response is not JSON object or array")


def _gemini_json_request(
    *,
    system_prompt: str,
    user_payload: dict[str, Any],
    model: str | None = None,
    max_tokens: int = 8192,
    array_key: str | None = None,
    image_png_base64: str | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    resolved_model = _normalize_text(model) or os.getenv("WORKFLOW_V2_LLM_MODEL", "").strip() or "gemini-2.5-pro"
    try:
        api_key = gemini_ocr_service._get_api_key()  # noqa: SLF001
    except RuntimeError as exc:
        return None, {
            "status": "skipped_no_api_key",
            "model": resolved_model,
            "error": str(exc),
        }
    content_parts: list[dict[str, Any]] = []
    if _normalize_text(image_png_base64):
        content_parts.append(
            {
                "inline_data": {
                    "mime_type": "image/png",
                    "data": _normalize_text(image_png_base64),
                }
            }
        )
    content_parts.append(
        {
            "text": json.dumps(user_payload, ensure_ascii=False, sort_keys=True),
        }
    )
    body = {
        "system_instruction": {"parts": [{"text": system_prompt}]},
        "contents": [
            {
                "parts": content_parts,
            }
        ],
        "generationConfig": {
            "temperature": 0,
            "maxOutputTokens": max_tokens,
            "responseMimeType": "application/json",
        },
    }
    try:
        raw = gemini_ocr_service._request_gemini_json(  # noqa: SLF001
            model=resolved_model,
            api_key=api_key,
            body=body,
            timeout=float(os.getenv("WORKFLOW_V2_LLM_TIMEOUT_SECONDS", "120")),
        )
        text = gemini_ocr_service._extract_response_text(raw)  # noqa: SLF001
        parsed = _extract_workflow_llm_json_payload(text, array_key=array_key)
    except Exception as exc:  # noqa: BLE001
        return None, {
            "status": "failed",
            "model": resolved_model,
            "error": str(exc),
        }
    return parsed, {
        "status": "ok",
        "model": resolved_model,
    }


def _normalize_llm_patches(payload: dict[str, Any] | None, fields: list[str], header: list[str]) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    patches = payload.get("patches")
    if not isinstance(patches, list):
        return []
    normalized: list[dict[str, Any]] = []
    seen: set[tuple[int, int, str]] = set()
    for item in patches:
        if not isinstance(item, dict):
            continue
        row_index = item.get("row_index")
        col_index = item.get("col_index")
        if not isinstance(row_index, int) or not isinstance(col_index, int):
            continue
        suggested = _normalize_text(item.get("suggested_value"))
        if not suggested:
            continue
        key = (row_index, col_index, suggested)
        if key in seen:
            continue
        seen.add(key)
        normalized.append(
            _make_patch(
                row_index=row_index,
                col_index=col_index,
                fields=fields,
                header=header,
                current_value=item.get("current_value"),
                suggested_value=suggested,
                reason=_normalize_text(item.get("reason")) or "llm_suggested_correction",
                confidence=_normalize_text(item.get("confidence")) or "medium",
                evidence=_normalize_text(item.get("evidence")),
                alternatives=[
                    _normalize_text(value)
                    for value in (item.get("alternatives") or [])
                    if _normalize_text(value)
                ],
                source="llm",
            )
        )
    return normalized


def propose_auto_sheet_edits(
    *,
    sheet: dict[str, Any],
    evidence_payload: dict[str, Any] | None = None,
    model: str | None = None,
    use_llm: bool = True,
    fax_image_png_base64: str | None = None,
    fax_image_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(sheet, dict):
        return {"status": "error", "error": "sheet_required", "patches": [], "rule_patches": [], "llm": {"status": "not_run"}}
    fields, header, _rows = _sheet_dimensions(sheet)
    rule_patches = _rule_based_auto_edit_patches(sheet, evidence_payload)
    llm_payload: dict[str, Any] | None = None
    llm_meta: dict[str, Any] = {"status": "disabled"}
    llm_patches: list[dict[str, Any]] = []
    if use_llm:
        system_prompt = (
            "You review a Japanese FAX order sheet. Return JSON only. "
            "Use the attached original FAX page image as the primary evidence. OCR evidence and the current sheet are auxiliary. "
            "For each quantity cell, compare the visible handwritten number in the FAX image with OCR candidates and the current sheet value. "
            "Propose cell edits when the FAX image suggests a missing number, a different number, or a crossed-out/slash-corrected number. "
            "For corrections, include alternative digit candidates and explain the visible basis from the FAX image. "
            "Do not invent menu rows or structural cells. Use row_index and col_index from the input."
        )
        llm_payload, llm_meta = _gemini_json_request(
            system_prompt=system_prompt,
            user_payload=_sheet_context_for_llm(sheet, rule_patches, evidence_payload=evidence_payload),
            model=model,
            array_key="patches",
            image_png_base64=fax_image_png_base64,
        )
        llm_meta["fax_image"] = fax_image_meta or {"status": "not_provided"}
        llm_patches = _normalize_llm_patches(llm_payload, fields, header)
    merged: list[dict[str, Any]] = []
    seen: set[tuple[int, int, str]] = set()
    for patch in llm_patches + rule_patches:
        key = (int(patch["row_index"]), int(patch["col_index"]), str(patch["suggested_value"]))
        if key in seen:
            continue
        seen.add(key)
        merged.append(patch)
    return {
        "status": "ok",
        "source": "workflow_v2_sheet_auto_edit",
        "patches": merged,
        "rule_patches": rule_patches,
        "llm_patches": llm_patches,
        "llm": llm_meta,
        "llm_raw": llm_payload if os.getenv("WORKFLOW_V2_LLM_DEBUG_RAW", "").strip() == "1" else None,
    }


def _warning(
    *,
    warning_type: str,
    severity: str,
    row_index: int | None,
    col_index: int | None,
    field: str | None,
    label: str | None,
    value: object,
    message: str,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_evidence = evidence or {}
    evidence_keys = normalized_evidence.get("keys") if isinstance(normalized_evidence.get("keys"), dict) else {}
    date = _normalize_text(normalized_evidence.get("date") or evidence_keys.get("date"))
    daypart = _normalize_text(normalized_evidence.get("daypart") or evidence_keys.get("daypart"))
    menu = _normalize_text(normalized_evidence.get("menu") or evidence_keys.get("menu"))
    context_parts = [part for part in (date, daypart, menu) if part]
    return {
        "type": warning_type,
        "severity": severity,
        "row_index": row_index,
        "col_index": col_index,
        "field": field,
        "label": label,
        "value": _normalize_text(value),
        "message": message,
        "date": date or None,
        "daypart": daypart or None,
        "menu": menu or None,
        "context_label": " / ".join(context_parts) if context_parts else None,
        "evidence": normalized_evidence,
    }


def _quantity_cell_records(sheet: dict[str, Any]) -> list[dict[str, Any]]:
    fields, header, rows = _sheet_dimensions(sheet)
    contexts = _row_context(sheet)
    records: list[dict[str, Any]] = []
    for row_idx, row in enumerate(rows):
        context = contexts[row_idx] if row_idx < len(contexts) else {}
        for col_idx, field in enumerate(fields):
            if _is_structural_field(field):
                continue
            value = row[col_idx] if col_idx < len(row) else ""
            numeric = _numeric_value(value)
            if numeric is None:
                continue
            records.append(
                {
                    "row_index": row_idx,
                    "col_index": col_idx,
                    "field": field,
                    "label": header[col_idx] if col_idx < len(header) else field,
                    "value": value,
                    "numeric": numeric,
                    "date": _normalize_text(context.get("date")),
                    "daypart": _normalize_text(context.get("daypart")),
                    "menu": _normalize_text(context.get("menu")),
                }
            )
    return records


def _sum_by(records: list[dict[str, Any]], keys: tuple[str, ...]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, ...], dict[str, Any]] = {}
    for record in records:
        key = tuple(_normalize_text(record.get(item)) for item in keys)
        if not all(key):
            continue
        entry = grouped.setdefault(
            key,
            {
                **{keys[index]: key[index] for index in range(len(keys))},
                "total": 0.0,
                "cell_count": 0,
                "breakdown": {},
            },
        )
        numeric = float(record.get("numeric") or 0)
        entry["total"] += numeric
        entry["cell_count"] += 1
        label = _normalize_text(record.get("label") or record.get("field")) or "unknown"
        entry["breakdown"][label] = float(entry["breakdown"].get(label) or 0.0) + numeric
    return sorted(grouped.values(), key=lambda item: tuple(str(item.get(key) or "") for key in keys))[:_MAX_CONTEXT_ITEMS]


def _aggregate_quantity_context(sheet: dict[str, Any]) -> dict[str, Any]:
    records = _quantity_cell_records(sheet)
    field_day_series: dict[str, list[dict[str, Any]]] = {}
    for item in _sum_by(records, ("field", "date")):
        field = _normalize_text(item.get("field"))
        if not field:
            continue
        field_day_series.setdefault(field, []).append(
            {
                "date": item.get("date"),
                "total": item.get("total"),
                "cell_count": item.get("cell_count"),
            }
        )
    return {
        "quantity_cell_count": len(records),
        "day_totals": _sum_by(records, ("date",)),
        "daypart_totals": _sum_by(records, ("date", "daypart")),
        "same_menu_totals": _sum_by(records, ("date", "daypart", "menu")),
        "field_day_series": dict(list(field_day_series.items())[:_MAX_CONTEXT_ITEMS]),
    }


def _total_outlier_warnings(
    *,
    items: list[dict[str, Any]],
    keys: tuple[str, ...],
    warning_type: str,
    label: str,
    minimum_baseline: float,
) -> list[dict[str, Any]]:
    totals = [float(item.get("total") or 0.0) for item in items if float(item.get("total") or 0.0) > 0]
    if len(totals) < 3:
        return []
    baseline = median(totals)
    if baseline <= 0:
        return []
    warnings: list[dict[str, Any]] = []
    for item in items:
        total = float(item.get("total") or 0.0)
        if total <= 0:
            continue
        key_text = " / ".join(_normalize_text(item.get(key)) for key in keys if _normalize_text(item.get(key)))
        if total >= max(minimum_baseline, baseline * 1.8):
            warnings.append(
                _warning(
                    warning_type=warning_type,
                    severity="high",
                    row_index=None,
                    col_index=None,
                    field=None,
                    label=label,
                    value=total,
                    message=f"{label} {key_text} の合計 {total:g} が基準 {baseline:g} に対して大きすぎます。",
                    evidence={"baseline": baseline, "keys": {key: item.get(key) for key in keys}, "breakdown": item.get("breakdown")},
                )
            )
        elif baseline >= minimum_baseline and total <= baseline * 0.5:
            warnings.append(
                _warning(
                    warning_type=warning_type,
                    severity="medium",
                    row_index=None,
                    col_index=None,
                    field=None,
                    label=label,
                    value=total,
                    message=f"{label} {key_text} の合計 {total:g} が基準 {baseline:g} に対して小さすぎます。",
                    evidence={"baseline": baseline, "keys": {key: item.get(key) for key in keys}, "breakdown": item.get("breakdown")},
                )
            )
    return warnings


def _other_day_quantity_warnings(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_field: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        field = _normalize_text(record.get("field"))
        date_value = _normalize_text(record.get("date"))
        if not field or not date_value:
            continue
        by_field.setdefault(field, []).append(record)
    warnings: list[dict[str, Any]] = []
    for field_records in by_field.values():
        dated_values = [float(item.get("numeric") or 0.0) for item in field_records if float(item.get("numeric") or 0.0) > 0]
        dated_dates = {_normalize_text(item.get("date")) for item in field_records if _normalize_text(item.get("date"))}
        if len(dated_values) < 3 or len(dated_dates) < 2:
            continue
        baseline = median(dated_values)
        if baseline <= 0:
            continue
        for record in field_records:
            value = float(record.get("numeric") or 0.0)
            if value <= 0:
                continue
            if value >= max(80.0, baseline * 2.5):
                warnings.append(
                    _warning(
                        warning_type="other_day_count_outlier",
                        severity="high",
                        row_index=int(record.get("row_index") or 0),
                        col_index=int(record.get("col_index") or 0),
                        field=_normalize_text(record.get("field")),
                        label=_normalize_text(record.get("label")),
                        value=record.get("value"),
                        message=f"{record.get('label')} が別日同列中央値 {baseline:g} に対して大きすぎます。",
                        evidence={
                            "baseline": baseline,
                            "date": record.get("date"),
                            "daypart": record.get("daypart"),
                            "menu": record.get("menu"),
                            "comparison_basis": "same_field_other_days",
                        },
                    )
                )
            elif baseline >= 20 and value <= baseline * 0.25:
                warnings.append(
                    _warning(
                        warning_type="other_day_count_outlier",
                        severity="medium",
                        row_index=int(record.get("row_index") or 0),
                        col_index=int(record.get("col_index") or 0),
                        field=_normalize_text(record.get("field")),
                        label=_normalize_text(record.get("label")),
                        value=record.get("value"),
                        message=f"{record.get('label')} が別日同列中央値 {baseline:g} に対して小さすぎます。",
                        evidence={
                            "baseline": baseline,
                            "date": record.get("date"),
                            "daypart": record.get("daypart"),
                            "menu": record.get("menu"),
                            "comparison_basis": "same_field_other_days",
                        },
                    )
                )
    return warnings


def _rule_based_anomaly_warnings(
    sheet: dict[str, Any],
) -> list[dict[str, Any]]:
    fields, header, rows = _sheet_dimensions(sheet)
    contexts = _row_context(sheet)
    computed_context = _aggregate_quantity_context(sheet)
    quantity_records = _quantity_cell_records(sheet)
    warnings: list[dict[str, Any]] = []

    numeric_by_col: dict[int, list[float]] = {}
    numeric_cells: list[tuple[int, int, float, str]] = []
    for row_idx, row in enumerate(rows):
        for col_idx, field in enumerate(fields):
            if _is_structural_field(field):
                continue
            numeric = _numeric_value(row[col_idx])
            if numeric is None:
                continue
            numeric_by_col.setdefault(col_idx, []).append(numeric)
            numeric_cells.append((row_idx, col_idx, numeric, row[col_idx]))

    for row_idx, col_idx, value, raw_value in numeric_cells:
        field = fields[col_idx]
        label = header[col_idx]
        col_values = [item for item in numeric_by_col.get(col_idx, []) if item > 0]
        baseline = median(col_values) if len(col_values) >= 3 else None
        context = contexts[row_idx] if row_idx < len(contexts) else {}
        if baseline and baseline > 0:
            if value >= max(80.0, baseline * 2.5):
                warnings.append(
                    _warning(
                        warning_type="high_outlier",
                        severity="high",
                        row_index=row_idx,
                        col_index=col_idx,
                        field=field,
                        label=label,
                        value=raw_value,
                        message=f"{label} が同列中央値 {baseline:g} に対して大きすぎます。",
                        evidence={"baseline": baseline, "date": context.get("date"), "daypart": context.get("daypart"), "menu": context.get("menu")},
                    )
                )
            elif value > 0 and baseline >= 20 and value <= baseline * 0.25:
                warnings.append(
                    _warning(
                        warning_type="low_outlier",
                        severity="medium",
                        row_index=row_idx,
                        col_index=col_idx,
                        field=field,
                        label=label,
                        value=raw_value,
                        message=f"{label} が同列中央値 {baseline:g} に対して小さすぎます。",
                        evidence={"baseline": baseline, "date": context.get("date"), "daypart": context.get("daypart"), "menu": context.get("menu")},
                    )
                )

    warnings.extend(
        _total_outlier_warnings(
            items=[item for item in computed_context.get("day_totals", []) if isinstance(item, dict)],
            keys=("date",),
            warning_type="same_day_total_outlier",
            label="同日注文数",
            minimum_baseline=80.0,
        )
    )
    warnings.extend(
        _total_outlier_warnings(
            items=[item for item in computed_context.get("daypart_totals", []) if isinstance(item, dict)],
            keys=("date", "daypart"),
            warning_type="same_daypart_total_outlier",
            label="同日食区分注文数",
            minimum_baseline=30.0,
        )
    )
    same_menu_totals = [item for item in computed_context.get("same_menu_totals", []) if isinstance(item, dict)]
    menu_series: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for item in same_menu_totals:
        key = (_normalize_text(item.get("daypart")), _normalize_text(item.get("menu")))
        if all(key):
            menu_series.setdefault(key, []).append(item)
    for grouped_items in menu_series.values():
        warnings.extend(
            _total_outlier_warnings(
                items=grouped_items,
                keys=("date", "daypart", "menu"),
                warning_type="same_menu_total_outlier",
                label="同一メニュー合計",
                minimum_baseline=20.0,
            )
        )
    warnings.extend(_other_day_quantity_warnings(quantity_records))

    return warnings


def _normalize_llm_warnings(payload: dict[str, Any] | None, fields: list[str], header: list[str]) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    warnings = payload.get("warnings")
    if not isinstance(warnings, list):
        return []
    normalized: list[dict[str, Any]] = []
    for item in warnings:
        if not isinstance(item, dict):
            continue
        row_index = item.get("row_index")
        col_index = item.get("col_index")
        if row_index is not None and not isinstance(row_index, int):
            row_index = None
        if col_index is not None and not isinstance(col_index, int):
            col_index = None
        field = fields[col_index] if isinstance(col_index, int) and 0 <= col_index < len(fields) else _normalize_text(item.get("field")) or None
        label = header[col_index] if isinstance(col_index, int) and 0 <= col_index < len(header) else _normalize_text(item.get("label")) or field
        normalized.append(
            _warning(
                warning_type=_normalize_text(item.get("type")) or "llm_anomaly",
                severity=_normalize_text(item.get("severity")) or "medium",
                row_index=row_index,
                col_index=col_index,
                field=field,
                label=label,
                value=item.get("value"),
                message=_normalize_text(item.get("message")) or _normalize_text(item.get("reason")) or "AIが確認対象として検出しました。",
                evidence=item.get("evidence") if isinstance(item.get("evidence"), dict) else {},
            )
        )
    return normalized


def build_sheet_anomaly_report(
    *,
    sheet: dict[str, Any],
    evidence_payload: dict[str, Any] | None = None,
    materialization_candidate: dict[str, Any] | None = None,
    bagging_result: dict[str, Any] | None = None,
    model: str | None = None,
    use_llm: bool | None = None,
) -> dict[str, Any]:
    if not isinstance(sheet, dict):
        return {"status": "error", "error": "sheet_required", "warnings": [], "rule_warnings": [], "llm": {"status": "not_run"}}
    fields, header, _rows = _sheet_dimensions(sheet)
    computed_context = _aggregate_quantity_context(sheet)
    rule_warnings = _rule_based_anomaly_warnings(sheet)
    should_use_llm = (
        bool(use_llm)
        if use_llm is not None
        else os.getenv("WORKFLOW_V2_LLM_ANOMALY_ENABLED", "1").strip().lower() not in {"0", "false", "no", "off"}
    )
    llm_payload: dict[str, Any] | None = None
    llm_meta: dict[str, Any] = {"status": "disabled"}
    llm_warnings: list[dict[str, Any]] = []
    if should_use_llm:
        system_prompt = (
            "You audit a Japanese meal-order quantity sheet before bagging. Return JSON only. "
            "Use only the confirmed sheet numbers and derived totals. Do not compare against OCR. "
            "Find suspicious quantity cells by comparing same-day totals, same-daypart totals, same-menu totals, same-column values on other days, and special diet totals. "
            "Flag obvious high/low values such as a likely extra digit. Do not rewrite the sheet."
        )
        llm_payload, llm_meta = _gemini_json_request(
            system_prompt=system_prompt,
            user_payload={
                **_sheet_context_for_llm(
                    sheet,
                    [],
                    rule_warnings,
                    evidence_payload=None,
                    computed_context=computed_context,
                    include_ocr_sheet_comparison=False,
                    include_ocr_context=False,
                ),
                "materialization_candidate": materialization_candidate or {},
                "bagging_summary": (bagging_result or {}).get("summary") if isinstance(bagging_result, dict) else {},
            },
            model=model,
            array_key="warnings",
        )
        llm_warnings = _normalize_llm_warnings(llm_payload, fields, header)

    merged: list[dict[str, Any]] = []
    seen: set[tuple[str, int | None, int | None, str]] = set()
    for item in rule_warnings + llm_warnings:
        key = (
            str(item.get("type") or ""),
            item.get("row_index") if isinstance(item.get("row_index"), int) else None,
            item.get("col_index") if isinstance(item.get("col_index"), int) else None,
            str(item.get("value") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
    return {
        "status": "ok",
        "source": "workflow_v2_sheet_anomaly_review",
        "warnings": merged,
        "rule_warnings": rule_warnings,
        "llm_warnings": llm_warnings,
        "summary": {
            "warning_count": len(merged),
            "high_count": sum(1 for item in merged if item.get("severity") == "high"),
            "medium_count": sum(1 for item in merged if item.get("severity") == "medium"),
            "quantity_cell_count": computed_context.get("quantity_cell_count"),
        },
        "computed_context": computed_context,
        "llm": llm_meta,
        "llm_raw": llm_payload if os.getenv("WORKFLOW_V2_LLM_DEBUG_RAW", "").strip() == "1" else None,
    }
