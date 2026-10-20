"""Real local receipts and pixel replay; synthetic inputs, not live OCR proof."""
from copy import deepcopy
from contextlib import ExitStack
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from src.services import canonical_ocr_artifact_service as artifacts
from src.services import evidence_manifest_service as manifests
from src.services import hakodate_ocr_checkpoint_service as checkpoints
from src.services import hakodate_preprocess_observation as observation
from src.services import hakodate_recognizer_contact_sheet as contact_sheet
from src.services import storage_service as storage
from src.services.workflow_attempt_guard_service import canonical_snapshot_json
from test_hakodate_ocr_checkpoint_service import _item
from test_hakodate_preprocess_observation import image, prepare, region
from test_hakodate_review_resolution_root import recognize
from test_modernization_canonical_ocr_artifacts import authored_outputs
from test_ocr_read_projection_ownership import packet_row  # noqa: F401


class FullGuardAnalysis(artifacts._EvidenceAnalysis):
    """Control path delegates all pure checks too; it never supplies success."""

    @property
    def recognition_review(self):
        return None

    @recognition_review.setter
    def recognition_review(self, value):
        pass

    def validate_observation(self, inputs, records, source):
        observation.validate_artifact(inputs["preprocess_observation"], records, inputs["contact_sheet"], source)

    def accepted_values(self, payload):
        from src.services.recognition_acceptance_service import accepted_values
        return accepted_values(payload)

    def target_cells(self, payload):
        from src.services.order_service import _extract_hakodate_target_cells_from_payload
        return _extract_hakodate_target_cells_from_payload(payload)

    def evidence_records(self, payload, *, order_id):
        from src.services.order_service import _extract_hakodate_ocr_evidence_records_from_payload
        return _extract_hakodate_ocr_evidence_records_from_payload(payload, order_id=order_id)

    def assign(self, **arguments):
        from src.services.hakodate_ocr_evidence_service import assign_evidence_to_target_cells
        return assign_evidence_to_target_cells(**arguments)


@pytest.fixture
def published(tmp_path, monkeypatch):
    monkeypatch.setenv("OCR_ARTIFACT_DIR", str(tmp_path / "published"))
    monkeypatch.delenv("OCR_OUTPUT_BUCKET", raising=False)
    item = _item(tmp_path)
    item["selected_pdf_receipt"]["order_id"] = item["order_id"]
    checkpoint = checkpoints.persist_recognizer_checkpoint(item=item, raw_rectified_bgr=image(),
        regions=[region()], expected_sheet_cells=["E11"])
    contact, regions, polygons, skipped = prepare(image())
    assert not skipped
    records, _ = recognize(monkeypatch, contact, regions, polygons, [""], [.99])
    output = BytesIO()
    contact.save(output, format="PNG")
    manifest = {"contract": observation.CONTRACT, "checkpoint": checkpoint,
        "observations": [record["recognizer_preprocess_observation"] for record in records]}
    source = {**checkpoint["source"], "expected_sheet_cells": ["E11"],
        "configured_input_digest": sha256(canonical_snapshot_json(item["configured_input"]).encode()).hexdigest()}
    workspace = tmp_path / "workspace"
    outputs = authored_outputs(workspace)
    Path(outputs["pdf"]).write_bytes(Path(item["selected_pdf_receipt"]["storage_uri"]).read_bytes())
    Path(outputs["records"]).write_text(json.dumps(records))
    Path(outputs["contact_sheet"]).write_bytes(output.getvalue())
    path = workspace / "preprocess.json"
    path.write_text(json.dumps(manifest))
    outputs["preprocess_observation"] = str(path)
    packet = artifacts.persist_outputs(order_id=item["order_id"], outputs=outputs, source_receipt=source,
        expected_sheet_cells=["E11"], producer_version="synthetic-dataflow/v1", workspace=workspace)
    references = {key: receipt["uri"] for key, receipt in packet["outputs"].items()}
    payload_records = [{**record, "raw_payload_ref": references["records"]} for record in records]
    return {"hakodate_repair_source_receipt": source, "hakodate_canonical_pipeline": {
        "artifact_packet": packet, "outputs": references, "expected_sheet_cells": ["E11"],
        "version": "synthetic-dataflow/v1", "requested_order_id": item["order_id"]},
        "hakodate_overlay": {"uri": references["overlay"], "sheet_review_base_uri": references["sheet_review_base"]},
        "hakodate_ocr_evidence": {"records": deepcopy(payload_records)},
        "hakodate_ocr_evidence_records": payload_records}


