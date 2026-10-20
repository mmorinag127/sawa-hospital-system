"""Base-SHA parity with local artifacts; no live latency claim."""
import ast
from contextlib import ExitStack, contextmanager
from copy import deepcopy
from pathlib import Path
import subprocess
from types import FunctionType
from unittest.mock import patch

import pytest

from src.services import canonical_ocr_artifact_service as artifacts
from src.services import ocr_evidence_service as evidence
from src.services import order_service as orders
from src.services import recognition_acceptance_service as acceptance
from src.services import storage_service
from test_ocr_read_projection_ownership import no_external_io, packet_row  # noqa: F401


BASE = "be6675e209d249d0bda862601ea0abcfa65175fc"
ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def old_functions():
    result = []
    for module, names in (
        (acceptance, ["payload_for_evidence"]),
        (evidence, ["evidence_run_selectability_error"]),
        (orders, ["_validate_hakodate_original_execution", "_validate_hakodate_owned_artifacts"]),
    ):
        source = subprocess.check_output(
            ["git", "show", f"{BASE}:backend/src/services/{module.__name__.split('.')[-1]}.py"],
            cwd=ROOT, text=True)
        tree = ast.parse(source)
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name in names:
                namespace = dict(vars(module))
                exec(compile(ast.Module(body=[node], type_ignores=[]), f"base:{node.name}", "exec"), namespace)
                template = namespace[node.name]
                function = FunctionType(template.__code__, vars(module), node.name, template.__defaults__)
                function.__kwdefaults__ = template.__kwdefaults__
                result.append((module, node.name, function))
    return result


@contextmanager
def baseline(old_functions):
    with ExitStack() as stack:
        for module, name, function in old_functions:
            stack.enter_context(patch.object(module, name, function))
        yield


def outcome(call):
    try:
        return ("value", call())
    except Exception as exc:
        return ("error", type(exc), str(exc))


def evaluate(row, analysis_enabled):
    trace = []
    watched = [
        (acceptance, "accepted_values"),
        (orders, "_original_hakodate_execution_source"),
        (orders, "_validate_hakodate_owned_artifacts"),
        (artifacts, "validate_payload"),
        (storage_service, "load_bytes_from_uri"),
    ]

    def spy(name, real):
        def call(*args, **kwargs):
            trace.append(name)
            return real(*args, **kwargs)
        return call

    with ExitStack() as stack:
        for module, name in watched:
            stack.enter_context(patch.object(module, name, spy(name, getattr(module, name))))
        result = outcome(lambda: evidence.evidence_run_selectability_error(
            row, _analysis=artifacts._EvidenceAnalysis() if analysis_enabled else None))
    return result, trace


@pytest.mark.parametrize("analysis_enabled", [False, True])
@pytest.mark.parametrize("mode", ["ready", "accepted", "review", "diagnostic", "operatorEdited"])
def test_base_parity_counts_reads_and_no_mutation(packet_row, old_functions, mode, analysis_enabled):
    row = packet_row(mode)
    before = deepcopy(vars(row))
    with baseline(old_functions):
        old, old_trace = evaluate(row, analysis_enabled)
    new, new_trace = evaluate(row, analysis_enabled)
    assert new == old
    assert vars(row) == before
    assert [x for x in new_trace if x != "accepted_values"] == [
        x for x in old_trace if x != "accepted_values"]
    assert new_trace.count("accepted_values") == 1
    assert old_trace.count("accepted_values") == (3 if mode == "accepted" else 2)
    assert new_trace.index("_original_hakodate_execution_source") < new_trace.index(
        "_validate_hakodate_owned_artifacts")
    if mode in ("ready", "accepted", "operatorEdited"):
        assert new == ("value", None)
        assert new_trace.count("load_bytes_from_uri") > 0
    else:
        assert new[1] is not None


