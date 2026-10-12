from copy import deepcopy
from datetime import date, datetime
from hashlib import sha256
from types import SimpleNamespace

import pytest

from src.services import fax_extractor, fax_parser, ingest_policy, ocr_job_service, order_service, ocr_evidence_service
from src.services.workflow_attempt_guard_service import nonworkflow_execution_context
from src.workers import ingest_worker
from src.workers.ingest_mail_adapter import IngestEmailPayload


WEEK = "2026-02@2026-02-15~2026-02-21"


@pytest.mark.parametrize("week", [None, "", "bad", "2026-02", "2026-02@2026-02-30~2026-03-01", [WEEK, WEEK]])
def test_missing_invalid_ambiguous_week_stops_before_registration_and_dispatch(monkeypatch, week):
    def forbidden(*args, **kwargs):
        raise AssertionError("registration/OCR started without unique explicit week")
    for name in ("create_job", "create_order_from_ingest", "preview_existing_order_for_ingest", "capture_nonworkflow_execution"):
        monkeypatch.setattr(ingest_worker, name, forbidden)
    with pytest.raises(ValueError, match="week_hint_required|week_hint_invalid|week_choice_required"):
        ingest_worker._process_ingest_inline(message_id="own-mail", pdf_uri="own.pdf", received_at="2026-02-16T00:00:00", week_hint=week)


@pytest.mark.parametrize("provider", ["openai", "gemini", "pipeline"])
def test_actual_provider_rows_raw_result_and_captured_policy_reach_persistence(monkeypatch, tmp_path, provider):
    from src.services import openai_ocr_service, gemini_ocr_service

    rows = [["2026-02-15", "morning", "meal", "0"], ["2026-02-16", "morning", "meal", "12"]]
    raw = {"status": "done", "table_rows": deepcopy(rows), "date_strings": ["2026-02-15"],
           "table_raw": "actual-provider-text", "model": "configured-model", "unknown": 0, "confidence": None}
    pipeline = {**deepcopy(raw), "engine": "yomitoku", "template_id": "authored-template", "output_reference": "own-output"}
    if provider != "pipeline":
        pipeline["table_rows"] = [["2026-02-15", "morning", "meal", "999"]]
    original = deepcopy(raw)
    policy = {"week_id_policy": {"date_format": "YYYY-MM-DD"}, "quantity_rules": {"zero_as_empty": False}}
    mapping = {"template": {"template_id": "authored-template", "main_ocr_provider": provider,
        "main_ocr_row_fields": ["date", "daypart", "menu", "qty"]}, "preferred_template_id": "authored-template", "policy": policy}
    monkeypatch.setattr(ocr_job_service, "validate_own_pipeline_result", lambda payload, *args, **kwargs: payload)
    monkeypatch.setattr(ingest_worker, "validate_own_pipeline_result", lambda payload, *args, **kwargs: payload)
    own_pdf = tmp_path / "own.pdf"
    own_pdf.write_bytes(b"own-pdf")
    own_uri = str(own_pdf)
    monkeypatch.setattr(ocr_job_service, "own_pipeline_source", lambda: ("real-job", own_uri))
    monkeypatch.setattr(fax_extractor, "_get_main_provider", lambda template: provider)
    monkeypatch.setattr(fax_extractor, "detect_table_grid", lambda *args: None)
    monkeypatch.setattr(ingest_worker, "detect_table_grid", lambda *args: None)
    monkeypatch.setattr(ingest_worker, "_dump_ocr_debug", lambda *args: None)
    monkeypatch.setattr(fax_extractor.hakodate_assignment_service, "resolve_quantity_assignment_strategy", lambda template: "standard")
    calls = []
    def recognize(**kwargs):
        calls.append(kwargs)
        assert kwargs["pdf_bytes"] == b"own-pdf"
        return raw
    monkeypatch.setattr(openai_ocr_service, "run_openai_ocr", recognize)
    monkeypatch.setattr(gemini_ocr_service, "run_gemini_ocr", recognize)
    def no_reload():
        raise AssertionError("mutable date policy reloaded after capture")
    monkeypatch.setattr(ingest_policy, "load_ingest_policy", no_reload)
    monkeypatch.setattr(fax_parser, "load_ingest_policy", no_reload)
    boundary = {"job_id": "real-job", "order_id": "order", "execution_id": "captured-execution", "input_reference": own_uri,
        "selected_pdf_receipt": {"storage_uri": own_uri, "sha256": sha256(b"own-pdf").hexdigest(), "generation": None}}
    payload = IngestEmailPayload("own-mail", own_uri, datetime(2026, 2, 16), facility_hint="facility", week_hint=WEEK)
    persisted = []
    def save(order_id, result, **kwargs):
        persisted.append((order_id, result, kwargs))
        return {"id": "own-provider-evidence", "payload_json": result}
    monkeypatch.setattr(order_service, "persist_ocr_evidence_run", save)
    monkeypatch.setattr(order_service, "current_nonworkflow_execution", lambda: boundary)
    with nonworkflow_execution_context(boundary):
        normal = ingest_worker.map_own_ingest_result(payload, b"own-pdf", pipeline, mapping)
        # Recovery supplies the same captured mapping, not a current policy read.
        recovered = ingest_worker.map_own_ingest_result(payload, b"own-pdf", pipeline, deepcopy(mapping))
        assert recovered == normal
        evidence = order_service._persist_ingest_pipeline_evidence(
            object(), SimpleNamespace(id="order"), normal["recognizer_result"], "success",
        )
        if provider != "pipeline":
            declared = normal["recognizer_result"]["page_correction_artifacts"]["recognition_input"]
            assert declared == {"representation": "original_identity", "receipt": boundary["selected_pdf_receipt"]}
            own_input, input_meta = order_service._resolve_reparse_llm_pdf_bytes(
                document_uri=own_uri, payload=normal["recognizer_result"],
            )
            assert own_input == b"own-pdf"
            assert own_input.selected_pdf_receipt == boundary["selected_pdf_receipt"]
            assert input_meta["used"] == "raw" and input_meta["fallback_reason"] is None
            assert normal["recognizer_result"]["auxiliary_pipeline_result"]["table_rows"][0][-1] == "999"
    assert [line["quantity_original"] for line in normal["lines"]] == [0, 12]
    assert [line["date"] for line in normal["lines"]] == [date(2026, 2, 15), date(2026, 2, 16)]
    result = persisted[0][1]
    assert result["_first_pass_recognizer"]["recognized_rows"] == rows
    assert result["_first_pass_recognizer"]["mapped_lines"] == [
        {**line, "date": line["date"].isoformat()} for line in normal["lines"]
    ]
    assert result["_first_pass_recognizer"]["execution_id"] == boundary["execution_id"]
    assert result["_first_pass_recognizer"]["input_sha256"] == sha256(b"own-pdf").hexdigest()
    assert persisted[0][2]["workflow_job_id"] == "real-job"
    assert evidence["id"] == "own-provider-evidence"
    assert payload.week_hint == WEEK
    record = ocr_evidence_service.build_evidence_run_record(
        "order", normal["recognizer_result"], producer_version="actual-provider-test", source="ocr-ingest-first-pass",
    )
    assert record["payload_json"]["_first_pass_recognizer"] == result["_first_pass_recognizer"]
    assert record["payload_json"]["raw_recognizer_payload"] == (original if provider != "pipeline" else pipeline)
    if provider != "pipeline":
        assert record["payload_json"]["auxiliary_pipeline_result"] == pipeline
        assert result["raw_recognizer_payload"] == original
        assert result["table_rows"] == rows
        assert result["auxiliary_pipeline_result"] == pipeline
        assert result["engine"] == provider
        assert len(calls) == 2
    else:
        assert calls == []
        assert "auxiliary_pipeline_result" not in result


