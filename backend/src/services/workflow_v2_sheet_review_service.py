from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
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
_TOTAL_FIELD_TOKENS = ("total", "sum", "合計", "計")
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


def _is_review_quantity_field(field: object, label: object = "") -> bool:
    normalized = _normalize_text(field).lower()
    normalized_label = _normalize_text(label).lower()
    if _is_structural_field(normalized):
        return False
    if any(token in normalized or token in normalized_label for token in _TOTAL_FIELD_TOKENS):
        return False
    return True


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
    # AI自動編集ではOCR結果値を正解候補として使わない。
    # シート値とFAX画像の矛盾だけをAIが判断するため、rule patchは生成しない。
    return []


def _target_cells_from_sheet(sheet: dict[str, Any]) -> list[dict[str, Any]]:
    items = sheet.get("target_cell_map") if isinstance(sheet, dict) else None
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def _quantity_presence_hints_from_sheet(sheet: dict[str, Any]) -> list[dict[str, Any]]:
    items = sheet.get("ocr_numeric_cell_items") if isinstance(sheet, dict) else None
    if not isinstance(items, list):
        return []
    hints: list[dict[str, Any]] = []
    seen: set[tuple[int, int]] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        row_index = _first_int(item, _EVIDENCE_ROW_KEYS)
        col_index = _first_int(item, _EVIDENCE_COL_KEYS)
        if row_index is None or col_index is None:
            continue
        key = (row_index, col_index)
        if key in seen:
            continue
        seen.add(key)
        hints.append(
            {
                "target_row_index": row_index,
                "target_col_index": col_index,
                "has_quantity_mark": True,
                "classification": _normalize_text(item.get("classification")),
                "confidence_tier": _normalize_text(item.get("confidence_tier")),
                "source": "ocr_quantity_presence_only",
            }
        )
    return hints[:_MAX_LLM_EVIDENCE]


def _float_pair(value: object) -> tuple[float, float] | None:
    if not isinstance(value, list) or len(value) < 2:
        return None
    try:
        return float(value[0]), float(value[1])
    except (TypeError, ValueError):
        return None


def _float_bbox(value: object) -> tuple[float, float, float, float] | None:
    if not isinstance(value, list) or len(value) < 4:
        return None
    try:
        return float(value[0]), float(value[1]), float(value[2]), float(value[3])
    except (TypeError, ValueError):
        return None


def _png_dimensions_from_base64(value: str | None) -> tuple[int, int] | None:
    if not _normalize_text(value):
        return None
    try:
        import base64
        import struct

        raw = base64.b64decode(_normalize_text(value), validate=False)
        if len(raw) < 24 or raw[:8] != b"\x89PNG\r\n\x1a\n" or raw[12:16] != b"IHDR":
            return None
        width, height = struct.unpack(">II", raw[16:24])
        if width <= 0 or height <= 0:
            return None
        return int(width), int(height)
    except Exception:  # noqa: BLE001
        return None


def _target_cell_coordinate_extent(target_cells: list[dict[str, Any]]) -> dict[str, float] | None:
    xs: list[float] = []
    ys: list[float] = []
    for item in target_cells:
        bbox = _float_bbox(item.get("bbox"))
        if bbox is None:
            continue
        xs.extend([bbox[0], bbox[2]])
        ys.extend([bbox[1], bbox[3]])
    if not xs or not ys:
        return None
    return {
        "min_x": min(xs),
        "max_x": max(xs),
        "min_y": min(ys),
        "max_y": max(ys),
        "width": max(xs),
        "height": max(ys),
    }


def _target_coordinate_transform(
    *,
    target_cells: list[dict[str, Any]],
    fax_image_meta: dict[str, Any] | None,
) -> dict[str, Any]:
    image_width = _coerce_int((fax_image_meta or {}).get("pixel_width"))
    image_height = _coerce_int((fax_image_meta or {}).get("pixel_height"))
    extent = _target_cell_coordinate_extent(target_cells)
    if not image_width or not image_height or not extent:
        return {
            "status": "unavailable",
            "reason": "image_dimensions_or_target_extent_missing",
            "image_width": image_width,
            "image_height": image_height,
            "target_extent": extent,
        }
    coordinate_width = float(extent["width"])
    coordinate_height = float(extent["height"])
    if coordinate_width <= 0 or coordinate_height <= 0:
        return {
            "status": "unavailable",
            "reason": "target_extent_invalid",
            "image_width": image_width,
            "image_height": image_height,
            "target_extent": extent,
        }
    return {
        "status": "scaled_to_attached_image",
        "image_width": image_width,
        "image_height": image_height,
        "target_extent": extent,
        "scale_x": image_width / coordinate_width,
        "scale_y": image_height / coordinate_height,
    }


