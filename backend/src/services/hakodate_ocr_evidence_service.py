from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import math
import re
from typing import Any

from openpyxl.utils import get_column_letter


_FULLWIDTH_DIGIT_TRANS = str.maketrans("０１２３４５６７８９，．", "0123456789,.")


@dataclass(frozen=True)
class OcrEvidenceRecord:
    evidence_id: str
    run_id: str
    engine: str
    source_scope: str
    raw_text: str
    normalized_value: str | None
    confidence: float | None = None
    source_bbox: list[float] | None = None
    center: list[float] | None = None
    candidate_type: str = "unknown"
    raw_payload_ref: str | None = None
    engine_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TargetCell:
    target_cell_id: str
    sheet_cell: str
    worksheet_row: int
    worksheet_col: int
    semantic_field: str
    bbox: list[float]
    center: list[float]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AssignedOcrResult:
    target_cell_id: str
    sheet_cell: str
    worksheet_row: int
    worksheet_col: int
    semantic_field: str
    evidence_ids: list[str]
    assigned_value: str | None
    assignment_confidence: float | None
    assignment_state: str
    raw_texts: list[str]
    target_metadata: dict[str, Any] = field(default_factory=dict)


def normalize_ocr_value(value: object) -> str:
    text = str(value if value is not None else "").strip().translate(_FULLWIDTH_DIGIT_TRANS)
    if not text:
        return ""
    if re.fullmatch(r"(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)(?:\.0+)?", text):
        return text.replace(",", "").split(".")[0].lstrip("0") or "0"
    # Keep uncertain raw text visible; validation, not character deletion, stops it.
    return str(value if value is not None else "")


def _quantity_recognizer_issue(source: dict[str, Any]) -> str | None:
    if source.get("role") != "quantity":
        return None
    raw = normalize_ocr_value(source.get("recognizer_raw_text", source.get("raw_text", "")))
    if raw and not re.fullmatch(r"[0-9]+", raw):
        return "hakodate_ocr_quantity_non_numeric"
    if "recognizer_digit_score_threshold" in source:
        threshold, score = source["recognizer_digit_score_threshold"], source.get("recognizer_score")
        if any(type(value) not in (int, float) or not math.isfinite(value) for value in (threshold, score)):
            raise ValueError("hakodate_recognizer_confidence_invalid")
        if score < threshold:
            return "hakodate_ocr_quantity_low_confidence"
    candidate = source.get("recognizer_accepted_candidate")
    if ((source.get("recognizer_candidate_accepted") is True and (
                not isinstance(candidate, dict) or not raw
                or not any(key in candidate and candidate[key] is not None
                           for key in ("text", "normalized_digits"))))
            or source.get("recognizer_decision_source") == "topk_digits"
            or any(key in source and source[key] is not None and normalize_ocr_value(source[key]) != raw
                   for key in ("ocr_text", "ocr_normalized", "pred_digits", "resolved_value"))
            or (isinstance(candidate, dict) and any(
                key in candidate and normalize_ocr_value(candidate[key]) != raw
                for key in ("text", "normalized_digits")))):
        return "hakodate_ocr_quantity_candidate_conflict"
    return None


