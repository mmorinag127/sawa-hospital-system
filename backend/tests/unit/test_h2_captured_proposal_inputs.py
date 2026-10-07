from contextlib import contextmanager
from copy import deepcopy
from hashlib import sha256
from types import SimpleNamespace

import pytest

from src.services import order_service, order_workflow_v2_service as workflow, storage_service
from src.services.workflow_attempt_guard_service import nonworkflow_execution_context
from src.services import config_service


@contextmanager
def fake_session():
    yield SimpleNamespace()


@pytest.fixture
def selected(tmp_path):
    path = tmp_path / "own.pdf"
    data = b"own original with 0"
    path.write_bytes(data)
    receipt = {"storage_uri": str(path), "sha256": sha256(data).hexdigest(), "generation": None}
    return path, data, receipt


def test_declared_noop_reparse_uses_verified_original_without_corrected_fallback(selected):
    path, data, receipt = selected
    payload = {"page_correction_artifacts": {"recognition_input": {
        "representation": "original_identity", "receipt": deepcopy(receipt),
    }}}
    with nonworkflow_execution_context({"selected_pdf_receipt": receipt}):
        result, meta = order_service._resolve_reparse_llm_pdf_bytes(document_uri=str(path), payload=payload)
        assert result == data
        assert result.selected_pdf_receipt == receipt
        assert meta["used"] == "raw"
        with pytest.raises(ValueError, match="corrected_pdf_receipt_required"):
            order_service._resolve_llm_review_pdf_bytes(document_uri=str(path), payload=payload, requested_variant="corrected")


def test_declared_corrected_input_retains_parent_receipt(selected, tmp_path):
    path, data, receipt = selected
    child = tmp_path / "corrected.pdf"
    corrected = b"actual corrected with 0"
    child.write_bytes(corrected)
    derived = {"storage_uri": str(child), "generation": None, "sha256": sha256(corrected).hexdigest(), "source_sha256": receipt["sha256"]}
    payload = {"page_correction_artifacts": {"corrected_pdf_receipt": derived, "corrected_pdf_uri": str(child),
        "recognition_input": {"representation": "corrected", "receipt": deepcopy(derived)}}}
    with nonworkflow_execution_context({"selected_pdf_receipt": receipt}):
        result, meta = order_service._resolve_reparse_llm_pdf_bytes(document_uri=str(path), payload=payload)
        assert result == corrected
        assert result.selected_pdf_receipt["parent"] == receipt
        assert meta["used"] == "corrected"


def test_missing_recognition_declaration_never_substitutes_raw(selected):
    path, _, receipt = selected
    with nonworkflow_execution_context({"selected_pdf_receipt": receipt}):
        with pytest.raises(ValueError, match="recognition_input_receipt_required"):
            order_service._resolve_reparse_llm_pdf_bytes(document_uri=str(path), payload={})


@pytest.mark.parametrize("expected", [None, 6])
def test_sync_stale_or_missing_token_calls_no_provider(monkeypatch, expected):
    monkeypatch.setattr(workflow, "session_scope", fake_session)
    monkeypatch.setattr(workflow, "_get_workflow_readonly", lambda *_: SimpleNamespace(revision=7))
    monkeypatch.setattr(order_service, "_capture_synchronous_cache_write_input", lambda _: {"workflow_revision": 7})
    monkeypatch.setattr(workflow.workflow_v2_sheet_review_service, "propose_auto_sheet_edits", lambda **_: pytest.fail("provider must not run"))
    result, error = workflow.propose_sheet_auto_edit(order_id="own", sheet={"quantity": 0}, expected_revision=expected)
    assert result is None
    assert error == (workflow.WORKFLOW_REVISION_REQUIRED if expected is None else workflow.WORKFLOW_REVISION_CONFLICT)


