"""Owned SQLite import policy tests; no live output claim."""
import ast
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock

import pytest
from sqlalchemy import update

from test_initial_saved_source_service import saved
from test_initial_saved_import_service import imported
from src.models.order import Order
from src.models.order_workflow_state import OrderWorkflowState
from src.services import order_service, output_builder, total_service, initial_saved_import_service as imports


def strict_serializer(order, *, allow_unresolved_menu_amount=False):
    if not allow_unresolved_menu_amount:
        raise ValueError("unresolved-current-menu")
    return {"id": order.id, "template_version_id": order.template_version_id, "lines": []}


def test_import_preserves_candidate_and_bags_with_unresolved_metadata(imported, monkeypatch):
    session, _, snapshot = imported
    serializer = Mock(side_effect=strict_serializer)
    monkeypatch.setattr(order_service, "serialize_order", serializer)
    monkeypatch.setattr(order_service, "_resolve_order_current_week_selection", lambda order: {})
    @contextmanager
    def scope():
        yield session
    monkeypatch.setattr(order_service, "session_scope", scope)
    for supplied in (True, False):
        payload = order_service.get_order_for_output_read("O", **({"read_session": session} if supplied else {}))
        candidate = output_builder._accepted_output_candidate(payload, write_session=session)
        assert candidate["initial_saved_bag_rows"] == snapshot["bagging"]["payload_json"]["bag_rows"]
        assert candidate["lines"] == imports.output_candidate(imports.get_binding(session, "O"))["lines"]
    assert serializer.call_count == 2
    assert all(call.kwargs == {"allow_unresolved_menu_amount": True} for call in serializer.call_args_list)


def test_nonimport_strict_and_missing_unchanged(imported, monkeypatch):
    session, _, _ = imported
    serializer = Mock(side_effect=strict_serializer)
    monkeypatch.setattr(order_service, "serialize_order", serializer)
    with pytest.raises(ValueError, match="^unresolved-current-menu$"):
        order_service.get_order_for_output_read("OTHER", read_session=session)
    assert serializer.call_args.kwargs == {"allow_unresolved_menu_amount": False}
    serializer.reset_mock()
    assert order_service.get_order_for_output_read("missing", read_session=session) is None
    serializer.assert_not_called()


@pytest.mark.parametrize("change", ["source", "retired", "corrupt"])
def test_binding_rejection_precedes_serialization(imported, monkeypatch, change):
    session, master, _ = imported
    if change == "source":
        session.execute(update(Order).where(Order.id == "O").values(document_uri="changed.pdf"))
    elif change == "retired":
        row = session.get(OrderWorkflowState, "O")
        actions = deepcopy(row.secondary_actions_json or {})
        actions[imports.RETIRED] = {}
        row.secondary_actions_json = actions
        session.flush()
    else:
        master[imports.IMPORTS]["O"] = {"invalid": True}
    serializer = Mock()
    monkeypatch.setattr(order_service, "get_order_by_id", serializer)
    with pytest.raises(ValueError, match="initial_saved_"):
        order_service.get_order_for_output_read("O", read_session=session)
    serializer.assert_not_called()


@pytest.mark.parametrize("module,function", [
    ("order_service", "get_daily_bag_summary"), ("order_service", "get_bag_summary"),
    ("total_service", "build_totals"), ("output_builder", "_capture_accepted_output_read"),
    ("output_builder", "_capture_output_adoption"), ("output_builder", "_prepare_output_context"),
])
def test_all_output_entrypoints_use_common_policy(module, function):
    root = Path(__file__).resolve().parents[2] / "src/services"
    tree = ast.parse((root / (module + ".py")).read_text())
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == function)
    calls = [n.func for n in ast.walk(node) if isinstance(n, ast.Call)]
    names = [n.id if isinstance(n, ast.Name) else n.attr if isinstance(n, ast.Attribute) else None for n in calls]
    assert "get_order_for_output_read" in names
    assert "get_order_by_id" not in names
    assert "serialize_order" not in names


@pytest.mark.parametrize("entry", ["daily", "bags", "totals", "accepted", "adoption", "context"])
def test_runtime_sibling_dispatch(imported, monkeypatch, entry):
    session, _, _ = imported
    stop = ValueError("shared-output-read-stop")
    loader = Mock(side_effect=stop)
    monkeypatch.setattr(order_service, "get_order_for_output_read", loader)
    @contextmanager
    def scope():
        yield session
    monkeypatch.setattr(order_service, "session_scope", scope)
    monkeypatch.setattr(order_service, "list_orders_by_line_date", lambda *a, **k: [{"id": "O"}])
    monkeypatch.setattr(total_service, "_iter_confirmed_orders", lambda *a: [session.get(Order, "O")])
    monkeypatch.setattr(order_service, "_capture_synchronous_cache_write_input", lambda *a, **k: {"owned": True})
    calls = {
        "daily": lambda: order_service.get_daily_bag_summary(None),
        "bags": lambda: order_service.get_bag_summary("O"),
        "totals": lambda: total_service.build_totals(None, None),
        "accepted": lambda: output_builder._capture_accepted_output_read("O", session),
        "adoption": lambda: output_builder._capture_output_adoption("O", session),
        "context": lambda: output_builder._prepare_output_context("O"),
    }
    with pytest.raises(ValueError) as caught:
        calls[entry]()
    assert caught.value is stop
    loader.assert_called_once()
    assert loader.call_args.args == ("O",)
    if entry in {"totals", "accepted", "adoption"}:
        assert loader.call_args.kwargs == {"read_session": session}
