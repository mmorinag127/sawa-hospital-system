"""Synthetic storage/packet component definitions, not native acceptance proof.

PNG/PDF/JSON and local persisted bytes are real. The small component source
binding is synthetic, not an owned execution receipt. Only failure transport
tests replace storage I/O; recognition/ownership/assignment are never replaced.
The final test separately uses the unchanged native OwnedProducer and real
SQLite models. Its prerequisites must succeed normally; no skip or substitutes.
"""

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import fitz
from PIL import Image
import pytest

from src.services import canonical_ocr_artifact_service as artifacts
from src.services import evidence_manifest_service as manifests
from src.services import storage_service as storage


KEYS = ("pdf", "overlay", "overlay_png", "sheet_review_base", "contact_sheet",
        "records", "ocr_regions", "sheet_values")
ORDER = "SYNTHETIC-ARTIFACT-ORDER"
VERSION = "synthetic-storage-component/v1"


@pytest.fixture
def local_storage(tmp_path, monkeypatch):
    for name in ("OCR_OUTPUT_BUCKET", "OCR_ARTIFACT_DIR", "RAW_BUCKET"):
        monkeypatch.delenv(name, raising=False)
    root = tmp_path / "published"
    monkeypatch.setenv("OCR_ARTIFACT_DIR", str(root))
    return root


def authored_outputs(workspace):
    workspace.mkdir(parents=True, exist_ok=True)
    outputs = {}
    for key in ("overlay", "sheet_review_base", "contact_sheet"):
        path = workspace / f"{key}.png"
        with Image.new("RGB", (24, 32), "white") as image:
            image.putpixel((3, 4), (0, 0, 0))
            image.save(path)
        outputs[key] = str(path)
    outputs["overlay_png"] = outputs["overlay"]
    path = workspace / "synthetic.pdf"
    with fitz.open() as document:
        page = document.new_page(width=200, height=100)
        page.insert_text((10, 30), "Synthetic artifact component, not operator FAX")
        document.save(path)
    outputs["pdf"] = str(path)
    for key, value in (
        ("records", [{"sheet_cell": "E11", "raw_text": "7"}]),
        ("ocr_regions", [{"sheet_cell": "E11", "ocr_text": "7"}]),
        ("sheet_values", {"cells": {}, "rows": [], "columns": []}),
    ):
        path = workspace / f"{key}.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        outputs[key] = str(path)
    return {key: outputs[key] for key in KEYS}


def persist(workspace, outputs=None):
    outputs = authored_outputs(workspace) if outputs is None else outputs
    selected_pdf = workspace.parent / "selected-input.pdf"
    selected_pdf.write_bytes(Path(outputs["pdf"]).read_bytes())
    source = {"selected_pdf_receipt": {
        "order_id": ORDER, "storage_uri": str(selected_pdf),
        "sha256": sha256(selected_pdf.read_bytes()).hexdigest(),
    }, "configured_input_digest": sha256(b"synthetic authored component config").hexdigest()}
    expected = ["E11"]
    packet = artifacts.persist_outputs(
        order_id=ORDER, outputs=outputs, source_receipt=source,
        expected_sheet_cells=expected, producer_version=VERSION, workspace=workspace,
    )
    arguments = {"source_receipt": source, "expected_sheet_cells": expected,
                 "producer_version": VERSION, "order_id": ORDER,
                 "outputs": {key: receipt["uri"] for key, receipt in packet["outputs"].items()}}
    records = [{"raw_payload_ref": arguments["outputs"]["records"]}]
    payload = {
        "hakodate_repair_source_receipt": deepcopy(source),
        "hakodate_canonical_pipeline": {
            "artifact_packet": packet, "outputs": arguments["outputs"],
            "expected_sheet_cells": expected, "version": VERSION, "requested_order_id": ORDER,
        },
        "hakodate_overlay": {"uri": arguments["outputs"]["overlay"],
                             "sheet_review_base_uri": arguments["outputs"]["sheet_review_base"]},
        "hakodate_ocr_evidence": {"records": deepcopy(records)},
        "hakodate_ocr_evidence_records": records,
    }
    return packet, arguments, payload


