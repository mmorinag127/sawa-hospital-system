"""Synthetic stored-state refusal proof, not accepted FAX or confirmation-flow proof."""
import hashlib
import json
from pathlib import Path
from unittest.mock import Mock

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import MetaData, event, select

from src.db import session_scope
from src.main import app
from src.models.order import Order, OrderLine
from src.models.order_confirmed_snapshot import OrderConfirmedSnapshot
from src.models.order_ocr_evidence_run import OrderOcrEvidenceRun
from src.models.order_sheet_draft import OrderSheetDraft
from src.models.order_workflow_state import OrderWorkflowState
from src.services import facility_template_version_service, order_workflow_v2_service
from tests.contract.auth_support import registered_hospital_operator


ERROR = "legacy_facility_template_column_override_disabled"
ORDER_ID = "disabled-columns-component"


def test_disabled_column_writer_does_not_access_session():
    assert facility_template_version_service.save_columns_for_order(
        object(), order=object(), columns=None,
    ) == ({"error": ERROR}, ERROR)


@pytest.mark.parametrize("facility_id,template_id", [
    ("", "template"), ("FAC00001", " "), (" FAC00001 ", "template"),
])
def test_disabled_template_registry_never_accesses_session(facility_id, template_id):
    error = "legacy_fax_template_registry_disabled"
    assert facility_template_version_service.save_template_registration_for_facility(
        object(), facility_id=facility_id, fax_template_id=template_id,
    ) == ({"error": error}, error)


def _snapshot(engine):
    metadata = MetaData()
    metadata.reflect(bind=engine)
    with engine.connect() as connection:
        return {name: sorted([tuple(row) for row in connection.execute(select(table))], key=repr)
                for name, table in metadata.tables.items()}


@pytest.fixture
def stored_records(isolated_formal_schema, tmp_path, monkeypatch):
    source = tmp_path / "facility-master.json"
    source.write_bytes((Path(__file__).resolve().parents[2] / "src/data/facility_master.template.json").read_bytes())
    monkeypatch.setenv("FACILITY_MASTER_PATH", str(source))
    monkeypatch.delenv("FACILITY_MASTER_URI", raising=False)
    quantities = [0, 7, 9]
    sheet = {"rows": [{"quantity": value} for value in quantities]}
    digest = hashlib.sha256(json.dumps(sheet).encode()).hexdigest()
    with session_scope() as session:
        session.add(Order(id=ORDER_ID, document_uri="component-only:stored-record", message_id=ORDER_ID))
        session.flush()
        session.add(OrderOcrEvidenceRun(id="disabled-evidence", order_id=ORDER_ID,
                    schema_version="component-only", status="diagnostic_only", payload_json=sheet, artifact_digest=digest))
        session.flush()
        session.add(OrderSheetDraft(id="disabled-draft", order_id=ORDER_ID,
                    base_evidence_run_id="disabled-evidence", draft_sheet_json=sheet, edited_by="component-operator"))
        session.flush()
        session.add(OrderConfirmedSnapshot(id="disabled-confirmed", order_id=ORDER_ID,
                    draft_id="disabled-draft", saved_sheet_id="disabled-draft", snapshot_json=sheet, snapshot_digest=digest))
        session.flush()
        for index, quantity in enumerate(quantities):
            session.add(OrderLine(id=f"disabled-line-{index}", order_id=ORDER_ID,
                        confirmed_snapshot_id="disabled-confirmed", quantity_original=quantity, quantity_corrected=quantity))
        workflow = OrderWorkflowState(order_id=ORDER_ID, state="confirmed", evidence_run_id="disabled-evidence",
                                      draft_id="disabled-draft", confirmed_snapshot_id="disabled-confirmed")
        session.add(workflow)
        session.flush()
        revision = workflow.revision
    return isolated_formal_schema, source, revision


@pytest.mark.parametrize("case,status,detail", [
    ("current", 400, ERROR),
    ("stale", 409, {"error": "workflow_revision_conflict"}),
    ("missing-revision", 428, {"error": "workflow_revision_required"}),
    ("missing-order", 404, "order_not_found"),
    ("unauthenticated", 401, "Unauthorized"),
    ("nonempty-current", 400, ERROR),
])
def test_disabled_columns_http_preserves_all_stored_state(stored_records, monkeypatch, record_property, case, status, detail):
    engine, source, revision = stored_records
    save = Mock(wraps=order_workflow_v2_service.save_facility_template_columns)
    monkeypatch.setattr(order_workflow_v2_service, "save_facility_template_columns", save)
    columns = [{"role": "quantity", "header": "常食", "diet_type": "regular", "area_id": "X"}] if case == "nonempty-current" else []
    with registered_hospital_operator(monkeypatch) as actor:
        before, source_before = _snapshot(engine), source.read_bytes()
        headers = actor.headers if case != "unauthenticated" else {}
        if case != "missing-revision":
            headers["If-Match"] = f'"{revision - 1 if case == "stale" else revision}"'
        order_id = "absent-component-order" if case == "missing-order" else ORDER_ID
        statements = []
        def observe(_connection, _cursor, statement, _parameters, _context, _executemany):
            statements.append(statement)
        event.listen(engine, "before_cursor_execute", observe)
        try:
            response = TestClient(app).put(f"/orders/{order_id}/workflow-v2/facility-template-columns",
                                          headers=headers, json={"columns": columns})
        finally:
            event.remove(engine, "before_cursor_execute", observe)
        record_property("response", json.dumps({"status": response.status_code, "body": response.json()}, sort_keys=True))
        assert _snapshot(engine) == before
        assert source.read_bytes() == source_before
        assert all(statement.lstrip().split()[0].upper() in {"SELECT", "PRAGMA"} for statement in statements)
        if case != "unauthenticated":
            actor.verifier.assert_called_once()
        else:
            actor.verifier.assert_not_called()
        assert response.status_code == status
        assert response.json() == {"detail": detail}
        if case in {"missing-revision", "unauthenticated"}:
            save.assert_not_called()
        else:
            save.assert_called_once_with(order_id, columns, expected_revision=revision - 1 if case == "stale" else revision)
        with session_scope() as session:
            assert [row.quantity_corrected for row in session.query(OrderLine).order_by(OrderLine.id)] == [0, 7, 9]
            assert session.get(OrderSheetDraft, "disabled-draft").draft_sheet_json == {"rows": [{"quantity": q} for q in [0, 7, 9]]}


def test_disabled_columns_command_keeps_error_identity_and_records(stored_records):
    engine, source, revision = stored_records
    before, source_before = _snapshot(engine), source.read_bytes()
    result, error = order_workflow_v2_service.save_facility_template_columns(ORDER_ID, [], expected_revision=revision)
    assert result == {"error": ERROR}
    assert error == ERROR
    assert _snapshot(engine) == before
    assert source.read_bytes() == source_before
