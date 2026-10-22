"""Ownership parity using real local validators, not live latency acceptance."""
import ast
from contextlib import ExitStack, contextmanager
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from PIL import Image

from src.services import canonical_ocr_artifact_service as artifacts
from src.services import hakodate_ocr_evidence_service as evidence
from src.services import order_service, order_workflow_v2_service as workflow, storage_service
from src.services import ocr_evidence_service as service
from src.services import recognition_acceptance_service as acceptance
from src.services.hakodate_recognizer_input_evidence import contact_crop_evidence
from test_h2_operator_repair_revision import _owned_artifact_fixture
from test_modernization_canonical_ocr_artifacts import authored_outputs

ROOT = Path(__file__).resolve().parents[3]


@contextmanager
def owned_acceptance_projection():
    real = acceptance._payload_for_evidence_with_accepted_values

    def owned(item, **_options):
        return real(item, copy_payload=True)

    with patch.object(acceptance, "_payload_for_evidence_with_accepted_values", side_effect=owned) as observed:
        yield observed
        assert observed.call_count > 0


def evaluate(function, row, *, owned_projection=False):
    trace = []
    full_copies = []
    watched = [
        (acceptance, "accepted_values"),
        (order_service, "_original_hakodate_execution_source"),
        (order_service, "_hakodate_owned_source"),
        (order_service, "_validate_hakodate_original_execution"),
        (order_service, "_validate_hakodate_owned_artifacts"),
        (artifacts, "validate_payload"), (artifacts, "validate_packet"),
        (storage_service, "load_bytes_from_uri"),
        (order_service, "_extract_hakodate_target_cells_from_payload"),
        (order_service, "_extract_hakodate_ocr_evidence_records_from_payload"),
        (evidence, "assign_evidence_to_target_cells"),
    ]
    def spy(name, real):
        def call(*args, **kwargs):
            trace.append(name)
            return real(*args, **kwargs)
        return call
    real_copy = acceptance.deepcopy
    def counted_copy(value):
        if value is row.payload_json:
            full_copies.append(1)
        return real_copy(value)
    with ExitStack() as stack:
        for module, name in watched:
            stack.enter_context(patch.object(module, name, side_effect=spy(name, getattr(module, name))))
        stack.enter_context(patch.object(acceptance, "deepcopy", side_effect=counted_copy))
        if owned_projection:
            stack.enter_context(owned_acceptance_projection())
        result = function(row)
    return result, trace, len(full_copies)


@pytest.fixture(autouse=True)
def no_external_io(monkeypatch):
    from sqlalchemy.engine import Engine
    import socket
    with patch.object(Engine, "connect", side_effect=AssertionError("database forbidden")), \
         patch.object(socket.socket, "connect", side_effect=AssertionError("network forbidden")):
        monkeypatch.delenv("OCR_OUTPUT_BUCKET", raising=False)
        yield