@pytest.fixture
def published(tmp_path, local_storage):
    return persist(tmp_path / "producer")


def test_read_only_target_views_preserve_values_and_default_callers_keep_defensive_copies():
    from src.services import order_service
    target = {"sheet_cell": "E11", "source": "recognizer", "metadata": {"original": ["", "symbol"]}}
    payload = {"hakodate_preprocessing": {"target_cell_map": [target]}}
    before = deepcopy(payload)
    analysis = artifacts._EvidenceAnalysis()
    copied = analysis.target_cells(payload)
    viewed = analysis.target_cells(payload, copy_records=False)
    assert copied == viewed == [target]
    assert copied[0] is not target
    assert viewed[0] is target
    assert payload == before
    copied[0]["metadata"]["original"].append("edited")
    assert payload == before
    payload["hakodate_target_cell_map"] = [{**target, "sheet_cell": "F11"}]
    for copy_records in (True, False):
        with pytest.raises(ValueError, match="hakodate_own_artifact_representations_conflict"):
            order_service._extract_hakodate_target_cells_from_payload(payload, copy_records=copy_records)


def test_packet_reads_bound_parallelism_and_preserve_pinned_dataset(published, monkeypatch):
    from collections import Counter
    from threading import Barrier, Lock, current_thread
    from src import staging_data as datasets

    packet, arguments, _ = published
    real_load = storage.load_bytes_from_uri
    selected_manifest = {"fixture": "owned-read-copy"}
    monkeypatch.setattr(datasets, "staging_runtime", lambda: True)
    monkeypatch.setattr(datasets, "manifest_for", lambda identity: selected_manifest)
    first_batch = {packet["outputs"][key]["uri"] for key in list(packet["outputs"])[:3]}
    barrier, lock = Barrier(3), Lock()
    active = peak = 0
    calls = []
    workers = [set(), set()]
    phase = 0

    def observed(uri, **kwargs):
        nonlocal active, peak
        assert datasets.current_dataset() == "replay-artifact-fixture"
        assert datasets._pinned_manifest.get() is selected_manifest
        assert kwargs == {"verification": True}
        with lock:
            active += 1
            peak = max(peak, active)
            calls.append(uri)
            if uri != packet['metadata']['uri']:
                workers[phase].add(current_thread())
        try:
            if uri in first_batch:
                barrier.wait(timeout=10)
            # Real authored local bytes; only the request's copy transport is doubled.
            with datasets.dataset_context("test"):
                return real_load(uri, **kwargs)
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(storage, "load_bytes_from_uri", observed)
    with datasets.dataset_context("replay-artifact-fixture"):
        first = artifacts.validate_packet(packet, **arguments)
        phase = 1
        second = artifacts.validate_packet(packet, **arguments)
    expected = Counter(receipt["uri"] for receipt in packet["outputs"].values())
    expected[packet["metadata"]["uri"]] += 1
    assert Counter(calls) == expected + expected
    assert first == second
    assert peak == 3 and active == 0
    assert len(workers[0]) == 3 and workers[0] == workers[1]
    assert datasets.current_dataset() == "test"


