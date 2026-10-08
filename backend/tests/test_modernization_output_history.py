"""Synthetic owned SQL generation records; no OCR, provider or live auth proof.

Definitions only until parent integration. The pre-existing formal-schema fixture
is reused unchanged; the history GET must neither repair nor regenerate artifacts.
"""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from io import BytesIO
import importlib.util
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event, select, update
from sqlalchemy.orm import sessionmaker

from src.db import session_scope
from src.models.order import Order
from src.models.order_sheet_draft import OrderSheetDraft
from src.models.order_output_artifact import OrderBaggingResult, OrderOutputBundle
from src.models.order_workflow_state import OrderWorkflowState
from src.models.facility import Facility
from src.models.facility_template_version import FacilityTemplateVersion
from src.services import order_output_artifact_service as artifacts
from src.services.output_history_service import list_output_history, OutputHistoryNotFound
from src.services.order_workflow_v2_service import WORKFLOW_V2_META_KEY


@pytest.fixture
def history(isolated_formal_schema):
    with session_scope() as session:
        for owner in ("synthetic-order", "other-order"):
            session.add(Order(id=owner, document_uri="fixture://not-an-ocr-pdf", message_id=owner))
            session.flush()
            session.add(OrderSheetDraft(id=f"{owner}-sheet", order_id=owner, draft_sheet_json={"rows": []}))
            session.flush()
            bag = {"bagging_result_id": f"{owner}-bag", "order_id": owner,
                   "source_saved_sheet_id": f"{owner}-sheet", "source_ocr_result_id": None,
                   "template_version_id": None, "quantities": [0, None, 6, 9]}
            artifacts.save_bagging_result_artifact(session, payload=bag)
            for index, key in enumerate(("old", "current", "newest")):
                payload = {"output_bundle_id": f"{owner}-{key}", "order_id": owner,
                           "source_saved_sheet_id": f"{owner}-sheet", "source_bagging_result_id": bag["bagging_result_id"],
                           "source_ocr_result_id": None, "template_version_id": None, "materialization_digest": None,
                           "quantities": [0, None, 6, 9], "file_path": "fixture://must-not-leak"}
                row = artifacts.save_output_bundle_artifact(session, payload=payload)
                row.created_at = datetime(2026, 2, 15, 9, 0, index, 123456)
            session.add(OrderWorkflowState(order_id=owner, revision=7, state="output_review",
                draft_id=f"{owner}-sheet", template_version_id=None, blockers_json=[],
                secondary_actions_json={WORKFLOW_V2_META_KEY: {
                    "output_bundle_id": f"{owner}-current", "bagging_result_id": bag["bagging_result_id"]}}))
    # SQLAlchemy's version mapper assigns the persisted initial revision.
    # Capture it once; subsequent reads never refresh this expected baseline.
    with session_scope() as session:
        baseline = session.get(OrderWorkflowState, "synthetic-order").revision
        assert type(baseline) is int and baseline > 0
        assert session.get(OrderWorkflowState, "other-order").revision == baseline
    isolated_formal_schema.modernization_history_revision = baseline
    return isolated_formal_schema


def read(session, **changes):
    return list_output_history(session, **{"order_id": "synthetic-order", "expected_workflow_revision": session.get_bind().modernization_history_revision,
                                         "page": 1, "limit": 2, **changes})


def snapshot(session):
    return {
        model.__tablename__: [deepcopy({column.name: getattr(row, column.name) for column in model.__table__.columns})
                              for row in session.scalars(select(model).order_by(list(model.__table__.primary_key.columns)[0])).all()]
        for model in (Order, OrderSheetDraft, OrderBaggingResult, OrderOutputBundle, OrderWorkflowState)
    }


