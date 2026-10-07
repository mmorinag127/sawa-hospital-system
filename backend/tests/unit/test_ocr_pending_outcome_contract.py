from contextlib import contextmanager
from copy import deepcopy
from hashlib import sha256
from types import SimpleNamespace

import pytest

from src.api import orders
from src.services import order_service, ocr_job_service


@pytest.fixture
def waiting_job(monkeypatch):
    job = SimpleNamespace(
        id="real-job", order_id="order", status="running",
        uploaded_pdf_id=None, order_document_id="selected-document", input_artifact_digest="own-digest",
        template_id="authored-template", template_version_id="authored-version", created_at=None, updated_at=None,
        input_reference="selected-input", output_reference=None, error_message=None,
        metrics={"selected_document_id": "selected-document", "workflow_attempt_id": "start-attempt",
                 "workflow_completion_revision": 7, "reparse_read_input": {"start": "immutable"},
                 **order_service._build_reparse_request_metadata(
                     ocr_prompt=" custom prompt ", prompt_preset="custom-preset",
                     requested_provider="gemini", ocr_model="custom-model", llm_assist=True,
                 ),
                 "pipeline_delivery_receipt": {
                     "job_id": "real-job", "order_id": "order", "workflow_attempt_id": "start-attempt",
                     "workflow_revision": 7, "execution_id": None,
                     "selected_input_reference": "selected-input", "input_reference": "new-upload-input",
                     "output_reference": "own-output", "generation": "42",
                     "pipeline_job_id": "own-bucket:input/current.pdf:42", "wait_timeout_seconds": 600,
                     "input_sha256": sha256(b"own-source-pdf").hexdigest(),
                 }},
    )
    session = SimpleNamespace(get=lambda model, key: job, flush=lambda: None)
    @contextmanager
    def transaction():
        before = deepcopy(vars(job))
        try:
            yield session
        except BaseException:
            vars(job).clear()
            vars(job).update(before)
            raise
    monkeypatch.setattr(order_service, "_ocr_result_transaction", transaction)
    monkeypatch.setattr(order_service, "session_scope", transaction)
    monkeypatch.setattr(ocr_job_service, "session_scope", transaction)
    monkeypatch.setattr(ocr_job_service, "_lock_own_execution_job", lambda session, job_id: job if job_id == job.id else None)
    monkeypatch.setattr(order_service, "_workflow_attempt_write_fence", lambda *args: True)
    monkeypatch.setattr(order_service, "current_nonworkflow_execution", lambda: None)
    with order_service.workflow_ocr_write_context("start-attempt", 7, job.id, "order"):
        result = order_service._mark_reparse_job_awaiting_output(
            job.id, input_reference="new-upload-input", output_reference="own-output",
            recovery_delay_seconds=30,
        )
    return job, result


def test_real_waiting_write_returns_nonterminal_original_fence(waiting_job):
    job, result = waiting_job
    assert job.status == "awaiting_output"
    assert job.input_reference == "selected-input"
    assert result["pending_receipt"]["input_reference"] == "new-upload-input"
    assert "rerun_terminal_receipt" not in job.metrics
    assert job.metrics["reparse_read_input"] == {"start": "immutable"}
    assert result["pending_receipt"] == job.metrics["pending_receipt"]
    assert order_service.is_own_pending_ocr_result(
        result, order_id="order", job_id=job.id,
        workflow_attempt_id="start-attempt", workflow_revision=7,
    )
    assert result["pending_receipt"]["selected_document_id"] == "selected-document"
    assert not order_service.is_own_pending_ocr_result(result, order_id="order", job_id=job.id)