def test_packet_transfers_share_process_bound_without_reusing_results(published, monkeypatch):
    from collections import Counter
    from concurrent.futures import ThreadPoolExecutor
    from contextvars import ContextVar
    from threading import Barrier, Lock

    packet, arguments, _ = published
    actor = ContextVar('synthetic_artifact_request', default=None)
    barrier, lock = Barrier(3), Lock()
    real_load = storage.load_bytes_from_uri
    active = peak = 0
    calls = Counter()
    first_batch = {packet['outputs'][key]['uri'] for key in list(packet['outputs'])[:3]}

    def observed(uri, **kwargs):
        nonlocal active, peak
        identity = actor.get()
        assert identity in ('request-a', 'request-b', 'request-c')
        transfer = uri != packet['metadata']['uri']
        with lock:
            calls[identity, uri] += 1
            if transfer:
                active += 1
                peak = max(peak, active)
        try:
            if uri in first_batch:
                barrier.wait(timeout=10)
            return real_load(uri, **kwargs)
        finally:
            if transfer:
                with lock:
                    active -= 1

    def validate(identity):
        token = actor.set(identity)
        try:
            return artifacts.validate_packet(packet, **arguments)
        finally:
            actor.reset(token)

    monkeypatch.setattr(storage, 'load_bytes_from_uri', observed)
    with ThreadPoolExecutor(max_workers=3) as callers:
        results = list(callers.map(validate, ('request-a', 'request-b', 'request-c')))
    expected = Counter(receipt['uri'] for receipt in packet['outputs'].values())
    expected[packet['metadata']['uri']] += 1
    assert calls == Counter({(identity, uri): count for identity in ('request-a', 'request-b', 'request-c')
                             for uri, count in expected.items()})
    assert results[0] == results[1] == results[2]
    assert peak == 3 and active == 0


def test_packet_parallel_failures_preserve_first_error_and_join_workers(published, monkeypatch):
    from threading import Event, Lock

    packet, arguments, _ = published
    real_load = storage.load_bytes_from_uri
    pdf_uri = packet["outputs"]["pdf"]["uri"]
    overlay_uri = packet["outputs"]["overlay"]["uri"]
    later_failed, lock = Event(), Lock()
    active = 0

    def fail_out_of_order(uri, **kwargs):
        nonlocal active
        with lock:
            active += 1
        try:
            if uri == pdf_uri:
                assert later_failed.wait(timeout=10)
                return b"changed first artifact"
            if uri == overlay_uri:
                later_failed.set()
                raise OSError("synthetic later transport failure")
            return real_load(uri, **kwargs)
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(storage, "load_bytes_from_uri", fail_out_of_order)
    with pytest.raises(ValueError, match="^canonical_artifact_bytes_changed$"):
        artifacts.validate_packet(packet, **arguments)
    assert later_failed.is_set() and active == 0


def test_real_outputs_survive_workspace_close_with_bound_metadata(tmp_path, local_storage, monkeypatch):
    monkeypatch.setenv("OCR_ARTIFACT_DIR", f"  {local_storage}  ")
    with TemporaryDirectory(dir=tmp_path) as temporary:
        workspace = Path(temporary)
        outputs = authored_outputs(workspace)
        original = {key: Path(path).read_bytes() for key, path in outputs.items()}
        packet, arguments, payload = persist(workspace, outputs)
    assert not workspace.exists()
    assert all(not Path(path).exists() for path in outputs.values())
    assert set(packet["outputs"]) == set(KEYS)
    assert packet["source_receipt"] == arguments["source_receipt"]
    selected_pdf = packet["source_receipt"]["selected_pdf_receipt"]
    assert Path(selected_pdf["storage_uri"]).read_bytes() == original["pdf"]
    assert sha256(Path(selected_pdf["storage_uri"]).read_bytes()).hexdigest() == selected_pdf["sha256"]
    assert packet["expected_sheet_cells"] == ["E11"]
    assert packet["order_id"] == ORDER
    assert packet["producer_version"] == VERSION
    for key, receipt in packet["outputs"].items():
        path = Path(receipt["uri"])
        assert path.is_relative_to(local_storage)
        assert path.read_bytes() == original[key]
        assert receipt["sha256"] == sha256(original[key]).hexdigest()
        assert receipt["size_bytes"] == len(original[key])
        expected_type = "application/pdf" if key == "pdf" else (
            "application/json" if key in {"records", "ocr_regions", "sheet_values"} else "image/png")
        assert receipt["content_type"] == expected_type
        if expected_type == "image/png":
            with Image.open(path) as image:
                assert image.size == (24, 32)
                assert image.getpixel((3, 4)) == (0, 0, 0)
    with fitz.open(arguments["outputs"]["pdf"]) as document:
        assert len(document) == 1
        assert "Synthetic artifact component" in document[0].get_text()
    assert packet["outputs"]["overlay"] == packet["outputs"]["overlay_png"]
    assert len({receipt["uri"] for receipt in packet["outputs"].values()}) == 7
    assert len(list(local_storage.rglob("*.*"))) == 8  # Seven files plus metadata.
    metadata_bytes = Path(packet["metadata"]["uri"]).read_bytes()
    assert packet["metadata"]["sha256"] == sha256(metadata_bytes).hexdigest()
    assert packet["metadata"]["size_bytes"] == len(metadata_bytes)
    assert packet["metadata"]["content_type"] == "application/json"
    metadata = json.loads(metadata_bytes)
    assert metadata == {key: value for key, value in packet.items() if key != "metadata"}
    artifacts.validate_packet(packet, **arguments)
    artifacts.validate_payload(payload)
    manifest = manifests.ensure_evidence_manifest(payload)["evidence_manifest"]
    assert manifest["contract"] == artifacts.CONTRACT
    assert manifest["required_artifacts"] == list(KEYS)
    assert manifest["artifacts"] == dict.fromkeys(KEYS, True)
    assert manifest["artifacts_complete"] is True
    assert manifest["missing_artifacts"] == []
    assert manifest["validation_errors"] == []
    assert json.loads(Path(arguments["outputs"]["sheet_values"]).read_bytes())["cells"] == {}