@pytest.mark.parametrize("analysis_enabled", [False, True])
@pytest.mark.parametrize("fault", [
    "values", "digest", "actor_and_values", "digest_and_source", "values_and_source",
    "source_and_owned", "artifact_and_manifest",
])
def test_invalid_and_error_priority(packet_row, old_functions, fault, analysis_enabled):
    row = packet_row("accepted")
    authored = row.artifact_manifest_json[acceptance.KEY]
    if "values" in fault:
        authored["values"] = {}
    if "actor" in fault:
        authored["edited_by"] = ""
    if "digest" in fault:
        authored["payload_sha256"] = "0" * 64
    if "source" in fault:
        row.payload_json["_ocr_execution"]["processing_start_input"] = {}
        if "digest" not in fault:
            authored["payload_sha256"] = acceptance.payload_digest(row.payload_json)
    if "owned" in fault:
        row.payload_json["hakodate_canonical_pipeline"]["version"] = "invalid"
        authored["payload_sha256"] = acceptance.payload_digest(row.payload_json)
    if "artifact" in fault:
        Path(row.payload_json["hakodate_canonical_pipeline"]["outputs"]["records"]).write_bytes(b"[]")
        authored["original_hakodate_source"]["job_id"] = "foreign"
    before = deepcopy(vars(row))
    with baseline(old_functions):
        old, old_trace = evaluate(row, analysis_enabled)
    new, new_trace = evaluate(row, analysis_enabled)
    assert new == old
    assert new != ("value", None)
    assert vars(row) == before
    assert [x for x in new_trace if x != "accepted_values"] == [
        x for x in old_trace if x != "accepted_values"]
    expected = {
        "values": "recognition_acceptance_explicit_values_required",
        "digest": "recognition_acceptance_changed",
        "actor_and_values": "recognition_acceptance_changed",
        "digest_and_source": "recognition_acceptance_changed",
        "values_and_source": "recognition_acceptance_explicit_values_required",
        "source_and_owned": "hakodate_original_expectation_required",
        "artifact_and_manifest": "canonical_artifact_bytes_changed",
    }
    assert new == ("value", expected[fault])


@pytest.mark.parametrize("payload", [None, [], ["raw"], "raw", 1, {acceptance.KEY: {}}, {}])
def test_malformed_public_and_selectability_parity(packet_row, old_functions, payload):
    row = packet_row("ready")
    row.payload_json = deepcopy(payload)
    before = deepcopy(vars(row))
    with baseline(old_functions):
        old_public = outcome(lambda: acceptance.payload_for_evidence(row))
        old_select = outcome(lambda: evidence.evidence_run_selectability_error(row))
    assert outcome(lambda: acceptance.payload_for_evidence(row)) == old_public
    assert outcome(lambda: evidence.evidence_run_selectability_error(row)) == old_select
    assert vars(row) == before


@pytest.mark.parametrize("analysis_enabled", [False, True])
def test_independent_calls_revalidate_and_reload(packet_row, analysis_enabled):
    row = packet_row("accepted")
    first, first_trace = evaluate(row, analysis_enabled)
    second, second_trace = evaluate(row, analysis_enabled)
    assert first == second == ("value", None)
    assert first_trace == second_trace
    assert first_trace.count("accepted_values") == 1
    assert first_trace.count("load_bytes_from_uri") > 0
    path = Path(row.payload_json["hakodate_canonical_pipeline"]["outputs"]["records"])
    original = path.read_bytes()
    path.write_bytes(b"[]")
    assert evaluate(row, analysis_enabled)[0] == ("value", "canonical_artifact_bytes_changed")
    path.write_bytes(original)
    row.artifact_manifest_json[acceptance.KEY]["values"] = {}
    assert evaluate(row, analysis_enabled)[0] == ("value", "recognition_acceptance_explicit_values_required")