def test_exact_current_reference_not_first_or_newest_pages_metadata_only_no_dml(history):
    statements = []
    def observe(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement.lstrip().split(None, 1)[0].upper())
    with session_scope() as session:
        before = snapshot(session)
        event.listen(history, "before_cursor_execute", observe)
        try:
            pages = [read(session, page=page) for page in (1, 2)]
            assert snapshot(session) == before
            assert not session.new and not session.dirty and not session.deleted
        finally:
            event.remove(history, "before_cursor_execute", observe)
        assert not {"INSERT", "UPDATE", "DELETE", "CREATE", "ALTER"}.intersection(statements)
        entries = [entry for page in pages for entry in page["entries"]]
        assert [entry["output_bundle_id"] for entry in entries] == ["synthetic-order-newest", "synthetic-order-current", "synthetic-order-old"]
        assert [entry["current"] for entry in entries] == [False, True, False]
        assert all(page["current_output_bundle_id"] == "synthetic-order-current" and page["total"] == 3 for page in pages)
        assert all(set(entry) == {"output_bundle_id", "order_id", "source_saved_sheet_id", "source_bagging_result_id",
                                 "template_version_id", "created_at", "current", "validation", "blocked_reasons"} for entry in entries)
        assert all(entry["created_at"].endswith("+00:00") for entry in entries)
        assert all(entry["validation"] == "validated" for entry in entries)
        assert artifacts.load_output_bundle_payload(session, "synthetic-order-current", expected_order_id="synthetic-order")["quantities"] == [0, None, 6, 9]


@pytest.mark.parametrize("changes,reason", [
    ({"order_id": ""}, "order_id_required"), ({"order_id": " synthetic-order"}, "order_id_required"),
    ({"expected_workflow_revision": True}, "workflow_revision_required"),
    ({"expected_workflow_revision": 0}, "workflow_revision_required"),
    ({"expected_workflow_revision": "next-baseline"}, "workflow_revision_mismatch"),
    ({"page": 0}, "pagination_invalid"), ({"page": 100001}, "pagination_invalid"),
    ({"limit": 0}, "pagination_invalid"), ({"limit": 101}, "pagination_invalid"),
])
def test_explicit_queries_refuse_invalid_or_mismatched_values(history, changes, reason):
    if changes.get("expected_workflow_revision") == "next-baseline":
        changes = {**changes, "expected_workflow_revision": history.modernization_history_revision + 1}
    with session_scope() as session, pytest.raises(ValueError, match=reason):
        read(session, **changes)


@pytest.mark.parametrize("damage,reason", [
    ("workflow-missing", "workflow_revision_required"), ("blocked", "workflow_unresolved"),
    ("meta-missing", "workflow_unresolved"), ("reference-missing", "current_reference_required"),
    ("reference-space", "current_reference_required"), ("reference-foreign", "current_artifact_invalid"),
    ("reference-nonexistent", "current_artifact_invalid"), ("sheet", "current_sheet_mismatch"),
    ("bagging", "current_bagging_mismatch"), ("digest", "current_artifact_invalid"),
    ("payload-owner", "current_artifact_invalid"),
])
def test_current_lineage_never_substitutes_valid_latest(history, damage, reason):
    with session_scope() as session:
        workflow = session.get(OrderWorkflowState, "synthetic-order")
        if damage == "workflow-missing": session.delete(workflow)
        elif damage == "blocked": workflow.blockers_json = ["original_template_projection_missing"]
        elif damage == "meta-missing": workflow.secondary_actions_json = {}
        elif damage.startswith("reference"):
            value = {"reference-missing": None, "reference-space": " synthetic-order-current",
                     "reference-foreign": "other-order-current", "reference-nonexistent": "missing"}[damage]
            workflow.secondary_actions_json = {WORKFLOW_V2_META_KEY: {"output_bundle_id": value, "bagging_result_id": "synthetic-order-bag"}}
        elif damage == "sheet": workflow.draft_id = "other-order-sheet"
        elif damage == "bagging": workflow.secondary_actions_json = {WORKFLOW_V2_META_KEY: {
            "output_bundle_id": "synthetic-order-current", "bagging_result_id": "other-order-bag"}}
        else:
            row = session.get(OrderOutputBundle, "synthetic-order-current")
            if damage == "digest": row.payload_digest = "invalid"
            else:
                row.payload_json = {**row.payload_json, "order_id": "other-order"}
                row.payload_digest = artifacts.payload_digest(row.payload_json)
        session.flush()
        # ORM revision changes are explicit, not stale tests pretending revision 7.
        revision = workflow.revision
    with session_scope() as session, pytest.raises(ValueError, match=reason):
        read(session, expected_workflow_revision=revision)


