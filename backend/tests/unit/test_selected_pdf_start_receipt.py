from hashlib import sha256
from types import SimpleNamespace

import pytest

from src.services import storage_service, order_service, ocr_pipeline_service
from src.services.workflow_attempt_guard_service import nonworkflow_execution_context
from src.services import workflow_attempt_guard_service as guard


@pytest.fixture
def own_pdf(tmp_path):
    path = tmp_path / "selected.pdf"
    data = b"selected-original-pdf-with-zero"
    path.write_bytes(data)
    receipt = {"storage_uri": str(path), "sha256": sha256(data).hexdigest(), "generation": None}
    return path, data, receipt


def test_valid_processing_start_read_retains_exact_bytes_and_receipt(own_pdf):
    path, data, receipt = own_pdf
    with nonworkflow_execution_context({"selected_pdf_receipt": receipt}):
        loaded = storage_service.load_selected_pdf_bytes(str(path))
        assert loaded == data
        assert loaded.selected_pdf_receipt == receipt
        assert storage_service.verify_selected_pdf_bytes(loaded) is loaded


def test_same_uri_overwrite_stops_original_bytes_before_recognition(own_pdf):
    path, data, receipt = own_pdf
    loaded = storage_service.load_selected_pdf_bytes(str(path), receipt)
    path.write_bytes(b"different-pdf-same-uri")
    with pytest.raises(ValueError, match="selected_pdf_bytes_changed"):
        storage_service.verify_selected_pdf_bytes(loaded)
    with pytest.raises(ValueError, match="selected_pdf_bytes_changed"):
        storage_service.load_selected_pdf_bytes(str(path), receipt)
    assert loaded == data


@pytest.mark.parametrize("digest", [None, "", "short"])
def test_missing_authoritative_digest_stops_before_body_read(monkeypatch, digest):
    monkeypatch.setattr(storage_service, "load_bytes_from_uri", lambda *a, **k: pytest.fail("late digest backfill"))
    with pytest.raises(ValueError, match="selected_pdf_digest_required"):
        storage_service.load_selected_pdf_bytes("own.pdf", {"storage_uri": "own.pdf", "sha256": digest})


@pytest.mark.parametrize("generation", [None, "7", "8"])
def test_generation_is_compared_not_recaptured(monkeypatch, generation):
    data = b"own-original"
    receipt = {"storage_uri": "gs://source/own.pdf?generation=7", "sha256": sha256(data).hexdigest(), "generation": generation}
    blob = SimpleNamespace(generation=8, reload=lambda **kwargs: None)
    monkeypatch.setattr(storage_service, "_get_thread_gcs_client", lambda: SimpleNamespace(bucket=lambda name: SimpleNamespace(blob=lambda name: blob)))
    reads = []
    monkeypatch.setattr(storage_service, "load_bytes_from_uri", lambda uri, **kwargs: reads.append(kwargs) or data)
    if generation is None:
        with pytest.raises(ValueError, match="selected_pdf_generation_required"):
            storage_service.load_selected_pdf_bytes(receipt["storage_uri"], receipt)
    elif generation == "7":
        with pytest.raises(ValueError, match="selected_pdf_generation_changed"):
            storage_service.load_selected_pdf_bytes(receipt["storage_uri"], receipt)
    else:
        # URI generation and supplied receipt may not disagree, even with equal bytes.
        with pytest.raises(ValueError, match="generation"):
            storage_service.load_selected_pdf_bytes(receipt["storage_uri"], receipt)
    assert reads == []


def test_pipeline_dispatch_stops_when_selected_source_changed(monkeypatch, own_pdf):
    path, _, receipt = own_pdf
    loaded = storage_service.load_selected_pdf_bytes(str(path), receipt)
    path.write_bytes(b"foreign")
    monkeypatch.setattr(ocr_pipeline_service, "_get_pipeline_bucket", lambda: pytest.fail("dispatch before PDF verification"))
    with pytest.raises(ValueError, match="selected_pdf_bytes_changed"):
        ocr_pipeline_service.run_ocr_pipeline(pdf_bytes=loaded, job_id="own-job", facility_id="facility")


def test_raw_operator_review_reuses_verified_original_bytes(own_pdf):
    path, _, receipt = own_pdf
    loaded = storage_service.load_selected_pdf_bytes(str(path), receipt)
    result, metadata = order_service._resolve_llm_review_pdf_bytes(
        document_uri=str(path), selected_pdf_receipt=receipt, original_pdf_bytes=loaded,
        requested_variant="raw",
    )
    assert result is loaded
    assert metadata["used"] == "raw"