def test_sync_proposal_uses_start_pdf_and_rejects_changed_end_input(monkeypatch, selected):
    path, data, receipt = selected
    start = {"workflow_revision": 7, "selected_pdf_receipt": receipt}
    calls = []
    monkeypatch.setattr(order_service, "_capture_synchronous_cache_write_input", lambda _: deepcopy(start))
    monkeypatch.setattr(workflow, "session_scope", fake_session)
    monkeypatch.setattr(workflow, "_get_workflow_readonly", lambda *_: SimpleNamespace(revision=7))
    monkeypatch.setattr(workflow, "_get_order_or_error", lambda *_: (SimpleNamespace(id="own", document_uri=str(path)), None))
    monkeypatch.setattr(workflow, "_get_workflow", lambda *_: None)
    checks = iter([True, False])
    monkeypatch.setattr(order_service, "_synchronous_cache_write_fence", lambda *_: next(checks))
    def render(**kwargs):
        assert kwargs["pdf_bytes"] == data
        assert kwargs["pdf_bytes"].selected_pdf_receipt == receipt
        return b"png"
    monkeypatch.setattr(workflow, "render_pdf_to_png_bytes", render)
    def propose(**kwargs):
        calls.append(kwargs["sheet"])
        return {"quantity": 0}
    monkeypatch.setattr(workflow.workflow_v2_sheet_review_service, "propose_auto_sheet_edits", propose)
    result, error = workflow.propose_sheet_auto_edit(order_id="own", sheet={"quantity": 0}, expected_revision=7)
    assert calls == [{"quantity": 0}]
    assert result is None and error == workflow.WORKFLOW_REVISION_CONFLICT


def test_pdf_replaced_before_proposal_render_stops_without_provider(monkeypatch, selected):
    path, _, receipt = selected
    path.write_bytes(b"replacement")
    monkeypatch.setattr(workflow, "render_pdf_to_png_bytes", lambda **_: pytest.fail("unverified rendering"))
    image, meta = workflow._render_order_fax_page_for_ai(str(path), selected_pdf_receipt=receipt)
    assert image is None
    assert meta["error"] == "selected_pdf_bytes_changed"


def test_async_stale_token_never_calls_provider_or_write(monkeypatch):
    job = {"job_id": "SAE-own", "start_input": {"workflow_revision": 7},
           "request": {"sheet_hash": workflow._stable_json_hash({"quantity": 0}), "model": None, "use_llm": True}}
    monkeypatch.setattr(workflow, "session_scope", fake_session)
    monkeypatch.setattr(workflow, "_get_workflow", lambda *_: object())
    monkeypatch.setattr(workflow, "_workflow_meta", lambda _: {workflow.SHEET_AUTO_EDIT_JOB_META_KEY: job})
    monkeypatch.setattr(workflow, "propose_sheet_auto_edit", lambda **_: pytest.fail("stale provider"))
    monkeypatch.setattr(order_service, "_synchronous_cache_write_fence", lambda *_: pytest.fail("stale write"))
    workflow.run_sheet_auto_edit_job(order_id="own", job_id="SAE-own", sheet={"quantity": 0}, expected_revision=6)
    assert job["start_input"]["workflow_revision"] == 7


def test_owned_policy_and_template_do_not_follow_changed_current_cache(monkeypatch):
    config = {"facility": {"facility_id": "own", "fax_template": {"quantity": 0, "unknown": None}},
              "policy": {"quantity_rules": {"minimum": 0}, "date_policy": "captured"}}
    execution = {"order_id": "own-order", "orders": {"own-order": {"configured_input": deepcopy(config)}}}
    monkeypatch.setattr(config_service, "_load_cached_ingest_policy", lambda: pytest.fail("late current policy"))
    monkeypatch.setattr(config_service, "load_facility_master", lambda: pytest.fail("late current template"))
    monkeypatch.setattr(config_service, "_validate_authored_facility_config", lambda value, _: value)
    with nonworkflow_execution_context(execution):
        assert config_service.load_ingest_policy() == config["policy"]
        assert config_service.get_authored_facility_config("own") == config["facility"]
        returned = config_service.load_ingest_policy()
        returned["quantity_rules"]["minimum"] = 99
        assert config_service.load_ingest_policy()["quantity_rules"]["minimum"] == 0