@pytest.mark.parametrize("field", ["id", "source_saved_sheet_id", "source_bagging_result_id", "template_version_id"])
def test_historical_metadata_blank_or_whitespace_not_silently_dropped(history, field):
    with session_scope() as session:
        # Malformed referenced identities remain real FK-valid persisted rows.
        if field == "source_saved_sheet_id":
            session.add(OrderSheetDraft(id=" ", order_id="synthetic-order", draft_sheet_json={"rows": []}))
        elif field == "source_bagging_result_id":
            session.add(OrderBaggingResult(id=" ", order_id="synthetic-order", source_saved_sheet_id="synthetic-order-sheet",
                payload_json={}, payload_digest=artifacts.payload_digest({})))
        elif field == "template_version_id":
            session.add(Facility(id="synthetic-facility", name="Synthetic fixture facility"))
            session.flush()
            session.add(FacilityTemplateVersion(id=" ", facility_id="synthetic-facility", version="synthetic-v1",
                status="draft", columns_json=[], template_digest="synthetic-authored-digest"))
        session.flush()
        session.execute(update(OrderOutputBundle).where(OrderOutputBundle.id == "synthetic-order-old").values(**{field: " "}))
    with session_scope() as session, pytest.raises(ValueError, match="metadata_invalid"):
        read(session, limit=25)


def test_bad_historical_digest_is_explicit_row_blocker_not_fake_valid_or_hidden(history):
    with session_scope() as session:
        session.execute(update(OrderOutputBundle).where(OrderOutputBundle.id == "synthetic-order-old").values(payload_digest="bad"))
    with session_scope() as session:
        data = read(session, limit=25)
        row = next(entry for entry in data["entries"] if entry["output_bundle_id"] == "synthetic-order-old")
        assert data["total"] == 3 and len(data["entries"]) == 3
        assert row["validation"] == "blocked" and row["blocked_reasons"] == ["output_bundle_artifact_invalid"]
        assert row["current"] is False


@pytest.mark.parametrize("kind", ["new", "dirty", "deleted"])
def test_dirty_read_session_rejected_before_populate_existing_can_discard_edits(history, kind):
    with session_scope() as session:
        if kind == "new": session.add(Order(id="pending", document_uri="fixture://pending", message_id="pending"))
        else:
            row = session.get(OrderOutputBundle, "synthetic-order-old")
            if kind == "dirty": row.created_by = "unsaved-operator"
            else: session.delete(row)
        with pytest.raises(ValueError, match="clean_read_session_required"): read(session)
        assert getattr(session, kind)
        session.rollback()


@pytest.mark.parametrize("timestamp,expected", [
    (datetime(2026, 2, 15, 9, 0, 0, 123456), "2026-02-15T09:00:00.123456+00:00"),
    (datetime(2026, 2, 15, 9, 0, 0, 123456, tzinfo=timezone(timedelta(hours=9))), "2026-02-15T09:00:00.123456+09:00"),
])
def test_timestamp_boundary_preserves_utc_contract_and_aware_offset(history, timestamp, expected):
    # Explicit SQLite storage-boundary definition, not PostgreSQL offset proof.
    assert history.dialect.name == "sqlite"
    with history.begin() as connection:
        connection.exec_driver_sql("UPDATE order_output_bundles SET created_at=? WHERE id=?",
            (timestamp.isoformat(sep=" "), "synthetic-order-old"))
    with session_scope() as session:
        data = read(session, limit=25)
        result = next(entry for entry in data["entries"] if entry["output_bundle_id"] == "synthetic-order-old")
        assert result["created_at"] == expected
        assert result["validation"] == "validated" and result["blocked_reasons"] == []


def test_malformed_persisted_timestamp_explicitly_stops_instead_of_guessing(history):
    assert history.dialect.name == "sqlite"
    with history.begin() as connection:
        connection.exec_driver_sql("UPDATE order_output_bundles SET created_at=? WHERE id=?", ("not-a-date", "synthetic-order-old"))
    with session_scope() as session, pytest.raises(ValueError):
        read(session, limit=25)