@pytest.mark.parametrize("outcome", ["pending", "missing", "wrong_revision"])
def test_actual_reparse_callback_distinguishes_wait_from_missing_terminal(monkeypatch, waiting_job, outcome):
    job, result = waiting_job
    supplied = deepcopy(result) if outcome != "missing" else {}
    if outcome == "wrong_revision":
        supplied["pending_receipt"]["workflow_revision"] = 6
    monkeypatch.setattr(order_service, "reparse_order", lambda *args, **kwargs: (supplied, None))
    completed = []
    def complete(*args, **kwargs):
        completed.append(kwargs)
        return None, "superseded"
    monkeypatch.setattr(orders, "_complete_workflow_ocr_run", complete)
    orders._run_reparse_background(
        "order", ocr_prompt=" custom prompt ", selected_document_id="selected-document", llm_assist=True,
        expected_revision=7, workflow_attempt_id="start-attempt", ocr_job_id=job.id,
    )
    if outcome == "pending":
        assert completed == []
        assert job.status == "awaiting_output"
    else:
        assert completed[0]["error"] == "reparse_evidence_missing"
        assert completed[0]["expected_revision"] == 7
        assert completed[0]["workflow_attempt_id"] == "start-attempt"


def test_resume_uses_original_waiting_receipt_and_own_returned_id(monkeypatch, waiting_job):
    job, result = waiting_job
    receipt = result["pending_receipt"]
    seen = []
    payload = {"recognized_quantity": 0}
    monkeypatch.setattr(order_service, "get_job_request_mode", lambda job: "llm_reparse")
    def load(ref, **kwargs):
        assert ref == "own-output"
        return payload
    def resume(order_id, **kwargs):
        seen.append(kwargs)
        return {"reparse_evidence_run_id": "own-evidence", "terminal_receipt": {
            "job_id": job.id, "order_id": "order", "workflow_attempt_id": "start-attempt",
            "workflow_revision": 7, "evidence_run_id": "own-evidence", "status": "done",
        }}, None
    monkeypatch.setattr(order_service, "_load_pipeline_output_with_retry", load)
    monkeypatch.setattr(order_service, "_output_is_pending", lambda payload: False)
    monkeypatch.setattr(order_service, "reparse_order", resume)
    assert order_service.reconcile_pending_workflow_reparse({"id": job.id, "order_id": "order", "metrics": {}}, receipt)
    assert seen[0]["workflow_attempt_id"] == "start-attempt"
    assert seen[0]["expected_workflow_revision"] == 7
    assert seen[0]["selected_document_id"] == "selected-document"
    assert seen[0]["resume_first_pass_payload"] == {"recognized_quantity": 0}
    assert seen[0]["resume_first_pass_output_reference"] == "own-output"
    assert seen[0]["ocr_prompt"] == " custom prompt "
    assert seen[0]["prompt_preset"] == "custom-preset"
    assert seen[0]["ocr_provider"] == "gemini"
    assert seen[0]["ocr_model"] == "custom-model"
    assert seen[0]["llm_assist"] is True


def test_foreign_delivery_cannot_create_waiting_receipt(waiting_job):
    job, _ = waiting_job
    before = deepcopy(vars(job))
    with order_service.workflow_ocr_write_context("start-attempt", 7, job.id, "order"):
        assert order_service._mark_reparse_job_awaiting_output(
            job.id, input_reference="foreign-upload", output_reference="own-output",
        ) is None
    assert vars(job) == before


def test_changed_selected_source_cannot_register_delivery(waiting_job):
    job, _ = waiting_job
    before = deepcopy(vars(job))
    with order_service.workflow_ocr_write_context("start-attempt", 7, job.id, "order"):
        assert not ocr_job_service.record_own_pipeline_delivery(
            job.id, selected_input_reference="changed-document", input_reference="new-upload",
            output_reference="new-output", generation="new-generation",
        )
    assert vars(job) == before


def test_superseded_execution_cannot_register_request_or_delivery(monkeypatch, waiting_job):
    job, _ = waiting_job
    before = deepcopy(vars(job))
    monkeypatch.setattr(ocr_job_service, "_lock_own_execution_job", lambda *args: None)
    with order_service.workflow_ocr_write_context("old-attempt", 6, job.id, "order"):
        assert not ocr_job_service.capture_own_reparse_request(job.id, {
            "ocr_prompt": None, "prompt_preset": None, "ocr_provider": None, "ocr_model": None, "llm_assist": False,
        })
        assert not ocr_job_service.record_own_pipeline_delivery(
            job.id, selected_input_reference="selected-input", input_reference="new-upload",
            output_reference="new-output", generation="new-generation",
        )
    assert vars(job) == before