def _scale_bbox_for_image(
    bbox: object,
    transform: dict[str, Any] | None,
) -> list[float] | None:
    parsed = _float_bbox(bbox)
    if parsed is None or not isinstance(transform, dict):
        return None
    if transform.get("status") != "scaled_to_attached_image":
        return None
    scale_x = float(transform.get("scale_x") or 0)
    scale_y = float(transform.get("scale_y") or 0)
    if scale_x <= 0 or scale_y <= 0:
        return None
    return [
        round(parsed[0] * scale_x, 2),
        round(parsed[1] * scale_y, 2),
        round(parsed[2] * scale_x, 2),
        round(parsed[3] * scale_y, 2),
    ]


def _scale_center_for_image(
    center: object,
    transform: dict[str, Any] | None,
) -> list[float] | None:
    parsed = _float_pair(center)
    if parsed is None or not isinstance(transform, dict):
        return None
    if transform.get("status") != "scaled_to_attached_image":
        return None
    scale_x = float(transform.get("scale_x") or 0)
    scale_y = float(transform.get("scale_y") or 0)
    if scale_x <= 0 or scale_y <= 0:
        return None
    return [round(parsed[0] * scale_x, 2), round(parsed[1] * scale_y, 2)]


def _review_contact_sheet_png_base64(
    *,
    fax_image_png_base64: str | None,
    target_cells: list[dict[str, Any]],
    coordinate_transform: dict[str, Any] | None,
) -> str | None:
    if not _normalize_text(fax_image_png_base64) or not target_cells:
        return None
    try:
        import base64
        from io import BytesIO

        from PIL import Image, ImageDraw, ImageFont

        raw = base64.b64decode(_normalize_text(fax_image_png_base64), validate=False)
        image = Image.open(BytesIO(raw)).convert("RGB")
        font = ImageFont.load_default()
        slots: list[tuple[dict[str, Any], Image.Image, list[int], list[int] | None]] = []
        for item in target_cells:
            scaled = _scale_bbox_for_image(item.get("bbox"), coordinate_transform)
            bbox = scaled or item.get("bbox")
            parsed = _float_bbox(bbox)
            if parsed is None:
                continue
            x0, y0, x1, y1 = parsed
            pad_x = max(18, int(round((x1 - x0) * 1.2)))
            pad_y = max(18, int(round((y1 - y0) * 1.0)))
            crop_box = [
                max(0, int(round(x0 - pad_x))),
                max(0, int(round(y0 - pad_y))),
                min(image.width, int(round(x1 + pad_x))),
                min(image.height, int(round(y1 + pad_y))),
            ]
            if crop_box[2] <= crop_box[0] or crop_box[3] <= crop_box[1]:
                continue
            crop = image.crop(tuple(crop_box)).convert("RGB")
            original_crop_size = crop.size
            crop.thumbnail((360, 220), Image.Resampling.LANCZOS)
            scale_x = crop.width / max(1, original_crop_size[0])
            scale_y = crop.height / max(1, original_crop_size[1])
            target_rect = [
                int(round((x0 - crop_box[0]) * scale_x)),
                int(round((y0 - crop_box[1]) * scale_y)),
                int(round((x1 - crop_box[0]) * scale_x)),
                int(round((y1 - crop_box[1]) * scale_y)),
            ]
            slots.append((item, crop, crop_box, target_rect))
        if not slots:
            return None
        slot_w = 420
        slot_h = 290
        columns = 2 if len(slots) > 1 else 1
        rows = (len(slots) + columns - 1) // columns
        sheet = Image.new("RGB", (columns * slot_w, rows * slot_h), "white")
        draw = ImageDraw.Draw(sheet)
        for index, (item, crop, crop_box, target_rect) in enumerate(slots):
            col = index % columns
            row = index // columns
            ox = col * slot_w
            oy = row * slot_h
            label = (
                f"target {index + 1}: row={item.get('target_row_index')} "
                f"col={item.get('target_col_index')} cell={item.get('sheet_cell') or ''}"
            )
            draw.text((ox + 10, oy + 8), label, fill=(0, 0, 0), font=font)
            draw.text((ox + 10, oy + 26), f"source crop px={crop_box}", fill=(70, 70, 70), font=font)
            px = ox + 10
            py = oy + 52
            sheet.paste(crop, (px, py))
            draw.rectangle((px, py, px + crop.width, py + crop.height), outline=(190, 0, 0), width=2)
            if target_rect is not None:
                draw.rectangle(
                    (
                        px + target_rect[0],
                        py + target_rect[1],
                        px + target_rect[2],
                        py + target_rect[3],
                    ),
                    outline=(0, 80, 220),
                    width=4,
                )
        out = BytesIO()
        sheet.save(out, format="PNG")
        return base64.b64encode(out.getvalue()).decode("ascii")
    except Exception:  # noqa: BLE001
        return None


