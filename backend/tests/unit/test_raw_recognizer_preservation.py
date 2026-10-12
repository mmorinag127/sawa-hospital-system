"""HTTP/GCS boundary doubles with real adapters and owned SQL receipts, not live OCR."""

from copy import deepcopy
from contextlib import contextmanager
from datetime import datetime
from io import BytesIO
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from modernization_owned_pipeline_transport import install_owned_pipeline_transport
from test_h2_operator_repair_revision import repair_order  # noqa: F401
from test_h2_operator_repair_revision import connected_physical_pipeline  # noqa: F401
from test_modernization_ocr_permanent_failure import configure_pipeline, response_from_receipt
from src import db
from src.models.document import OrderDocument
from src.models.facility_template_version import FacilityTemplateVersion
from src.models.order import Order, OrderLine
from src.models.order_ocr_cache import OrderOcrCache
from src.models.order_ocr_evidence_run import OrderOcrEvidenceRun
from src.models.order_sheet_draft import OrderSheetDraft
from src.models.order_workflow_state import OrderWorkflowState
from src.services import fax_extractor as fax, openai_ocr_service, gemini_ocr_service
from src.services import ocr_job_service as jobs, ocr_pipeline_service as pipeline
from src.services import order_service as orders, workflow_attempt_guard_service as guard
from src.services import order_workflow_v2_service as workflow
from src.services import ocr_evidence_service
from src.services.storage_service import load_selected_pdf_bytes
from src.workers import ingest_worker
from src.workers.ingest_mail_adapter import IngestEmailPayload


FIELDS = ["date_mmdd", "daypart", "menu", "qty.regular_x"]
WEEK = "2026-10@2026-10-05~2026-10-11"