def test_corrected_review_requires_own_parent_receipt_not_old_overlay(own_pdf):
    path, _, receipt = own_pdf
    with pytest.raises(ValueError, match="corrected_pdf_receipt_required"):
        order_service._resolve_llm_review_pdf_bytes(
            document_uri=str(path), selected_pdf_receipt=receipt, requested_variant="corrected",
            payload={"overlay_pdf_uri": "old-overlay.pdf"},
        )


@pytest.mark.parametrize("change", [None, "document", "uploaded", "bytes"])
def test_actual_write_guard_compares_document_upload_and_original_bytes(own_pdf, change):
    path, _, receipt = own_pdf
    receipt = {**receipt, "order_id": "order", "document_id": "document", "uploaded_pdf_id": "upload", "message_id": "message"}
    document = SimpleNamespace(order_id="order", storage_uri=str(path), source_email_id="message")
    upload = SimpleNamespace(storage_uri=str(path), message_id="message", content_sha256=receipt["sha256"])
    if change == "document":
        document.storage_uri = "foreign.pdf"
    elif change == "uploaded":
        upload.content_sha256 = sha256(b"foreign").hexdigest()
    elif change == "bytes":
        path.write_bytes(b"foreign")
    locks = []
    def get(model, identity, **kwargs):
        locks.append(kwargs.get("with_for_update"))
        return document if model is guard.OrderDocument else upload
    session = SimpleNamespace(get=get)
    if change == "bytes":
        with pytest.raises(ValueError, match="selected_pdf_bytes_changed"):
            guard.verify_selected_pdf_receipt(session, receipt)
    else:
        assert guard.verify_selected_pdf_receipt(session, receipt) is (change is None)
    assert all(locks)


