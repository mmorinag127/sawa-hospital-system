"""Persist the actual best-method output contract before its workspace closes."""

from copy import deepcopy
import hashlib
import json
import math
import mimetypes
import pickle
from pathlib import Path
import re
from uuid import uuid4

from src.services import storage_service


REQUIRED_OUTPUTS = (
    "pdf", "overlay", "overlay_png", "sheet_review_base", "contact_sheet",
    "records", "ocr_regions", "sheet_values",
)
CONTRACT = "hakodate_best_method_outputs/v1"


class _EvidenceAnalysis:
    """Successful pure work owned by one evidence operation, never a read cache."""

    def __init__(self):
        self.recognition_review = None
        self.acceptance = None
        self.targets = None
        self.records = None
        self.assignment = None

    def accepted_values(self, payload):
        from src.services.recognition_acceptance_service import accepted_values
        binding = _analysis_input_bytes(payload)
        if binding is not None and self.acceptance is not None and self.acceptance[0] == binding:
            return deepcopy(self.acceptance[1])
        accepted = accepted_values(payload)
        self.acceptance = (binding, deepcopy(accepted)) if binding is not None else None
        return accepted

    def target_cells(self, payload):
        from src.services.order_service import _extract_hakodate_target_cells_from_payload
        binding = _analysis_input_bytes(payload)
        if binding is not None and self.targets is not None and self.targets[0] == binding:
            return deepcopy(self.targets[1])
        targets = _extract_hakodate_target_cells_from_payload(payload)
        self.targets = (binding, deepcopy(targets)) if binding is not None else None
        return targets

    def evidence_records(self, payload, *, order_id):
        from src.services.order_service import _extract_hakodate_ocr_evidence_records_from_payload
        binding = _analysis_input_bytes([payload, order_id])
        if binding is not None and self.records is not None and self.records[0] == binding:
            return deepcopy(self.records[1])
        records = _extract_hakodate_ocr_evidence_records_from_payload(payload, order_id=order_id)
        self.records = (binding, deepcopy(records)) if binding is not None else None
        return records

    def assign(self, *, evidence_records, target_cells, accepted_values):
        from src.services.hakodate_ocr_evidence_service import assign_evidence_to_target_cells
        binding = _analysis_input_bytes([evidence_records, target_cells, accepted_values])
        if binding is not None and self.assignment is not None and self.assignment[0] == binding:
            return deepcopy(self.assignment[1])
        assigned = assign_evidence_to_target_cells(evidence_records=evidence_records,
            target_cells=target_cells, accepted_values=accepted_values)
        self.assignment = (binding, deepcopy(assigned)) if binding is not None else None
        return assigned

    def validate_observation(self, inputs, records, source):
        from src.services.hakodate_preprocess_observation import validate_artifact
        # Keep the live checkpoint boundary and its original exception wrapper.
        validate_artifact(inputs['preprocess_observation'], records,
                          inputs['contact_sheet'], source)


def _json_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _analysis_input_bytes(value):
    containers = set()
    active = set()

    def native(item):
        kind = type(item)
        if item is None or kind in (str, bool, int):
            return
        if kind is float:
            if not math.isfinite(item):
                raise ValueError("non_native_analysis_input")
            return
        identity = id(item)
        if kind not in (dict, list) or identity in active:
            raise ValueError("non_native_analysis_input")
        if identity in containers:
            return
        containers.add(identity)
        active.add(identity)
        if kind is dict:
            if any(type(key) is not str for key in item):
                raise ValueError("non_native_analysis_input")
            for entry in item.values():
                native(entry)
        else:
            for entry in item:
                native(entry)
        active.remove(identity)

    try:
        native(value)
        # In-memory comparison bytes only; never persisted or unpickled.
        return pickle.dumps(value, protocol=5)
    except (TypeError, ValueError, RecursionError):
        # Unrepresentable inputs still reach the original validator in its
        # original position; they never establish a reusable success.
        return None


def _verified_input_types(inputs):
    return type(inputs) is dict and all(type(key) is str and type(data) is bytes
                                       for key, data in inputs.items())


def _receipt(uri, data, content_type):
    return {"uri": uri, "sha256": hashlib.sha256(data).hexdigest(),
            "size_bytes": len(data), "content_type": content_type}


def _verify(receipt):
    if not isinstance(receipt, dict) or not re.fullmatch(r"[0-9a-f]{64}", str(receipt.get("sha256") or "")):
        raise ValueError("canonical_artifact_receipt_invalid")
    uri = receipt.get("uri")
    if not isinstance(uri, str) or not uri:
        raise ValueError("canonical_artifact_uri_required")
    try:
        data = storage_service.load_bytes_from_uri(uri, verification=True)
    except Exception as exc:
        raise ValueError("canonical_artifact_bytes_unavailable") from exc
    if len(data) != receipt.get("size_bytes") or hashlib.sha256(data).hexdigest() != receipt["sha256"]:
        raise ValueError("canonical_artifact_bytes_changed")
    return data