def test_original_request_null_presence_is_required_and_immutable(waiting_job):
    job, _ = waiting_job
    original = order_service._extract_reparse_request_kwargs_from_job({"metrics": job.metrics})
    with order_service.workflow_ocr_write_context("start-attempt", 7, job.id, "order"):
        assert ocr_job_service.capture_own_reparse_request(job.id, original)
        assert not ocr_job_service.capture_own_reparse_request(job.id, {**original, "ocr_model": None})
    metadata = order_service._build_reparse_request_metadata(
        ocr_prompt=None, prompt_preset=None, requested_provider=None, ocr_model=None, llm_assist=False,
    )
    assert order_service._extract_reparse_request_kwargs_from_job({"metrics": metadata}) == {
        "ocr_prompt": None, "prompt_preset": None, "ocr_provider": None, "ocr_model": None, "llm_assist": False,
    }
    del metadata["reparse_ocr_model"]
    with pytest.raises(ValueError, match="reparse_original_request_missing"):
        order_service._extract_reparse_request_kwargs_from_job({"metrics": metadata})


def test_actual_queue_captures_complete_original_request(monkeypatch):
    from fastapi import BackgroundTasks

    seen = []
    monkeypatch.setattr(orders, "_resolve_order_document_version", lambda *args: (
        {"facility": "authored-facility"}, {"storage_uri": "selected-input", "document_id": "selected-document"},
    ))
    monkeypatch.setattr(orders.config_service, "get_facility_config", lambda *args: {"authored": True})
    def queue(*args, **kwargs):
        seen.append(kwargs)
        return {"workflow": {"revision": 8}, "ocr_job": {"workflow_attempt_id": "queue-attempt"}}, None
    monkeypatch.setattr(orders.order_workflow_v2_service, "mark_ocr_run_queued", queue)
    monkeypatch.setattr(orders, "_dispatch_workflow_ocr_task", lambda *args, **kwargs: None)
    result = orders._enqueue_order_reparse_job(
        "order", BackgroundTasks(), ocr_prompt=" custom prompt ", prompt_preset="custom-preset",
        ocr_provider="gemini", ocr_model="custom-model", llm_assist=True, expected_revision=7,
    )
    assert result["accepted"] is True
    assert seen[0]["expected_revision"] == 7
    assert order_service._extract_reparse_request_kwargs_from_job({"metrics": seen[0]["job_metrics"]}) == {
        "ocr_prompt": " custom prompt ", "prompt_preset": "custom-preset", "ocr_provider": "gemini",
        "ocr_model": "custom-model", "llm_assist": True,
    }


def test_actual_force_upload_timeout_records_own_delivery_before_wait(monkeypatch, waiting_job):
    from src.services import ocr_pipeline_service, config_service

    job, _ = waiting_job
    monkeypatch.setattr(config_service, "get_authored_facility_config", lambda *args: {"authored": True})
    monkeypatch.setattr(ocr_pipeline_service, "_get_pipeline_semaphore", lambda: None)
    monkeypatch.setattr(ocr_pipeline_service, "_get_pipeline_bucket", lambda: "own-bucket")
    monkeypatch.setattr(ocr_pipeline_service, "_get_pipeline_url", lambda: None)
    monkeypatch.setattr(ocr_pipeline_service, "_get_prefix", lambda name, prefix: prefix)
    monkeypatch.setattr(ocr_pipeline_service, "_upload_pdf_bytes", lambda **kwargs: "42")
    monkeypatch.setattr(ocr_pipeline_service, "save_pipeline_request", lambda *args: None)
    monkeypatch.setattr(ocr_pipeline_service, "save_pipeline_error", lambda *args: None)
    def wait(**kwargs):
        delivery = job.metrics["pipeline_delivery_receipt"]
        assert delivery["output_reference"] == "gs://own-bucket/" + kwargs["object_name"]
        assert delivery["generation"] == "42"
        raise TimeoutError("external output not ready")
    monkeypatch.setattr(ocr_pipeline_service, "_wait_for_output", wait)
    with order_service.workflow_ocr_write_context("start-attempt", 7, job.id, "order"):
        with pytest.raises(ocr_pipeline_service.OCRPipelineOutputPendingError) as pending:
            ocr_pipeline_service.run_ocr_pipeline(
                pdf_bytes=b"own-source-pdf", job_id=job.id, facility_id="authored-facility",
                input_reference="selected-input", force_upload=True,
            )
        assert pending.value.input_reference != "selected-input"
        result = order_service._mark_reparse_job_awaiting_output(
            job.id, input_reference=pending.value.input_reference, output_reference=pending.value.output_reference,
        )
    assert result["outcome"] == "pending"
    assert job.input_reference == "selected-input"
    assert result["pending_receipt"]["pipeline_delivery_receipt"] == job.metrics["pipeline_delivery_receipt"]
    assert result["pending_receipt"]["request_kwargs"]["ocr_model"] == "custom-model"