@pytest.mark.parametrize("analysis_enabled", [False, True])
@pytest.mark.parametrize("entry", ["owned", "original"])
def test_direct_call_keeps_validation_and_priority(packet_row, old_functions, analysis_enabled, entry):
    row = packet_row("accepted")
    payload = acceptance.payload_for_evidence(row)
    source = deepcopy(payload["hakodate_repair_source_receipt"])
    payload[acceptance.KEY]["payload_sha256"] = "invalid"
    payload["_ocr_execution"]["processing_start_input"] = {}
    before = deepcopy(payload)

    def run():
        options = {"_analysis": artifacts._EvidenceAnalysis() if analysis_enabled else None}
        if entry == "owned":
            return orders._validate_hakodate_owned_artifacts(payload, source, **options)
        return orders._validate_hakodate_original_execution(payload, **options)

    with baseline(old_functions):
        old = outcome(run)
    assert outcome(run) == old
    assert old == ("error", ValueError, "recognition_acceptance_changed" if entry == "owned"
                   else "hakodate_original_expectation_required")
    assert payload == before


def test_public_projection_validation_and_independent_returns(packet_row):
    row = packet_row("accepted")
    before = deepcopy(vars(row))
    with patch.object(acceptance, "accepted_values", wraps=acceptance.accepted_values) as validate:
        first = acceptance.payload_for_evidence(row)
        second = acceptance.payload_for_evidence(row)
    assert validate.call_count == 2
    first[acceptance.KEY]["values"].clear()
    first["hakodate_ocr_evidence"]["records"].clear()
    assert second == acceptance.payload_for_evidence(row)
    assert vars(row) == before
    row.artifact_manifest_json[acceptance.KEY]["payload_sha256"] = "invalid"
    with pytest.raises(ValueError, match="recognition_acceptance_changed"):
        acceptance.payload_for_evidence(row)


def test_validated_empty_result_is_not_unprovided(packet_row):
    row = packet_row("ready")
    payload = acceptance.payload_for_evidence(row)
    before = deepcopy(payload)
    with patch.object(acceptance, "accepted_values", wraps=acceptance.accepted_values) as validate:
        orders._validate_hakodate_original_execution(payload, _accepted_values={})
        assert validate.call_count == 0
        orders._validate_hakodate_original_execution(payload)
        assert validate.call_count == 1
    assert payload == before


@pytest.mark.parametrize("analysis_enabled", [False, True])
def test_handoff_and_real_source_callee_chain_are_read_only(packet_row, analysis_enabled):
    row = packet_row("accepted")
    real_project = acceptance._payload_for_evidence_with_accepted_values
    real_source = orders._original_hakodate_execution_source
    real_owned = orders._validate_hakodate_owned_artifacts
    handoff = {}
    boundaries = []

    def project(*args, **kwargs):
        payload, accepted = real_project(*args, **kwargs)
        handoff.update(payload=payload, accepted=accepted, before=deepcopy((payload, accepted)))
        return payload, accepted

    def unchanged(payload):
        assert payload is handoff["payload"]
        assert (payload, handoff["accepted"]) == handoff["before"]

    def source(payload):
        unchanged(payload)
        result = real_source(payload)
        unchanged(payload)
        boundaries.append("source")
        return result

    def owned(payload, expected_source, **kwargs):
        unchanged(payload)
        assert kwargs["_accepted_values"] is handoff["accepted"]
        assert boundaries == ["source"]
        result = real_owned(payload, expected_source, **kwargs)
        unchanged(payload)
        boundaries.append("owned")
        return result

    with patch.object(acceptance, "_payload_for_evidence_with_accepted_values", project), \
         patch.object(orders, "_original_hakodate_execution_source", source), \
         patch.object(orders, "_validate_hakodate_owned_artifacts", owned):
        assert evidence.evidence_run_selectability_error(
            row, _analysis=artifacts._EvidenceAnalysis() if analysis_enabled else None) is None
    unchanged(handoff["payload"])
    assert boundaries == ["source", "owned", "source"]