@pytest.fixture
def http_model(monkeypatch):
    calls = []

    def install(provider, raw):
        monkeypatch.setenv("OPENAI_API_KEY" if provider == "openai" else "GEMINI_API_KEY", "component-only")
        content = json.dumps(raw, ensure_ascii=False)
        response = (
            {"choices": [{"finish_reason": "stop", "message": {"content": content}}]}
            if provider == "openai" else
            {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": content}]}}]}
        )

        def urlopen(request, **kwargs):
            calls.append((provider, json.loads(request.data), kwargs))
            return BytesIO(json.dumps(response).encode())

        service = openai_ocr_service if provider == "openai" else gemini_ocr_service
        monkeypatch.setattr(service.urllib.request, "urlopen", urlopen)
        return service.run_openai_ocr if provider == "openai" else service.run_gemini_ocr

    return install, calls


@contextmanager
def owned_execution(repair_order, tmp_path, monkeypatch, *, table_rows=None):
    tmp_path.mkdir(parents=True, exist_ok=True)
    order_id, _, configured = repair_order
    with db.session_scope() as session:
        order = session.get(Order, order_id)
        orders._record_current_order_version(session, order, session.get(OrderDocument, order.current_document_id))
        uri, template_version = order.document_uri, order.template_version_id
    job_id = str(uuid4())
    _, created = jobs.create_job(job_id, uri, order_id=order_id, template_version_id=template_version)
    assert created
    boundary = guard.capture_nonworkflow_execution(job_id, order_id=order_id, input_reference=uri)
    assert boundary["selected_pdf_error"] is None
    configure_pipeline(SimpleNamespace(root=tmp_path, engine=db.engine), monkeypatch)

    def publish(key):
        receipt = jobs.get_job(job_id)["metrics"]["pipeline_delivery_receipt"]
        result = response_from_receipt(receipt)
        result.update(engine="yomitoku", template_id="own",
                      table_rows=deepcopy(table_rows) if table_rows is not None else [["2026-10-07", "昼", "own", "7"]],
                      page_correction_artifacts={"recognition_input": {
                          "representation": "original_identity", "receipt": boundary["selected_pdf_receipt"],
                      }})
        objects[key] = (json.dumps(result).encode(), {}, 71, "application/json")

    objects, events = install_owned_pipeline_transport(monkeypatch, on_missing=publish)
    with guard.nonworkflow_execution_context(boundary):
        pdf = load_selected_pdf_bytes(uri)
        result = pipeline.run_ocr_pipeline(pdf_bytes=pdf, job_id=job_id, facility_id="fixture",
                                          preferred_template_id="own", input_reference=uri, force_upload=True)
        assert jobs.validate_own_pipeline_result(result, result["output_reference"]) == result
        jobs.update_job(job_id, status="running", output_reference=result["output_reference"])
        template = deepcopy(configured["facility"]["fax_template"])
        template["main_ocr_row_fields"] = list(FIELDS)
        yield SimpleNamespace(order_id=order_id, job_id=job_id, boundary=boundary, pdf=pdf, uri=uri,
                              template=template, policy=configured["policy"], pipeline=result, events=events)


@pytest.fixture
def owned(repair_order, tmp_path, monkeypatch):
    with owned_execution(repair_order, tmp_path, monkeypatch) as execution:
        yield execution


def model_payload(value, *, nested=False):
    row = {"row_index": 0, "date_mmdd": "2026-10-07", "daypart": "昼", "menu": "own"}
    row.update({"qty": {"regular_x": value}} if nested else {"qty.regular_x": value})
    return {"facility_name": "fixture", "date_strings": ["2026-10-07"], "rows": [row],
            "uncertainty": {"raw": value, "confidence": None}}


@pytest.mark.parametrize("provider", ["openai", "gemini"])
@pytest.mark.parametrize("value,expected", [(0, 0), ("12", 12), ("２０", 20), ("12a", None), ("２０食", None), (False, None), (True, None)])
def test_real_adapter_fax_ingest_keeps_raw_and_rejects_uncertainty(owned, http_model, provider, value, expected):
    install, calls = http_model
    raw = model_payload(value)
    run = install(provider, raw)
    template = {**owned.template, "_force_main_ocr_provider": provider}
    display = run(pdf_bytes=owned.pdf, template=template)
    assert display["_ocr_raw_payload"] == raw
    assert type(display["_ocr_raw_payload"]["rows"][0]["qty.regular_x"]) is type(value)
    assert display["rows"][0]["qty.regular_x"] == (str(expected) if expected not in (None, 0) else "")
    display["rows"][0]["qty.regular_x"] = "presentation-only"
    assert display["_ocr_raw_payload"] == raw
    payload = IngestEmailPayload("model-component", owned.uri, datetime(2026, 10, 7),
                                 facility_hint="fixture", week_hint=WEEK)
    mapped = ingest_worker.map_own_ingest_result(payload, owned.pdf, owned.pipeline, {
        "template": template, "preferred_template_id": "own", "policy": owned.policy,
    })
    result = mapped["recognizer_result"]
    assert result["raw_recognizer_payload"] == raw
    assert result["table_rows"][0][-1] == str(value)
    assert result["_first_pass_recognizer"]["recognized_rows"] == result["table_rows"]
    assert result["auxiliary_pipeline_result"] == owned.pipeline
    if expected is None:
        assert mapped["lines"] is None
        assert result["status"] == "error" and result["error"] == "sheet_ocr_review_required"
        assert result["cell_issues"][0]["raw"] == str(value)
        assert result["_first_pass_recognizer"]["mapped_lines"] is None
        with db.session_scope() as session:
            evidence = orders._persist_ingest_pipeline_evidence(session, session.get(Order, owned.order_id), result, "error")
        with db.session_scope() as session:
            stored = session.get(OrderOcrEvidenceRun, evidence["id"])
            assert stored.status == "diagnostic_only"
            assert stored.payload_json["raw_recognizer_payload"] == raw
            assert stored.payload_json["_ocr_execution"]["disposition"] == "rejected"
    else:
        assert [line["quantity_original"] for line in mapped["lines"]] == [expected]
    assert len(calls) == 2


@pytest.mark.parametrize("provider", ["openai", "gemini"])
@pytest.mark.parametrize("value", [0, "12", "２０", "12a", "２０食", None, False, True])
def test_full_table_nested_fields_survive_actual_provider_and_bounded_merge(owned, http_model, provider, value):
    raw = model_payload(value, nested=True)
    http_model[0](provider, raw)
    template = {**owned.template, "_force_main_ocr_provider": provider,
                "llm_full_table_mode": True, "llm_full_table_expected_row_count": 1}
    extracted = fax.extract_fax_data(owned.pdf, template)
    assert extracted.recognizer_payload == raw
    assert extracted.provider_debug["returned_row_fields"] == {"0": sorted(FIELDS)}
    assert extracted.provider_debug["returned_row_indexes"] == [0]
    baseline = {"fields": FIELDS, "header": FIELDS, "rows": [["2026-10-07", "昼", "own", "9"]],
                "row_ids": ["saved"], "source": "saved_sheet"}
    original = deepcopy(baseline)
    rows, result = orders._validate_and_merge_reparse_full_table_candidate(
        baseline=baseline, candidate_rows=extracted.table_rows,
        candidate_returned_row_indexes=extracted.provider_debug["returned_row_indexes"],
        candidate_returned_field_map={0: set(extracted.provider_debug["returned_row_fields"]["0"])},
    )
    assert baseline == original
    if isinstance(value, bool) or value in ("12a", "２０食"):
        assert rows == [] and result["error"] == "llm_full_table_invalid_quantity_text"
        assert result["detail"]["invalid_quantity_cells"][0]["new_text"] == str(value)
    else:
        assert not result.get("error")
        assert rows[0][-1] == ("" if value is None else str(value))


@pytest.mark.parametrize("provider", ["openai", "gemini"])
@pytest.mark.parametrize("full_table", [False, True], ids=["quantity-only", "full-table"])
def test_actual_reparse_rejects_raw_uncertainty_before_parse(owned, http_model, provider, full_table):
    raw = model_payload("12a", nested=full_table)
    http_model[0](provider, raw)
    result, error = orders.reparse_order(
        owned.order_id, ocr_provider=provider, llm_assist=full_table, ocr_job_id=owned.job_id,
        resume_first_pass_payload=owned.pipeline,
        resume_first_pass_output_reference=owned.pipeline["output_reference"],
    )
    assert error == "sheet_ocr_review_required", (error, result)
    assert result is not None
    with db.session_scope() as session:
        evidence = session.query(OrderOcrEvidenceRun).filter_by(order_id=owned.order_id, source="reparse-pipeline-result").all()
        assert evidence
        assert any(item.payload_json.get("_reparse_recognizer_result", {}).get("raw_recognizer_payload") == raw
                   for item in evidence)
    assert jobs.get_job(owned.job_id)["status"] == "failed"


def selected_state(order_id):
    with db.session_scope() as session:
        state = session.get(OrderWorkflowState, order_id)
        snapshot = lambda row: deepcopy({column.key: getattr(row, column.key) for column in row.__table__.columns})
        return {
            "workflow": snapshot(state),
            "revision": state.revision, "draft_id": state.draft_id, "evidence_run_id": state.evidence_run_id,
            "draft": snapshot(session.get(OrderSheetDraft, state.draft_id)),
            "evidence": snapshot(session.get(OrderOcrEvidenceRun, state.evidence_run_id)),
            "cache": snapshot(session.get(OrderOcrCache, order_id)),
            "order": snapshot(session.get(Order, order_id)),
            "lines": [snapshot(row) for row in session.query(OrderLine).filter_by(order_id=order_id).order_by(OrderLine.id)],
        }


@pytest.mark.parametrize("provider", ["openai", "gemini"])
@pytest.mark.parametrize("full_table", [False, True], ids=["quantity-only", "full-table"])
@pytest.mark.parametrize("saved_quantity", [0, 7])
@pytest.mark.parametrize("invalid", ["12a", False], ids=["uncertain-text", "boolean"])
def test_rejected_reparse_preserves_real_operator_saved_draft(
    connected_physical_pipeline, tmp_path, monkeypatch, http_model, provider, full_table, saved_quantity, invalid,
):
    from PIL import Image
    from src.hakodate_best_method_runtime import render_best_method_overlay_all_facilities as runtime

    order_id, _, original, item, _ = connected_physical_pipeline
    with db.session_scope() as session:
        revision = session.get(OrderWorkflowState, order_id).revision
    period = original["order"]["week_code"].split("@", 1)[1].split("~")
    context, error = workflow.confirm_context(
        order_id=order_id, facility_id="fixture", week_start=period[0], week_end=period[1],
        template_id="own", expected_revision=revision,
    )
    assert error is None, (error, context)
    recognized = []

    def recognize(*, raw_rectified_bgr, regions, truth):
        recognized.append(deepcopy(regions))
        return [{**deepcopy(region), "truth": truth[region["sheet_cell"]],
                 "expected_digits": truth[region["sheet_cell"]]["expected_digits"],
                 "pred_digits": "0", "raw_text": "0", "score": 1.0,
                 "recognizer_accepted_candidate": {"normalized_digits": "0"}, "recognizer_score": 1.0}
                for region in regions], {}, Image.new("RGB", (80, 80), "white")

    monkeypatch.setattr(runtime, "_run_text_recognizer_records", recognize)
    setup = (order_id, revision, original["configured_input"])
    physical = item["original_physical_draft"]["rows"]
    with owned_execution(setup, tmp_path / "initial", monkeypatch,
                         table_rows=[list(row) + ["0"] for row in physical]) as initial:
        evidence, error = orders.rerun_ocr_evidence_only(
            order_id, job_id=initial.job_id, project_sheet=False, refresh_workflow=False,
        )
        assert error is None, (error, evidence)
        assert len(recognized) == 1
        assert [region["sheet_cell"] for region in recognized[0]] == item["original_expected_sheet_cells"]
        assert evidence["terminal_receipt"]["status"] == "done"
        with db.session_scope() as session:
            produced = session.get(OrderOcrEvidenceRun, evidence["id"])
            assert produced.payload_json["_ocr_execution"]["disposition"] == "pipeline_result"
            assert ocr_evidence_service.evidence_run_is_selectable(produced)
    mismatch_candidate = None
    if provider == "openai" and not full_table and invalid == "12a":
        with owned_execution(setup, tmp_path / "template-candidate", monkeypatch,
                             table_rows=[list(row) + ["0"] for row in physical]) as sibling:
            mismatch_candidate, error = orders.rerun_ocr_evidence_only(
                order_id, job_id=sibling.job_id, project_sheet=False, refresh_workflow=False,
            )
            assert error is None, (error, mismatch_candidate)
            assert mismatch_candidate["terminal_receipt"]["status"] == "done"
    with db.session_scope() as session:
        revision = session.get(OrderWorkflowState, order_id).revision
    selected, error = workflow.select_ocr_result(order_id, evidence["id"], expected_revision=revision)
    assert error is None, (error, selected)
    assert selected["selected_ocr_result_id"] == evidence["id"]
    assert selected["revision"] > revision
    with db.session_scope() as session:
        assert session.get(OrderWorkflowState, order_id).revision == selected["revision"]
    projected, error = workflow.build_sheet_from_selected_ocr(order_id)
    assert error is None, (error, projected)
    assert projected["blockers"] == []
    sheet = deepcopy(projected["sheet"])
    quantity = sheet["fields"].index("qty.regular_x")
    assert [row[quantity] for row in sheet["rows"]] == ["0"] * 3
    for row in sheet["rows"]:
        row[quantity] = str(saved_quantity)
    saved, error = workflow.save_sheet(
        order_id=order_id, sheet=sheet, edited_by="component-operator", expected_revision=selected["revision"],
    )
    assert error is None, (error, saved)
    before = selected_state(order_id)
    assert before["draft"]["warnings_json"] == []
    assert before["draft"]["draft_sheet_json"]["warnings"] == ["hakodate_ocr_evidence_assignment"]
    assert orders.apply_gate_service.has_clean_saved_draft(before["draft"])
    assert before["draft"]["draft_sheet_json"] == sheet
    assert before["draft"]["base_evidence_run_id"] == evidence["id"]
    assert before["revision"] == selected["revision"] + 1
    candidate_rows = [list(row) + [str(saved_quantity)] for row in physical]
    with owned_execution(setup, tmp_path / "candidate", monkeypatch, table_rows=candidate_rows) as candidate:
        assert guard.processing_start_read_input()["draft"]["id"] == before["draft_id"]
        raw = {"rows": [dict(zip(FIELDS, row, strict=True), row_index=index)
                        for index, row in enumerate(candidate_rows)]}
        for row in raw["rows"]:
            row["qty.regular_x"] = invalid
            if full_table:
                row["qty"] = {"regular_x": row.pop("qty.regular_x")}
        http_model[0](provider, raw)
        rejected, error = orders.reparse_order(
            order_id, ocr_provider=provider, llm_assist=full_table, ocr_job_id=candidate.job_id,
            resume_first_pass_payload=candidate.pipeline,
            resume_first_pass_output_reference=candidate.pipeline["output_reference"],
        )
        assert error == "sheet_ocr_review_required", (error, rejected)
        assert jobs.get_job(candidate.job_id)["status"] == "failed"
        with db.session_scope() as session:
            evidence = session.query(OrderOcrEvidenceRun).filter_by(order_id=order_id, source="reparse-pipeline-result").all()
            rejected_evidence = [row for row in evidence if row.payload_json["_ocr_execution"]["job_id"] == candidate.job_id]
            assert len(rejected_evidence) == 1
            assert rejected_evidence[0].status == "ready"
            assert rejected_evidence[0].payload_json["_reparse_recognizer_result"]["raw_recognizer_payload"] == raw
            assert rejected_evidence[0].payload_json["_ocr_execution"]["disposition"] == "rejected"
            assert not ocr_evidence_service.evidence_run_is_selectable(rejected_evidence[0])
            candidate_evidence_id = rejected_evidence[0].id
    assert selected_state(order_id) == before
    _, conflict = workflow.select_ocr_result(
        order_id, candidate_evidence_id, expected_revision=before["revision"] - 1,
    )
    assert conflict == "workflow_revision_conflict"
    assert selected_state(order_id) == before
    selected_candidate, selection_error = workflow.select_ocr_result(
        order_id, candidate_evidence_id, expected_revision=before["revision"],
    )
    assert selection_error == "ocr_result_execution_lineage_mismatch", selected_candidate
    assert selected_candidate["selected_ocr_result_id"] == before["evidence_run_id"]
    assert selected_candidate["saved_sheet_id"] == before["draft_id"]
    assert selected_candidate["revision"] == before["revision"]
    assert selected_candidate["blockers"] == before["workflow"]["blockers_json"]
    assert selected_state(order_id) == before
    if mismatch_candidate is not None:
        # An intentionally invalid FK target is not a new canonical template.
        with db.session_scope() as session:
            current = session.get(FacilityTemplateVersion, before["workflow"]["template_version_id"])
            wrong_id = str(uuid4())
            session.add(FacilityTemplateVersion(
                id=wrong_id, facility_id=current.facility_id, version=current.version,
                status="invalid", columns_json=deepcopy(current.columns_json),
                template_digest=f"invalid:{wrong_id}",
            ))
        for template_id in (wrong_id, None):
            with db.session_scope() as session:
                candidate_row = session.get(OrderOcrEvidenceRun, mismatch_candidate["id"])
                candidate_row.template_version_id = template_id
                assert candidate_row.payload_json["_ocr_execution"]["disposition"] == "pipeline_result"
                assert ocr_evidence_service.evidence_run_is_selectable(candidate_row)
            for expected_revision, expected_error in (
                (before["revision"] - 1, "workflow_revision_conflict"),
                (before["revision"], "template_version_mismatch"),
            ):
                refused, error = workflow.select_ocr_result(
                    order_id, mismatch_candidate["id"], expected_revision=expected_revision,
                )
                assert error == expected_error, refused
                assert refused["selected_ocr_result_id"] == before["evidence_run_id"]
                assert refused["saved_sheet_id"] == before["draft_id"]
                assert refused["revision"] == before["revision"]
                assert selected_state(order_id) == before


@pytest.mark.parametrize("raw", [None, [], "{\"rows\": []}"])
def test_missing_parsed_raw_is_not_recovered_from_diagnostic_text(raw):
    with pytest.raises(ValueError, match="^ocr_raw_recognizer_payload_required$"):
        fax.require_raw_recognizer_payload(raw)


def test_generic_quantity_zero_and_uncertainty_share_strict_validation():
    assert orders._canonical_quantity_cell_issues([[0], ["２０"]], ["qty"]) == []
    issues = orders._canonical_quantity_cell_issues([["12a"], ["２０食"], [False], [True]], ["qty"])
    assert [issue["raw"] for issue in issues] == ["12a", "２０食", "False", "True"]


@pytest.mark.parametrize("provider", ["openai", "gemini"])
@pytest.mark.parametrize("raw", [{}, {"rows": []}], ids=["missing-rows", "empty-rows"])
@pytest.mark.parametrize("path", ["ingest", "quantity-only", "full-table"])
def test_empty_model_rows_cannot_adopt_auxiliary_identity_or_quantities(owned, http_model, provider, raw, path):
    http_model[0](provider, raw)
    if path == "ingest":
        payload = IngestEmailPayload("empty-component", owned.uri, datetime(2026, 10, 7),
                                     facility_hint="fixture", week_hint=WEEK)
        mapped = ingest_worker.map_own_ingest_result(payload, owned.pdf, owned.pipeline, {
            "template": {**owned.template, "_force_main_ocr_provider": provider},
            "preferred_template_id": "own", "policy": owned.policy,
        })
        result = mapped["recognizer_result"]
        assert mapped["lines"] is None
        assert result["status"] == "error"
        assert result["error"] == "first_pass_own_recognizer_result_missing"
        assert result["raw_recognizer_payload"] == raw
        assert result["_first_pass_recognizer"]["recognized_rows"] == []
        with db.session_scope() as session:
            evidence = orders._persist_ingest_pipeline_evidence(session, session.get(Order, owned.order_id), result, "error")
        with db.session_scope() as session:
            stored = session.get(OrderOcrEvidenceRun, evidence["id"])
            assert stored.status == "diagnostic_only"
            assert stored.payload_json["raw_recognizer_payload"] == raw
            assert stored.payload_json["_ocr_execution"]["disposition"] == "rejected"
    else:
        result, error = orders.reparse_order(
            owned.order_id, ocr_provider=provider, llm_assist=path == "full-table", ocr_job_id=owned.job_id,
            resume_first_pass_payload=owned.pipeline,
            resume_first_pass_output_reference=owned.pipeline["output_reference"],
        )
        assert error == "lines_empty", (error, result)
        assert result is not None
        assert jobs.get_job(owned.job_id)["status"] == "failed"
        with db.session_scope() as session:
            evidence = session.query(OrderOcrEvidenceRun).filter_by(order_id=owned.order_id, source="reparse-pipeline-result").all()
            assert any(item.payload_json.get("_reparse_recognizer_result", {}).get("raw_recognizer_payload") == raw
                       for item in evidence)