@pytest.fixture
def packet_row(tmp_path, monkeypatch):
    monkeypatch.setenv("OCR_ARTIFACT_DIR", str(tmp_path / "published"))
    def build(mode="ready"):
        config = {"facility": {"facility_id": "synthetic", "facility_name": "Synthetic ownership fixture",
            "fax_template": {"page": 1, "columns": [
                {"column_id": "date", "index": 0, "role": "date"},
                {"column_id": "daypart", "index": 1, "role": "daypart"},
                {"column_id": "menu", "index": 2, "role": "menu"},
                {"column_id": "quantity", "index": 3, "role": "quantity", "diet_type": "regular", "area_id": "x"},
            ]}}}
        outputs = authored_outputs(tmp_path / mode)
        pdf = Path(outputs["pdf"])
        selected = {"order_id": "ownership-order", "storage_uri": str(pdf), "sha256": sha256(pdf.read_bytes()).hexdigest()}
        start = {"configured_input": config, "physical_menu_entries": [{"menu_date": "2026-10-07",
            "daypart": "lunch", "slot_index": 0, "name": "Synthetic menu"}],
            "order": {"week_code": "2026-10@2026-10-05~2026-10-11"}, "evidence_run_id": "ownership-evidence"}
        execution = {"job_id": "ownership-job", "workflow_attempt_id": "ownership-attempt",
            "workflow_revision": 1, "evidence_run_id": "ownership-evidence", "selected_pdf_receipt": selected,
            "processing_start_input": start,
            "disposition": "rejected" if mode in {"review", "accepted", "diagnostic"} else "pipeline_result"}
        payload = {"_ocr_execution": execution}
        source = order_service._original_hakodate_execution_source(payload)
        review = mode in {"review", "accepted"}
        raw = "-" if review else "7"
        targets, records = _owned_artifact_fixture(source, raw)
        with Image.open(outputs["contact_sheet"]) as image:
            crop = contact_crop_evidence(image, [[0, 0], [24, 0], [24, 32], [0, 32]])
        originals = []
        for record in records:
            original = record["engine_metadata"]["recognizer_output"]
            original.update(recognizer_raw_text=raw, recognizer_input_evidence=crop,
                ocr_contact_crop_box=crop["bbox"], resolution_state="review_required" if review else "resolved",
                resolved_value=None if review else raw, ocr_text=None if review else raw,
                ocr_normalized=None if review else raw, pred_digits=None if review else raw)
            if review:
                original["quantity_source_issue"] = "hakodate_ocr_quantity_non_numeric"
            record.update(normalized_value=None if review else raw,
                resolution_state="review_required" if review else "resolved")
            if mode == "operatorEdited":
                original.update(recognition_source="operator", operator_value=raw)
                record.update(engine="operator", source_scope="operator_cell_review")
                record["engine_metadata"].update(source="operator", edited_by="operator",
                    sheet_cell=original["sheet_cell"], operator_output=deepcopy(original))
            originals.append(original)
        for key in ("records", "ocr_regions"):
            Path(outputs[key]).write_text(json.dumps(originals))
        packet = artifacts.persist_outputs(order_id="ownership-order", outputs=outputs, source_receipt=source,
            expected_sheet_cells=source["expected_sheet_cells"], producer_version=order_service.HAKODATE_CANONICAL_PIPELINE_VERSION,
            workspace=tmp_path / mode)
        uris = {key: entry["uri"] for key, entry in packet["outputs"].items()}
        for record in records:
            record["raw_payload_ref"] = uris["records"]
        payload.update(hakodate_repair_source_receipt=source,
            hakodate_canonical_pipeline={"status": "blocked" if review else "ready",
                "version": order_service.HAKODATE_CANONICAL_PIPELINE_VERSION,
                "expected_sheet_cells": source["expected_sheet_cells"], "outputs": uris,
                "artifact_packet": packet, "requested_order_id": "ownership-order",
                "blockers": [f"hakodate_ocr_quantity_unresolved: {r['evidence_id']}" for r in records] if review else []},
            hakodate_preprocessing={"target_cell_map": targets}, hakodate_ocr_evidence={"records": records},
            hakodate_ocr_evidence_records=deepcopy(records), hakodate_recognition_review=evidence.recognition_review(records),
            hakodate_overlay={"uri": uris["overlay"], "sheet_review_base_uri": uris["sheet_review_base"]})
        manifest = {"original_hakodate_source": deepcopy(source)}
        digest = acceptance.payload_digest(payload)
        if mode == "accepted":
            authored = acceptance.acknowledge(payload,
                values={cell["sheet_cell"]: "" for cell in payload["hakodate_recognition_review"]["cells"]},
                edited_by="operator", created_at="2026-10-19T00:00:00", source_receipt={
                    "workflow_revision": 1, "selected_pdf_receipt": selected, "recognition_evidence_run_id": "ownership-evidence"})
            manifest[acceptance.KEY] = {**authored[acceptance.KEY], "artifact_digest": digest,
                "original_hakodate_source": deepcopy(source)}
        return SimpleNamespace(id="ownership-evidence", payload_json=payload, artifact_manifest_json=manifest,
            artifact_digest=digest, status="diagnostic_only" if mode in {"diagnostic", "review", "accepted"} else "ready",
            source="synthetic", producer_version="synthetic", order_id="ownership-order",
            template_version_id="synthetic-template", schema_version="synthetic", created_at=None)
    return build


def test_default_deep_isolation_and_borrowed_top_level():
    row = {"payload_json": {"nested": {"values": ["raw"]}}}
    owned = acceptance.payload_for_evidence(row)
    borrowed = acceptance.payload_for_evidence(row, copy_payload=False)
    owned["nested"]["values"].append("edit")
    borrowed["new"] = True
    assert row == {"payload_json": {"nested": {"values": ["raw"]}}}
    assert borrowed is not row["payload_json"]
    assert borrowed["nested"] is row["payload_json"]["nested"]