def outcome(action):
    try:
        return ("value", action())
    except Exception as exc:
        return ("error", type(exc), str(exc))


def test_operation_reuses_only_pure_replay_and_rechecks_every_receipt(published, monkeypatch):
    before = deepcopy(published)
    replay = Mock(wraps=contact_sheet.build_recognizer_contact_sheet)
    checkpoint = Mock(wraps=checkpoints.load_recognizer_checkpoint)
    load = Mock(wraps=storage.load_bytes_from_uri)
    monkeypatch.setattr(contact_sheet, "build_recognizer_contact_sheet", replay)
    monkeypatch.setattr(checkpoints, "load_recognizer_checkpoint", checkpoint)
    monkeypatch.setattr(storage, "load_bytes_from_uri", load)
    analysis = artifacts._EvidenceAnalysis()
    left = artifacts.validate_payload(published, _analysis=analysis)
    first_reads = list(load.call_args_list)
    right = artifacts.validate_payload(deepcopy(published), _analysis=analysis)
    assert left == right and published == before
    assert replay.call_count == 2
    assert checkpoint.call_count == 2
    assert load.call_args_list[len(first_reads):] == first_reads
    assert all(call.kwargs == {"verification": True} for call in load.call_args_list)
    # A new operation and standalone public validators must replay from bytes.
    artifacts.validate_payload(published, _analysis=artifacts._EvidenceAnalysis())
    artifacts.validate_payload(published)
    artifacts.validate_payload(published)
    assert replay.call_count == 5


@pytest.mark.parametrize("key", ["metadata", *artifacts.REQUIRED_OUTPUTS, "preprocess_observation"])
@pytest.mark.parametrize("mutation", ["deleted", "changed"])
def test_warm_analysis_cannot_hide_any_missing_or_changed_artifact(published, key, mutation):
    analysis = artifacts._EvidenceAnalysis()
    artifacts.validate_payload(published, _analysis=analysis)
    packet = published["hakodate_canonical_pipeline"]["artifact_packet"]
    receipt = packet["metadata"] if key == "metadata" else packet["outputs"][key]
    path = Path(receipt["uri"])
    if mutation == "deleted":
        path.unlink()
    else:
        path.write_bytes(path.read_bytes() + b"changed")
    baseline = outcome(lambda: artifacts.validate_payload(published))
    candidate = outcome(lambda: artifacts.validate_payload(published, _analysis=analysis))
    assert baseline == candidate
    assert baseline[:2] == ("error", ValueError)
    assert baseline[2] == ("canonical_artifact_bytes_unavailable" if mutation == "deleted"
                           else "canonical_artifact_bytes_changed")


@pytest.mark.parametrize("key", ["checkpoint", "image", "pdf"])
@pytest.mark.parametrize("mutation", ["deleted", "changed"])
def test_live_checkpoint_and_pdf_remain_required_after_success(published, key, mutation):
    analysis = artifacts._EvidenceAnalysis()
    artifacts.validate_payload(published, _analysis=analysis)
    packet = published["hakodate_canonical_pipeline"]["artifact_packet"]
    manifest = json.loads(Path(packet["outputs"]["preprocess_observation"]["uri"]).read_bytes())
    checkpoint = manifest["checkpoint"]
    path = Path(checkpoint["source"]["selected_pdf_receipt"]["storage_uri"] if key == "pdf"
                else checkpoint[key]["uri"])
    if mutation == "deleted":
        path.unlink()
    else:
        path.write_bytes(path.read_bytes() + b"changed")
    baseline = outcome(lambda: artifacts.validate_payload(published))
    candidate = outcome(lambda: artifacts.validate_payload(published, _analysis=analysis))
    assert baseline == candidate and baseline[0] == "error"