def test_native_cell_recognition_does_not_reload_original_uri(monkeypatch, own_pdf):
    path, _, receipt = own_pdf
    loaded = storage_service.load_selected_pdf_bytes(str(path), receipt)
    monkeypatch.setattr(order_service, "_hakodate_best_method_draft_sheet", lambda order: {"rows": [[0]]})
    calls = []
    def build(**kwargs):
        assert kwargs["pdf_bytes_override"] is loaded
        return {"_verified_pdf_bytes": loaded, "fax_pdf": "materialized-own.pdf"}
    monkeypatch.setattr(order_service, "_build_live_hakodate_manifest_item", build)
    def recognize(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(metrics={}, outputs={}), None
    monkeypatch.setattr(order_service.hakodate_cell_ocr_batch_service, "build_hakodate_best_method_for_manifest_item", recognize)
    monkeypatch.setattr(storage_service, "load_selected_pdf_bytes", lambda *a, **k: pytest.fail("duplicate URI body read"))
    assert order_service._hakodate_canonical_payload_from_manifest_item(
        order_id="order", facility_id="facility", item={"source": "own-start"},
        document_uri_override=str(path), pdf_bytes_override=loaded,
    ) is None  # Missing recognizer regions remain missing, never synthetic quantity success.
    assert len(calls) == 1
    assert calls[0]["draft_sheet"]["rows"] == [[0]]


def test_unbound_prebuilt_manifest_never_runs_native_recognizer(monkeypatch):
    monkeypatch.setattr(order_service, "_hakodate_best_method_draft_sheet", lambda order, *, synchronous_write_input=None: {"rows": [[0]]})
    monkeypatch.setattr(order_service.hakodate_cell_ocr_batch_service, "build_hakodate_best_method_for_manifest_item", lambda **k: pytest.fail("unbound PDF recognized"))
    with pytest.raises(ValueError, match="hakodate_manifest_selected_pdf_receipt_required"):
        order_service._hakodate_canonical_payload_from_manifest_item(
            order_id="order", facility_id="facility", item={"fax_pdf": "old.pdf", "template_pdf": "template.pdf", "step2_png": "old.png"},
        )


@pytest.mark.parametrize("changed", [False, True])
def test_historical_authoritative_digest_without_generation_is_not_backfilled(monkeypatch, changed):
    data = b"verified-historical-pdf"
    receipt = {"storage_uri": "gs://source/historical.pdf", "sha256": sha256(data).hexdigest(), "generation": None}
    monkeypatch.setattr(storage_service, "load_bytes_from_uri", lambda *a, **k: b"replacement" if changed else data)
    monkeypatch.setattr(storage_service, "_get_thread_gcs_client", lambda: pytest.fail("generation recapture"))
    if changed:
        with pytest.raises(ValueError, match="selected_pdf_bytes_changed"):
            storage_service.load_selected_pdf_bytes(receipt["storage_uri"], receipt)
    else:
        assert storage_service.load_selected_pdf_bytes(receipt["storage_uri"], receipt) == data
    assert receipt["generation"] is None


def test_signed_pdf_link_preserves_generation_without_object_name_query(monkeypatch):
    calls = []
    monkeypatch.setattr(order_service, "generate_signed_url", lambda bucket, path, **kw: calls.append((bucket, path, kw)) or "signed")
    assert order_service._signed_url_from_uri("gs://source/own.pdf?generation=7") == "signed"
    assert calls == [("source", "own.pdf", {"generation": "7"})]


def test_explicit_corrected_pdf_retains_parent_proof_and_real_derived_bytes(own_pdf, tmp_path):
    path, _, parent = own_pdf
    corrected = tmp_path / "corrected.pdf"
    data = b"real-corrected-pdf"
    corrected.write_bytes(data)
    derived = {"storage_uri": str(corrected), "sha256": sha256(data).hexdigest(),
               "generation": None, "source_sha256": parent["sha256"]}
    loaded, meta = order_service._resolve_llm_review_pdf_bytes(
        document_uri=str(path), selected_pdf_receipt=parent, requested_variant="corrected",
        payload={"corrected_pdf_uri": str(corrected), "page_correction_artifacts": {"corrected_pdf_receipt": derived}},
    )
    assert loaded == data
    assert meta["used"] == "corrected"
    assert loaded.selected_pdf_receipt["parent"] == parent
    path.write_bytes(b"original-replaced")
    with pytest.raises(ValueError, match="selected_pdf_bytes_changed"):
        storage_service.verify_selected_pdf_bytes(loaded)


def test_independent_diagnostic_provider_bytes_have_no_canonical_adoption(monkeypatch):
    monkeypatch.setattr(guard, "current_nonworkflow_execution", lambda: None)
    monkeypatch.setattr(guard, "current_workflow_execution", lambda: None)
    monkeypatch.setattr(storage_service, "verify_selected_pdf_bytes", lambda *a: pytest.fail("diagnostic changed to owned execution"))
    data = b"explicit-diagnostic-source"
    assert storage_service.verify_recognizer_pdf_bytes(data) is data


@pytest.mark.parametrize("superseded", [False, True])
def test_source_failure_terminal_receipt_is_same_session_and_never_adopts(monkeypatch, superseded):
    from contextlib import contextmanager
    from src.services import ocr_job_service

    job = SimpleNamespace(id="job", status="awaiting_output", metrics={}, error_message=None)
    document = SimpleNamespace(status="processing", error_message=None)
    flushes = []
    session = SimpleNamespace(get=lambda model, key, **kw: job if model is ocr_job_service.OcrJob else document,
                              flush=lambda: flushes.append(True))
    @contextmanager
    def own_session():
        yield session
    monkeypatch.setattr(ocr_job_service, "session_scope", own_session)
    def ownership(actual_session, **kw):
        assert actual_session is session
        assert kw["job_id"] == "job" and kw["order_id"] == "order"
        assert kw["source_failure"] == "selected_pdf_bytes_changed"
        return not superseded
    monkeypatch.setattr(ocr_job_service, "lock_actual_write_ownership", ownership)
    boundary = {"job_id": "job", "order_id": "order", "execution_id": "own-start",
                "orders": {"order": {"document": {"id": "document"}}}}
    with nonworkflow_execution_context(boundary):
        receipt = ocr_job_service.complete_own_selected_pdf_failure("job", "selected_pdf_bytes_changed")
    if superseded:
        assert receipt is None and flushes == []
        assert job.status == "awaiting_output" and document.status == "processing"
    else:
        assert flushes == [True]
        assert job.status == "failed" and document.status == "error"
        assert receipt["execution_id"] == "own-start"
        assert receipt["evidence_run_id"] is None and receipt["adopted"] is False
        assert job.metrics["source_failure_receipt"] is receipt


def test_selected_document_body_uses_receipt_and_never_current_archive(own_pdf, monkeypatch):
    from src.api import orders as orders_api
    path, data, receipt = own_pdf
    monkeypatch.setattr(order_service, "get_ocr_output", lambda *a, **k: pytest.fail("current/archive PDF substitution"))
    result, kind, variant = orders_api._load_document_bytes(str(path), receipt)
    assert result == data and (kind, variant) == ("original", "source_uri")
    path.write_bytes(b"old-or-foreign-pdf")
    with pytest.raises(ValueError, match="selected_pdf_bytes_changed"):
        orders_api._load_document_bytes(str(path), receipt)


def test_copy_of_verified_pdf_keeps_parent_receipt(own_pdf):
    from copy import deepcopy
    path, data, receipt = own_pdf
    copied = deepcopy(storage_service.load_selected_pdf_bytes(str(path), receipt))
    assert copied == data and copied.selected_pdf_receipt == receipt
    assert storage_service.verify_selected_pdf_bytes(copied) is copied