def recognition_input_issue(source: dict[str, Any]) -> str | None:
    observation_issue = None
    if 'recognizer_preprocess_observation' in source:
        from src.services.hakodate_preprocess_observation import validate_metadata
        observation_issue = validate_metadata(source)
    evidence = source.get("recognizer_input_evidence")
    if "recognizer_input_evidence" not in source:
        return _quantity_recognizer_issue(source) or source.get("quantity_source_issue") or None
    if (not isinstance(evidence, dict) or evidence.get("contract") != "hakodate_contact_crop/v1"
            or type(evidence.get("all_white")) is not bool
            or not isinstance(evidence.get("bbox"), list) or len(evidence["bbox"]) != 4
            or any(type(v) is not int for v in evidence["bbox"])
            or type(evidence.get("width")) is not int or evidence["width"] <= 0
            or type(evidence.get("height")) is not int or evidence["height"] <= 0
            or not re.fullmatch(r"[0-9a-f]{64}", str(evidence.get("rgb_sha256") or ""))):
        raise ValueError("hakodate_recognizer_input_evidence_invalid")
    x0, y0, x1, y1 = evidence["bbox"]
    if x0 < 0 or y0 < 0 or x1 - x0 != evidence["width"] or y1 - y0 != evidence["height"]:
        raise ValueError("hakodate_recognizer_input_evidence_invalid")
    if evidence["all_white"] and any(str(source.get(key) or "").strip() for key in
            ("raw_text", "text", "recognizer_raw_text", "ocr_text", "ocr_normalized", "pred_digits")):
        return "hakodate_blank_input_nonempty_prediction"
    if observation_issue:
        return observation_issue
    if source.get("recognizer_preprocess_ink_removed") is True and 'recognizer_preprocess_observation' not in source:
        return "hakodate_preprocess_ink_loss"
    return _quantity_recognizer_issue(source) or source.get("quantity_source_issue") or None


def recognition_resolution(source: dict[str, Any]) -> dict[str, Any]:
    """Keep model diagnostics separate from a usable cell value."""
    issue = recognition_input_issue(source)
    if issue:
        return {"resolution_state": "review_required", "resolved_value": None,
                "quantity_source_issue": issue}
    value = source.get("ocr_normalized")
    if source.get("role") == "quantity":
        value = normalize_ocr_value(source.get("recognizer_raw_text", source.get("raw_text", "")))
    elif source.get("role") == "note":
        value = source.get("recognizer_raw_text", source.get("raw_text", ""))
    return {"resolution_state": "resolved", "resolved_value": value}


def recognition_review(records: list[dict[str, Any]]) -> dict[str, Any]:
    cells = []
    seen = set()
    for record in records:
        metadata = record.get("engine_metadata") or {}
        source = metadata.get("recognizer_output")
        if not isinstance(source, dict):
            continue
        issue = recognition_input_issue(source)
        if issue is None:
            continue
        cell = metadata.get("sheet_cell")
        if (issue not in {"hakodate_blank_input_nonempty_prediction", "hakodate_ocr_quantity_non_numeric", "hakodate_preprocess_ink_loss",
                          "hakodate_ocr_quantity_low_confidence", "hakodate_ocr_quantity_candidate_conflict"} or not isinstance(cell, str)
                or not cell or cell in seen or not isinstance(record.get("raw_text"), str)):
            raise ValueError("hakodate_recognition_review_invalid")
        seen.add(cell)
        cells.append({"sheet_cell": cell, "raw_text": record["raw_text"], "reason": issue})
    return {"cells": cells, "count": len(cells)}


def validate_ocr_quantity_source(record: dict[str, Any]) -> str:
    sources = [record]
    metadata = record.get("engine_metadata")
    if isinstance(metadata, dict):
        if metadata.get("quantity_source_issue"):
            raise ValueError("hakodate_ocr_quantity_unresolved")
        original = metadata.get("recognizer_output")
        if isinstance(original, dict):
            sources.append(original)
        original_values = metadata.get("source_values")
        if isinstance(original_values, dict):
            sources.append(original_values)
    values = []
    for source in sources:
        if recognition_input_issue(source):
            raise ValueError("hakodate_ocr_quantity_unresolved")
        raw_keys = [key for key in ("raw_text", "text", "recognizer_raw_text", "ocr_text") if key in source]
        if not raw_keys:
            raise ValueError("hakodate_ocr_quantity_source_missing")
        for key in raw_keys:
            value = normalize_ocr_value(source[key])
            if value and not re.fullmatch(r"[0-9]+", value):
                raise ValueError("hakodate_ocr_quantity_unresolved")
            values.append(value)
        for key in ("normalized_value", "ocr_normalized", "pred_digits", "resolved_value"):
            if key in source and source[key] is not None:
                value = normalize_ocr_value(source[key])
                if value and not re.fullmatch(r"[0-9]+", value):
                    raise ValueError("hakodate_ocr_quantity_unresolved")
                values.append(value)
        candidate = source.get("recognizer_accepted_candidate")
        if isinstance(candidate, dict):
            for key in ("text", "normalized_digits"):
                if key in candidate and candidate[key] is not None:
                    value = normalize_ocr_value(candidate[key])
                    if value and not re.fullmatch(r"[0-9]+", value):
                        raise ValueError("hakodate_ocr_quantity_unresolved")
                    values.append(value)
    if len(set(values)) != 1:
        raise ValueError("hakodate_ocr_quantity_source_conflict")
    return values[0]