@pytest.mark.parametrize("mutation", ["source", "geometry", "pixels", "contact", "metadata", "uri", "size"])
def test_changed_inputs_are_not_certified_by_previous_success(published, mutation):
    analysis = artifacts._EvidenceAnalysis()
    artifacts.validate_payload(published, _analysis=analysis)
    changed = deepcopy(published)
    pipeline = changed["hakodate_canonical_pipeline"]
    packet = pipeline["artifact_packet"]
    if mutation == "source":
        changed["hakodate_repair_source_receipt"]["configured_input_digest"] = "0" * 64
    elif mutation in ("uri", "size"):
        packet["outputs"]["records"]["uri" if mutation == "uri" else "size_bytes"] = "missing" if mutation == "uri" else 1
    elif mutation == "metadata":
        packet["producer_version"] = "changed"
    else:
        inputs = artifacts.validate_payload(published)
        records = json.loads(inputs["records"])
        if mutation == "geometry":
            records[0]["bbox"][0] += 1
        elif mutation == "pixels":
            records[0]["recognizer_preprocess_observation"]["actual_warp"]["sha256"] = "0" * 64
        else:
            records[0]["recognizer_input_evidence"]["rgb_sha256"] = "0" * 64
        inputs["records"] = json.dumps(records).encode()
        source = changed["hakodate_repair_source_receipt"]
        baseline = outcome(lambda: observation.validate_artifact(inputs["preprocess_observation"], records,
            inputs["contact_sheet"], source))
        candidate = outcome(lambda: analysis.validate_observation(inputs, records, source))
        assert baseline == candidate and baseline[0] == "error"
        return
    baseline = outcome(lambda: artifacts.validate_payload(changed))
    candidate = outcome(lambda: artifacts.validate_payload(changed, _analysis=analysis))
    assert baseline == candidate and baseline[0] == "error"


def test_multi_failure_priority_and_manifest_errors_stay_identical(published):
    analysis = artifacts._EvidenceAnalysis()
    artifacts.validate_payload(published, _analysis=analysis)
    packet = published["hakodate_canonical_pipeline"]["artifact_packet"]
    Path(packet["outputs"]["records"]["uri"]).unlink()
    Path(packet["metadata"]["uri"]).write_bytes(b"bad metadata first")
    baseline = manifests.ensure_evidence_manifest(published)
    candidate = manifests.ensure_evidence_manifest(published, _analysis=analysis)
    assert baseline == candidate
    assert candidate["evidence_manifest"]["validation_errors"] == ["canonical_artifact_bytes_changed"]
    assert candidate["evidence_manifest"]["artifacts_complete"] is False


def test_source_binding_does_not_conflate_booleans_with_numbers(published):
    analysis = artifacts._EvidenceAnalysis()
    inputs = artifacts.validate_payload(published, _analysis=analysis)
    changed_source = deepcopy(published["hakodate_repair_source_receipt"])
    changed_source["expected_sheet_cells"] = [True]
    records = json.loads(inputs["records"])
    baseline = outcome(lambda: observation.validate_artifact(inputs["preprocess_observation"], records,
        inputs["contact_sheet"], changed_source))
    candidate = outcome(lambda: analysis.validate_observation(inputs, records, changed_source))
    assert candidate == baseline and candidate[0] == "error"


@pytest.mark.parametrize("mode", ["ready", "review", "accepted", "diagnostic", "operatorEdited"])
def test_four_phase_full_value_and_error_parity_with_real_owned_inputs(packet_row, mode):
    from src.services import ocr_evidence_service as evidence, order_service as orders
    from src.services import order_workflow_v2_service as workflow
    row = packet_row(mode)
    before = deepcopy(vars(row))
    execution = row.payload_json["_ocr_execution"]
    captured = {**execution["processing_start_input"], "selected_pdf_receipt": execution["selected_pdf_receipt"]}
    for action in (
        lambda analysis: artifacts.validate_payload(row.payload_json, _analysis=analysis),
        lambda analysis: workflow._evidence_selection_blocker(row, _analysis=analysis),
        lambda analysis: orders._validate_selected_hakodate_artifacts(vars(row), captured, _analysis=analysis),
        lambda analysis: evidence._serialize_evidence_run(row, include_payload=True, _analysis=analysis),
    ):
        assert outcome(lambda: action(FullGuardAnalysis())) == outcome(lambda: action(artifacts._EvidenceAnalysis()))
        assert vars(row) == before


