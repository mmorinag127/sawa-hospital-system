"""Owned SQL/JSON revision components, not HTTP/auth/native recognition proof.

The repair fixture supplies the real dated projection and SQL menu. Synthetic
external OCR records are persisted/selected through the actual owned services.
Only explicitly named delegation cases replace the canonical context getter;
those cases establish exception/absence handling, not canonical validation.
"""

from contextlib import contextmanager
from copy import deepcopy
import json
import os
from pathlib import Path
import re
from uuid import uuid4

import pytest
from sqlalchemy import event

from test_h2_operator_repair_revision import (
    repair_order as repair_order,
    _attach_provider_artifact_packet,
    _owned_artifact_fixture,
)
from test_modernization_workflow_surface_parity import database_snapshot
from src import db
from src.models.ocr_job import OcrJob
from src.models.order import Order
from src.models.order_current_state import OrderCurrentState
from src.models.order_ocr_evidence_run import OrderOcrEvidenceRun
from src.models.order_sheet_draft import OrderSheetDraft
from src.models.order_workflow_state import OrderWorkflowState
from src.services import ocr_evidence_service, order_current_state_service, order_service
from src.services import order_workflow_v2_service as workflow
from src.services import workflow_attempt_guard_service as guard


def revision(order_id):
    with db.session_scope() as session:
        return session.get(OrderWorkflowState, order_id).revision


@contextmanager
def no_writes():
    before = database_snapshot()
    with db.session_scope() as session:
        engine = session.get_bind()
    writes = []

    def observe(_conn, _cursor, statement, _params, context, _many):
        if (context.isinsert or context.isupdate or context.isdelete
                or re.match(r"\s*(INSERT|UPDATE|DELETE|REPLACE|CREATE|ALTER|DROP)\b", statement, re.I)):
            writes.append(statement)

    event.listen(engine, "before_cursor_execute", observe)
    try:
        yield
        assert database_snapshot() == before
        assert writes == []
    finally:
        event.remove(engine, "before_cursor_execute", observe)


@pytest.fixture
def selected_repair(repair_order):
    order_id, initial_revision, _configured = repair_order
    context, error = workflow.confirm_context(
        order_id=order_id, facility_id="fixture", week_start="2026-10-05",
        week_end="2026-10-11", template_id="own", expected_revision=initial_revision,
    )
    assert error is None, (error, context)
    job_id, attempt = str(uuid4()), str(uuid4())
    current_revision = revision(order_id)
    with db.session_scope() as session:
        order = session.get(Order, order_id)
        captured = guard.capture_reparse_read_input(session, order)
        pdf = guard.capture_selected_pdf_receipt(session, order)
        version = order.template_version_id
        session.add(OcrJob(
            id=job_id, order_id=order_id, template_version_id=version,
            status="running", input_reference=pdf["storage_uri"],
            metrics={"workflow_attempt_id": attempt, "reparse_read_input": captured,
                     "selected_pdf_receipt": pdf},
        ))
    variables = (
        (order_service._workflow_ocr_job_id, job_id),
        (order_service._workflow_ocr_order_id, order_id),
        (order_service._workflow_ocr_attempt_id, attempt),
        (order_service._workflow_ocr_revision, current_revision),
    )
    tokens = [(var, var.set(value)) for var, value in variables]
    try:
        with guard.workflow_execution_context(job_id, order_id, attempt, current_revision):
            source = order_service._hakodate_owned_source(captured["configured_input"], pdf)
            targets, records = _owned_artifact_fixture(source, 0)
            payload = {
                "hakodate_repair_source_receipt": deepcopy(source),
                "hakodate_preprocessing": {"target_cell_map": targets},
                "hakodate_ocr_evidence": {"records": records},
                "hakodate_canonical_pipeline": {
                    "status": "ready", "version": order_service.HAKODATE_CANONICAL_PIPELINE_VERSION,
                    "expected_sheet_cells": deepcopy(source["expected_sheet_cells"]),
                },
            }
            _attach_provider_artifact_packet(payload)
            evidence = ocr_evidence_service.persist_evidence_run(
                order_id, payload, template_version_id=version, status="ready",
                source="synthetic-external-recognizer", producer_version="owned-revision-unit/v1",
                workflow_job_id=job_id, workflow_attempt_id=attempt,
                expected_workflow_revision=current_revision,
            )
            assert evidence is not None
    finally:
        for var, token in reversed(tokens):
            var.reset(token)
    selected, error = workflow.select_ocr_result(
        order_id, evidence["id"], expected_revision=revision(order_id),
    )
    assert error is None, (error, selected)
    sheet, error = order_service._build_hakodate_weekly_menu_base_sheet(
        order_id, processing_read_input=captured,
    )
    assert error is None, error
    assert sheet["rows"]
    yield order_id, sheet, evidence