def authored_cell_role(target: dict[str, Any]) -> str:
    role = target.get("role")
    fields = [target[key] for key in ("field", "semantic_field") if target.get(key) is not None]
    if not isinstance(role, str) or role not in {"quantity", "note"} or not fields:
        raise ValueError("hakodate_target_role_unresolved")
    if any(not isinstance(field, str) or (
        not field.startswith("qty.") if role == "quantity" else field not in {"note", "remarks"}
    ) for field in fields):
        raise ValueError("hakodate_target_role_conflict")
    if len(set(fields)) != 1:
        raise ValueError("hakodate_target_role_conflict")
    return role


def validate_ocr_source_for_role(record: dict[str, Any], role: str) -> str:
    sources = [record]
    metadata = record.get("engine_metadata")
    if isinstance(metadata, dict):
        sources.extend(metadata[key] for key in ("recognizer_output", "source_values") if isinstance(metadata.get(key), dict))
    for source in sources:
        if "role" in source and authored_cell_role(source) != role:
            raise ValueError("hakodate_target_role_conflict")
        for key in ("field", "semantic_field"):
            if key in source and source[key] is not None:
                authored_cell_role({"role": role, "field": source[key]})
    if role == "quantity":
        return validate_ocr_quantity_source(record)
    if role != "note":
        raise ValueError("hakodate_target_role_unresolved")
    values = []
    for source in sources:
        if recognition_input_issue(source):
            raise ValueError("hakodate_ocr_quantity_unresolved")
        raw_keys = [key for key in ("raw_text", "text", "recognizer_raw_text", "ocr_text") if key in source]
        if not raw_keys:
            raise ValueError("hakodate_ocr_quantity_source_missing")
        values.extend(str(source[key] if source[key] is not None else "") for key in raw_keys)
    if len(set(values)) != 1:
        raise ValueError("hakodate_ocr_quantity_source_conflict")
    return values[0]


def validate_assigned_ocr_value(assignment: dict[str, Any]) -> str:
    targets = _logical_sheet_targets_for_assignment(assignment)
    role = authored_cell_role(targets[0])
    if "role" in assignment and authored_cell_role(assignment) != role:
        raise ValueError("hakodate_target_role_conflict")
    if role == "quantity":
        return validate_assigned_ocr_quantity(assignment)
    state = assignment.get("assignment_state")
    value = assignment.get("assigned_value") if "assigned_value" in assignment else assignment.get("value_normalized")
    value = str(value if value is not None else "")
    raw_texts = assignment.get("raw_texts")
    if not isinstance(raw_texts, list) or state not in {"assigned", "blank", "blank_evidence"}:
        raise ValueError("hakodate_ocr_quantity_unresolved")
    if (not raw_texts and (state != "blank" or value)) or any(str(raw if raw is not None else "") != value for raw in raw_texts):
        raise ValueError("hakodate_ocr_quantity_source_conflict")
    if (state in {"blank", "blank_evidence"} and value) or (state == "assigned" and not value):
        raise ValueError("hakodate_ocr_quantity_source_conflict")
    return value