def test_current_template_mismatch_refuses_otherwise_valid_current_artifact(history):
    with session_scope() as session:
        session.add(Facility(id="synthetic-facility", name="Synthetic fixture facility"))
        session.flush()
        session.add(FacilityTemplateVersion(id="synthetic-template", facility_id="synthetic-facility",
            version="synthetic-v1", status="draft", columns_json=[], template_digest="synthetic-authored-digest"))
        session.flush()
        workflow = session.get(OrderWorkflowState, "synthetic-order")
        workflow.template_version_id = "synthetic-template"
        session.flush()
        revision = workflow.revision
    with session_scope() as session, pytest.raises(ValueError, match="current_template_mismatch"):
        read(session, expected_workflow_revision=revision)


@pytest.mark.parametrize("change,reason", [
    ("revision", "workflow_revision_mismatch"), ("reference", "current_reference_changed"),
])
def test_final_observed_workflow_read_rejects_interleaved_change(history, change, reason):
    # Deterministic real SQL interleaving at the final SELECT. This is not proof
    # of PostgreSQL cross-transaction isolation or actual concurrent providers.
    fired = []
    def interleave(connection, _cursor, statement, _parameters, _context, _many):
        normalized = " ".join(statement.lower().split())
        if fired or not normalized.startswith("select order_workflow_states.revision, order_workflow_states.secondary_actions_json"):
            return
        fired.append(change)
        values = {"revision": history.modernization_history_revision + 1} if change == "revision" else {
            "secondary_actions_json": {WORKFLOW_V2_META_KEY: {
                "output_bundle_id": "synthetic-order-newest", "bagging_result_id": "synthetic-order-bag"}}}
        connection.execute(update(OrderWorkflowState).where(OrderWorkflowState.order_id == "synthetic-order").values(**values))
    with session_scope() as session:
        before = snapshot(session)
        event.listen(history, "before_cursor_execute", interleave)
        try:
            with pytest.raises(ValueError, match=reason): read(session)
            assert fired == [change]
        finally:
            event.remove(history, "before_cursor_execute", interleave)
            session.rollback()
        assert snapshot(session) == before


def test_foreign_order_records_never_enter_owned_page(history):
    with session_scope() as session:
        data = read(session, limit=25)
        assert data["total"] == 3
        assert {entry["order_id"] for entry in data["entries"]} == {"synthetic-order"}
        assert not any(entry["output_bundle_id"].startswith("other-order-") for entry in data["entries"])


def test_registered_get_actual_role_query_status_and_no_generation(history, monkeypatch):
    from src.api import output_history as route, auth
    monkeypatch.setenv("AUTH_DISABLED", "false")
    monkeypatch.setattr(route, "SessionLocal", sessionmaker(history, autoflush=False))
    monkeypatch.setattr(auth, "AUTH_PROVIDER", "portal")
    monkeypatch.setattr(auth, "PORTAL_AUTH_ME_URL", "https://synthetic.invalid/auth/me")
    role = {"role": "operator", "account": "synthetic@example.invalid"}
    monkeypatch.setattr(auth.urllib.request, "urlopen", lambda *args, **kwargs: BytesIO(json.dumps(role).encode()))
    headers = {"Authorization": "Bearer synthetic-not-live"}
    app = FastAPI(); app.include_router(route.router, prefix="/outputs")
    with TestClient(app) as client:
        query = {"order_id": "synthetic-order", "expected_workflow_revision": history.modernization_history_revision, "page": 1, "limit": 25}
        assert client.get("/outputs/history", params=query).status_code == 401
        response = client.get("/outputs/history", params=query, headers=headers)
        assert response.status_code == 200 and response.json()["current_output_bundle_id"] == "synthetic-order-current"
        assert client.get("/outputs/history", params={**query, "order_id": "missing"}, headers=headers).status_code == 404
        assert client.get("/outputs/history", params={**query, "expected_workflow_revision": history.modernization_history_revision + 1}, headers=headers).status_code == 409
        assert client.get("/outputs/history", params={"order_id": "synthetic-order"}, headers=headers).status_code == 422
        role["role"] = "unknown"
        assert client.get("/outputs/history", params=query, headers=headers).status_code == 403


