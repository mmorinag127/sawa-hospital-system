"""Reuse only the source just validated; preserve independent fresh validation."""
import ast
from contextlib import ExitStack
from copy import deepcopy
from pathlib import Path
import subprocess
from unittest.mock import patch

import pytest

from src.services import order_service as orders
from src.services import ocr_evidence_service as evidence
from src.services import canonical_ocr_artifact_service as artifacts
from src.services import recognition_acceptance_service as acceptance
from src.services import storage_service
from test_ocr_read_projection_ownership import no_external_io, packet_row  # noqa: F401

BASE = "6e2d0856088ffff99966c6d97e4a46037b4ac5ea"


@pytest.fixture(scope="module")
def baseline():
    root = Path(__file__).resolve().parents[3]
    source = subprocess.check_output(
        ["git", "show", f"{BASE}:backend/src/services/ocr_evidence_service.py"],
        cwd=root, text=True)
    node = next(node for node in ast.parse(source).body if isinstance(node, ast.FunctionDef)
                and node.name == "evidence_run_selectability_error")
    namespace = dict(vars(evidence))
    exec(compile(ast.Module(body=[node], type_ignores=[]), "base_selectability", "exec"), namespace)
    return namespace[node.name]


def evaluate(function, row, analysis_enabled):
    trace = []
    def spy(name, real):
        def call(*args, **kwargs):
            trace.append(name)
            return real(*args, **kwargs)
        return call
    with ExitStack() as stack:
        for module, name in [
            (orders, "_original_hakodate_execution_source"),
            (orders, "_validate_hakodate_owned_artifacts"),
            (artifacts, "validate_payload"),
            (storage_service, "load_bytes_from_uri"),
        ]:
            stack.enter_context(patch.object(module, name, spy(name, getattr(module, name))))
        result = function(row, _analysis=artifacts._EvidenceAnalysis() if analysis_enabled else None)
    return result, trace


@pytest.mark.parametrize("analysis_enabled", [False, True])
@pytest.mark.parametrize("case", ["accepted", "ready", "source_mismatch", "artifact_and_source", "invalid_execution"])
def test_result_error_order_fresh_reads_and_input_parity(packet_row, baseline, analysis_enabled, case):
    row = packet_row("ready" if case == "ready" else "accepted")
    if case in ("source_mismatch", "artifact_and_source"):
        row.artifact_manifest_json[acceptance.KEY]["original_hakodate_source"]["job_id"] = "foreign"
    if case == "artifact_and_source":
        Path(row.payload_json["hakodate_canonical_pipeline"]["outputs"]["records"]).write_bytes(b"[]")
    if case == "invalid_execution":
        row.payload_json["_ocr_execution"]["processing_start_input"] = {}
        row.artifact_manifest_json[acceptance.KEY]["payload_sha256"] = acceptance.payload_digest(row.payload_json)
    before = deepcopy(vars(row))
    old, old_trace = evaluate(baseline, row, analysis_enabled)
    new, new_trace = evaluate(evidence.evidence_run_selectability_error, row, analysis_enabled)
    assert new == old
    assert vars(row) == before
    source = "_original_hakodate_execution_source"
    assert new_trace.count(source) == 1
    assert old_trace.count(source) == (2 if case in ("accepted", "source_mismatch") else 1)
    assert [x for x in new_trace if x != source] == [x for x in old_trace if x != source]
    assert new == {
        "accepted": None, "ready": None, "source_mismatch": "recognition_acceptance_changed",
        "artifact_and_source": "canonical_artifact_bytes_changed",
        "invalid_execution": "hakodate_original_expectation_required",
    }[case]
    if case in ("accepted", "ready", "source_mismatch"):
        assert new_trace.count("load_bytes_from_uri") > 0


def test_success_returns_actual_validated_source_and_next_call_is_fresh(packet_row):
    row = packet_row("accepted")
    payload = acceptance.payload_for_evidence(row)
    before = deepcopy(payload)
    real = orders._validate_hakodate_owned_artifacts
    seen = []
    def owned(payload, expected, **kwargs):
        seen.append(expected)
        return real(payload, expected, **kwargs)
    with patch.object(orders, "_validate_hakodate_owned_artifacts", owned), \
         patch.object(storage_service, "load_bytes_from_uri", wraps=storage_service.load_bytes_from_uri) as reads:
        first = orders._validate_hakodate_original_execution(payload)
        count = reads.call_count
        second = orders._validate_hakodate_original_execution(payload)
        assert reads.call_count == count * 2 and count > 0
    assert first is seen[0] and second is seen[1]
    assert first == second and first is not second
    first["selected_pdf_receipt"]["sha256"] = "changed"
    assert payload == before and second == seen[1]
    path = Path(payload["hakodate_canonical_pipeline"]["outputs"]["records"])
    path.write_bytes(b"[]")
    with pytest.raises(ValueError, match="canonical_artifact_bytes_changed"):
        orders._validate_hakodate_original_execution(payload)