@pytest.mark.parametrize("selection,error", [
    ("missing", "canonical_artifact_storage_required"),
    ("raw_only", "canonical_artifact_storage_required"),
    ("whitespace", "canonical_artifact_storage_required"),
    ("both", "canonical_artifact_storage_ambiguous"),
    ("relative", "canonical_artifact_directory_must_be_absolute"),
])
def test_storage_selection_never_uses_legacy_or_ambiguous_provider(local_storage, monkeypatch, selection, error):
    def unexpected_transport(*args, **kwargs):
        raise AssertionError("invalid selection must stop before any storage transport")

    monkeypatch.setattr(storage, "save_bytes_to_gcs", unexpected_transport)
    monkeypatch.setattr(storage.StorageService, "save_bytes", unexpected_transport)
    monkeypatch.delenv("OCR_ARTIFACT_DIR")
    if selection == "raw_only":
        monkeypatch.setenv("RAW_BUCKET", "legacy-raw-must-not-be-used")
    elif selection == "whitespace":
        monkeypatch.setenv("OCR_OUTPUT_BUCKET", " \t ")
        monkeypatch.setenv("OCR_ARTIFACT_DIR", "  ")
    elif selection == "both":
        monkeypatch.setenv("OCR_OUTPUT_BUCKET", " explicit-output ")
        monkeypatch.setenv("OCR_ARTIFACT_DIR", f" {local_storage} ")
    elif selection == "relative":
        monkeypatch.setenv("OCR_ARTIFACT_DIR", "relative-directory")
    with pytest.raises(ValueError, match=f"^{error}$"):
        storage.save_canonical_artifact_bytes("synthetic", "image.png", b"not-published", "image/png")
    assert not local_storage.exists()


def test_local_storage_inside_producer_workspace_is_rejected(tmp_path, local_storage, monkeypatch):
    workspace = tmp_path / "producer"
    monkeypatch.setenv("OCR_ARTIFACT_DIR", str(workspace / "published"))
    with pytest.raises(ValueError, match="^canonical_artifact_storage_inside_workspace$"):
        persist(workspace)