def test_nonworkflow_new_upload_wait_keeps_original_document_and_request(waiting_job):
    from src.services.workflow_attempt_guard_service import nonworkflow_execution_context

    job, _ = waiting_job
    execution = {"job_id": job.id, "order_id": "order", "execution_id": "own-execution"}
    job.metrics.pop("workflow_attempt_id")
    job.metrics.pop("pending_receipt")
    with nonworkflow_execution_context(execution):
        assert ocr_job_service.record_own_pipeline_delivery(
            job.id, selected_input_reference="selected-input", input_reference="new-nonworkflow-upload",
            output_reference="own-nonworkflow-output", generation="nonworkflow-generation",
        )
        updated = ocr_job_service.mark_own_pipeline_waiting(
            job.id, input_reference="new-nonworkflow-upload", output_reference="own-nonworkflow-output",
            error_message="ocr_output_pending", metrics_patch={"processing_stage": "ocr_pipeline"},
        )
        assert updated["input_reference"] == "selected-input"
        assert updated["output_reference"] == "own-nonworkflow-output"
        before = deepcopy(vars(job))
        assert ocr_job_service.mark_own_pipeline_waiting(
            job.id, input_reference="foreign-upload", output_reference="own-nonworkflow-output",
            error_message="ocr_output_pending", metrics_patch={"processing_stage": "foreign"},
        ) is None
        assert vars(job) == before
    assert order_service._extract_reparse_request_kwargs_from_job({"metrics": job.metrics})["ocr_model"] == "custom-model"


def test_superseded_resume_does_not_load_or_mutate(monkeypatch, waiting_job):
    job, result = waiting_job
    before = deepcopy(vars(job))
    monkeypatch.setattr(order_service, "get_job_request_mode", lambda job: "llm_reparse")
    monkeypatch.setattr(order_service, "_workflow_attempt_write_fence", lambda *args: False)
    def forbidden(*args, **kwargs):
        raise AssertionError("superseded resume loaded output")
    monkeypatch.setattr(order_service, "_load_pipeline_output_with_retry", forbidden)
    assert not order_service.reconcile_pending_workflow_reparse({"id": job.id, "order_id": "order"}, result["pending_receipt"])
    assert vars(job) == before


def test_actual_rerun_callback_keeps_own_pending_nonterminal(monkeypatch, waiting_job):
    job, result = waiting_job
    @contextmanager
    def slot(**kwargs):
        yield {}
    def forbidden(*args, **kwargs):
        raise AssertionError("pending result terminalized")
    monkeypatch.setattr(orders, "acquire_ocr_execution_slot", slot)
    monkeypatch.setattr(orders, "get_ocr_job", lambda job_id: {"metrics": {}})
    monkeypatch.setattr(orders.ocr_job_service, "update_job_for_workflow_attempt", lambda *args, **kwargs: ({}, True))
    monkeypatch.setattr(order_service, "rerun_ocr_evidence_only", lambda *args, **kwargs: (result, None))
    monkeypatch.setattr(orders, "_complete_workflow_ocr_run", forbidden)
    orders._run_ocr_rerun_background("order", job.id, expected_revision=7, workflow_attempt_id="start-attempt")
    assert job.status == "awaiting_output"


