"""Explicit operator interpretations, kept separate from immutable OCR artifacts."""
from copy import deepcopy
import hashlib
import json
import re

from src.services import hakodate_ocr_evidence_service as evidence

KEY = "operator_recognition_acceptance"


def payload_digest(payload):
    original = {key: value for key, value in payload.items() if key != KEY}
    return hashlib.sha256(json.dumps(original, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def review_values(payload, interpretations):
    from src.services.order_service import _extract_hakodate_ocr_evidence_records_from_payload
    records = _extract_hakodate_ocr_evidence_records_from_payload(payload, order_id="", copy_records=False)
    review = evidence.recognition_review(records)
    if not review["cells"] or payload.get("hakodate_recognition_review") != review:
        raise ValueError("recognition_acceptance_review_changed")
    if (not isinstance(interpretations, dict)
            or set(interpretations) != {cell["sheet_cell"] for cell in review["cells"]}
            or any(not isinstance(value, str) or re.fullmatch(r"[0-9]*", value) is None
                   for value in interpretations.values())):
        raise ValueError("recognition_acceptance_explicit_values_required")
    by_cell = {(row.get("engine_metadata") or {}).get("sheet_cell"): row for row in records}
    if len(by_cell) != len(records):
        raise ValueError("recognition_acceptance_review_changed")
    values = {}
    for cell in review["cells"]:
        row = by_cell[cell["sheet_cell"]]
        source = row["engine_metadata"]["recognizer_output"]
        raw = cell["raw_text"]
        if (evidence.authored_cell_role(source) != "quantity" or not isinstance(raw, str)
                or source.get("recognizer_raw_text", source.get("raw_text")) != raw):
            raise ValueError("recognition_acceptance_review_changed")
        values[row["evidence_id"]] = evidence.normalize_ocr_value(interpretations[cell["sheet_cell"]])
    allowed = {f"hakodate_ocr_quantity_unresolved: {identity}" for identity in values}
    allowed.add("hakodate_assignment_missing")
    blockers = (payload.get("hakodate_canonical_pipeline") or {}).get("blockers", [])
    if not isinstance(blockers, list) or any(item not in allowed for item in blockers):
        raise ValueError("recognition_acceptance_other_blockers")
    return values


def accepted_values(payload):
    if not isinstance(payload, dict):
        return {}
    acceptance = payload.get(KEY)
    if acceptance is None:
        return {}
    execution = payload.get("_ocr_execution") or {}
    source = payload.get("hakodate_repair_source_receipt") or {}
    review = payload.get("hakodate_recognition_review")
    if not isinstance(execution, dict) or not isinstance(source, dict) or not isinstance(review, dict):
        raise ValueError("recognition_acceptance_changed")
    if (not isinstance(acceptance, dict) or acceptance.get("version") != 1
            or not isinstance(acceptance.get("edited_by"), str) or not acceptance["edited_by"].strip()
            or not isinstance(acceptance.get("created_at"), str) or not acceptance["created_at"]
            or acceptance.get("payload_sha256") != payload_digest(payload)
            or not execution.get("evidence_run_id")
            or acceptance.get("evidence_run_id") != execution["evidence_run_id"]
            or acceptance.get("selected_pdf_receipt") != source.get("selected_pdf_receipt")
            or type(review.get("count")) is not int
            or acceptance.get("cells") != review.get("cells")
            or type(acceptance.get("workflow_revision")) is not int):
        raise ValueError("recognition_acceptance_changed")
    return review_values(payload, acceptance.get("values"))


def acknowledge(payload, *, values, edited_by, created_at, source_receipt):
    review_values(payload, values)
    if KEY in payload:
        raise ValueError("recognition_acceptance_already_recorded")
    result = deepcopy(payload)
    result[KEY] = {"version": 1, "edited_by": edited_by, "created_at": created_at,
        "workflow_revision": source_receipt["workflow_revision"],
        "selected_pdf_receipt": deepcopy(source_receipt["selected_pdf_receipt"]),
        "evidence_run_id": source_receipt["recognition_evidence_run_id"],
        "source_receipt": deepcopy(source_receipt),
        "payload_sha256": payload_digest(payload),
        "values": deepcopy(values),
        "cells": deepcopy(payload["hakodate_recognition_review"]["cells"])}
    accepted_values(result)
    return result


def payload_for_evidence(row, *, copy_payload=True):
    """Project acceptance; borrowed nested payloads are for read-only validation."""
    snapshot = row if isinstance(row, dict) else {
        "payload_json": row.payload_json, "artifact_manifest_json": row.artifact_manifest_json,
        "artifact_digest": row.artifact_digest, "id": row.id}
    original = snapshot.get("payload_json") or {}
    payload = dict(original) if not copy_payload and type(original) is dict else deepcopy(original)
    if KEY in payload:
        raise ValueError("recognition_acceptance_invalid_storage")
    acceptance = (snapshot.get("artifact_manifest_json") or {}).get(KEY)
    if acceptance is not None:
        if (not isinstance(acceptance, dict) or acceptance.get("artifact_digest") != snapshot.get("artifact_digest")
                or acceptance.get("evidence_run_id") != snapshot.get("id")):
            raise ValueError("recognition_acceptance_changed")
        payload[KEY] = deepcopy(acceptance)
        accepted_values(payload)
    return payload