@pytest.mark.parametrize("key", KEYS)
@pytest.mark.parametrize("failure", ["missing_key", "missing_file", "empty"])
def test_every_actual_output_is_required(tmp_path, local_storage, key, failure):
    workspace = tmp_path / "producer"
    outputs = authored_outputs(workspace)
    # Leave source PDF intact: an independently missing output path exercises
    # the output contract rather than failing source-receipt setup first.
    if failure == "missing_key":
        outputs.pop(key)
    elif failure == "missing_file":
        outputs[key] = str(workspace / "absent-output")
    else:
        path = workspace / "empty-output"
        path.write_bytes(b"")
        outputs[key] = str(path)
    source = {"selected_pdf_receipt": {"order_id": ORDER}}
    error = FileNotFoundError if failure == "missing_file" else ValueError
    with pytest.raises(error):
        artifacts.persist_outputs(order_id=ORDER, outputs=outputs, source_receipt=source,
                                 expected_sheet_cells=["E11"], producer_version=VERSION, workspace=workspace)


@pytest.mark.parametrize("provider", ["local", "gcs"])
def test_failed_provider_never_tries_alternative_transport(tmp_path, local_storage, monkeypatch, provider):
    calls = []

    def local_failure(self, path, data):
        calls.append("local")
        raise OSError("synthetic local save failure")

    def gcs_failure(bucket, path, data, content_type=None, **kwargs):
        calls.append("gcs")
        assert bucket == "explicit-output"
        assert kwargs["pin_generation"] is True
        raise RuntimeError("synthetic GCS transport failure")

    # Explicit failure-only transport doubles, never a fake successful upload.
    monkeypatch.setattr(storage.StorageService, "save_bytes", local_failure)
    monkeypatch.setattr(storage, "save_bytes_to_gcs", gcs_failure)
    monkeypatch.setenv("RAW_BUCKET", "forbidden-alternative")
    if provider == "gcs":
        monkeypatch.delenv("OCR_ARTIFACT_DIR")
        monkeypatch.setenv("OCR_OUTPUT_BUCKET", " explicit-output ")
    with pytest.raises(OSError if provider == "local" else RuntimeError, match="synthetic"):
        persist(tmp_path / "producer")
    assert calls == [provider]


@pytest.mark.parametrize("key,value", [
    ("records", b"{}"), ("ocr_regions", b"{}"), ("sheet_values", b"[]"),
    ("overlay", b"not PNG"), ("pdf", b"not PDF"),
])
def test_invalid_actual_output_content_is_not_published_as_valid(tmp_path, local_storage, key, value):
    workspace = tmp_path / "producer"
    outputs = authored_outputs(workspace)
    Path(outputs[key]).write_bytes(value)
    with pytest.raises(ValueError, match="^canonical_artifact_content_invalid$"):
        persist(workspace, outputs)


@pytest.mark.parametrize("change,error", [
    ("missing_packet", "canonical_artifact_packet_required"),
    ("altered_bytes", "canonical_artifact_bytes_changed"),
    ("deleted_bytes", "canonical_artifact_bytes_unavailable"),
    ("metadata_bytes", "canonical_artifact_bytes_changed"),
    ("metadata_content", "canonical_artifact_metadata_changed"),
    ("source", "canonical_artifact_source_changed"),
    ("order", "canonical_artifact_source_changed"),
    ("version", "canonical_artifact_source_changed"),
    ("expected", "canonical_artifact_source_changed"),
    ("uri", "canonical_artifact_reference_changed"),
])
def test_packet_rejects_missing_tampered_and_mismatched_input(published, change, error):
    packet, arguments, _ = published
    if change == "missing_packet":
        packet = None
    elif change in {"altered_bytes", "deleted_bytes"}:
        path = Path(arguments["outputs"]["records"])
        if change == "deleted_bytes":
            path.unlink()
        else:
            path.write_bytes(b"[]")
    elif change in {"metadata_bytes", "metadata_content"}:
        path = Path(packet["metadata"]["uri"])
        path.write_bytes(b"{}")
        if change == "metadata_content":
            packet["metadata"]["sha256"] = sha256(b"{}").hexdigest()
            packet["metadata"]["size_bytes"] = 2
    elif change == "source":
        arguments["source_receipt"]["configured_input_digest"] = "0" * 64
    elif change == "order":
        arguments["order_id"] = "OTHER"
    elif change == "version":
        arguments["producer_version"] = "OTHER"
    elif change == "expected":
        arguments["expected_sheet_cells"] = ["E12"]
    else:
        arguments["outputs"]["records"] = arguments["outputs"]["ocr_regions"]
    with pytest.raises(ValueError, match=f"^{error}$"):
        artifacts.validate_packet(packet, **arguments)


