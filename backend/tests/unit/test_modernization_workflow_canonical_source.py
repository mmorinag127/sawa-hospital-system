"""Component import/delegation proof, not native OCR, quantity or HTTP proof.

Only delegation-only cases replace canonical return values. SQL-backed cases
use the existing owned repair fixture and observe real candidate/review calls.
"""

import builtins
from contextlib import contextmanager

import pytest
from sqlalchemy import event

from src.db import session_scope
from src.services import order_current_state_service, order_service
from src.services import workflow_state_service as workflow
from src.services.workflow_attempt_guard_service import capture_current_sheet_input, bind_current_sheet_input
from tests.unit.test_h2_operator_repair_revision import repair_order  # noqa: F401


ERROR_TYPES = (ImportError, RuntimeError, AttributeError)


def _forbidden(*args, **kwargs):
    pytest.fail("fallback or projection write must not run")


def _fail_canonical_import(monkeypatch, error):
    original = builtins.__import__
    attempts = []

    def selective_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "src.services" and "order_service" in (fromlist or ()):
            attempts.append(name)
            raise error
        return original(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", selective_import)
    return attempts


def _forbid_projection_writes(monkeypatch):
    monkeypatch.setattr(workflow.critical_decision_service, "sync_pending_decisions", _forbidden)
    monkeypatch.setattr(workflow, "lock_order_execution_input", _forbidden)
    monkeypatch.setattr(order_current_state_service, "persist_current_state", _forbidden)
    monkeypatch.setattr(order_current_state_service, "delete_current_state", _forbidden)


@contextmanager
def _no_sql_writes():
    with session_scope() as session:
        engine = session.get_bind()
    statements = []

    def observe(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)
        assert statement.lstrip().split(None, 1)[0].upper() not in {
            "INSERT", "UPDATE", "DELETE", "REPLACE", "CREATE", "DROP", "ALTER",
        }, statement

    event.listen(engine, "before_cursor_execute", observe)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", observe)


@pytest.mark.parametrize("error_type", ERROR_TYPES)
@pytest.mark.parametrize("entry", ["order", "menu", "sheet_get", "sheet_refresh"])
def test_helpers_propagate_original_import_failure_without_fallback(monkeypatch, error_type, entry):
    monkeypatch.setattr(workflow, "session_scope", _forbidden)
    monkeypatch.setattr(workflow.draft_sheet_service, "get_selected_sheet_draft", _forbidden)
    _forbid_projection_writes(monkeypatch)
    error = error_type("canonical source unavailable")
    attempts = _fail_canonical_import(monkeypatch, error)
    with pytest.raises(error_type) as caught:
        if entry == "order":
            workflow._load_order_payload("owned-order")
        elif entry == "menu":
            workflow._build_menu_context(facility_code="fixture", week_code="2026-10")
        else:
            workflow._load_workflow_current_sheet_context(
                "owned-order", force_refresh=entry == "sheet_refresh",
            )
    assert caught.value is error
    assert attempts == ["src.services"]


@pytest.mark.parametrize("error_type", ERROR_TYPES)
@pytest.mark.parametrize("entry", ["refresh", "project"])
def test_public_projection_entries_stop_before_draft_read_or_write(monkeypatch, error_type, entry):
    monkeypatch.setattr(workflow, "session_scope", _forbidden)
    monkeypatch.setattr(workflow.draft_sheet_service, "get_selected_sheet_draft", _forbidden)
    _forbid_projection_writes(monkeypatch)
    error = error_type("canonical source unavailable")
    attempts = _fail_canonical_import(monkeypatch, error)
    with pytest.raises(error_type) as caught:
        if entry == "refresh":
            workflow.refresh_workflow_state("owned-order")
        else:
            workflow.project_workflow_state("owned-order")
    assert caught.value is error
    assert attempts == ["src.services"]


@pytest.mark.parametrize("error_type", ERROR_TYPES)
@pytest.mark.parametrize("preview", [False, True])
def test_shared_candidate_review_import_stops_before_projection_writes(repair_order, monkeypatch, error_type, preview):
    order_id, _, _ = repair_order
    context = bind_current_sheet_input(
        {"order_id": order_id, "order_payload": order_service.get_order_by_id(order_id)},
        capture_current_sheet_input(order_id),
    )
    assert isinstance(context["order_payload"], dict)
    _forbid_projection_writes(monkeypatch)
    monkeypatch.setattr(order_service, "candidate_sheet_state", _forbidden)
    monkeypatch.setattr(order_service, "get_order_review_summary", _forbidden)
    monkeypatch.setattr(workflow.draft_sheet_service, "get_selected_sheet_draft", _forbidden)
    error = error_type("canonical source unavailable")
    with _no_sql_writes():
        attempts = _fail_canonical_import(monkeypatch, error)
        with pytest.raises(error_type) as caught:
            workflow._build_workflow_state_projection(
                order_id, current_sheet_context_override=context,
                include_candidate_preview=preview, persist=True,
            )
    assert caught.value is error
    assert attempts == ["src.services"]


@pytest.mark.parametrize("missing", [False, True])
def test_order_delegation_returns_exact_canonical_result_without_db_rebuild(monkeypatch, missing):
    # Delegation-only stub: no business-validity or quantity claim.
    result = None if missing else {"id": "canonical-order"}
    calls = []

    def get_order(*args, **kwargs):
        calls.append((args, kwargs))
        return result

    monkeypatch.setattr(order_service, "get_order_by_id", get_order)
    monkeypatch.setattr(workflow, "session_scope", _forbidden)
    assert workflow._load_order_payload(" canonical-order ") is result
    assert calls == [(("canonical-order",), {})]


@pytest.mark.parametrize("force_refresh", [False, True])
@pytest.mark.parametrize("refresh_semantic", [False, True])
@pytest.mark.parametrize("missing", [False, True])
def test_sheet_delegation_preserves_kwargs_and_result(monkeypatch, force_refresh, refresh_semantic, missing):
    # Delegation-only stub; a canonical None remains None, never a saved draft.
    result = None if missing else {"canonical": object()}
    calls = []

    def delegated(*args, **kwargs):
        calls.append((args, kwargs))
        return result

    selected = "refresh_current_sheet_context" if force_refresh else "get_current_sheet_context"
    other = "get_current_sheet_context" if force_refresh else "refresh_current_sheet_context"
    monkeypatch.setattr(order_service, selected, delegated)
    monkeypatch.setattr(order_service, other, _forbidden)
    monkeypatch.setattr(workflow.draft_sheet_service, "get_selected_sheet_draft", _forbidden)
    assert workflow._load_workflow_current_sheet_context(
        "canonical-order", force_refresh=force_refresh,
        refresh_draft_from_semantic=refresh_semantic,
    ) is result
    assert calls == [(("canonical-order",), {
        "refresh_draft_from_semantic": refresh_semantic,
        "upgrade_generic_from_sheet": True, "backfill_from_revision": False,
        **({"publish_snapshot": False} if force_refresh else {}),
    })]


def test_owned_order_and_menu_diagnostics_use_real_canonical_delegates(repair_order, monkeypatch):
    order_id, _, _ = repair_order
    expected_order = order_service.get_order_by_id(order_id)
    original = order_service._build_monthly_menu_diagnostics
    expected = original(week_id=expected_order["week_value"], facility_id="fixture")
    calls = []

    def observe(*args, **kwargs):
        calls.append((args, kwargs))
        return original(*args, **kwargs)

    monkeypatch.setattr(order_service, "_build_monthly_menu_diagnostics", observe)
    with _no_sql_writes():
        assert workflow._load_order_payload(order_id) == expected_order
        result = workflow._build_menu_context(facility_code="fixture", week_code=expected_order["week_value"])
    assert calls == [((), {"week_id": expected_order["week_value"], "facility_id": "fixture"})]
    assert result == {
        "month_id": "2026-10",
        "weekly_menu_missing": "monthly_menu_object_missing" in expected["order_codes"],
        "menu_entries_missing": "menu_entries_missing" in expected["order_codes"],
        "entries_count": int(expected.get("facility_entries_count") or expected.get("global_entries_count") or 0),
        "order_codes": expected["order_codes"],
    }


def test_invalid_month_keeps_existing_result_without_canonical_import(monkeypatch):
    attempts = _fail_canonical_import(monkeypatch, ImportError("must not be reached"))
    assert workflow._build_menu_context(facility_code="fixture", week_code="") == {
        "month_id": None, "weekly_menu_missing": False,
        "menu_entries_missing": False, "entries_count": 0, "order_codes": [],
    }
    assert attempts == []


@pytest.mark.parametrize("preview", [False, True])
def test_owned_projection_observes_real_candidate_and_review_delegation(repair_order, monkeypatch, preview):
    order_id, _, _ = repair_order
    order = order_service.get_order_by_id(order_id)
    context = {
        "order_id": order_id,
        "order_payload": order,
        "menu_diagnostics": order_service._build_monthly_menu_diagnostics(
            week_id=order["week_value"], facility_id=order["facility"],
        ),
    }
    context = bind_current_sheet_input(context, capture_current_sheet_input(order_id))
    candidate_calls, review_calls = [], []
    candidate = order_service.candidate_sheet_state
    review = order_service.get_order_review_summary

    def observe_candidate(*args, **kwargs):
        result = candidate(*args, **kwargs)
        candidate_calls.append((args, kwargs, result))
        return result

    def observe_review(*args, **kwargs):
        result = review(*args, **kwargs)
        review_calls.append((args, kwargs, result))
        return result

    monkeypatch.setattr(order_service, "candidate_sheet_state", observe_candidate)
    monkeypatch.setattr(order_service, "get_order_review_summary", observe_review)
    _forbid_projection_writes(monkeypatch)
    with _no_sql_writes():
        evidence = workflow.ocr_evidence_service.get_selected_evidence_run(order_id)
        result = workflow.project_workflow_state(
            order_id, current_sheet_context=context, include_candidate_preview=preview,
        )
    assert isinstance(result, dict)
    assert len(candidate_calls) == int(preview)
    if preview:
        args, kwargs, candidate_result = candidate_calls[0]
        assert args == (order_id,)
        assert kwargs == {
            "candidate_evidence_run_id": None, "current_sheet_context": context,
            "current_sheet_context_supplied": True,
        }
        assert result["candidate_sheet_state"] == {
            key: candidate_result[key] for key in (
                "current_sheet_revision_id", "candidate_evidence_run_id",
                "candidate_preview_available", "candidate_has_meaningful_diff", "candidate_preview_error",
            )
        }
    assert len(review_calls) == 1
    args, kwargs, review_result = review_calls[0]
    assert args == (order_id,)
    assert kwargs == {
        "lines_updated_at": order["lines_updated_at"], "ocr_status": "done",
        "cached_payload": evidence["payload_json"], "ocr_metrics": None,
        "order_status": order["status"], "current_sheet_context": context,
        "current_sheet_context_supplied": True,
        "sheet_gate": result["apply_gate"],
    }
    assert all(result[key] == value for key, value in review_result.items())