@pytest.mark.parametrize("mode", ["ready", "accepted", "operatorEdited"])
def test_selected_validation_shares_review_without_removing_source_comparisons(packet_row, monkeypatch, mode):
    from src.services import order_service as orders
    row = packet_row(mode)
    execution = row.payload_json["_ocr_execution"]
    captured = {**execution["processing_start_input"], "selected_pdf_receipt": execution["selected_pdf_receipt"]}
    review = Mock(wraps=orders._validate_hakodate_recognition_review)
    owned = Mock(wraps=orders._validate_hakodate_owned_artifacts)
    monkeypatch.setattr(orders, "_validate_hakodate_recognition_review", review)
    monkeypatch.setattr(orders, "_validate_hakodate_owned_artifacts", owned)
    orders._validate_selected_hakodate_artifacts(vars(row), captured, _analysis=FullGuardAnalysis())
    assert review.call_count == owned.call_count == 2
    review.reset_mock()
    owned.reset_mock()
    orders._validate_selected_hakodate_artifacts(vars(row), captured)
    assert review.call_count == 1 and owned.call_count == 2


def test_changed_review_type_cannot_reuse_successful_pixel_review(packet_row):
    from src.services import order_service as orders
    row = packet_row("ready")
    analysis = artifacts._EvidenceAnalysis()
    source = row.payload_json["hakodate_repair_source_receipt"]
    orders._validate_hakodate_owned_artifacts(row.payload_json, source, _analysis=analysis)
    row.payload_json["hakodate_recognition_review"]["count"] = False
    baseline = outcome(lambda: orders._validate_hakodate_owned_artifacts(row.payload_json, source))
    candidate = outcome(lambda: orders._validate_hakodate_owned_artifacts(row.payload_json, source, _analysis=analysis))
    assert candidate == baseline == ("error", ValueError, "hakodate_recognition_review_invalid")


@pytest.mark.parametrize("mode", ["ready", "accepted", "operatorEdited"])
def test_owned_pure_facts_are_detached_and_assignment_is_shared(packet_row, monkeypatch, mode):
    from src.services import hakodate_ocr_evidence_service as assignment, recognition_acceptance_service as acceptance
    row = packet_row(mode)
    payload = acceptance.payload_for_evidence(row)
    before = deepcopy(payload)
    analysis = artifacts._EvidenceAnalysis()
    accepted = analysis.accepted_values(payload)
    targets = analysis.target_cells(payload)
    records = analysis.evidence_records(payload, order_id=row.order_id)
    spy = Mock(wraps=assignment.assign_evidence_to_target_cells)
    monkeypatch.setattr(assignment, "assign_evidence_to_target_cells", spy)
    first = analysis.assign(evidence_records=records, target_cells=targets, accepted_values=accepted)
    second = analysis.assign(evidence_records=deepcopy(records), target_cells=deepcopy(targets), accepted_values=deepcopy(accepted))
    assert first == second and spy.call_count == 1
    first["assignments"][0]["synthetic_edit"] = True
    assert "synthetic_edit" not in second["assignments"][0]
    targets[0]["metadata"]["synthetic_edit"] = True
    records[0]["engine_metadata"]["synthetic_edit"] = True
    assert analysis.target_cells(payload) != targets
    assert analysis.evidence_records(payload, order_id=row.order_id) != records
    assert payload == before


@pytest.mark.parametrize("fault", ["acceptance", "target", "record", "revision", "source"])
def test_owned_fact_reuse_rejects_mutations_with_original_error_priority(packet_row, fault):
    from src.services import recognition_acceptance_service as acceptance, order_service as orders
    row = packet_row("accepted")
    payload = acceptance.payload_for_evidence(row)
    source = deepcopy(payload["hakodate_repair_source_receipt"])
    analysis = artifacts._EvidenceAnalysis()
    orders._validate_hakodate_owned_artifacts(payload, source, _analysis=analysis)
    if fault == "acceptance":
        payload[acceptance.KEY]["edited_by"] = ""
    elif fault == "target":
        payload["hakodate_preprocessing"]["target_cell_map"][0]["sheet_cell"] = "foreign"
    elif fault == "record":
        payload["hakodate_ocr_evidence"]["records"][0]["engine_metadata"]["source_receipt"] = {}
    elif fault == "revision":
        payload["_ocr_execution"]["workflow_revision"] = -1
    else:
        payload["hakodate_repair_source_receipt"]["job_id"] = "foreign"
    old = outcome(lambda: orders._validate_hakodate_owned_artifacts(payload, source))
    new = outcome(lambda: orders._validate_hakodate_owned_artifacts(payload, source, _analysis=analysis))
    assert old == new and old[0] == "error"


