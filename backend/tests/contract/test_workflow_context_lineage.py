"""Owned context/CAS component proof; no OCR, FAX acceptance or saved-source invention."""
from copy import deepcopy
from datetime import date, timedelta
from hashlib import sha256
import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import event

from src.db import session_scope
from src.main import app
from src.models.facility import Facility
from src.models.facility_template_version import FacilityTemplateVersion
from src.models.order import Order, OrderLine
from src.models.order_confirmed_snapshot import OrderConfirmedSnapshot
from src.models.order_current_state import OrderCurrentState
from src.models.order_ocr_evidence_run import OrderOcrEvidenceRun
from src.models.order_sheet_draft import OrderSheetDraft
from src.models.order_workflow_state import OrderWorkflowState
from src.services import canonical_facility_lineage as lineage, facility_master_service
from src.services.canonical_facility_store import CanonicalStoreError
from tests.contract.auth_support import registered_hospital_operator
from tests.unit.test_disabled_facility_columns import _snapshot


ORDER = "context-owned-order"
FACILITY = "context-owned-facility"
TEMPLATE = "context-dated-template"
QUANTITIES = [0, 7, 9]


@pytest.fixture
def context_source(isolated_formal_schema, tmp_path, monkeypatch):
    engine = isolated_formal_schema
    engine.dispose()

    def foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    event.listen(engine, "connect", foreign_keys)
    facility = {
        "facility_id": FACILITY, "facility_name": "Authored context facility", "areas": None,
        "fax_template_id": TEMPLATE,
        "fax_template": {"template_id": TEMPLATE, "page": 1, "columns": [
            {"index": 0, "source_index": 0, "role": "menu_name", "header": "Menu"},
            {"index": 1, "source_index": 1, "role": "quantity", "header": "Regular",
             "diet_type": "regular", "area_id": "X"},
        ]},
    }
    authored = {"version_id": "authored-January", "valid_from": "2026-01-01",
                "valid_to": "2026-01-31", "facility": facility}
    root = deepcopy(facility)
    root["facility_name"] = "Undated root must not supplement selection"
    root["fax_template_id"] = "root-only-template"
    root["fax_template"]["template_id"] = "root-only-template"
    master = {"facilities": [{**root, "versions": [authored]}]}
    source = tmp_path / "canonical.json"
    source.write_text(json.dumps(master), encoding="utf-8")
    monkeypatch.setenv("FACILITY_MASTER_PATH", str(source))
    for name in ("FACILITY_MASTER_URI", "K_SERVICE"):
        monkeypatch.delenv(name, raising=False)
    with session_scope() as session:
        assert session.connection().exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
        session.add(Facility(id=FACILITY, name=facility["facility_name"]))
        session.add(Order(id=ORDER, message_id=ORDER, document_uri="component:context-only"))
    with registered_hospital_operator(monkeypatch) as actor:
        yield SimpleNamespace(engine=engine, source=source, master=master, authored=authored,
                              actor=actor, client=TestClient(app, raise_server_exceptions=False))
    event.remove(engine, "connect", foreign_keys)


def _request(f, *, day="2026-01-15", template=TEMPLATE, revision=0, order_id=ORDER):
    headers = f.actor.headers
    if revision is not None:
        headers["If-Match"] = f'"{revision}"'
    body = {"facility_id": FACILITY, "week_start": day,
            "week_end": (date.fromisoformat(day) + timedelta(days=6)).isoformat()}
    if template is not None:
        body["template_id"] = template
    return f.client.post(f"/orders/{order_id}/workflow-v2/context", headers=headers, json=body)