def save(order_id, sheet):
    original = deepcopy(sheet)
    result, error = workflow.save_sheet(
        order_id=order_id, sheet=sheet, edited_by="synthetic-revision-operator",
        expected_revision=revision(order_id),
    )
    assert error is None, (error, result)
    assert sheet == original
    saved, error = workflow.get_saved_sheet(order_id)
    assert error is None, error
    assert saved["sheet"] == original
    return saved["saved_sheet_id"]


@pytest.mark.parametrize("quantity", [0, None, ""], ids=["zero", "null", "blank"])
def test_real_repeated_save_keeps_latest_revision_despite_old_state_json(selected_repair, quantity):
    order_id, sheet, evidence = selected_repair
    sheet = deepcopy(sheet)
    quantity_index = sheet["fields"].index("qty.regular_x")
    sheet["rows"][0][quantity_index] = quantity
    first = save(order_id, sheet)
    context = order_service.get_current_sheet_context(order_id)
    assert context["draft_id"] == context["current_sheet_revision_id"] == first
    assert order_current_state_service.persist_current_state(
        order_id=order_id, state_json=context, draft_id=first,
        evidence_run_id=evidence["id"], template_version_id=context["template_version_id"],
    ) is not None
    for _ in range(2):
        latest = save(order_id, sheet)
        assert latest != first
        with db.session_scope() as session:
            state = session.get(OrderCurrentState, order_id)
            assert state.draft_id is None
            assert state.state_json["current_sheet_revision_id"] == first
            historical = session.get(OrderSheetDraft, first)
            assert historical.draft_sheet_json == sheet
            assert historical.base_evidence_run_id == evidence["id"]
            assert session.get(OrderOcrEvidenceRun, evidence["id"]).payload_json == evidence["payload_json"]
        stale_payload = deepcopy(context)
        before_payload = deepcopy(stale_payload)
        with no_writes():
            assert order_service._current_sheet_revision_id(
                order_id=order_id, payload=stale_payload,
            ) == latest
            summary = order_service.get_order_review_summary(order_id, cached_payload=evidence["payload_json"])
            assert summary["current_sheet_revision_id"] == latest
            current = order_service.get_current_sheet_context(order_id)
            assert current["draft_id"] == current["current_sheet_revision_id"] == latest
            assert current["draft_payload"]["rows"] == sheet["rows"]
        assert stale_payload == before_payload
    order_current_state_service.delete_current_state(order_id)
    with no_writes():
        assert order_service._current_sheet_revision_id(order_id=order_id) == latest


def test_real_caller_fallback_boundaries_do_not_reenter_resolver(selected_repair):
    order_id, sheet, _evidence = selected_repair
    latest = save(order_id, sheet)
    with no_writes():
        context = order_service.get_current_sheet_context(order_id)
        assert order_service.candidate_sheet_state(order_id, candidate_evidence_run_id=None, current_sheet_context=context)["current_sheet_revision_id"] == latest
        draft = context["draft_record"]
        assert order_service.flatten_current_sheet_payload(order_id, draft, include_meta=False)["current_sheet_revision_id"] == latest
        assert order_service._candidate_preview_signature(order_id, draft) is not None
        assert order_service.get_order_review_summary(order_id, current_sheet_context=context)["current_sheet_revision_id"] == latest
        augmented = order_service._augment_sheet_review_payload(order_id=order_id, payload={})
        assert augmented["current_sheet_revision_id"] == latest
        assert order_service._sheet_revision_conflict_detail(
            order_id=order_id, expected_revision_id=latest, payload={"current_sheet_revision_id": "old"},
        ) is None