@pytest.mark.parametrize("value", [(1,), {1: "value"}, float("nan"), float("inf"), -float("inf")])
def test_non_native_values_cannot_establish_or_reuse_a_binding(value):
    assert artifacts._analysis_input_bytes({"probe": value}) is None
    assert artifacts._analysis_input_bytes({"probe": [1]}) is not None


def test_container_subclasses_cycles_and_alias_graphs_are_not_json_tree_proofs():
    class Mapping(dict):
        pass
    class Sequence(list):
        pass
    class Text(str):
        pass
    cycle = []
    cycle.append(cycle)
    shared = []
    for value in (Mapping(probe=[1]), {"probe": Sequence([1])}, {Text("probe"): [1]},
                  {"probe": Text("value")}, {"probe": cycle}):
        assert artifacts._analysis_input_bytes(value) is None
    aliased = artifacts._analysis_input_bytes({"left": shared, "right": shared})
    detached = artifacts._analysis_input_bytes({"left": [], "right": []})
    assert aliased is not None and detached is not None and aliased != detached


@pytest.mark.parametrize("left,right", [
    (True, 1), (1, 1.0), (0.0, -0.0),
    ({"a": 1, "b": 2}, {"b": 2, "a": 1}),
    ([1, 2], [2, 1]), ("1", 1),
])
def test_binding_preserves_types_order_and_signed_zero(left, right):
    assert artifacts._analysis_input_bytes(left) != artifacts._analysis_input_bytes(right)


def test_binding_does_not_invoke_custom_serialization():
    class Unexpected(dict):
        def __reduce_ex__(self, protocol):
            raise AssertionError("custom serialization must not execute")
    assert artifacts._analysis_input_bytes({"nested": Unexpected(a=1)}) is None


def test_binding_preserves_detached_copy_and_detects_nested_mutation():
    shared = ["日本語", 1, 1.5, False, None]
    value = {"left": shared, "right": shared}
    original = artifacts._analysis_input_bytes(value)
    copied = deepcopy(value)
    assert artifacts._analysis_input_bytes(copied) == original
    copied["left"][1] = 2
    assert artifacts._analysis_input_bytes(copied) != original


def test_parent_list_tuple_repro_delegates_actual_extractor_on_second_call(monkeypatch):
    from src.services import order_service as orders
    original = orders._extract_hakodate_target_cells_from_payload
    calls = []
    def extracting(payload):
        calls.append(type(payload["probe"]))
        if type(payload["probe"]) is not list:
            raise ValueError("list_required")
        return ["valid"]
    monkeypatch.setattr(orders, "_extract_hakodate_target_cells_from_payload", extracting)
    analysis = artifacts._EvidenceAnalysis()
    assert analysis.target_cells({"probe": [1]}) == ["valid"]
    assert outcome(lambda: analysis.target_cells({"probe": (1,)})) == ("error", ValueError, "list_required")
    assert calls == [list, tuple]
    monkeypatch.setattr(orders, "_extract_hakodate_target_cells_from_payload", original)


@pytest.mark.parametrize("field", ["targets", "records", "review_cells"])
def test_real_owned_packet_list_to_tuple_keeps_original_error(packet_row, field):
    from src.services import order_service as orders
    row = packet_row("ready")
    payload = row.payload_json
    source = deepcopy(payload["hakodate_repair_source_receipt"])
    analysis = artifacts._EvidenceAnalysis()
    orders._validate_hakodate_owned_artifacts(payload, source, _analysis=analysis)
    if field == "targets":
        preprocessing = payload["hakodate_preprocessing"]
        preprocessing["target_cell_map"] = tuple(preprocessing["target_cell_map"])
    elif field == "records":
        container = payload["hakodate_ocr_evidence"]
        container["records"] = tuple(container["records"])
    else:
        review = payload["hakodate_recognition_review"]
        review["cells"] = tuple(review["cells"])
    old = outcome(lambda: orders._validate_hakodate_owned_artifacts(payload, source))
    new = outcome(lambda: orders._validate_hakodate_owned_artifacts(payload, source, _analysis=analysis))
    assert old == new and old[0] == "error"