def _stored_downstream():
    # Explicit existing-state fixture, not a claim that synthetic evidence was accepted OCR.
    sheet = {"rows": [{"quantity": q} for q in QUANTITIES]}
    digest = sha256(json.dumps(sheet).encode()).hexdigest()
    with session_scope() as session:
        session.add(OrderOcrEvidenceRun(id="context-evidence", order_id=ORDER,
                    schema_version="component-state", status="diagnostic_only", payload_json=sheet, artifact_digest=digest))
        session.flush()
        session.add(OrderSheetDraft(id="context-draft", order_id=ORDER,
                    base_evidence_run_id="context-evidence", draft_sheet_json=sheet, edited_by="operator"))
        session.flush()
        session.add(OrderConfirmedSnapshot(id="context-confirmed", order_id=ORDER,
                    draft_id="context-draft", saved_sheet_id="context-draft", snapshot_json=sheet, snapshot_digest=digest))
        session.flush()
        for i, q in enumerate(QUANTITIES):
            session.add(OrderLine(id=f"context-line-{i}", order_id=ORDER,
                        confirmed_snapshot_id="context-confirmed", quantity_original=q, quantity_corrected=q))
        session.add(OrderCurrentState(order_id=ORDER, draft_id="context-draft",
                    evidence_run_id="context-evidence", state_json=sheet))
        workflow = session.get(OrderWorkflowState, ORDER)
        if workflow is None:
            workflow = OrderWorkflowState(order_id=ORDER, state="sheet_saved")
            session.add(workflow)
        workflow.evidence_run_id = "context-evidence"
        workflow.draft_id = "context-draft"
        workflow.confirmed_snapshot_id = "context-confirmed"
        session.flush()
        return workflow.revision


@pytest.mark.parametrize("day", ["2026-01-01", "2026-01-15", "2026-01-31"])
@pytest.mark.parametrize("template", [None, TEMPLATE])
@pytest.mark.parametrize("materialized", [False, True])
def test_context_materializes_exact_dated_selection_once_and_reuses_it(context_source, monkeypatch, day, template, materialized, record_property):
    f = context_source
    snapshot = facility_master_service.get_snapshot()
    source_before = f.source.read_bytes()
    with session_scope() as session:
        if materialized:
            selected, _ = lineage.resolve(session, FACILITY, day)
            lineage.materialize(session, selected)
        assert session.query(FacilityTemplateVersion).count() == int(materialized)
        assert session.get(OrderWorkflowState, ORDER) is None
    actual = facility_master_service.get_snapshot
    reads = []

    def observe():
        value = actual()
        reads.append(value)
        return value

    monkeypatch.setattr(facility_master_service, "get_snapshot", observe)
    response = _request(f, day=day, template=template)
    record_property("initial_http_status", response.status_code)
    assert response.status_code == 200, response.text
    initial_read_count = len(reads)
    result = response.json()
    assert result["revision"] == 2
    with session_scope() as session:
        projection = session.query(FacilityTemplateVersion).one()
        receipt = lineage.verify_projection(projection)
        assert receipt["authored_version"] == f.authored
        assert projection.config_json == f.authored["facility"]
        assert projection.template_id == TEMPLATE
        assert projection.validation_json["creation_source_proof"] == {
            "revision": snapshot.revision, "source": "canonical_json", "effective_date": day}
        assert session.get(Order, ORDER).template_version_id == projection.id
        workflow = session.get(OrderWorkflowState, ORDER)
        assert workflow.template_version_id == projection.id
        assert workflow.secondary_actions_json["workflow_v2"]["template_id"] == TEMPLATE
        first_projection = _snapshot(f.engine)["facility_template_versions"]
    repeated = _request(f, day=day, template=template, revision=result["revision"])
    assert repeated.status_code == 200, repeated.text
    record_property("revision_sequence", json.dumps([result["revision"], repeated.json()["revision"]]))
    # Existing downstream invalidation flush and command finalization each advance CAS.
    assert repeated.json()["revision"] == result["revision"] + 2
    assert initial_read_count == 1
    assert len(reads) == 2
    assert _snapshot(f.engine)["facility_template_versions"] == first_projection
    assert f.source.read_bytes() == source_before


