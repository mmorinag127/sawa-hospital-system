"""Owned SQLite/dated JSON component definitions, not OCR or staging proof.

The HTTP case uses synthetic AUTH_DISABLED and no application lifespan.
Only session bindings and explicit environment are patched, never selectors.
"""

from contextlib import contextmanager
from copy import deepcopy
import importlib

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import event, select

from modernization_saved_sheet_export_fixtures import SavedSheetFixture
from test_modernization_explicit_ocr_quantity_contract import authored_template
from src import db
from src.models.facility_template_version import FacilityTemplateVersion
from src.models.menu import MonthlyMenu, MonthlyMenuEntry, MonthlyMenuItem
from src.models.order import Order
from src.models.order_current_state import OrderCurrentState
from src.models.order_sheet_draft import OrderSheetDraft
from src.services import apply_gate_service, config_service, order_current_state_service, order_service


@pytest.fixture
def owned(tmp_path, monkeypatch):
    fixture = SavedSheetFixture(tmp_path, monkeypatch)
    try:
        fixture.authored["fax_template"] = authored_template()
        fixture.republish()
        fixture.master["facilities"][0].update(deepcopy(fixture.authored))
        fixture.write_master()
        monkeypatch.setattr(db, "SessionLocal", fixture.sessions)
        monkeypatch.setattr(order_service, "session_scope", fixture.session_scope)
        monkeypatch.setattr(order_current_state_service, "session_scope", fixture.session_scope)
        with fixture.session_scope() as session:
            draft = session.get(OrderSheetDraft, fixture.draft_id)
            draft.draft_state = "saved"
            draft.blockers_json = []
            draft.warnings_json = []
            assert apply_gate_service.has_clean_saved_draft({
                "id": draft.id, "draft_sheet_json": draft.draft_sheet_json,
                "blockers_json": draft.blockers_json, "warnings_json": draft.warnings_json,
            })
        yield fixture
    finally:
        fixture.close()


def snapshot(fixture):
    with fixture.session_scope() as session:
        return {table.name: deepcopy([
            dict(row) for row in session.execute(
                select(table).order_by(*table.primary_key.columns),
            ).mappings()
        ]) for table in db.Base.metadata.sorted_tables}