@pytest.mark.parametrize("change,error", [
    ("packet", "canonical_artifact_packet_required"),
    ("overlay", "canonical_artifact_overlay_reference_changed"),
    ("base", "canonical_artifact_overlay_reference_changed"),
    ("raw_ref", "canonical_artifact_evidence_reference_changed"),
    ("record_alias", "canonical_artifact_evidence_reference_changed"),
    ("order", "canonical_artifact_source_changed"),
])
def test_payload_and_manifest_share_reference_rejection(published, change, error):
    _, _, payload = published
    pipeline = payload["hakodate_canonical_pipeline"]
    if change == "packet":
        pipeline.pop("artifact_packet")
    elif change in {"overlay", "base"}:
        payload["hakodate_overlay"]["uri" if change == "overlay" else "sheet_review_base_uri"] = "wrong"
    elif change == "raw_ref":
        for records in (payload["hakodate_ocr_evidence_records"], payload["hakodate_ocr_evidence"]["records"]):
            records[0]["raw_payload_ref"] = "wrong"
    elif change == "record_alias":
        payload["hakodate_ocr_evidence"]["records"] = []
    else:
        pipeline["requested_order_id"] = "OTHER"
    with pytest.raises(ValueError, match=f"^{error}$"):
        artifacts.validate_payload(payload)
    manifest = manifests.ensure_evidence_manifest(payload)["evidence_manifest"]
    assert manifest["artifacts_complete"] is False
    assert manifest["missing_artifacts"] == list(KEYS)
    assert manifest["validation_errors"] == [error]


@pytest.mark.parametrize("change,error", [
    ("altered", "canonical_artifact_bytes_changed"),
    ("deleted", "canonical_artifact_bytes_unavailable"),
    ("metadata", "canonical_artifact_bytes_changed"),
])
def test_cached_complete_manifest_rechecks_actual_bytes(published, change, error):
    packet, arguments, payload = published
    cached = manifests.ensure_evidence_manifest(payload)
    assert cached["evidence_manifest"]["artifacts_complete"] is True
    path = Path(packet["metadata"]["uri"] if change == "metadata" else arguments["outputs"]["records"])
    if change == "deleted":
        path.unlink()
    else:
        path.write_bytes(b"[]")
    rebuilt = manifests.ensure_evidence_manifest(cached)["evidence_manifest"]
    assert rebuilt["artifacts_complete"] is False
    assert rebuilt["missing_artifacts"] == list(KEYS)
    assert rebuilt["validation_errors"] == [error]
    assert rebuilt["digest"] != cached["evidence_manifest"]["digest"]


@pytest.mark.parametrize("marker", ["pipeline", "manifest", "overlay", "preprocessing"])
def test_canonical_marker_cannot_retire_to_bogus_legacy_six_key_success(marker):
    payload = {
        "evidence_manifest": {"artifacts_complete": True, "missing_artifacts": [],
                              "required_artifacts": list(manifests.REQUIRED_ARTIFACT_KEYS),
                              "artifacts": dict.fromkeys(manifests.REQUIRED_ARTIFACT_KEYS, True), "digest": "bogus"},
        "page_correction_artifacts": {"corrected_pdf_uri": "nonexistent.pdf"},
        "pages": [{"ocr_overlay_uri": "nonexistent.png"}], "table_raw": "nonexistent.json",
        "quantity_subgrid_passes": [], "template_resolution": {},
    }
    if marker == "pipeline":
        payload["hakodate_canonical_pipeline"] = {}
    elif marker == "manifest":
        payload["evidence_manifest"]["contract"] = artifacts.CONTRACT
    elif marker == "overlay":
        payload["hakodate_overlay"] = {"producer": "hakodate_best_method_pipeline"}
    else:
        payload["hakodate_preprocessing"] = {"source": "hakodate_best_method_pipeline"}
    manifest = manifests.ensure_evidence_manifest(payload)["evidence_manifest"]
    assert manifest["contract"] == artifacts.CONTRACT
    assert manifest["required_artifacts"] == list(KEYS)
    assert manifest["artifacts_complete"] is False
    assert manifest["missing_artifacts"] == list(KEYS)
    assert manifest["validation_errors"]