@pytest.mark.parametrize("value", [None, [], ["raw"], "raw", 1, {acceptance.KEY: {}}, {"nested": []}])
def test_malformed_behavior_matches_default(value):
    row = {"payload_json": value}
    def outcome(function):
        try:
            return ("result", function(row))
        except Exception as exc:
            return (type(exc).__name__, str(exc))
    assert outcome(acceptance.payload_for_evidence) == outcome(lambda item: acceptance.payload_for_evidence(item, copy_payload=False))


def test_dict_subclass_keeps_deepcopy():
    class Payload(dict):
        pass
    original = Payload(nested=["raw"])
    result = acceptance.payload_for_evidence({"payload_json": original}, copy_payload=False)
    assert type(result) is Payload
    result["nested"].append("edit")
    assert original["nested"] == ["raw"]


def test_only_selectability_borrows_and_other_seven_calls_are_unchanged():
    calls = []
    helper_calls = []
    for path in (ROOT / "backend/src").rglob("*.py"):
        source = path.read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and ((isinstance(node.func, ast.Name) and node.func.id == "payload_for_evidence")
                    or (isinstance(node.func, ast.Attribute) and node.func.attr == "payload_for_evidence")):
                calls.append((path, node))
            if isinstance(node, ast.Call) and (
                    (isinstance(node.func, ast.Name) and node.func.id == "_payload_for_evidence_with_accepted_values")
                    or (isinstance(node.func, ast.Attribute) and node.func.attr == "_payload_for_evidence_with_accepted_values")):
                helper_calls.append((path, node))
    assert len(calls) == 7
    assert all(not node.keywords for _, node in calls)
    assert len(helper_calls) == 2
    wrapper_calls = [(path, node) for path, node in helper_calls
                     if path.name == "recognition_acceptance_service.py"]
    assert len(wrapper_calls) == 1
    wrapper_path, wrapper_call = wrapper_calls[0]
    wrapper_tree = ast.parse(wrapper_path.read_text())
    wrapper = next(item for item in wrapper_tree.body if isinstance(item, ast.FunctionDef)
                   and item.name == "payload_for_evidence")
    assert wrapper.lineno <= wrapper_call.lineno <= wrapper.end_lineno
    assert len(wrapper_call.args) == 1 and isinstance(wrapper_call.args[0], ast.Name)
    assert wrapper_call.args[0].id == "row"
    assert len(wrapper_call.keywords) == 1
    forwarded = wrapper_call.keywords[0]
    assert forwarded.arg == "copy_payload"
    assert isinstance(forwarded.value, ast.Name) and forwarded.value.id == "copy_payload"
    borrowers = [(path, node) for path, node in helper_calls if path != wrapper_path]
    assert len(borrowers) == 1
    path, node = borrowers[0]
    assert path.name == "ocr_evidence_service.py"
    assert [(keyword.arg, ast.literal_eval(keyword.value)) for keyword in node.keywords] == [("copy_payload", False)]
    tree = ast.parse(path.read_text())
    function = next(item for item in tree.body if isinstance(item, ast.FunctionDef)
                    and item.name == "evidence_run_selectability_error")
    assert function.lineno <= node.lineno <= function.end_lineno
    assert {name: sum(path.name == name for path, _ in calls) for name in
            ("ocr_evidence_service.py", "order_service.py", "order_workflow_v2_service.py")} == {
                "ocr_evidence_service.py": 1, "order_service.py": 4, "order_workflow_v2_service.py": 2}
    assert sum(not node.keywords for _, node in calls) == 7


@pytest.mark.parametrize("mode", ["ready", "review", "accepted", "diagnostic", "operatorEdited"])
def test_real_guards_decisions_order_and_immutability(packet_row, mode):
    row = packet_row(mode)
    before = deepcopy(vars(row))
    old, old_trace, old_copies = evaluate(service.evidence_run_selectability_error, row, owned_projection=True)
    new, new_trace, new_copies = evaluate(service.evidence_run_selectability_error, row)
    assert new == old
    assert new_trace == old_trace
    assert old_copies == 1 and new_copies == 0
    assert new_trace.count("_validate_hakodate_original_execution") == 1
    assert "validate_packet" in new_trace
    assert "assign_evidence_to_target_cells" in new_trace
    assert new is None if mode in {"ready", "review", "accepted", "operatorEdited"} else new is not None
    assert vars(row) == before