def validate_assigned_ocr_quantity(assignment: dict[str, Any]) -> str:
    state = assignment.get("assignment_state")
    value_key = "assigned_value" if "assigned_value" in assignment else "value_normalized"
    value = assignment.get(value_key)
    raw_texts = assignment.get("raw_texts")
    if not isinstance(raw_texts, list):
        raise ValueError("hakodate_ocr_quantity_source_missing")
    if state == "blank" and not raw_texts:
        return validate_ocr_quantity_source({"raw_text": "", "normalized_value": value})
    if state not in {"assigned", "blank_evidence"} or not raw_texts:
        raise ValueError("hakodate_ocr_quantity_unresolved")
    values = [
        validate_ocr_quantity_source({"raw_text": raw, "normalized_value": value})
        for raw in raw_texts
    ]
    if (
        len(set(values)) != 1 or (state == "blank_evidence" and values[0])
        or (state == "assigned" and not values[0])
    ):
        raise ValueError("hakodate_ocr_quantity_source_conflict")
    return values[0]


def normalize_ocr_confidence(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        score = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return score if math.isfinite(score) and 0.0 <= score <= 1.0 else None


def _stable_id(parts: list[object]) -> str:
    payload = "\u001f".join(str(part) for part in parts)
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()
    return digest[:16]


def _as_float_box(value: object) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        x0, y0, x1, y1 = [float(item) for item in value]
    except Exception:
        return None
    if x1 < x0 or y1 < y0:
        return None
    return [x0, y0, x1, y1]


def _center_from_box(box: list[float] | None) -> list[float] | None:
    if not box:
        return None
    return [(float(box[0]) + float(box[2])) / 2.0, (float(box[1]) + float(box[3])) / 2.0]


def _as_center(value: object, *, fallback_box: list[float] | None = None) -> list[float] | None:
    if isinstance(value, (list, tuple)) and len(value) == 2:
        try:
            return [float(value[0]), float(value[1])]
        except Exception:
            return None
    return _center_from_box(fallback_box)


def evidence_from_records(
    records: list[dict[str, Any]],
    *,
    run_id: str,
    engine: str,
    source_scope: str,
    candidate_type: str = "numeric",
    raw_payload_ref: str | None = None,
    retain_empty_records: bool = False,
) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    for index, record in enumerate(records):
        raw_text_value = record.get("raw_text") if "raw_text" in record else record.get("text")
        raw_text = str(raw_text_value if raw_text_value is not None else "")
        metadata = dict(record.get("engine_metadata") or {})
        # Preserve source aliases until the authored target supplies the cell role.
        metadata.setdefault("source_values", {key: record[key] for key in (
            "raw_text", "text", "recognizer_raw_text", "ocr_text", "normalized_value",
            "ocr_normalized", "pred_digits", "resolved_value", "recognizer_accepted_candidate", "role", "field", "semantic_field",
        ) if key in record})
        original = metadata.get("recognizer_output")
        review_required = isinstance(original, dict) and recognition_input_issue(original) is not None
        normalized_value = None if review_required else raw_text
        source_bbox = _as_float_box(record.get("source_bbox") or record.get("bbox"))
        center = _as_center(record.get("center"), fallback_box=source_bbox)
        evidence_id = str(
            record.get("evidence_id")
            or _stable_id([run_id, engine, source_scope, index, raw_text, source_bbox, center])
        )
        confidence = record.get("confidence")
        confidence_value = normalize_ocr_confidence(confidence)
        metadata.setdefault("raw_confidence", confidence)
        if metadata.get("confidence_source_issue") or normalize_ocr_confidence(metadata["raw_confidence"]) is None:
            confidence_value = None
        if confidence_value is None:
            metadata["confidence_source_issue"] = "hakodate_ocr_confidence_unresolved"
        evidence.append(
            asdict(
                OcrEvidenceRecord(
                    evidence_id=evidence_id,
                    run_id=run_id,
                    engine=engine,
                    source_scope=source_scope,
                    raw_text=raw_text,
                    normalized_value=normalized_value,
                    confidence=confidence_value,
                    source_bbox=source_bbox,
                    center=center,
                    candidate_type=str(record.get("candidate_type") or candidate_type),
                    raw_payload_ref=str(record.get("raw_payload_ref") or raw_payload_ref or ""),
                    engine_metadata=metadata,
                )
            )
        )
        if isinstance(original, dict) and "resolution_state" in original:
            evidence[-1]["resolution_state"] = "review_required" if review_required else "resolved"
    return evidence


def target_cells_from_regions(regions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cells: list[dict[str, Any]] = []
    for region in regions:
        if not isinstance(region, dict):
            raise ValueError("hakodate_logical_targets_invalid")
        metadata = region.get("metadata", {})
        if not isinstance(metadata, dict) or "metadata" in metadata:
            raise ValueError("hakodate_logical_targets_invalid")
        metadata = dict(metadata)
        for key, value in region.items():
            if key in {
                "target_cell_id", "sheet_cell", "worksheet_row", "worksheet_col",
                "semantic_field", "field", "bbox", "center", "metadata",
            }:
                continue
            if key in metadata and metadata[key] != value:
                raise ValueError("hakodate_logical_targets_invalid")
            metadata[key] = value
        box = _as_float_box(region.get("bbox"))
        if not box or not all(math.isfinite(value) for value in box) or box[0] >= box[2] or box[1] >= box[3]:
            raise ValueError("hakodate_target_bbox_invalid")
        if "center" in region:
            raw_center = region["center"]
            if not isinstance(raw_center, (list, tuple)) or len(raw_center) != 2:
                raise ValueError("hakodate_target_center_invalid")
            center = _as_center(raw_center)
        else:
            center = _center_from_box(box)
        if not center or not all(math.isfinite(value) for value in center) or not _contains_point(box, center):
            raise ValueError("hakodate_target_center_invalid")
        target_id = str(region.get("target_cell_id") or region.get("sheet_cell") or region.get("region_id") or "")
        if not target_id.strip():
            raise ValueError("hakodate_target_identity_required")
        if any(cell["target_cell_id"] == target_id for cell in cells):
            raise ValueError("hakodate_logical_targets_invalid")
        _logical_sheet_targets_for_assignment({
            **region, "semantic_field": region.get("semantic_field") or region.get("field"),
            "target_metadata": metadata,
        })
        cells.append(
            asdict(
                TargetCell(
                    target_cell_id=target_id,
                    sheet_cell=str(region.get("sheet_cell") or ""),
                    worksheet_row=int(region.get("worksheet_row") or 0),
                    worksheet_col=int(region.get("worksheet_col") or 0),
                    semantic_field=str(region.get("semantic_field") or region.get("field") or ""),
                    bbox=box,
                    center=center,
                    metadata=metadata,
                )
            )
        )
    return cells


def _contains_point(box: list[float], center: list[float]) -> bool:
    return float(box[0]) <= float(center[0]) <= float(box[2]) and float(box[1]) <= float(center[1]) <= float(box[3])


def _area(box: list[float]) -> float:
    return max(0.0, float(box[2]) - float(box[0])) * max(0.0, float(box[3]) - float(box[1]))


def _mean_confidence(records: list[dict[str, Any]]) -> float | None:
    for record in records:
        metadata = record.get("engine_metadata")
        if isinstance(metadata, dict) and (
            metadata.get("confidence_source_issue")
            or ("raw_confidence" in metadata and normalize_ocr_confidence(metadata["raw_confidence"]) is None)
        ):
            return None
    values = [normalize_ocr_confidence(record.get("confidence")) for record in records]
    if not values or any(value is None for value in values):
        return None
    return round(sum(values) / len(values), 4)


def assign_evidence_to_target_cells(
    *,
    evidence_records: list[dict[str, Any]],
    target_cells: list[dict[str, Any]],
) -> dict[str, Any]:
    targets = target_cells_from_regions(target_cells)
    by_target: dict[str, list[dict[str, Any]]] = {str(target["target_cell_id"]): [] for target in targets}
    unassigned: list[dict[str, Any]] = []
    blockers: list[str] = []

    for evidence in evidence_records:
        center = _as_center(evidence.get("center"), fallback_box=_as_float_box(evidence.get("source_bbox")))
        if not center:
            unassigned.append({**evidence, "unassigned_reason": "missing_center"})
            blockers.append(f"hakodate_target_role_unresolved: {evidence.get('evidence_id')}")
            continue
        matches = [target for target in targets if _contains_point(target["bbox"], center)]
        if not matches:
            unassigned.append({**evidence, "unassigned_reason": "outside_target_cells"})
            blockers.append(f"hakodate_target_role_unresolved: {evidence.get('evidence_id')}")
            continue
        matches.sort(key=lambda target: (_area(target["bbox"]), str(target["target_cell_id"])))
        best = matches[0]
        if len(matches) > 1 and _area(matches[0]["bbox"]) == _area(matches[1]["bbox"]):
            unassigned.append(
                {
                    **evidence,
                    "unassigned_reason": "ambiguous_target_cell",
                    "candidate_target_cell_ids": [str(target["target_cell_id"]) for target in matches],
                }
            )
            blockers.append(f"ambiguous evidence target: {evidence.get('evidence_id')}")
            continue
        by_target[str(best["target_cell_id"])].append(evidence)

    assignments: list[dict[str, Any]] = []
    for target in targets:
        target_id = str(target["target_cell_id"])
        records = by_target.get(target_id) or []
        logical_targets = _logical_sheet_targets_for_assignment({**target, "target_metadata": target["metadata"]})
        role = authored_cell_role(logical_targets[0])
        values = []
        unresolved = False
        for record in records:
            try:
                values.append(validate_ocr_source_for_role(record, role))
            except ValueError as exc:
                blockers.append(f"{exc}: {record.get('evidence_id')}")
                unresolved = True
        unique_values = sorted(set(values))
        if unresolved:
            state = "unresolved"
            assigned_value = ""
        elif not records:
            state = "blank"
            assigned_value = ""
        elif len(unique_values) <= 1:
            state = "assigned" if unique_values and unique_values[0] else "blank_evidence"
            assigned_value = unique_values[0] if unique_values else ""
        else:
            state = "conflict"
            assigned_value = ""
            blockers.append(f"conflicting evidence for target: {target_id}")
        assignments.append(
            asdict(
                AssignedOcrResult(
                    target_cell_id=target_id,
                    sheet_cell=str(target["sheet_cell"]),
                    worksheet_row=int(target["worksheet_row"]),
                    worksheet_col=int(target["worksheet_col"]),
                    semantic_field=str(target["semantic_field"]),
                    evidence_ids=[str(record.get("evidence_id")) for record in records],
                    assigned_value=assigned_value,
                    assignment_confidence=_mean_confidence(records),
                    assignment_state=state,
                    raw_texts=[str(record.get("raw_text") if record.get("raw_text") is not None else "") for record in records],
                    target_metadata=dict(target.get("metadata") or {}),
                )
            )
        )
        if unresolved and any((record.get("engine_metadata") or {}).get("recognizer_output", {}).get("resolution_state") == "review_required" for record in records):
            assignments[-1].update(assigned_value=None, resolution_state="review_required")

    return {
        "assignments": assignments,
        "unassigned_evidence": unassigned,
        "blockers": sorted(set(blockers)),
        "summary": {
            "target_cell_count": len(targets),
            "evidence_count": len(evidence_records),
            "assigned_target_count": sum(1 for item in assignments if item["assignment_state"] == "assigned"),
            "conflict_target_count": sum(1 for item in assignments if item["assignment_state"] == "conflict"),
            "unassigned_evidence_count": len(unassigned),
            "blocker_count": len(set(blockers)),
        },
    }


def has_resolved_blank_targets(result: dict[str, Any]) -> bool:
    """All targets have actual empty recognition evidence, not missing values."""
    assignments = result.get("assignments") or []
    return bool(assignments) and not result.get("blockers") and not result.get("unassigned_evidence") and all(
        item.get("assignment_state") == "blank_evidence" and item.get("evidence_ids") for item in assignments)


def _logical_sheet_targets_for_assignment(assignment: dict[str, Any]) -> list[dict[str, Any]]:
    metadata = assignment.get("target_metadata", {})
    if not isinstance(metadata, dict):
        raise ValueError("hakodate_logical_targets_invalid")
    logical_targets = metadata.get("logical_targets") if isinstance(metadata, dict) else None
    if "logical_targets" in metadata:
        if not isinstance(logical_targets, list) or not logical_targets or any(
            not isinstance(target, dict) for target in logical_targets
        ):
            raise ValueError("hakodate_logical_targets_invalid")
        targets = logical_targets
        roles = {authored_cell_role(target) for target in targets}
        if len(roles) != 1:
            raise ValueError("hakodate_target_role_conflict")
        if "role" in assignment and assignment["role"] not in tuple(roles):
            raise ValueError("hakodate_target_role_conflict")
        if "role" in metadata:
            if metadata["role"] not in tuple(roles):
                raise ValueError("hakodate_target_role_conflict")
            if assignment.get("semantic_field") is not None:
                authored_cell_role({"role": metadata["role"], "field": assignment["semantic_field"]})
        for target in targets:
            row, col = target.get("worksheet_row"), target.get("worksheet_col")
            sheet_cell = target.get("sheet_cell")
            semantic_field = target.get("field") or target.get("semantic_field")
            if (
                type(row) is not int or type(col) is not int or row <= 0 or col <= 0
                or not isinstance(sheet_cell, str)
                or sheet_cell != f"{get_column_letter(col)}{row}"
                or not isinstance(semantic_field, str) or not semantic_field.strip()
            ):
                raise ValueError("hakodate_logical_targets_invalid")
        sheet_cells = [target["sheet_cell"] for target in targets]
        if len(set(sheet_cells)) != len(sheet_cells):
            raise ValueError("hakodate_logical_targets_invalid")
        covered = metadata.get("covered_sheet_cells")
        if covered is not None and (
            not isinstance(covered, list) or any(not isinstance(cell, str) for cell in covered)
            or len(set(covered)) != len(covered)
            or set(covered) != set(sheet_cells)
        ):
            raise ValueError("hakodate_logical_targets_invalid")
        return targets
    if metadata.get("merged_cell") or "covered_sheet_cells" in metadata:
        raise ValueError("hakodate_logical_targets_required")
    target = {
        **{key: metadata[key] for key in (
            "grid_row_index", "grid_col_index", "field_label", "date", "daypart", "menu_name", "role",
        ) if key in metadata},
        "sheet_cell": assignment.get("sheet_cell"),
        "worksheet_row": assignment.get("worksheet_row"),
        "worksheet_col": assignment.get("worksheet_col"),
        "field": assignment.get("semantic_field"),
        "role": metadata.get("role") if "role" in metadata else assignment.get("role"),
    }
    return _logical_sheet_targets_for_assignment({"target_metadata": {"logical_targets": [target]}})


def logical_sheet_targets_from_region(region: dict[str, Any]) -> list[dict[str, Any]]:
    target = target_cells_from_regions([region])[0]
    return _logical_sheet_targets_for_assignment({**target, "target_metadata": target["metadata"]})


def sheet_output_from_assigned_results(
    *,
    assignments: list[dict[str, Any]],
    blockers: list[str] | None = None,
    unassigned_evidence: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    cells: dict[str, dict[str, Any]] = {}
    row_indexes: set[int] = set()
    col_indexes: set[int] = set()
    output_blockers = list(blockers or [])
    for assignment in assignments:
        state = str(assignment.get("assignment_state") or "")
        try:
            assigned_value = validate_assigned_ocr_value(assignment)
        except ValueError as exc:
            output_blockers.append(str(exc))
            assigned_value = None if assignment.get("resolution_state") == "review_required" else ""
            state = "conflict" if state == "conflict" else "unresolved"
        try:
            targets = _logical_sheet_targets_for_assignment(assignment)
        except ValueError as exc:
            output_blockers.append(str(exc))
            continue
        for target in targets:
            sheet_cell = str(target.get("sheet_cell") or "").strip()
            if not sheet_cell:
                output_blockers.append(f"assignment missing sheet_cell: {assignment.get('target_cell_id')}")
                continue
            try:
                worksheet_row = int(target.get("worksheet_row") or 0)
                worksheet_col = int(target.get("worksheet_col") or 0)
            except Exception:
                output_blockers.append(f"assignment has invalid worksheet position: {sheet_cell}")
                continue
            if worksheet_row <= 0 or worksheet_col <= 0:
                output_blockers.append(f"assignment has non-positive worksheet position: {sheet_cell}")
                continue
            if sheet_cell in cells:
                output_blockers.append(f"duplicate sheet cell assignment: {sheet_cell}")
                continue
            row_indexes.add(worksheet_row)
            col_indexes.add(worksheet_col)
            if state == "conflict":
                output_blockers.append(f"conflicting assignment cannot enter sheet: {sheet_cell}")
            semantic_field = target.get("field") or target.get("semantic_field") or assignment.get("semantic_field")
            cells[sheet_cell] = {
                "sheet_cell": sheet_cell,
                "worksheet_row": worksheet_row,
                "worksheet_col": worksheet_col,
                "column_letter": get_column_letter(worksheet_col),
                "semantic_field": semantic_field,
                "role": target.get("role"),
                "field_label": target.get("field_label"),
                "date": target.get("date"),
                "daypart": target.get("daypart"),
                "menu_name": target.get("menu_name"),
                "value_text": assigned_value,
                "value_normalized": assigned_value,
                "assignment_state": state,
                "assignment_confidence": assignment.get("assignment_confidence"),
                "target_cell_id": assignment.get("target_cell_id"),
                "source_region_id": (assignment.get("target_metadata") or {}).get("region_id")
                if isinstance(assignment.get("target_metadata"), dict)
                else None,
                "merged_cell": (assignment.get("target_metadata") or {}).get("merged_cell")
                if isinstance(assignment.get("target_metadata"), dict)
                else None,
                "evidence_ids": list(assignment.get("evidence_ids") or []),
                "raw_texts": list(assignment.get("raw_texts") or []),
            }
            if assignment.get("resolution_state") == "review_required":
                cells[sheet_cell]["resolution_state"] = "review_required"

    sorted_cols = sorted(col_indexes)
    rows: list[dict[str, Any]] = []
    for row_index in sorted(row_indexes):
        values_by_column: dict[str, str] = {}
        cells_by_column: dict[str, dict[str, Any]] = {}
        states_by_column: dict[str, str] = {}
        for col_index in sorted_cols:
            col_letter = get_column_letter(col_index)
            sheet_cell = f"{col_letter}{row_index}"
            cell = cells.get(sheet_cell)
            values_by_column[col_letter] = None if (cell or {}).get("resolution_state") == "review_required" else str((cell or {}).get("value_text") or "")
            states_by_column[col_letter] = str((cell or {}).get("assignment_state") or "missing")
            if cell is not None:
                cells_by_column[col_letter] = cell
        rows.append(
            {
                "worksheet_row": row_index,
                "values_by_column": values_by_column,
                "states_by_column": states_by_column,
                "cells_by_column": cells_by_column,
            }
        )

    unassigned = list(unassigned_evidence or [])
    if unassigned:
        output_blockers.append(f"unassigned OCR evidence exists: {len(unassigned)}")
    return {
        "cells": cells,
        "rows": rows,
        "columns": [get_column_letter(index) for index in sorted_cols],
        "unassigned_evidence": unassigned,
        "blockers": sorted(set(output_blockers)),
        "summary": {
            "cell_count": len(cells),
            "row_count": len(rows),
            "column_count": len(sorted_cols),
            "blocker_count": len(set(output_blockers)),
            "unassigned_evidence_count": len(unassigned),
            "conflict_cell_count": sum(1 for cell in cells.values() if cell["assignment_state"] == "conflict"),
        },
    }