def validate_payload(payload, *, _analysis=None):
    pipeline = payload.get("hakodate_canonical_pipeline")
    if not isinstance(pipeline, dict):
        raise ValueError("canonical_artifact_packet_required")
    source = payload.get("hakodate_repair_source_receipt")
    selected_pdf = source.get("selected_pdf_receipt") if isinstance(source, dict) else None
    order_id = selected_pdf.get("order_id") if isinstance(selected_pdf, dict) else None
    if not order_id or pipeline.get("requested_order_id") != order_id:
        raise ValueError("canonical_artifact_source_changed")
    verified_inputs = validate_packet(
        pipeline.get("artifact_packet"), source_receipt=source,
        outputs=pipeline.get("outputs"), expected_sheet_cells=pipeline.get("expected_sheet_cells"),
        producer_version=pipeline.get("version"), order_id=order_id,
        _analysis=_analysis,
    )
    outputs = pipeline["outputs"]
    overlay = payload.get("hakodate_overlay")
    if (not isinstance(overlay, dict) or overlay.get("uri") != outputs["overlay"]
            or overlay.get("sheet_review_base_uri") != outputs["sheet_review_base"]):
        raise ValueError("canonical_artifact_overlay_reference_changed")
    evidence = payload.get("hakodate_ocr_evidence")
    records = payload.get("hakodate_ocr_evidence_records")
    if (not isinstance(evidence, dict) or not isinstance(records, list)
            or evidence.get("records") != records
            or any(not isinstance(record, dict)
                   or record.get("raw_payload_ref") != outputs["records"] for record in records)):
        raise ValueError("canonical_artifact_evidence_reference_changed")
    return verified_inputs


def persist_outputs(*, order_id, outputs, source_receipt, expected_sheet_cells, producer_version, workspace):
    if not isinstance(outputs, dict) or any(not outputs.get(key) for key in REQUIRED_OUTPUTS):
        raise ValueError("canonical_artifact_outputs_missing")
    if not isinstance(source_receipt, dict) or not source_receipt or not expected_sheet_cells:
        raise ValueError("canonical_artifact_source_required")
    namespace = f"OCR-{order_id}-{uuid4().hex}"
    receipts = {}
    saved_paths = {}
    for key, value in outputs.items():
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_]+", key) or not isinstance(value, str):
            raise ValueError("canonical_artifact_output_invalid")
        path = Path(value).resolve(strict=True)
        if not path.is_file():
            raise ValueError("canonical_artifact_output_not_file")
        if path not in saved_paths:
            data = path.read_bytes()
            if not data:
                raise ValueError("canonical_artifact_output_empty")
            content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            uri = storage_service.save_canonical_artifact_bytes(namespace, f"{key}{path.suffix}", data, content_type)
            if not uri.startswith("gs://") and Path(uri).resolve().is_relative_to(Path(workspace).resolve()):
                raise ValueError("canonical_artifact_storage_inside_workspace")
            saved_paths[path] = _receipt(uri, data, content_type)
            _verify(saved_paths[path])
        receipts[key] = deepcopy(saved_paths[path])
    metadata = {"contract": CONTRACT, "order_id": order_id, "producer_version": producer_version,
                "source_receipt": deepcopy(source_receipt), "expected_sheet_cells": list(expected_sheet_cells),
                "outputs": receipts}
    data = _json_bytes(metadata)
    uri = storage_service.save_canonical_artifact_bytes(namespace, "metadata.json", data, "application/json")
    packet = {**metadata, "metadata": _receipt(uri, data, "application/json")}
    validate_packet(packet, source_receipt=source_receipt, outputs={key: value["uri"] for key, value in receipts.items()},
                    expected_sheet_cells=expected_sheet_cells, producer_version=producer_version, order_id=order_id)
    return packet


def validate_packet(packet, *, source_receipt, outputs, expected_sheet_cells, producer_version, order_id,
                    _analysis=None):
    if not isinstance(packet, dict) or packet.get("contract") != CONTRACT:
        raise ValueError("canonical_artifact_packet_required")
    if (packet.get("source_receipt") != source_receipt or not source_receipt
            or packet.get("expected_sheet_cells") != expected_sheet_cells or not expected_sheet_cells
            or packet.get("producer_version") != producer_version or not producer_version
            or packet.get("order_id") != order_id or not order_id):
        raise ValueError("canonical_artifact_source_changed")
    receipts = packet.get("outputs")
    if (not isinstance(receipts, dict) or not isinstance(outputs, dict) or set(receipts) != set(outputs)
            or any(key not in receipts for key in REQUIRED_OUTPUTS)):
        raise ValueError("canonical_artifact_outputs_missing")
    metadata = {key: value for key, value in packet.items() if key != "metadata"}
    if _verify(packet.get("metadata")) != _json_bytes(metadata):
        raise ValueError("canonical_artifact_metadata_changed")
    verified_inputs = {}
    for key, receipt in receipts.items():
        if not isinstance(receipt, dict) or outputs[key] != receipt.get("uri"):
            raise ValueError("canonical_artifact_reference_changed")
        data = _verify(receipt)
        if key in ("records", "ocr_regions") and not isinstance(json.loads(data), list):
            raise ValueError("canonical_artifact_content_invalid")
        if key == "sheet_values" and not isinstance(json.loads(data), dict):
            raise ValueError("canonical_artifact_content_invalid")
        if key in ("overlay", "overlay_png", "sheet_review_base", "contact_sheet") and not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("canonical_artifact_content_invalid")
        if key == "pdf" and not data.startswith(b"%PDF-"):
            raise ValueError("canonical_artifact_content_invalid")
        if key in ("records", "contact_sheet"):
            verified_inputs[key] = data
        if key == 'preprocess_observation':
            verified_inputs[key] = data
    original_records = json.loads(verified_inputs['records'])
    has_observation = any(isinstance(row,dict) and 'recognizer_preprocess_observation' in row for row in original_records)
    if has_observation != ('preprocess_observation' in verified_inputs):
        raise ValueError('hakodate_observation_artifact_required')
    if has_observation:
        if _analysis is None:
            from src.services.hakodate_preprocess_observation import validate_artifact
            validate_artifact(verified_inputs['preprocess_observation'],original_records,
                verified_inputs['contact_sheet'],source_receipt)
        else:
            _analysis.validate_observation(verified_inputs, original_records, source_receipt)
    return verified_inputs