@pytest.mark.parametrize("fault", ["delete", "change"])
def test_real_authored_version_failure_cannot_borrow_old_revision(selected_repair, fault):
    order_id, sheet, _evidence = selected_repair
    old = save(order_id, sheet)
    path = Path(os.environ["FACILITY_MASTER_PATH"])
    original = path.read_bytes()
    changed = json.loads(original)
    if fault == "delete":
        changed["facilities"][0]["versions"] = []
    else:
        changed["facilities"][0]["versions"][0]["facility"]["facility_name"] = "changed authored version"
    try:
        path.write_text(json.dumps(changed, ensure_ascii=False), encoding="utf-8")
        with no_writes():
            context = order_service.get_current_sheet_context(order_id)
            assert context["draft_payload"] is None
            assert context["blockers"]
            assert order_service._current_sheet_revision_id(
                order_id=order_id, payload={"current_sheet_revision_id": old},
            ) is None
    finally:
        path.write_bytes(original)


@pytest.mark.parametrize("draft_id", ["checked-draft", None], ids=["id", "digest"])
def test_delegation_only_normal_artifact_revision_and_input_nonmutation(monkeypatch, draft_id):
    context = {"draft_id": draft_id, "draft_payload": {"rows": [[0, None, ""]]},
               "fields": ["a", "b", "c"], "header": ["A", "B", "C"],
               "rows": [[0, None, ""]], "row_ids": ["physical"]}
    before = deepcopy(context)
    payload = {"current_sheet_revision_id": "untrusted"}
    calls = []

    def canonical(order_id, **kwargs):
        calls.append((order_id, kwargs))
        return context

    monkeypatch.setattr(order_service, "get_current_sheet_context", canonical)
    expected = order_service._current_sheet_revision_id_from_artifact(
        draft_id=draft_id, fields=context["fields"], header=context["header"],
        rows_payload=context["rows"], row_ids=context["row_ids"],
    )
    assert order_service._current_sheet_revision_id(order_id=" order ", payload=payload) == expected
    assert calls == [("order", {"refresh_draft_from_semantic": True,
                               "upgrade_generic_from_sheet": True, "backfill_from_revision": False})]
    assert context == before
    assert payload == {"current_sheet_revision_id": "untrusted"}


@pytest.mark.parametrize("context", [None, {"draft_payload": None, "fields": [], "rows": [],
                                          "blockers": ["template_version_mismatch"]}])
def test_delegation_only_absent_or_blocked_context_has_no_substitution(monkeypatch, context):
    monkeypatch.setattr(order_service, "get_current_sheet_context", lambda *args, **kwargs: context)

    def forbidden(*args, **kwargs):
        pytest.fail("revision resolver bypassed canonical context")

    monkeypatch.setattr(order_current_state_service, "get_current_state_payload", forbidden)
    monkeypatch.setattr(order_service.draft_sheet_service, "get_selected_sheet_draft", forbidden)
    assert order_service._current_sheet_revision_id(
        order_id="order", payload={"current_sheet_revision_id": "old"},
    ) is None


@pytest.mark.parametrize("exception", [RuntimeError("unknown"), ValueError("template_version_mismatch")])
def test_delegation_only_original_exception_propagates(monkeypatch, exception):
    def fail(*args, **kwargs):
        raise exception

    monkeypatch.setattr(order_service, "get_current_sheet_context", fail)
    with pytest.raises(type(exception)) as caught:
        order_service._current_sheet_revision_id(order_id="order", payload={"current_sheet_revision_id": "old"})
    assert caught.value is exception