def test_auto_llm_pending_does_not_save_failed_diagnostic(monkeypatch):
    from src.workers import ingest_worker

    result = {"outcome": "pending", "status": "awaiting_output", "output_reference": "own-output", "pending_receipt": {
        "job_id": "real-job", "order_id": "order", "workflow_attempt_id": None,
        "workflow_revision": None, "execution_id": "start-execution", "output_reference": "own-output",
    }}
    monkeypatch.setattr(ingest_worker, "run_order_reparse", lambda *args, **kwargs: (result, None))
    def forbidden(*args, **kwargs):
        raise AssertionError("pending auto LLM saved terminal diagnostic")
    monkeypatch.setattr(order_service, "_persist_ocr_rerun_completion", forbidden)
    ingest_worker._run_auto_llm_reparse(
        "order", job_id="real-job", execution_input={"job_id": "real-job", "execution_id": "start-execution"},
        own_payload={"recognized_quantity": 0},
    )


def test_firstpass_recovery_pending_never_requires_evidence_or_terminal(monkeypatch):
    execution = {"job_id": "real-job", "order_id": "order"}
    monkeypatch.setattr(order_service, "current_nonworkflow_execution", lambda: execution)
    monkeypatch.setattr(order_service, "get_ocr_job", lambda job_id: {
        "id": job_id, "order_id": "order", "metrics": {}, "output_reference": "own-output",
    })
    monkeypatch.setattr(order_service, "get_job_request_mode", lambda job: "ingest_first_pass")
    monkeypatch.setattr(order_service, "_resolve_message_id_for_ocr_job", lambda *args, **kwargs: None)
    monkeypatch.setattr(order_service, "_load_pipeline_output_with_retry", lambda *args, **kwargs: {"pending": True})
    monkeypatch.setattr(order_service, "_output_is_pending", lambda payload: True)
    def forbidden(*args, **kwargs):
        raise AssertionError("pending firstpass applied result")
    monkeypatch.setattr(order_service, "create_order_from_ingest", forbidden)
    monkeypatch.setattr(order_service, "_persist_ocr_rerun_completion", forbidden)
    assert not order_service._reconcile_completed_ocr_job_with_input("real-job")


def own_delivery_payload(job, status="done"):
    receipt = job.metrics["pipeline_delivery_receipt"]
    return {"engine": "yomitoku", "status": status, "job_id": receipt["pipeline_job_id"],
            "template_id": "authored-template",
            "input_reference": receipt["input_reference"], "input_generation": receipt["generation"],
            "input_sha256": receipt["input_sha256"], "output_reference": receipt["output_reference"],
            "table_rows": [["2/15", "morning", "meal", "0"]]}


@pytest.mark.parametrize("field,value", [
    ("input_generation", "41"), ("input_reference", "foreign-pdf"),
    ("output_reference", "old-output"), ("input_sha256", "different-pdf"), ("job_id", "foreign-job"),
])
def test_actual_result_gate_rejects_foreign_generation_and_pdf(waiting_job, field, value):
    job, _ = waiting_job
    payload = own_delivery_payload(job)
    payload[field] = value
    before = deepcopy(vars(job))
    with order_service.workflow_ocr_write_context("start-attempt", 7, job.id, "order"):
        with pytest.raises(ocr_job_service.OwnPipelineLineageError):
            ocr_job_service.validate_own_pipeline_result(payload, "own-output")
    assert vars(job) == before


def test_generation_pinned_storage_read_calls_actual_loader(monkeypatch):
    from src.services import storage_service

    calls = []
    class Blob:
        def download_as_bytes(self, **kwargs):
            calls.append(("download", kwargs))
            return b"own-source-pdf"
    class Bucket:
        def blob(self, name, **kwargs):
            calls.append((name, kwargs))
            return Blob()
    monkeypatch.setattr(storage_service, "_get_thread_gcs_client", lambda: SimpleNamespace(bucket=lambda name: Bucket()))
    assert storage_service.load_bytes_from_uri("gs://own-bucket/input/current.pdf", generation="42") == b"own-source-pdf"
    assert calls == [("input/current.pdf", {"generation": 42}), ("download", {"if_generation_match": 42})]
    with pytest.raises(ValueError, match="storage_generation_invalid"):
        storage_service.load_bytes_from_uri("gs://own-bucket/input/current.pdf", generation="old")