@pytest.mark.parametrize("decision", ["approved_estimate", "manual_override"])
def test_quad_replaced_pdf_denies_adoption(monkeypatch, selected, decision):
    path, _, pdf = selected
    path.write_bytes(b"replaced")
    order = SimpleNamespace(id="own")
    state = SimpleNamespace(revision=7)
    physical = [{"menu_date": "2026-10-07", "daypart": "lunch", "slot_index": 0, "name": "own"}]
    start = {"workflow_revision": 7, "configured_input": {"facility": {"fax_template": {"template_id": "own", "page": 1, "columns": [{"role": "menu", "index": 0}]}}, "policy": {"quantity": 0}}, "physical_menu_entries": physical, "order": {"facility_code": "own", "week_code": "2026-10"}}
    quad = [[0, 0], [10, 0], [10, 10], [0, 10]]
    receipt = {"workflow_revision": 7, "input_digest": workflow._stable_json_hash(start),
               "configured_input_digest": workflow._stable_json_hash(start["configured_input"]), "order_input_identity": start["order"],
               "selected_pdf_receipt": pdf, "suggested_quad_px": quad}
    receipt["physical_menu_entries"] = deepcopy(start["physical_menu_entries"])
    receipt["geometry_source_digest"] = order_service.geometry_source_digest(pdf, start["configured_input"], start["order"], physical_entries=start["physical_menu_entries"])
    monkeypatch.setattr(order_service, "_build_position_menu_entries_safe", lambda *args: deepcopy(physical))
    monkeypatch.setattr(workflow, "session_scope", fake_session)
    monkeypatch.setattr(workflow, "_get_order_or_error", lambda *_: (order, None))
    monkeypatch.setattr(workflow, "_get_workflow", lambda *_: state)
    monkeypatch.setattr(workflow, "capture_reparse_read_input", lambda *_: deepcopy(start))
    monkeypatch.setattr(workflow, "capture_selected_pdf_receipt", lambda *_: pdf)
    def verify(*_):
        storage_service.load_selected_pdf_bytes(str(path), pdf)
        return True
    monkeypatch.setattr(workflow, "verify_selected_pdf_receipt", verify)
    monkeypatch.setattr(workflow, "_write_workflow_meta", lambda *_: pytest.fail("replaced source adoption"))
    result, error = workflow.save_quad_review_decision.__wrapped__("own", decision=decision, quad_px=quad, source_receipt=receipt)
    assert result is None and error == "selected_pdf_bytes_changed"


def test_changed_authored_policy_blocks_shared_write_before_sql_adoption(monkeypatch):
    session = SimpleNamespace(get=lambda *_: SimpleNamespace(facility_code="own"))
    start = {"order_id": "own-order", "configured_input": {"policy": {"minimum": 0}}}
    monkeypatch.setattr(config_service, "capture_authored_processing_config", lambda _: {"policy": {"minimum": 1}})
    monkeypatch.setattr(order_service, "lock_current_synchronous_cache_write", lambda *_, **__: pytest.fail("changed policy adoption"))
    assert not order_service._synchronous_cache_write_fence(session, "own-order", start)
    assert start["configured_input"]["policy"]["minimum"] == 0


def test_async_completion_uses_own_start_snapshot_and_preserves_zero(monkeypatch):
    sheet = {"quantity": 0}
    start = {"workflow_revision": 7, "configured_input": {"policy": {"minimum": 0}}}
    job = {"job_id": "SAE-own", "start_input": deepcopy(start),
           "request": {"sheet_hash": workflow._stable_json_hash(sheet), "model": "own-model", "use_llm": True}}
    state = SimpleNamespace(revision=7)
    meta = {workflow.SHEET_AUTO_EDIT_JOB_META_KEY: job}
    monkeypatch.setattr(workflow, "session_scope", fake_session)
    monkeypatch.setattr(workflow, "_get_workflow", lambda *_: state)
    monkeypatch.setattr(workflow, "_get_order_or_error", lambda *_: (SimpleNamespace(id="own"), None))
    monkeypatch.setattr(workflow, "_workflow_meta", lambda _: deepcopy(meta))
    monkeypatch.setattr(workflow, "_require_workflow_revision", lambda *_: None)
    seen = []
    def fence(*args, **kwargs):
        seen.append(deepcopy(args[2]))
        return True
    monkeypatch.setattr(order_service, "_synchronous_cache_write_fence", fence)
    def propose(**kwargs):
        assert kwargs["start_input"] == start
        assert kwargs["expected_revision"] == 7
        assert kwargs["model"] == "own-model"
        return {"quantity": 0}, None
    monkeypatch.setattr(workflow, "propose_sheet_auto_edit", propose)
    monkeypatch.setattr(workflow, "_write_workflow_meta", lambda _, value: meta.update(value))
    monkeypatch.setattr(workflow, "_finalize_workflow_mutation", lambda *_: None)
    workflow.run_sheet_auto_edit_job(order_id="own", job_id="SAE-own", sheet=sheet, model="own-model", expected_revision=7)
    result = meta[workflow.SHEET_AUTO_EDIT_JOB_META_KEY]
    assert result["status"] == "done"
    assert result["result"] == {"quantity": 0}
    assert result["start_input"] == start
    assert seen == [start]