def test_registered_contract_matches_committed_openapi_without_regeneration():
    root = Path(__file__).resolve().parents[2]
    path = root / "scripts/export_output_history_openapi.py"
    spec = importlib.util.spec_from_file_location("modernization_history_contract", path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    expected = json.loads((root / "frontend/src/generated/output-history-openapi.json").read_text())
    actual = module.response_contract()
    assert actual == expected
    assert set(actual["paths"]) == {"/outputs/history"}
    assert {p["name"] for p in actual["paths"]["/outputs/history"]["get"]["parameters"]} == {
        "order_id", "expected_workflow_revision", "page", "limit"}
    assert all(p["required"] for p in actual["paths"]["/outputs/history"]["get"]["parameters"])


def test_four_registered_facility_contracts_match_committed_schema_without_generation():
    from src.api import facilities
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location("modernization_facility_contract", root / "scripts/export_facility_version_openapi.py")
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    actual = module.response_contract()
    assert actual == json.loads((root / "frontend/src/generated/facility-version-openapi.json").read_text())
    operations = {
        ("/facilities/{facility_id}/resolved", "get"),
        ("/facilities/{facility_id}/versions", "get"),
        ("/facilities/{facility_id}/versions", "post"),
        ("/facilities/{facility_id}/versions/{version_id}", "put"),
    }
    assert {(path, method) for path, methods in actual["paths"].items() for method in methods} == operations
    selected = [route for route in facilities.router.routes
                if any(("/facilities" + route.path, method.lower()) in operations for method in route.methods)]
    assert len(selected) == 4
    assert all(route.response_model is None for route in selected)
    assert all(route.responses[200]["model"] is not None for route in selected)
    def assert_references(value):
        if isinstance(value, dict):
            if "$ref" in value:
                assert value["$ref"].startswith("#/")
                target = actual
                for segment in value["$ref"][2:].split("/"):
                    target = target[segment.replace("~1", "/").replace("~0", "~")]
            for nested in value.values(): assert_references(nested)
        elif isinstance(value, list):
            for nested in value: assert_references(nested)
    assert_references(actual)


def test_facility_documentation_body_preserves_raw_extras_cas_and_service_validation():
    from fastapi import HTTPException
    from pydantic import TypeAdapter, ValidationError
    from src.api import facilities
    from src.api.facility_version_contract import FacilityVersionBody
    raw = {"version": {"version_id": "synthetic-v1", "valid_from": "authored-not-a-date",
           "valid_to": None, "facility": {"custom_nested": {"zero": 0, "null": None}},
           "arbitrary_extra": [0, None, {"preserved": True}]}, "expected_revision": 17,
           "unknown_top_level": {"preserved": True}}
    adapter = TypeAdapter(FacilityVersionBody)
    parsed = adapter.validate_python(raw)
    assert parsed == raw
    assert parsed["expected_revision"] == 17
    assert parsed["version"]["valid_from"] == "authored-not-a-date"
    assert adapter.validate_python({}) == {}  # Documentation does not supply canonical defaults.
    for invalid in ([], "not-an-object", None):
        with pytest.raises(ValidationError): adapter.validate_python(invalid)
    # Actual service rejects target shape before reaching canonical storage.
    with pytest.raises(HTTPException) as failure:
        facilities.update_facility_version("synthetic-facility", "different-version", parsed)
    assert failure.value.status_code == 422
    assert failure.value.detail["error"] == "facility_version_target_mismatch"
    with pytest.raises(HTTPException) as failure:
        facilities.create_facility_version("synthetic-facility", {"version": None, "expected_revision": "synthetic-cas"})
    assert failure.value.status_code == 422
    assert failure.value.detail["error"] == "facility_version_target_mismatch"
    assert raw == parsed


@pytest.mark.parametrize("action", ["row", "bulk", "delete"])
def test_public_daily_override_binding_still_explicitly_blocks_not_callback_acceptance(action):
    from src.services import order_service
    body = {"output_date": date(2026, 2, 15), "daypart": "昼食", "menu_name": "synthetic-menu",
            "menu_category": "主菜", "unit_type": "g", "qty_per_serving": 0}
    with pytest.raises(ValueError, match="daily_output_original_ownership_required"):
        if action == "row": order_service.upsert_daily_output_portion_override(**body, facility_id="synthetic-facility", diet_type="regular")
        elif action == "bulk": order_service.upsert_daily_output_portion_override_bulk(**body)
        else: order_service.delete_daily_output_portion_override("synthetic-override")