def test_fax_pipeline_reuses_exact_result_zero_without_second_upload(monkeypatch, waiting_job):
    from src.services import fax_extractor

    job, _ = waiting_job
    payload = own_delivery_payload(job)
    original = deepcopy(payload)
    monkeypatch.setattr(fax_extractor, "_get_main_provider", lambda template: "pipeline")
    monkeypatch.setattr(fax_extractor, "detect_table_grid", lambda *args: None)
    monkeypatch.setattr(fax_extractor.hakodate_assignment_service, "resolve_quantity_assignment_strategy", lambda template: "standard")
    def forbidden(*args, **kwargs):
        raise AssertionError("second recognition/transport")
    monkeypatch.setattr(fax_extractor, "run_ocr_pipeline", forbidden)
    with order_service.workflow_ocr_write_context("start-attempt", 7, job.id, "order"):
        extracted = fax_extractor.extract_fax_data(
            b"own-source-pdf", {"main_ocr_row_fields": ["date_mmdd", "daypart", "menu", "qty"]},
            facility_id="authored-facility", own_pipeline_payload=payload,
        )
    assert extracted.table_rows == [["2/15", "morning", "meal", "0"]]
    assert extracted.own_pipeline_payload is payload
    assert payload == original


def test_fax_new_execution_returns_raw_own_result_not_selected_baseline(monkeypatch, waiting_job):
    from src.services import fax_extractor

    job, _ = waiting_job
    current = own_delivery_payload(job)
    selected_baseline = deepcopy(current)
    selected_baseline.update(job_id="previous-execution", input_generation="41")
    calls = []
    def recognize(**kwargs):
        calls.append(kwargs)
        return current
    monkeypatch.setattr(fax_extractor, "run_ocr_pipeline", recognize)
    monkeypatch.setattr(fax_extractor, "_get_main_provider", lambda template: "pipeline")
    monkeypatch.setattr(fax_extractor, "detect_table_grid", lambda *args: None)
    monkeypatch.setattr(fax_extractor.hakodate_assignment_service, "resolve_quantity_assignment_strategy", lambda template: "standard")
    with order_service.workflow_ocr_write_context("start-attempt", 7, job.id, "order"):
        extracted = fax_extractor.extract_fax_data(
            b"own-source-pdf", {"main_ocr_row_fields": ["date_mmdd", "daypart", "menu", "qty"]},
        )
        assert extracted.own_pipeline_payload is current
        assert len(calls) == 1 and calls[0]["job_id"] == job.id
        with pytest.raises(ocr_job_service.OwnPipelineLineageError):
            fax_extractor.extract_fax_data(b"own-source-pdf", {}, own_pipeline_payload=selected_baseline)
    assert len(calls) == 1
    assert extracted.table_rows == [["2/15", "morning", "meal", "0"]]


@pytest.mark.parametrize("status", ["running", "failed"])
def test_fax_pending_is_not_generic_failure_and_real_failure_is_not_wait(monkeypatch, waiting_job, status):
    from src.services import fax_extractor, ocr_pipeline_service

    job, _ = waiting_job
    payload = own_delivery_payload(job, status)
    monkeypatch.setattr(fax_extractor, "_get_main_provider", lambda template: "pipeline")
    monkeypatch.setattr(fax_extractor, "detect_table_grid", lambda *args: None)
    monkeypatch.setattr(fax_extractor.hakodate_assignment_service, "resolve_quantity_assignment_strategy", lambda template: "standard")
    with order_service.workflow_ocr_write_context("start-attempt", 7, job.id, "order"):
        expected = ocr_pipeline_service.OCRPipelineOutputPendingError if status == "running" else RuntimeError
        with pytest.raises(expected) as error:
            fax_extractor.extract_fax_data(b"own-source-pdf", {}, own_pipeline_payload=payload)
        if status == "running":
            pending = order_service._mark_reparse_job_awaiting_output(
                job.id, input_reference=error.value.input_reference, output_reference=error.value.output_reference,
            )
            assert pending["outcome"] == "pending"
            assert "rerun_terminal_receipt" not in job.metrics