def _target_cell_context_items(
    target_cells: list[dict[str, Any]],
    *,
    coordinate_transform: dict[str, Any] | None = None,
    quantity_presence_by_cell: dict[tuple[int, int], dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for item in target_cells:
        row_index = _coerce_int(item.get("target_row_index"))
        col_index = _coerce_int(item.get("target_col_index"))
        presence_hint = (
            quantity_presence_by_cell.get((row_index, col_index))
            if row_index is not None and col_index is not None and isinstance(quantity_presence_by_cell, dict)
            else None
        )
        scaled_bbox = _scale_bbox_for_image(item.get("bbox"), coordinate_transform)
        scaled_center = _scale_center_for_image(item.get("center"), coordinate_transform)
        context = {
            "target_cell_id": item.get("target_cell_id") or item.get("sheet_cell"),
            "sheet_cell": item.get("sheet_cell"),
            "worksheet_row": item.get("worksheet_row"),
            "worksheet_col": item.get("worksheet_col"),
            "target_row_index": item.get("target_row_index"),
            "target_col_index": item.get("target_col_index"),
            "bbox": scaled_bbox or item.get("bbox"),
            "bbox_original": item.get("bbox"),
            "center": scaled_center or item.get("center"),
            "center_original": item.get("center"),
            "field": item.get("field") or item.get("semantic_field"),
            "field_label": item.get("field_label") or item.get("label"),
            "logical_targets": item.get("logical_targets"),
        }
        if isinstance(item.get("suspect_review"), dict):
            context["suspect_review"] = item.get("suspect_review")
        if isinstance(presence_hint, dict):
            context["ocr_quantity_presence"] = presence_hint
        if scaled_bbox is not None:
            context["bbox_coordinate_space"] = "attached_image_pixels"
        items.append(context)
    return items


def _chunk_items(items: list[dict[str, Any]], chunk_size: int) -> list[list[dict[str, Any]]]:
    safe_size = max(1, chunk_size)
    return [items[index : index + safe_size] for index in range(0, len(items), safe_size)]


def _suspect_target_cells_from_presence(sheet: dict[str, Any]) -> list[dict[str, Any]]:
    fields, header, rows = _sheet_dimensions(sheet)
    target_cells = _target_cells_from_sheet(sheet)
    target_by_cell = {}
    for item in target_cells:
        row_index = _coerce_int(item.get("target_row_index"))
        col_index = _coerce_int(item.get("target_col_index"))
        if row_index is not None and col_index is not None:
            target_by_cell[(row_index, col_index)] = item
    presence_cells = set()
    for item in _quantity_presence_hints_from_sheet(sheet):
        row_index = _coerce_int(item.get("target_row_index"))
        col_index = _coerce_int(item.get("target_col_index"))
        if row_index is not None and col_index is not None:
            presence_cells.add((row_index, col_index))
    suspects: dict[tuple[int, int], dict[str, Any]] = {}

    def is_reviewable(row_index: int, col_index: int) -> bool:
        if row_index < 0 or row_index >= len(rows) or col_index < 0 or col_index >= len(fields):
            return False
        return (row_index, col_index) in target_by_cell and _is_review_quantity_field(
            fields[col_index],
            header[col_index] if col_index < len(header) else "",
        )

    def add_suspect(row_index: int, col_index: int, reason: str, priority: int) -> None:
        if not is_reviewable(row_index, col_index):
            return
        key = (row_index, col_index)
        current = _normalize_text(rows[row_index][col_index] if col_index < len(rows[row_index]) else "")
        existing = suspects.get(key)
        reasons = list((existing or {}).get("reasons") or [])
        if reason not in reasons:
            reasons.append(reason)
        suspects[key] = {
            "row_index": row_index,
            "col_index": col_index,
            "priority": min(priority, int((existing or {}).get("priority", priority))),
            "reasons": reasons,
            "current_value": current,
            "has_quantity_presence": key in presence_cells,
        }

    for row_index, row in enumerate(rows):
        for col_index, field in enumerate(fields):
            if not is_reviewable(row_index, col_index):
                continue
            current = _normalize_text(row[col_index] if col_index < len(row) else "")
            has_presence = (row_index, col_index) in presence_cells
            if has_presence and not current:
                add_suspect(row_index, col_index, "presence_mark_but_sheet_blank", 10)
            if current and not has_presence:
                add_suspect(row_index, col_index, "sheet_value_but_no_presence_mark", 10)
            if not ((has_presence and not current) or (current and not has_presence)):
                continue
            for neighbor_col in (col_index - 2, col_index - 1, col_index + 1, col_index + 2):
                if not is_reviewable(row_index, neighbor_col):
                    continue
                neighbor_current = _normalize_text(row[neighbor_col] if neighbor_col < len(row) else "")
                neighbor_has_presence = (row_index, neighbor_col) in presence_cells
                if current and not has_presence and neighbor_has_presence and not neighbor_current:
                    add_suspect(row_index, col_index, "possible_adjacent_column_extra_value", 0)
                    add_suspect(row_index, neighbor_col, "possible_adjacent_column_missing_value", 0)
                if has_presence and not current and neighbor_current and not neighbor_has_presence:
                    add_suspect(row_index, col_index, "possible_adjacent_column_missing_value", 0)
                    add_suspect(row_index, neighbor_col, "possible_adjacent_column_extra_value", 0)

    for row_index, row in enumerate(rows):
        date = _normalize_text(row[0] if len(row) > 0 else "")
        daypart = _normalize_text(row[1] if len(row) > 1 else "")
        for col_index, field in enumerate(fields):
            if not is_reviewable(row_index, col_index):
                continue
            current = _normalize_text(row[col_index] if col_index < len(row) else "")
            if current or (row_index, col_index) in presence_cells:
                continue
            neighbor_signal = 0
            for neighbor_row in (row_index - 1, row_index + 1):
                if not is_reviewable(neighbor_row, col_index):
                    continue
                neighbor = rows[neighbor_row]
                if _normalize_text(neighbor[0] if len(neighbor) > 0 else "") != date:
                    continue
                if _normalize_text(neighbor[1] if len(neighbor) > 1 else "") != daypart:
                    continue
                neighbor_current = _normalize_text(neighbor[col_index] if col_index < len(neighbor) else "")
                if neighbor_current or (neighbor_row, col_index) in presence_cells:
                    neighbor_signal += 1
            if neighbor_signal >= 2:
                add_suspect(row_index, col_index, "same_block_gap_between_quantity_rows", 40)

    ordered = sorted(
        suspects.values(),
        key=lambda item: (
            int(item.get("priority", 100)),
            int(item.get("row_index", 0)),
            int(item.get("col_index", 0)),
        ),
    )
    selected: list[dict[str, Any]] = []
    for item in ordered[:_MAX_LLM_EVIDENCE]:
        key = (int(item["row_index"]), int(item["col_index"]))
        target = dict(target_by_cell[key])
        target["suspect_review"] = {
            "reasons": item["reasons"],
            "priority": item["priority"],
            "current_value": item["current_value"],
            "has_quantity_presence": item["has_quantity_presence"],
        }
        selected.append(target)
    return selected


def _second_pass_suspect_target_cells(sheet: dict[str, Any]) -> list[dict[str, Any]]:
    return _suspect_target_cells_from_presence(sheet)


def _sheet_context_for_llm(
    sheet: dict[str, Any],
    patches: list[dict[str, Any]],
    warnings: list[dict[str, Any]] | None = None,
    evidence_payload: dict[str, Any] | None = None,
    computed_context: dict[str, Any] | None = None,
    include_ocr_sheet_comparison: bool = True,
    include_ocr_context: bool = True,
    include_target_cell_map: bool | None = None,
    target_cells_override: list[dict[str, Any]] | None = None,
    fax_image_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    fields, header, _rows = _sheet_dimensions(sheet)
    include_target_cell_map = include_ocr_context if include_target_cell_map is None else include_target_cell_map
    selected_target_cells = (
        target_cells_override
        if target_cells_override is not None
        else _target_cells_from_sheet(sheet)[:_MAX_LLM_EVIDENCE]
    )
    coordinate_transform = _target_coordinate_transform(
        target_cells=_target_cells_from_sheet(sheet),
        fax_image_meta=fax_image_meta,
    )
    quantity_presence_hints = _quantity_presence_hints_from_sheet(sheet)
    quantity_presence_by_cell = {
        (int(item["target_row_index"]), int(item["target_col_index"])): item
        for item in quantity_presence_hints
        if _coerce_int(item.get("target_row_index")) is not None
        and _coerce_int(item.get("target_col_index")) is not None
    }
    payload = {
        "fields": fields,
        "header": header,
        "rows": _row_context(sheet)[:_MAX_LLM_ROWS],
        "ocr_numeric_cell_items": (
            _combined_ocr_items(sheet=sheet, evidence_payload=evidence_payload)[:_MAX_LLM_EVIDENCE]
            if include_ocr_context
            else []
        ),
        "target_cell_map": _target_cell_context_items(
            selected_target_cells if include_target_cell_map else [],
            coordinate_transform=coordinate_transform,
            quantity_presence_by_cell=quantity_presence_by_cell,
        ),
        "ocr_quantity_presence_hints": quantity_presence_hints if include_target_cell_map else [],
        "fax_image": {
            "coordinate_transform": coordinate_transform,
            "instruction": (
                "target_cell_map.bbox is in attached_image_pixels when bbox_coordinate_space is attached_image_pixels. "
                "Use bbox, not bbox_original, to inspect the attached image."
            ),
        },
        "computed_context": computed_context or {},
        "rule_patches": patches[:_MAX_LLM_WARNINGS],
        "rule_warnings": (warnings or [])[:_MAX_LLM_WARNINGS],
    }
    if include_ocr_sheet_comparison and include_ocr_context:
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
    response_schema: dict[str, Any] | None = None,
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
    if response_schema:
        body["generationConfig"]["responseSchema"] = response_schema
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


def _patch_index_from_item(item: dict[str, Any], primary_key: str, fallback_keys: tuple[str, ...]) -> int | None:
    value = _coerce_int(item.get(primary_key))
    if value is not None:
        return value
    return _first_int(item, fallback_keys)


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
        row_index = _patch_index_from_item(item, "row_index", ("target_row_index", "sheet_row_index"))
        col_index = _patch_index_from_item(item, "col_index", ("target_col_index", "sheet_col_index"))
        if row_index is None or col_index is None:
            continue
        has_suggested_value = "suggested_value" in item
        suggested = _normalize_text(item.get("suggested_value"))
        if not suggested and not (has_suggested_value and _normalize_text(item.get("current_value"))):
            continue
        current = _normalize_text(item.get("current_value"))
        current_numeric = _numeric_value(current)
        suggested_numeric = _numeric_value(suggested)
        if current_numeric is not None and suggested_numeric is not None and abs(current_numeric - suggested_numeric) <= 0.0001:
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
                current_value=current,
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
        if isinstance(fax_image_meta, dict):
            dimensions = _png_dimensions_from_base64(fax_image_png_base64)
            if dimensions is not None:
                fax_image_meta = {
                    **fax_image_meta,
                    "pixel_width": dimensions[0],
                    "pixel_height": dimensions[1],
                }
        patch_response_schema = {
            "type": "OBJECT",
            "properties": {
                "patches": {
                    "type": "ARRAY",
                    "items": {
                        "type": "OBJECT",
                        "properties": {
                            "row_index": {"type": "INTEGER"},
                            "col_index": {"type": "INTEGER"},
                            "target_row_index": {"type": "INTEGER"},
                            "target_col_index": {"type": "INTEGER"},
                            "current_value": {"type": "STRING"},
                            "suggested_value": {"type": "STRING"},
                            "confidence": {"type": "STRING"},
                            "reason": {"type": "STRING"},
                            "evidence": {"type": "STRING"},
                            "alternatives": {
                                "type": "ARRAY",
                                "items": {"type": "STRING"},
                            },
                        },
                    },
                },
            },
            "required": ["patches"],
        }
        system_prompt = (
            "You review a Japanese FAX order sheet. Return JSON only. "
            "Use only the attached original FAX page image, the current sheet values, and target_cell_map geometry. "
            "Do not use OCR output values, OCR digit candidates, OCR recognized text, OCR confidence scores, OCR evidence values, or OCR-vs-sheet comparisons. "
            "You may use ocr_quantity_presence_hints only as red-dot style hints that some quantity mark exists in that cell; never copy or infer a digit from OCR. "
            "The provided target_cell_map contains suspect cells only. They were selected because quantity-presence hints and current sheet values disagree, an adjacent-column shift is plausible, or the value is a gap inside the same date/daypart block. "
            "The attached image is a review contact sheet containing enlarged crops around the suspect cells. "
            "Each crop label shows the matching row_index, col_index, and sheet cell. "
            "The blue rectangle inside each crop is the exact suspect cell; ignore handwritten numbers outside that blue rectangle except to detect adjacent-column shifts. "
            "For each suspect quantity cell, inspect the matching blue rectangle and judge whether the current sheet value contradicts the visible handwritten number in that FAX cell. "
            "Ignore bbox_original for visual lookup; it is included only for audit lineage. "
            "Return no patch only when you can determine with 100% certainty that the current sheet value exactly matches the FAX cell image. "
            "If the match is merely plausible, approximate, partially readable, faint, messy, overwritten, crossed out, slash-corrected, or otherwise not 100% certain, you must return a patch with the best visible candidate. "
            "If the current sheet has a number but the FAX cell is blank, or the mark visibly belongs to an adjacent column, return a patch for the extra cell with suggested_value as an empty string. "
            "When suspect_review.reasons contains sheet_value_but_no_presence_mark, you must explicitly verify the blue rectangle. If the blue rectangle is blank, return suggested_value as an empty string. "
            "When a mark appears one column left or right from the current sheet value, report both sides when needed: blank the extra cell and fill the missing adjacent cell. "
            "For corrections or uncertainty reviews, include alternative digit candidates and explain the visible basis from the FAX image only. "
            "Do not invent menu rows or structural cells. Use row_index and col_index from the input."
        )
        try:
            chunk_size = max(1, min(int(os.getenv("WORKFLOW_V2_AUTO_EDIT_TARGET_CHUNK_SIZE", "4")), 8))
        except ValueError:
            chunk_size = 4
        try:
            max_workers = max(1, min(int(os.getenv("WORKFLOW_V2_AUTO_EDIT_MAX_WORKERS", "6")), 6))
        except ValueError:
            max_workers = 6
        suspect_targets = _suspect_target_cells_from_presence(sheet)
        target_chunks = _chunk_items(suspect_targets, chunk_size)
        if not target_chunks:
            target_chunks = [[]]

        def request_chunk(
            chunk_item: tuple[int, list[dict[str, Any]]],
            *,
            prompt: str = system_prompt,
            second_pass: bool = False,
        ) -> tuple[dict[str, Any] | None, dict[str, Any], int, int]:
            chunk_index, target_chunk = chunk_item
            coordinate_transform = _target_coordinate_transform(
                target_cells=_target_cells_from_sheet(sheet),
                fax_image_meta=fax_image_meta,
            )
            review_image_png_base64 = _review_contact_sheet_png_base64(
                fax_image_png_base64=fax_image_png_base64,
                target_cells=target_chunk,
                coordinate_transform=coordinate_transform,
            )
            payload, meta = _gemini_json_request(
                system_prompt=prompt,
                user_payload=_sheet_context_for_llm(
                    sheet,
                    rule_patches,
                    evidence_payload=None,
                    computed_context={
                        "target_chunk_index": chunk_index,
                        "target_chunk_count": len(target_chunks),
                        "target_chunk_size": len(target_chunk),
                        "suspect_target_cell_count": len(suspect_targets),
                        "second_pass_suspect_review": bool(second_pass),
                    },
                    include_ocr_sheet_comparison=False,
                    include_ocr_context=False,
                    include_target_cell_map=True,
                    target_cells_override=target_chunk,
                    fax_image_meta=fax_image_meta,
                ),
                model=model,
                array_key="patches",
                image_png_base64=review_image_png_base64 or fax_image_png_base64,
                response_schema=patch_response_schema,
            )
            return payload, meta, chunk_index, len(target_chunk)

        chunk_meta: list[dict[str, Any]] = []
        combined_payload_patches: list[dict[str, Any]] = []
        failed_chunks = 0
        with ThreadPoolExecutor(max_workers=min(max_workers, len(target_chunks))) as executor:
            chunk_results = list(executor.map(request_chunk, enumerate(target_chunks)))
        for payload, meta, chunk_index, target_count in chunk_results:
            chunk_status = _normalize_text(meta.get("status") if isinstance(meta, dict) else "")
            patch_items = payload.get("patches") if isinstance(payload, dict) else []
            patch_count = len(patch_items) if isinstance(patch_items, list) else 0
            chunk_meta.append(
                {
                    "chunk_index": chunk_index,
                    "status": chunk_status or "unknown",
                    "target_count": target_count,
                    "patch_count": patch_count,
                    "error": meta.get("error") if isinstance(meta, dict) else None,
                }
            )
            if chunk_status != "ok":
                failed_chunks += 1
                continue
            if isinstance(patch_items, list):
                combined_payload_patches.extend([item for item in patch_items if isinstance(item, dict)])
        if failed_chunks:
            for payload, meta, chunk_index, target_count in chunk_results:
                chunk_status = _normalize_text(meta.get("status") if isinstance(meta, dict) else "")
                if chunk_status == "ok":
                    continue
                target_chunk = target_chunks[chunk_index] if chunk_index < len(target_chunks) else []
                retry_patch_count = 0
                retry_failed = 0
                for target_cell in target_chunk:
                    retry_payload, retry_meta, _retry_chunk_index, _retry_target_count = request_chunk(
                        (chunk_index, [target_cell])
                    )
                    retry_status = _normalize_text(retry_meta.get("status") if isinstance(retry_meta, dict) else "")
                    retry_patches = retry_payload.get("patches") if isinstance(retry_payload, dict) else []
                    if retry_status != "ok":
                        retry_failed += 1
                        continue
                    if isinstance(retry_patches, list):
                        retry_patch_count += len(retry_patches)
                        combined_payload_patches.extend([item for item in retry_patches if isinstance(item, dict)])
                chunk_meta.append(
                    {
                        "chunk_index": chunk_index,
                        "status": "retry_failed" if retry_failed == len(target_chunk) else "retry_ok",
                        "target_count": len(target_chunk),
                        "patch_count": retry_patch_count,
                        "error": None if retry_failed == 0 else f"{retry_failed}_single_cell_retries_failed",
                        "retry_of_failed_chunk": True,
                    }
                )
        llm_payload = {"patches": combined_payload_patches}
        llm_payload = {"patches": combined_payload_patches}
        if failed_chunks == len(target_chunks):
            llm_status = "failed"
        elif failed_chunks:
            llm_status = "partial_failed"
        else:
            llm_status = "ok"
        first_error = next((item.get("error") for item in chunk_meta if item.get("error")), None)
        llm_meta = {
            "status": llm_status,
            "model": model or os.getenv("WORKFLOW_V2_LLM_MODEL", "").strip() or "gemini-2.5-pro",
            "chunks": chunk_meta,
            "failed_chunks": failed_chunks,
            "total_chunks": len(target_chunks),
            "error": first_error,
            "fax_image": fax_image_meta or {"status": "not_provided"},
        }
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
    suggested_value: object | None = None,
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
        "suggested_value": _normalize_text(suggested_value) if suggested_value is not None else None,
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
                    suggested_value=_numeric_display(baseline),
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
                    suggested_value=_numeric_display(baseline),
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
                        suggested_value=_numeric_display(baseline),
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
                        suggested_value=_numeric_display(baseline),
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
                        suggested_value=_numeric_display(baseline),
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
                        suggested_value=_numeric_display(baseline),
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
        evidence = item.get("evidence") if isinstance(item.get("evidence"), dict) else {}
        suggested_value = _normalize_text(item.get("suggested_value")) or _normalize_text(evidence.get("baseline"))
        if not suggested_value:
            continue
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
                evidence=evidence,
                suggested_value=suggested_value,
            )
        )
    return normalized


def build_sheet_anomaly_report(
    *,
    sheet: dict[str, Any],
    evidence_payload: dict[str, Any] | None = None,
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
            "Flag obvious high/low values such as a likely extra digit. "
            "When a safe correction is clear for a cell, include suggested_value; otherwise omit it."
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
