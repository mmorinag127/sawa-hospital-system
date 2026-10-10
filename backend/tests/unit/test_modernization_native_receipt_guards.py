"""One real native run plus explicit original-FAX operator save and guard probes.

The reused scenario performs recognition, strict low-score holds, pre-save
review and actual SQL save. Faults below are restored component injections,
not public operator actions. No native result/threshold/validator doubles;
this is not UI/auth/live proof and does not waive the five native failures.
"""

from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import shutil

import pytest

from test_modernization_native_operator_review import (
    test_real_low_score_hold_then_explicit_original_fax_operator_save as
    run_original_fax_operator_scenario,
)
from test_modernization_owned_numeric_fax import numeric_fax as numeric_fax
from test_modernization_owned_ocr_workflow import ORDER, OTHER
from test_modernization_workflow_surface_parity import database_snapshot
from src import db
from src.models.order_ocr_evidence_run import OrderOcrEvidenceRun
from src.models.order_sheet_draft import OrderSheetDraft
from src.models.order_workflow_state import OrderWorkflowState
from src.services import canonical_ocr_artifact_service as artifacts
from src.services import master_order_form_template_service as master
from src.services import ocr_evidence_service, order_service
from src.services import order_workflow_v2_service as workflow
from src.services.storage_service import load_selected_pdf_bytes


def _assert_saved_original(f, evidence_id, draft_id, original_sheet):
    with db.session_scope() as session:
        state = session.get(OrderWorkflowState, ORDER)
        draft = session.get(OrderSheetDraft, draft_id)
        assert state.draft_id == draft_id
        assert state.evidence_run_id == draft.base_evidence_run_id == evidence_id
        assert draft.template_version_id == state.template_version_id == f.projection_id
        assert draft.draft_sheet_json == original_sheet
        index = original_sheet["fields"].index("qty.regular_x")
        assert [row[index] for row in draft.draft_sheet_json["rows"]] == list(f.expected.values())