@pytest.mark.parametrize("shape", ["dict_subclass", "list_subclass", "nonstring_key", "nan", "infinity", "cycle"])
def test_real_owned_packet_ineligible_shapes_delegate_in_original_order(packet_row, monkeypatch, shape):
    from src.services import order_service as orders
    class Mapping(dict):
        pass
    class Sequence(list):
        pass
    row = packet_row("ready")
    analysis = artifacts._EvidenceAnalysis()
    payload = row.payload_json
    analysis.target_cells(payload)
    changed = deepcopy(payload)
    if shape == "dict_subclass":
        changed = Mapping(changed)
    elif shape == "list_subclass":
        changed["hakodate_preprocessing"]["target_cell_map"] = Sequence(changed["hakodate_preprocessing"]["target_cell_map"])
    elif shape == "nonstring_key":
        changed[1] = "value"
    elif shape == "nan":
        changed["probe"] = float("nan")
    elif shape == "infinity":
        changed["probe"] = float("inf")
    else:
        changed["probe"] = changed
    assert artifacts._analysis_input_bytes(changed) is None
    extractor = Mock(wraps=orders._extract_hakodate_target_cells_from_payload)
    monkeypatch.setattr(orders, "_extract_hakodate_target_cells_from_payload", extractor)
    old = outcome(lambda: extractor(changed))
    new = outcome(lambda: analysis.target_cells(changed))
    assert old == new and extractor.call_count == 2


def test_observation_reuse_cannot_accept_tuple_expected_cells(published):
    analysis = artifacts._EvidenceAnalysis()
    inputs = artifacts.validate_payload(published, _analysis=analysis)
    source = deepcopy(published["hakodate_repair_source_receipt"])
    source["expected_sheet_cells"] = tuple(source["expected_sheet_cells"])
    records = json.loads(inputs["records"])
    old = outcome(lambda: observation.validate_artifact(inputs["preprocess_observation"], records,
        inputs["contact_sheet"], source))
    new = outcome(lambda: analysis.validate_observation(inputs, records, source))
    assert old == new == ("error", ValueError, "hakodate_observation_source_changed")


def test_in_flight_checkpoint_loss_preserves_entire_guard_error_sequence(published):
    analysis = artifacts._EvidenceAnalysis()
    inputs = artifacts.validate_payload(published, _analysis=analysis)
    manifest = json.loads(inputs["preprocess_observation"])
    Path(manifest["checkpoint"]["image"]["uri"]).unlink()
    watched = [(checkpoints, "load_recognizer_checkpoint"), (observation, "validate_artifact"),
        (artifacts, "validate_packet"), (artifacts, "validate_payload")]

    def evaluate(arguments):
        errors = []
        def watch(name, real):
            def call(*args, **kwargs):
                try:
                    return real(*args, **kwargs)
                except Exception as exc:
                    errors.append((name, type(exc).__name__, str(exc)))
                    raise
            return call
        with ExitStack() as stack:
            for module, name in watched:
                stack.enter_context(patch.object(module, name, watch(name, getattr(module, name))))
            result = outcome(lambda: artifacts.validate_payload(published, **arguments))
        return result, errors

    old = evaluate({})
    new = evaluate({"_analysis": analysis})
    assert old[0] == new[0]
    assert old[1] == new[1]


def test_outer_payload_subclass_get_item_review_mismatch_cannot_reuse_success(packet_row, monkeypatch):
    from src.services import order_service as orders

    class ConflictingReview(dict):
        def get(self, key, default=None):
            if key == "hakodate_recognition_review":
                return None
            return super().get(key, default)

    row = packet_row("ready")
    payload = row.payload_json
    source = deepcopy(payload["hakodate_repair_source_receipt"])
    analysis = artifacts._EvidenceAnalysis()
    orders._validate_hakodate_owned_artifacts(payload, source, _analysis=analysis)
    assert analysis.recognition_review is not None
    changed = ConflictingReview(payload)
    assert changed["hakodate_recognition_review"] == payload["hakodate_recognition_review"]
    assert changed.get("hakodate_recognition_review") is None
    assert artifacts._analysis_input_bytes(changed) is None
    review = Mock(wraps=orders._validate_hakodate_recognition_review)
    monkeypatch.setattr(orders, "_validate_hakodate_recognition_review", review)
    old = outcome(lambda: orders._validate_hakodate_owned_artifacts(changed, source))
    new = outcome(lambda: orders._validate_hakodate_owned_artifacts(changed, source, _analysis=analysis))
    assert old == new == ("error", ValueError, "hakodate_recognition_review_invalid")
    assert review.call_count == 2