@contextmanager
def unchanged_reads(fixture):
    before = snapshot(fixture)
    writes = []

    def observe(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().split(None, 1)[0].upper() in {
            "INSERT", "UPDATE", "DELETE", "CREATE", "ALTER", "DROP", "REPLACE",
        }:
            writes.append(statement)

    event.listen(fixture.engine, "before_cursor_execute", observe)
    try:
        yield
        assert snapshot(fixture) == before
        assert writes == []
    finally:
        event.remove(fixture.engine, "before_cursor_execute", observe)


def resolve(fixture, **kwargs):
    arguments = {"order_id": fixture.order_id, "facility_id": "F", "log_context": "selected-read-component"}
    arguments.update(kwargs)
    return order_service._resolve_effective_sheet_template(**arguments)


def diverge_root(fixture, mode):
    root = fixture.master["facilities"][0]
    if mode == "versions-only":
        fixture.master["facilities"][0] = {
            "facility_id": "F", "versions": deepcopy(root["versions"]),
        }
    else:
        root["fax_template"]["header_rows"] = 2
        root["fax_template"]["columns"][4]["diet_type"] = "root-only-diet"
    fixture.write_master()


@pytest.mark.parametrize("root_mode", ["different-template", "versions-only"])
def test_selected_complete_version_survives_root_divergence_without_menu_or_policy(
    owned, monkeypatch, tmp_path, root_mode,
):
    expected = deepcopy(owned.authored)
    diverge_root(owned, root_mode)
    with owned.session_scope() as session:
        for model in (MonthlyMenu, MonthlyMenuItem, MonthlyMenuEntry):
            assert session.query(model).count() == 0
    missing_policy = tmp_path / "intentionally-missing-policy.json"
    assert not missing_policy.exists()
    # Reload the real module to bind its import-time path to the explicit env.
    try:
        with monkeypatch.context() as environment:
            environment.setenv("INGEST_POLICY_PATH", str(missing_policy))
            importlib.reload(config_service)
            assert config_service.INGEST_POLICY_PATH == missing_policy
            with unchanged_reads(owned):
                config, template, identifier, error = resolve(owned)
                assert (config, template, identifier, error) == (
                    expected, expected["fax_template"], "fax_layout_regular_forbidden_v1", None,
                )
                assert order_service._resolve_order_fax_template(owned.order_id) == expected["fax_template"]
                assert order_service._load_current_sheet_facility_template(
                    "F", order_id=owned.order_id,
                ) == (expected, expected["fax_template"])
                template["columns"][4]["diet_type"] = "edited-return-only"
                assert config == expected
                assert resolve(owned)[1] == expected["fax_template"]
    finally:
        importlib.reload(config_service)


def test_orderless_lookup_uses_current_root_but_supplied_snapshot_stays_authoritative(owned):
    captured = deepcopy(owned.authored)
    owned.master["facilities"][0]["fax_template"]["header_rows"] = 2
    owned.authored["fax_template"]["header_rows"] = 3
    owned.write_master()
    current = deepcopy(owned.master["facilities"][0])
    with unchanged_reads(owned):
        assert resolve(owned, order_id=None) == (
            current, current["fax_template"], "fax_layout_regular_forbidden_v1", None,
        )
        assert resolve(owned, facility_config=captured) == (
            captured, captured["fax_template"], "fax_layout_regular_forbidden_v1", None,
        )
        assert captured["fax_template"]["header_rows"] == 0


REJECTIONS = [
    ("missing-selection", "original_template_projection_missing"),
    ("foreign-owner", "original_template_configured_source_mismatch"),
    ("corrupt-projection", "canonical_lineage_snapshot_mismatch"),
    ("missing-authored", "original_template_configured_source_mismatch"),
    ("changed-authored", "original_template_configured_source_mismatch"),
]


def reject_selected(fixture, failure):
    with fixture.session_scope() as session:
        order = session.get(Order, fixture.order_id)
        if failure == "missing-selection":
            order.template_version_id = None
        elif failure == "foreign-owner":
            order.facility_code = "FOREIGN"
            foreign_root = deepcopy(fixture.master["facilities"][0])
            foreign_root["facility_id"] = "FOREIGN"
            foreign_root["versions"][0]["facility"]["facility_id"] = "FOREIGN"
            fixture.master["facilities"].append(foreign_root)
        elif failure == "corrupt-projection":
            projection = session.get(FacilityTemplateVersion, fixture.projection_id)
            changed = deepcopy(projection.config_json)
            changed["fax_template"]["header_rows"] = 3
            projection.config_json = changed
    if failure == "missing-authored":
        fixture.master["facilities"][0]["versions"] = []
    elif failure == "changed-authored":
        fixture.authored["fax_template"]["header_rows"] = 3
    fixture.write_master()


@pytest.mark.parametrize("failure,reason", REJECTIONS)
def test_invalid_selected_source_never_uses_valid_root(owned, failure, reason):
    reject_selected(owned, failure)
    facility = "FOREIGN" if failure == "foreign-owner" else "F"
    with unchanged_reads(owned):
        assert config_service.get_authored_facility_config(facility)["fax_template"]["header_rows"] == 0
        assert resolve(owned, facility_id=facility) == (None, None, None, reason)
        assert order_service._resolve_order_fax_template(owned.order_id) is None
        assert order_service._load_current_sheet_facility_template(
            facility, order_id=owned.order_id,
        ) == (None, None)


def seed_current_state(fixture):
    with fixture.session_scope() as session:
        session.add(OrderCurrentState(
            order_id=fixture.order_id, template_version_id=fixture.projection_id,
            draft_id=fixture.draft_id, evidence_run_id=fixture.evidence_id,
            snapshot_version="v2", state_json={
                "order_id": fixture.order_id, "fields": deepcopy(fixture.payload["fields"]),
                "rows": deepcopy(fixture.payload["rows"]), "blockers": [],
                "has_persisted_draft": True, "clean_saved_draft": True,
            },
        ))


def public_readers():
    return (
        order_service.get_current_sheet_context,
        order_service._build_current_sheet_context_uncached,
        order_service.refresh_current_sheet_context,
    )


def blocked_context(order_id, reason):
    return {
        "order_id": order_id, "resolved_week_id": None,
        "fields": [], "header": [], "rows": [], "row_ids": [],
        "blockers": [reason], "warnings": [],
        "draft_record": {"blockers_json": [reason]},
        "draft_payload": None, "has_persisted_draft": False,
        "authoritative_persisted_draft": False, "clean_saved_draft": False,
    }


@pytest.mark.parametrize("persisted", [False, True], ids=["no-current-state", "current-state"])
@pytest.mark.parametrize("failure,reason", REJECTIONS)
def test_clean_saved_draft_cannot_bypass_selected_source_blocker(owned, persisted, failure, reason):
    if persisted:
        seed_current_state(owned)
    reject_selected(owned, failure)
    for reader in public_readers():
        with unchanged_reads(owned):
            assert reader(owned.order_id) == blocked_context(owned.order_id, reason)


@pytest.mark.parametrize("persisted", [False, True])
def test_unresolved_week_has_priority_over_missing_projection(owned, persisted):
    if persisted:
        seed_current_state(owned)
    with owned.session_scope() as session:
        order = session.get(Order, owned.order_id)
        order.week_code = None
        order.template_version_id = None
    for reader in public_readers():
        with unchanged_reads(owned):
            assert reader(owned.order_id) == blocked_context(owned.order_id, "week_unresolved")


def test_nonexistent_order_stays_none_without_database_mutation(owned):
    for reader in public_readers():
        with unchanged_reads(owned):
            assert reader("COMPONENT-NO-SUCH-ORDER") is None


def test_order_bound_requested_facility_mismatch_stops_without_database_mutation(owned):
    with unchanged_reads(owned):
        assert resolve(owned, facility_id="ABSENT") == (
            None, None, None, "configured_execution_facility_mismatch",
        )


def test_registered_template_candidate_http_discloses_missing_selection(owned, monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "true")
    reject_selected(owned, "missing-selection")
    from src.main import app

    client = TestClient(app)
    try:
        with unchanged_reads(owned):
            response = client.get(f"/orders/{owned.order_id}/hakodate-template-candidate")
            assert response.status_code == 500, response.text
            assert response.json() == {"detail": "original_template_projection_missing"}
    finally:
        client.close()