@pytest.mark.parametrize("wait", [True, False])
def test_api_sync_async_forward_same_explicit_if_match(monkeypatch, wait):
    from fastapi import BackgroundTasks, HTTPException
    from src.api import orders
    seen = []
    def reject(**kwargs):
        seen.append(kwargs["expected_revision"])
        return None, workflow.WORKFLOW_REVISION_CONFLICT
    monkeypatch.setattr(workflow, "propose_sheet_auto_edit" if wait else "start_sheet_auto_edit_job", reject)
    tasks = BackgroundTasks()
    body = orders.WorkflowV2SheetAutoEditBody(sheet={"quantity": 0}, model="own-model", use_llm=True)
    with pytest.raises(HTTPException) as raised:
        orders.propose_order_workflow_v2_sheet_auto_edit("own", body, tasks, wait=wait, expected_revision=6)
    assert raised.value.status_code == 409
    assert seen == [6]
    assert tasks.tasks == []


@pytest.mark.parametrize("decision", ["approved_estimate", "manual_override"])
def test_valid_quad_own_source_adoption_preserves_zero_coordinates(isolated_formal_schema, monkeypatch, selected, decision):
    from src.db import session_scope
    from src.models.order import Order
    from src.models.document import OrderDocument
    from src.models.uploaded_pdf import UploadedPdf
    from src.models.order_workflow_state import OrderWorkflowState
    from src.services.workflow_attempt_guard_service import capture_reparse_read_input, capture_selected_pdf_receipt
    path, data, _ = selected
    configured = {"facility": {"facility_id": "own", "fax_template_id": "own", "fax_template": {"template_id": "own", "page": 1, "columns": [
        {"column_id": "date", "index": 0, "source_index": 0, "role": "date"},
        {"column_id": "daypart", "index": 1, "source_index": 1, "role": "daypart"},
        {"column_id": "menu", "index": 2, "source_index": 2, "role": "menu"},
        {"column_id": "quantity", "index": 3, "source_index": 3, "role": "quantity", "diet_type": "regular", "area_id": "x"},
    ]}}, "policy": {"quantity_rules": {"zero_as_empty": False}}}
    config_service._validate_authored_facility_config(configured["facility"], "own")
    physical = [{"menu_date": "2026-10-07", "daypart": "lunch", "slot_index": 0, "name": "own"}]
    monkeypatch.setattr(config_service, "capture_authored_processing_config", lambda *args: deepcopy(configured))
    monkeypatch.setattr(order_service, "_build_position_menu_entries_safe", lambda *args: deepcopy(physical))
    with session_scope() as session:
        order = Order(id="own", facility_code="own", week_code="2026-10@2026-10-05~2026-10-11", current_document_id="own-document", document_uri=str(path), message_id="own-message")
        session.add(order)
        session.add(OrderDocument(id="own-document", storage_uri=str(path), source_email_id="own-message"))
        session.add(UploadedPdf(id="own-pdf", storage_uri=str(path), message_id="own-message", original_filename="own.pdf", content_sha256=sha256(data).hexdigest()))
        session.add(OrderWorkflowState(order_id="own", revision=7, state="context_confirmed"))
        session.flush()
        start = capture_reparse_read_input(session, order)
        pdf = capture_selected_pdf_receipt(session, order)
    quad = [[0, 0], [10, 0], [10, 10], [0, 10]]
    identity = {key: start["order"][key] for key in ("facility_code", "week_code")}
    receipt = {"workflow_revision": 7, "input_digest": workflow._stable_json_hash(start),
               "configured_input_digest": workflow._stable_json_hash(start["configured_input"]), "order_input_identity": identity,
               "selected_pdf_receipt": pdf, "suggested_quad_px": quad}
    receipt["physical_menu_entries"] = deepcopy(start["physical_menu_entries"])
    receipt["geometry_source_digest"] = order_service.geometry_source_digest(pdf, start["configured_input"], identity, physical_entries=start["physical_menu_entries"])
    meta = {}
    monkeypatch.setattr(workflow, "_workflow_meta", lambda _: deepcopy(meta))
    monkeypatch.setattr(workflow, "_write_workflow_meta", lambda _, value: meta.update(value))
    monkeypatch.setattr(workflow, "_finalize_workflow_mutation", lambda *_: None)
    monkeypatch.setattr(workflow, "_serialize_workflow", lambda *_, **__: deepcopy(meta))
    result, error = workflow.save_quad_review_decision.__wrapped__("own", decision=decision, quad_px=quad, source_receipt=receipt)
    assert error is None
    assert result["quad_override"]["quad_px"] == quad
    assert result["quad_override"]["source_receipt"] == receipt