@pytest.mark.parametrize("mode", ["ready", "review", "accepted", "diagnostic", "operatorEdited"])
def test_local_response_fields_and_selection_are_unchanged(packet_row, mode):
    row = packet_row(mode)
    before = deepcopy(vars(row))
    for selected in (False, True):
        with owned_acceptance_projection():
            expected = workflow._serialize_ocr_result(row, selected=selected)
        assert workflow._serialize_ocr_result(row, selected=selected) == expected
        assert expected["selected"] is selected
    assert vars(row) == before


def test_acceptance_sidecar_is_always_detached(packet_row):
    row = packet_row("accepted")
    before = deepcopy(vars(row))
    for options in ({}, {"copy_payload": False}):
        projected = acceptance.payload_for_evidence(row, **options)
        projected[acceptance.KEY]["values"]["changed"] = "9"
        projected[acceptance.KEY]["cells"].append({"changed": True})
    assert vars(row) == before


@pytest.mark.parametrize("fault", ["source", "manifest", "acceptance", "pdf", "job", "receipt", "artifact",
    "source_and_manifest", "artifact_and_manifest", "pdf_and_manifest", "acceptance_and_source"])
def test_failures_and_multifailure_precedence(packet_row, fault):
    row = packet_row("accepted" if "acceptance" in fault else "ready")
    payload = row.payload_json
    if "manifest" in fault:
        row.artifact_manifest_json["original_hakodate_source"]["job_id"] = "foreign"
    if "source" in fault:
        payload["hakodate_repair_source_receipt"]["job_id"] = "foreign"
    if "acceptance" in fault:
        row.artifact_manifest_json[acceptance.KEY]["edited_by"] = ""
    if "pdf" in fault:
        payload["_ocr_execution"]["selected_pdf_receipt"]["sha256"] = "0" * 64
    if fault == "job":
        payload["_ocr_execution"]["job_id"] = "foreign"
    if fault == "receipt":
        payload["hakodate_ocr_evidence"]["records"][0]["engine_metadata"]["source_receipt"] = {}
        payload["hakodate_ocr_evidence_records"] = deepcopy(payload["hakodate_ocr_evidence"]["records"])
    if "artifact" in fault:
        Path(payload["hakodate_canonical_pipeline"]["outputs"]["records"]).write_bytes(b"[]")
    before = deepcopy(vars(row))
    old = evaluate(service.evidence_run_selectability_error, row, owned_projection=True)
    new = evaluate(service.evidence_run_selectability_error, row)
    assert new[:2] == old[:2] and new[0] is not None
    expected = ("recognition_acceptance_changed" if "acceptance" in fault else
                "canonical_artifact_bytes_changed" if "artifact" in fault else
                "hakodate_artifact_source_mismatch")
    assert new[0] == expected
    assert vars(row) == before


def test_extracted_records_and_assignment_do_not_alias_raw(packet_row):
    row = packet_row()
    before = deepcopy(vars(row))
    payload = acceptance.payload_for_evidence(row, copy_payload=False)
    records = service._extract_hakodate_evidence_records(payload)
    targets = service._extract_hakodate_target_cells(payload)
    assigned = evidence.assign_evidence_to_target_cells(evidence_records=records, target_cells=targets)
    assert assigned["assignments"]
    records[0]["engine_metadata"]["recognizer_output"]["raw_text"] = "999"
    targets[0]["metadata"]["changed"] = True
    assigned["assignments"][0]["changed"] = True
    assert vars(row) == before


def test_changed_artifact_is_revalidated_next_request(packet_row):
    row = packet_row()
    before = deepcopy(vars(row))
    assert service.evidence_run_selectability_error(row) is None
    path = Path(row.payload_json["hakodate_canonical_pipeline"]["outputs"]["records"])
    original = path.read_bytes()
    path.write_bytes(b"[]")
    old = evaluate(service.evidence_run_selectability_error, row, owned_projection=True)
    new = evaluate(service.evidence_run_selectability_error, row)
    assert new[:2] == old[:2] and new[0] == "canonical_artifact_bytes_changed"
    path.write_bytes(original)
    assert service.evidence_run_selectability_error(row) is None
    assert vars(row) == before