@pytest.mark.parametrize("mapping_case", ["normal", "missing", "wrong_template"])
def test_arrived_firstpass_maps_own_rows_not_saved_lines(monkeypatch, waiting_job, mapping_case):
    from src.workers import ingest_worker
    from src.services import fax_extractor
    from src.services.workflow_attempt_guard_service import nonworkflow_execution_context
    import json

    job, _ = waiting_job
    job.metrics["pipeline_delivery_receipt"].update(workflow_attempt_id=None, workflow_revision=None, execution_id="own-execution")
    job.metrics["request_mode"] = "ingest_first_pass"
    raw = own_delivery_payload(job)
    mapping = {"template": {"template_id": "authored-template", "main_ocr_row_fields": ["date_mmdd", "daypart", "menu", "qty"]},
               "preferred_template_id": None, "policy": {"quantity_rules": {}}}
    boundary = {"job_id": job.id, "order_id": "order", "execution_id": "own-execution", "input_reference": "selected-input",
                "first_pass_application": {"payload": {"message_id": "own-message", "pdf_uri": "selected-input",
                    "received_at": "2026-10-07T00:00:00", "facility_hint": "authored-facility", "order_id": "order",
                    "week_hint": "2026-02@2026-02-15~2026-02-21"},
                    "mapping_input": mapping, "ocr_attempts": 1, "lines": [{"quantity_original": 999}]}}
    if mapping_case == "missing":
        del boundary["first_pass_application"]["mapping_input"]
    elif mapping_case == "wrong_template":
        raw["template_id"] = "other-template"
    monkeypatch.setattr(order_service, "current_nonworkflow_execution", lambda: boundary)
    monkeypatch.setattr(order_service, "get_ocr_job", lambda *args: {"id": job.id, "order_id": "order",
                         "output_reference": "own-output", "metrics": job.metrics})
    monkeypatch.setattr(order_service, "_resolve_message_id_for_ocr_job", lambda *args, **kwargs: None)
    def read(uri, *, generation=None):
        if uri == "own-output":
            assert generation is None
            return json.dumps(raw).encode()
        assert uri == "new-upload-input" and generation == "42"
        return b"own-source-pdf"
    monkeypatch.setattr(order_service, "load_bytes_from_uri", read)
    monkeypatch.setattr(fax_extractor, "_get_main_provider", lambda template: "pipeline")
    monkeypatch.setattr(fax_extractor, "detect_table_grid", lambda *args: None)
    monkeypatch.setattr(ingest_worker, "detect_table_grid", lambda *args: None)
    monkeypatch.setattr(fax_extractor.hakodate_assignment_service, "resolve_quantity_assignment_strategy", lambda template: "standard")
    monkeypatch.setattr(ingest_worker, "_dump_ocr_debug", lambda *args: None)
    def parse(rows, *args, **kwargs):
        assert rows == [["2/15", "morning", "meal", "0"]]
        return [{"quantity_original": 0}]
    monkeypatch.setattr(ingest_worker, "parse_order_lines", parse)
    seen = []
    def apply(payload, **kwargs):
        seen.append(kwargs)
        return {"first_pass_terminal_receipt": {"job_id": job.id, "execution_id": "own-execution",
                "evidence_run_id": "own-evidence", "status": "done" if kwargs["document_status"] == "success" else "failed"},
                "first_pass_evidence": {"id": "own-evidence"}}
    monkeypatch.setattr(order_service, "create_order_from_ingest", apply)
    monkeypatch.setattr(order_service, "_finalize_async_first_pass_side_effects", lambda **kwargs: None)
    with nonworkflow_execution_context(boundary):
        assert order_service._reconcile_completed_ocr_job_with_input(job.id)
    if mapping_case == "normal":
        raw["raw_recognizer_payload"] = deepcopy(raw)
        raw["_first_pass_recognizer"] = {
            "job_id": job.id, "execution_id": "own-execution", "workflow_attempt_id": None, "workflow_revision": None,
            "template_id": "authored-template", "week_hint": "2026-02@2026-02-15~2026-02-21",
            "input_sha256": sha256(b"own-source-pdf").hexdigest(), "provider": "pipeline",
            "selected_input_reference": "selected-input",
            "recognized_rows": [["2/15", "morning", "meal", "0"]], "mapped_lines": [{"quantity_original": 0}],
        }
        assert seen[0]["lines"] == [{"quantity_original": 0}]
        assert seen[0]["document_status"] == "success"
    else:
        assert seen[0]["lines"] is None
        assert seen[0]["document_status"] == "error"
        assert seen[0]["error_message"]
    assert seen[0]["pipeline_result"] == raw