def test_native_saved_receipt_guards_without_recognition_regeneration(numeric_fax):
    f, _identity = numeric_fax
    original_expected = deepcopy(f.expected)
    original_receipt = deepcopy(f.receipt)
    original_files = {
        path: sha256(path.read_bytes()).hexdigest()
        for path in f.root.rglob("*")
        if path.is_file() and path.suffix.lower() in {".pdf", ".xlsx", ".json"}
    }
    original_files[master.MASTER_TEMPLATE_PATH] = f.locked_hash

    # This alias calls the unchanged scenario once, including its 56-value,
    # strict confidence-hold, real review/save and immutable-input assertions.
    run_original_fax_operator_scenario(numeric_fax)
    evidence = deepcopy(f.evidence)
    evidence_id = evidence["id"]
    payload = deepcopy(evidence["payload_json"])
    assert evidence["terminal_receipt"]["status"] == "done"
    artifacts.validate_payload(payload)
    packet = payload["hakodate_canonical_pipeline"]["artifact_packet"]
    assert packet["source_receipt"]["selected_pdf_receipt"] == original_receipt
    output_bytes = {
        Path(receipt["uri"]): Path(receipt["uri"]).read_bytes()
        for receipt in [*packet["outputs"].values(), packet["metadata"]]
    }
    for receipt in [*packet["outputs"].values(), packet["metadata"]]:
        data = output_bytes[Path(receipt["uri"])]
        assert len(data) == receipt["size_bytes"]
        assert sha256(data).hexdigest() == receipt["sha256"]
    with db.session_scope() as session:
        state = session.get(OrderWorkflowState, ORDER)
        draft_id = state.draft_id
        original_sheet = deepcopy(session.get(OrderSheetDraft, draft_id).draft_sheet_json)
        assert session.get(OrderOcrEvidenceRun, evidence_id).payload_json == payload
    baseline = database_snapshot()
    _assert_saved_original(f, evidence_id, draft_id, original_sheet)
    baseline_captured = order_service._capture_synchronous_cache_write_input(ORDER)
    assert baseline_captured is not None
    order_service._validate_selected_hakodate_artifacts(evidence, baseline_captured)
    assert database_snapshot() == baseline

    foreign, error = workflow.get_workflow_readonly(OTHER)
    assert error is None, (error, foreign)
    rejected, error = workflow.select_ocr_result(
        OTHER, evidence_id, expected_revision=foreign["revision"],
    )
    assert rejected is None
    assert error == "ocr_result_not_found"
    assert database_snapshot() == baseline

    records_uri = Path(payload["hakodate_canonical_pipeline"]["outputs"]["records"])
    for fault, blocker in (
        ("missing_packet", "canonical_artifact_packet_required"),
        ("records_changed", "canonical_artifact_bytes_changed"),
        ("records_deleted", "canonical_artifact_bytes_unavailable"),
        ("source_changed", "canonical_artifact_source_changed"),
    ):
        changed = deepcopy(payload)
        pipeline = changed["hakodate_canonical_pipeline"]
        try:
            if fault == "missing_packet":
                pipeline.pop("artifact_packet")
            elif fault == "records_changed":
                records_uri.write_bytes(b"[]")
            elif fault == "records_deleted":
                records_uri.unlink()
            else:
                source = pipeline["artifact_packet"]["source_receipt"]
                digest = source["configured_input_digest"]
                source["configured_input_digest"] = ("1" if digest[0] != "1" else "0") + digest[1:]
            # Only the owned evidence payload is deliberately corrupted. The
            # fault-state snapshot distinguishes injection from consumer writes.
            with db.session_scope() as session:
                session.get(OrderOcrEvidenceRun, evidence_id).payload_json = deepcopy(changed)
            fault_snapshot = database_snapshot()
            assessment = ocr_evidence_service.classify_evidence_payload(changed)
            assert assessment["persistable"] is False, (fault, assessment)
            assert blocker in assessment["message"], (fault, assessment)
            with db.session_scope() as session:
                assert ocr_evidence_service.evidence_run_is_selectable(
                    session.get(OrderOcrEvidenceRun, evidence_id),
                ) is False, fault
            with pytest.raises(ValueError, match=f"^{blocker}$"):
                artifacts.validate_payload(changed)
            with pytest.raises(ocr_evidence_service.SelectedEvidenceRejected, match=f"^{blocker}$"):
                order_service._capture_synchronous_cache_write_input(ORDER)
            # Independent validator component input, not a consumer fallback
            # continuing from the rejected capture of the corrupted payload.
            changed_serialized_evidence = deepcopy(evidence)
            changed_serialized_evidence["payload_json"] = deepcopy(changed)
            with pytest.raises(ValueError, match=f"^{blocker}$"):
                order_service._validate_selected_hakodate_artifacts(
                    changed_serialized_evidence, baseline_captured,
                )
            rejected, error = workflow.build_sheet_from_selected_ocr(ORDER)
            assert rejected is None, (fault, rejected)
            assert error == blocker, (fault, error)
            assert database_snapshot() == fault_snapshot
            _assert_saved_original(f, evidence_id, draft_id, original_sheet)
        finally:
            records_uri.write_bytes(output_bytes[records_uri])
            with db.session_scope() as session:
                session.get(OrderOcrEvidenceRun, evidence_id).payload_json = deepcopy(payload)
        assert database_snapshot() == baseline
        artifacts.validate_payload(payload)
        with db.session_scope() as session:
            assert ocr_evidence_service.evidence_run_is_selectable(
                session.get(OrderOcrEvidenceRun, evidence_id),
            ) is True
        assert all(path.read_bytes() == data for path, data in output_bytes.items())
        assert f.expected == original_expected
        assert all(sha256(path.read_bytes()).hexdigest() == digest
                   for path, digest in original_files.items())

    # A real document producer changes the current source identity, not bytes.
    # This transaction is intentionally stateful, unlike the following reads.
    successor = f.root / "native-guard-successor.pdf"
    shutil.copyfile(f.pdf, successor)
    with db.session_scope() as session:
        f.add_document(session, "DOC-NATIVE-GUARD-SUCCESSOR", "PDF-NATIVE-GUARD-SUCCESSOR",
                       successor, message_id="UNIT-NATIVE-GUARD-SUCCESSOR")
    assert f.receipt != original_receipt
    assert f.receipt["sha256"] == original_receipt["sha256"]
    after_document_switch = database_snapshot()
    assert after_document_switch != baseline
    captured = order_service._capture_synchronous_cache_write_input(ORDER)
    assert captured is not None
    with pytest.raises(ValueError, match="^hakodate_artifact_source_mismatch$"):
        order_service._validate_selected_hakodate_artifacts(captured["evidence"], captured)
    rejected, error = workflow.build_sheet_from_selected_ocr(ORDER)
    assert rejected is None
    assert error == "hakodate_artifact_source_mismatch"
    assert database_snapshot() == after_document_switch
    _assert_saved_original(f, evidence_id, draft_id, original_sheet)
    with db.session_scope() as session:
        assert session.get(OrderOcrEvidenceRun, evidence_id).payload_json == payload
    assert load_selected_pdf_bytes(str(f.pdf), original_receipt) == f.pdf.read_bytes()
    assert all(path.read_bytes() == data for path, data in output_bytes.items())
    assert all(sha256(path.read_bytes()).hexdigest() == digest
               for path, digest in original_files.items())
    assert f.expected == original_expected
    artifacts.validate_payload(payload)