@pytest.mark.parametrize("field,value", [(None, None), ("execution_id", "old"), ("input_sha256", "wrong"), ("week_hint", "2026-03@2026-03-01~2026-03-07")])
def test_actual_write_gate_rejects_foreign_recognizer_lineage(monkeypatch, field, value):
    pipeline = {"output_reference": "own-output", "template_id": "authored-template"}
    result = {"engine": "openai", "table_rows": [["0"]], "raw_recognizer_payload": {"table_rows": [["0"]]},
        "auxiliary_pipeline_result": pipeline, "_first_pass_recognizer": {
            "job_id": "real-job", "execution_id": "own", "input_sha256": "sha", "template_id": "authored-template",
            "week_hint": WEEK, "provider": "openai", "recognized_rows": [["0"]], "selected_input_reference": "own.pdf"}}
    if field is not None:
        result["_first_pass_recognizer"][field] = value
    monkeypatch.setattr(ocr_job_service, "validate_own_pipeline_result", lambda *args, **kwargs: pipeline)
    monkeypatch.setattr(ocr_job_service, "own_pipeline_delivery_for_output", lambda *args, **kwargs: {"input_sha256": "sha", "selected_input_reference": "own.pdf"})
    session = SimpleNamespace(get=lambda *args: SimpleNamespace(week_code=WEEK))
    with nonworkflow_execution_context({"job_id": "real-job", "order_id": "order", "execution_id": "own", "input_reference": "own.pdf"}):
        if field is None:
            assert ocr_job_service.validate_own_first_pass_recognizer(result, write_session=session) is result
        else:
            with pytest.raises(ocr_job_service.OwnPipelineLineageError):
                ocr_job_service.validate_own_first_pass_recognizer(result, write_session=session)


def test_hakodate_structure_only_is_not_yomitoku_quantity_success(monkeypatch, tmp_path):
    own_pdf = tmp_path / "own.pdf"
    own_pdf.write_bytes(b"own-pdf")
    own_uri = str(own_pdf)
    pipeline = {"engine": "yomitoku", "template_id": "authored-template", "output_reference": "own-output", "table_rows": [["999"]]}
    monkeypatch.setattr(ingest_worker, "validate_own_pipeline_result", lambda *args: pipeline)
    monkeypatch.setattr(ingest_worker, "extract_fax_data", lambda *args, **kwargs: fax_extractor.FaxExtractedData(
        None, [], [], [], ocr_provider="hakodate", provider_debug={"quantity_assignment_strategy": "hakodate"},
    ))
    monkeypatch.setattr(ingest_worker, "detect_table_grid", lambda *args: None)
    monkeypatch.setattr(ingest_worker, "_dump_ocr_debug", lambda *args: None)
    payload = IngestEmailPayload("own-mail", own_uri, datetime(2026, 2, 16), week_hint=WEEK)
    with nonworkflow_execution_context({"job_id": "real-job", "order_id": "order", "execution_id": "own", "input_reference": own_uri,
        "selected_pdf_receipt": {"storage_uri": own_uri, "sha256": sha256(own_pdf.read_bytes()).hexdigest(), "generation": None}}):
        mapped = ingest_worker.map_own_ingest_result(payload, b"own-pdf", pipeline, {
            "template": {"template_id": "authored-template"}, "preferred_template_id": "authored-template", "policy": {},
        })
    assert mapped["lines"] is None
    assert mapped["recognizer_result"]["error"] == "first_pass_own_recognizer_result_missing"
    assert mapped["recognizer_result"]["engine"] == "hakodate"
    assert mapped["recognizer_result"]["auxiliary_pipeline_result"] == pipeline
    assert mapped["recognizer_result"]["_first_pass_recognizer"]["recognized_rows"] == []