def test_real_owned_candidate_invalid_packet_blocks_classifier_selectability_and_selected_adoption(tmp_path, monkeypatch, isolated_formal_sqlite_template):
    # Native-dependent boundary regression, not the small synthetic storage
    # fixture above and not a replacement for the original three native tests.
    from test_modernization_owned_ocr_workflow import OwnedProducer, ORDER as OWNED_ORDER
    from src import db
    from src.models.order_ocr_evidence_run import OrderOcrEvidenceRun
    from src.services import ocr_evidence_service as evidence_service, order_service as orders

    fixture = OwnedProducer(tmp_path, monkeypatch, formal_template=isolated_formal_sqlite_template)
    monkeypatch.setenv("OCR_ARTIFACT_DIR", str(tmp_path / "owned-published"))
    try:
        evidence = fixture.produce()
        payload = evidence["payload_json"]
        assert evidence_service.classify_evidence_payload(payload)["persistable"] is True
        with db.session_scope() as session:
            assert evidence_service.evidence_run_is_selectable(session.get(OrderOcrEvidenceRun, evidence["id"])) is True
        fixture.select()
        captured = orders._capture_synchronous_cache_write_input(OWNED_ORDER)
        assert captured is not None
        orders._validate_selected_hakodate_artifacts(evidence, captured)
        uri = payload["hakodate_canonical_pipeline"]["outputs"]["records"]
        original = Path(uri).read_bytes()
        for change, error in (
            ("missing", "canonical_artifact_packet_required"),
            ("altered", "canonical_artifact_bytes_changed"),
            ("deleted", "canonical_artifact_bytes_unavailable"),
            ("source", "canonical_artifact_source_changed"),
        ):
            changed = deepcopy(evidence)
            pipeline = changed["payload_json"]["hakodate_canonical_pipeline"]
            if change == "missing":
                pipeline.pop("artifact_packet")
            elif change == "altered":
                Path(uri).write_bytes(b"[]")
            elif change == "deleted":
                Path(uri).unlink()
            else:
                pipeline["artifact_packet"]["source_receipt"]["configured_input_digest"] = "0" * 64
            assessment = evidence_service.classify_evidence_payload(changed["payload_json"])
            assert assessment["persistable"] is False
            assert error in assessment["message"]
            with db.session_scope() as session:
                stored = session.get(OrderOcrEvidenceRun, evidence["id"])
                stored.payload_json = deepcopy(changed["payload_json"])
            with db.session_scope() as session:
                assert evidence_service.evidence_run_is_selectable(session.get(OrderOcrEvidenceRun, evidence["id"])) is False
            with pytest.raises(ValueError, match=f"^{error}$"):
                orders._validate_selected_hakodate_artifacts(changed, captured)
            Path(uri).write_bytes(original)
            with db.session_scope() as session:
                session.get(OrderOcrEvidenceRun, evidence["id"]).payload_json = deepcopy(payload)
        assert evidence_service.classify_evidence_payload(payload)["persistable"] is True
        orders._validate_selected_hakodate_artifacts(evidence, captured)
    finally:
        fixture.engine.dispose()
        from src.services import master_order_form_template_service as master
        assert sha256(master.MASTER_TEMPLATE_PATH.read_bytes()).hexdigest() == fixture.locked_hash