@pytest.mark.parametrize("populated", [False, True])
@pytest.mark.parametrize("fault,status,error", [
    ("gap", 400, "facility_version_date_unrecorded"),
    ("overlap", 400, "facility_version_period_overlap"),
    ("malformed", 400, "facility_version_snapshot_incomplete"),
    ("missing-source", 400, "facility_master_source_required"),
    ("template", 400, "template_version_mismatch"),
    ("parent", 400, "canonical_lineage_parent_projection_missing"),
    ("stale", 409, "workflow_revision_conflict"),
    ("missing-revision", 428, "workflow_revision_required"),
    ("missing-order", 404, "order_not_found"),
    ("after-insert", 400, "context_materialization_fault"),
])
def test_context_refusal_preserves_every_row_and_source(context_source, monkeypatch, populated, fault, status, error, record_property):
    f = context_source
    revision = _stored_downstream() if populated else 0
    if fault == "gap":
        f.authored["valid_to"] = "2026-01-14"
    elif fault == "overlap":
        duplicate = deepcopy(f.authored)
        duplicate["version_id"] = "explicit-overlap"
        f.master["facilities"][0]["versions"].append(duplicate)
    elif fault == "malformed":
        del f.authored["facility"]["areas"]
    elif fault == "missing-source":
        monkeypatch.delenv("FACILITY_MASTER_PATH")
    elif fault == "parent":
        with session_scope() as session:
            session.delete(session.get(Facility, FACILITY))
    f.source.write_text(json.dumps(f.master), encoding="utf-8")
    before, source_before = _snapshot(f.engine), f.source.read_bytes()
    inserted = []

    def fail_after_insert(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.startswith("INSERT INTO facility_template_versions"):
            inserted.append(statement)
            raise CanonicalStoreError("context_materialization_fault", status_code=409)

    if fault == "after-insert":
        event.listen(f.engine, "after_cursor_execute", fail_after_insert)
    try:
        response = _request(f, template="root-only-template" if fault == "template" else TEMPLATE,
                            revision=None if fault == "missing-revision" else revision + 1 if fault == "stale" else revision,
                            order_id="absent-context-order" if fault == "missing-order" else ORDER)
    finally:
        if fault == "after-insert":
            event.remove(f.engine, "after_cursor_execute", fail_after_insert)
    record_property("response", json.dumps({"status": response.status_code, "body": response.text}))
    assert _snapshot(f.engine) == before
    assert f.source.read_bytes() == source_before
    assert response.status_code == status, response.text
    detail = response.json()["detail"]
    assert (detail.get("error") if isinstance(detail, dict) else detail) == error
    assert len(inserted) == (1 if fault == "after-insert" else 0)
    with session_scope() as session:
        assert session.query(FacilityTemplateVersion).count() == 0
        if populated:
            assert [row.quantity_corrected for row in session.query(OrderLine).order_by(OrderLine.id)] == QUANTITIES
            assert session.get(OrderSheetDraft, "context-draft").draft_sheet_json == {"rows": [{"quantity": q} for q in QUANTITIES]}
        else:
            assert session.get(OrderWorkflowState, ORDER) is None


def test_successful_reconfirm_keeps_existing_invalidation_semantics(context_source):
    f = context_source
    with session_scope() as session:
        selected, _ = lineage.resolve(session, FACILITY, "2026-01-15")
        lineage.materialize(session, selected)
    initial = _request(f)
    assert initial.status_code == 200, initial.text
    revision = _stored_downstream()
    projection_before = _snapshot(f.engine)["facility_template_versions"]
    source_before = f.source.read_bytes()
    response = _request(f, revision=revision)
    assert response.status_code == 200, response.text
    assert response.json()["revision"] == revision + 3
    with session_scope() as session:
        for model in (OrderOcrEvidenceRun, OrderSheetDraft, OrderConfirmedSnapshot, OrderLine):
            assert session.query(model).count() == 0
        workflow = session.get(OrderWorkflowState, ORDER)
        assert workflow.state == "context_confirmed"
        assert workflow.evidence_run_id is workflow.draft_id is workflow.confirmed_snapshot_id is None
        assert session.get(OrderCurrentState, ORDER).draft_id is None
        assert session.get(OrderCurrentState, ORDER).evidence_run_id is None
    assert _snapshot(f.engine)["facility_template_versions"] == projection_before
    assert f.source.read_bytes() == source_before
