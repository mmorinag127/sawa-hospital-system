"""Local artifact/SQL parity; no live-order or latency acceptance claim."""
import ast
from copy import deepcopy
from pathlib import Path
import subprocess
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.db import session_scope
from src.models.order_ocr_evidence_run import OrderOcrEvidenceRun
from src.services import ocr_evidence_service as service
from src.services import order_service, storage_service
from src.services import recognition_acceptance_service as acceptance
from src.services import ocr_recognition_recovery_service as recovery
from test_recognition_review_acceptance import reviewed_order, snapshot  # noqa: F401


BASE = "b46739cbd362aba64e6c4e5bbfd18ff3a50ec17e"
ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def baseline():
    source = subprocess.check_output([
        "git", "show", f"{BASE}:backend/src/services/ocr_evidence_service.py",
    ], cwd=ROOT, text=True)
    tree = ast.parse(source)
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                    and node.name == "evidence_run_selectability_error")
    namespace = dict(vars(service))
    exec(compile(ast.Module(body=[function], type_ignores=[]), "baseline_selectability", "exec"), namespace)
    return namespace[function.name]


def row_snapshot(evidence_id):
    with session_scope() as session:
        row = session.get(OrderOcrEvidenceRun, evidence_id)
        return SimpleNamespace(**{
            column.name: deepcopy(getattr(row, column.name)) for column in row.__table__.columns
        })


def accept_review(reviewed_order):
    identity, _, review, _ = reviewed_order
    result, error = recovery.accept_review(identity, source_receipt=review["source_receipt"],
        values={cell["sheet_cell"]: "" for cell in review["review_cells"]},
        edited_by="single-validation-test", expected_revision=review["source_receipt"]["workflow_revision"])
    assert error is None, (error, result)


def evaluate(function, row):
    with patch.object(order_service, "_validate_hakodate_owned_artifacts",
                      wraps=order_service._validate_hakodate_owned_artifacts) as validation:
        with patch.object(storage_service, "load_bytes_from_uri",
                          wraps=storage_service.load_bytes_from_uri) as reads:
            result = function(row)
    return result, validation.call_count, reads.call_count


@pytest.mark.parametrize("reviewed_order", ["normal", None, "numeric"], indirect=True)
@pytest.mark.parametrize("accepted", [False, True])
def test_real_validation_and_raw_snapshot_parity(reviewed_order, accepted, baseline):
    _, evidence_id, review, engine = reviewed_order
    if accepted and review is not None:
        accept_review(reviewed_order)
    row = row_snapshot(evidence_id)
    before = deepcopy(vars(row)), snapshot(engine)
    old, old_validations, old_reads = evaluate(baseline, row)
    new, new_validations, new_reads = evaluate(service.evidence_run_selectability_error, row)
    assert new == old
    assert new_validations == 1
    if new is None:
        assert old_validations == 2
        assert old_reads == 2 * new_reads and new_reads > 0
    else:
        assert old_validations == 1 and old_reads == new_reads
    assert (vars(row), snapshot(engine)) == before


@pytest.mark.parametrize("reviewed_order", ["normal"], indirect=True)
@pytest.mark.parametrize("change", [
    "manifest", "missing_manifest", "pdf", "job", "receipt", "artifact", "diagnostic", "assignment",
    "artifact_and_manifest", "pdf_and_manifest",
])
def test_real_guard_failures_and_precedence_match_baseline(reviewed_order, baseline, change):
    _, evidence_id, _, engine = reviewed_order
    row = row_snapshot(evidence_id)
    payload = row.payload_json
    if change in {"manifest", "artifact_and_manifest", "pdf_and_manifest"}:
        row.artifact_manifest_json["original_hakodate_source"]["job_id"] = "foreign-manifest-job"
    if change == "missing_manifest":
        row.artifact_manifest_json.pop("original_hakodate_source")
    if change in {"pdf", "pdf_and_manifest"}:
        payload["_ocr_execution"]["selected_pdf_receipt"]["sha256"] = "0" * 64
    if change == "job":
        payload["_ocr_execution"]["job_id"] = "foreign-job"
    if change == "receipt":
        payload["hakodate_ocr_evidence"]["records"][0]["engine_metadata"]["source_receipt"] = {}
        payload["hakodate_ocr_evidence_records"] = deepcopy(payload["hakodate_ocr_evidence"]["records"])
    if change == "diagnostic":
        row.status = "diagnostic_only"
    if change == "assignment":
        payload["_ocr_execution"]["disposition"] = "foreign-disposition"
    path = Path(payload["hakodate_canonical_pipeline"]["outputs"]["records"])
    original_bytes = path.read_bytes()
    try:
        if change in {"artifact", "artifact_and_manifest"}:
            path.write_bytes(b"[]")
        before = deepcopy(vars(row)), snapshot(engine)
        old = evaluate(baseline, row)[0]
        new = evaluate(service.evidence_run_selectability_error, row)[0]
        assert new == old and new is not None
        if change == "artifact_and_manifest":
            assert new == "canonical_artifact_bytes_changed"
        if change == "manifest":
            assert new == "hakodate_artifact_source_mismatch"
        assert (vars(row), snapshot(engine)) == before
    finally:
        path.write_bytes(original_bytes)


@pytest.mark.parametrize("change", ["original_source", "pdf", "actor", "digest"])
def test_acceptance_source_guards_match_baseline(reviewed_order, baseline, change):
    _, evidence_id, _, engine = reviewed_order
    accept_review(reviewed_order)
    row = row_snapshot(evidence_id)
    authored = row.artifact_manifest_json[acceptance.KEY]
    if change == "original_source":
        authored["original_hakodate_source"]["job_id"] = "foreign"
    elif change == "pdf":
        authored["selected_pdf_receipt"]["sha256"] = "0" * 64
    elif change == "actor":
        authored["edited_by"] = ""
    else:
        authored["payload_sha256"] = "0" * 64
    before = deepcopy(vars(row)), snapshot(engine)
    assert service.evidence_run_selectability_error(row) == baseline(row) == "recognition_acceptance_changed"
    assert (vars(row), snapshot(engine)) == before


@pytest.mark.parametrize("reviewed_order", ["normal"], indirect=True)
def test_next_invocation_reloads_changed_artifact(reviewed_order, baseline):
    _, evidence_id, _, engine = reviewed_order
    row = row_snapshot(evidence_id)
    before = deepcopy(vars(row)), snapshot(engine)
    assert service.evidence_run_selectability_error(row) is None
    path = Path(row.payload_json["hakodate_canonical_pipeline"]["outputs"]["records"])
    original_bytes = path.read_bytes()
    try:
        path.write_bytes(b"[]")
        assert service.evidence_run_selectability_error(row) == baseline(row) == "canonical_artifact_bytes_changed"
    finally:
        path.write_bytes(original_bytes)
    assert service.evidence_run_selectability_error(row) is None
    assert (vars(row), snapshot(engine)) == before
