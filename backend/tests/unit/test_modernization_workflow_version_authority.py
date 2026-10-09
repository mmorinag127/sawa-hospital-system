"""Owned SQLite/JSON/PDF components, not native OCR, UI, provider or live proof.

Evidence is manually seeded by unchanged fixtures; bagging/confirmation are real.
Individual ID/date mutations are deliberate DB faults, not operator workflows.
Public reads retain their real existing captures; only the template-only guard
is checked with missing policy and empty monthly tables.
"""

from contextlib import contextmanager
from copy import deepcopy
from datetime import date
import json
import re

import pytest
from sqlalchemy import event, select

from test_modernization_real_bagging_confirmation import (
    actual_bagging,
    owned_bagging as owned_bagging,
    revision,
)
from test_modernization_total_facility_receipt import confirmed_receipt as confirmed_receipt
from test_modernization_workflow_surface_parity import database_snapshot, surface as surface
from src.models.facility_template_version import FacilityTemplateVersion
from src.models.menu import MonthlyMenu, MonthlyMenuEntry, MonthlyMenuItem
from src.models.order import Order, OrderLine
from src.models.order_confirmed_snapshot import OrderConfirmedSnapshot
from src.models.order_ocr_evidence_run import OrderOcrEvidenceRun
from src.models.order_output_artifact import OrderOutputBundle
from src.models.order_sheet_draft import OrderSheetDraft
from src.models.order_workflow_state import OrderWorkflowState
from src.services import canonical_facility_lineage as lineage
from src.services import config_service
from src.services import facility_template_version_service
from src.services import order_output_artifact_service as artifacts
from src.services import order_workflow_v2_service as workflow


def write_meta(session, fixture, change):
    """Direct fault/setup write, without an unrelated ORM revision increment."""
    state = session.get(OrderWorkflowState, fixture.order_id)
    payload = deepcopy(state.secondary_actions_json)
    change(payload["workflow_v2"])
    session.execute(OrderWorkflowState.__table__.update().where(
        OrderWorkflowState.order_id == fixture.order_id,
    ).values(secondary_actions_json=payload))


@pytest.fixture
def selected_owned(owned_bagging):
    fixture = owned_bagging
    original = fixture.master_path.read_bytes()
    # The fixture already owns a real projection, dated JSON and PDF. Add only
    # the explicit logical ID required by queue/select/save public contracts.
    with fixture.session_scope() as session:
        projection = lineage.read_projection(session, fixture.projection_id)
        assert lineage.verify_projection(projection)["facility_id"] == "F"
        assert projection.template_id == fixture.authored["fax_template"]["template_id"]
        write_meta(session, fixture, lambda meta: meta.update(template_id=projection.template_id))
    try:
        yield fixture
    finally:
        fixture.master_path.write_bytes(original)


@pytest.fixture(params=["bagged", "confirmed"])
def accepted_phase(request):
    if request.param == "confirmed":
        fixture, candidate = request.getfixturevalue("confirmed_receipt")
    else:
        fixture = request.getfixturevalue("selected_owned")
        candidate = actual_bagging(fixture)["bagging_result"]["materialization_candidate"]
    return fixture, candidate, request.param


@contextmanager
def unchanged_read(fixture):
    before = database_snapshot()
    statements = []

    def observe(_conn, _cursor, statement, _parameters, context, _many):
        if (context.isinsert or context.isupdate or context.isdelete
                or re.match(r"\s*(INSERT|UPDATE|DELETE|REPLACE|CREATE|ALTER|DROP|TRUNCATE)\b", statement, re.I)):
            statements.append(statement)

    event.listen(fixture.engine, "before_cursor_execute", observe)
    try:
        yield
        assert database_snapshot() == before
        assert statements == []
    finally:
        event.remove(fixture.engine, "before_cursor_execute", observe)


def assert_saved(fixture, expected):
    assert fixture.saved() == expected
    assert [row[4] for row in expected["rows"]] == ["7", "", "", "0"]


def alternate_projection(fixture):
    # A real second, nonoverlapping authored version, never auto-selected for
    # this March order. Only deliberate ID corruption below references it.
    master = json.loads(fixture.master_path.read_bytes())
    authored = deepcopy(master["facilities"][0]["versions"][0])
    authored.update(version_id="component-April", valid_from="2026-04-01", valid_to="2026-04-30")
    authored["facility"]["facility_name"] = "Explicit April component"
    master["facilities"][0]["versions"].append(authored)
    fixture.master_path.write_text(json.dumps(master, ensure_ascii=False), encoding="utf-8")
    with fixture.session_scope() as session:
        selection, _snapshot = lineage.resolve(session, "F", date(2026, 4, 1))
        _, projection = lineage.materialize(session, selection)
        assert lineage.verify_projection(projection)["authored_version"]["version_id"] == "component-April"
        assert projection.id != fixture.projection_id
        return projection.id


FAULTS = [
    ("row-none-meta-valid", "template_version_required"),
    ("meta-other", "template_version_mismatch"),
    ("order-other", "template_version_mismatch"),
    ("order-none", "template_version_required"),
    ("foreign-owner", "template_version_mismatch"),
    ("json-deleted-version", "template_version_mismatch"),
    ("json-changed-version", "template_version_mismatch"),
    ("projection-corrupt", "template_version_mismatch"),
    ("month-only", "week_range_required"),
    ("week-missing", "week_range_required"),
    ("meta-start", "template_version_mismatch"),
    ("meta-end", "template_version_mismatch"),
    ("meta-effective", "template_version_mismatch"),
    ("meta-week-code", "template_version_mismatch"),
    ("meta-date-invalid", "week_range_invalid"),
]


def inject_fault(fixture, fault):
    other = alternate_projection(fixture) if fault in {"meta-other", "order-other"} else None
    with fixture.session_scope() as session:
        order = session.get(Order, fixture.order_id)
        state = session.get(OrderWorkflowState, fixture.order_id)
        assert order.received_at is not None  # Never a replacement meal date.
        if fault == "row-none-meta-valid":
            assert state.secondary_actions_json["workflow_v2"]["template_version_id"] == fixture.projection_id
            state.template_version_id = None
        elif fault == "meta-other":
            write_meta(session, fixture, lambda meta: meta.update(template_version_id=other))
        elif fault == "order-other":
            order.template_version_id = other
        elif fault == "order-none":
            order.template_version_id = None
        elif fault == "foreign-owner":
            order.facility_code = "COMPONENT-FOREIGN"
            write_meta(session, fixture, lambda meta: meta.update(facility_id="COMPONENT-FOREIGN"))
        elif fault == "projection-corrupt":
            projection = session.get(FacilityTemplateVersion, fixture.projection_id)
            config = deepcopy(projection.config_json)
            config["facility_name"] = "Deliberately corrupt projection"
            projection.config_json = config
        elif fault in {"month-only", "week-missing"}:
            order.week_code = "2026-03" if fault == "month-only" else None
        elif fault.startswith("meta-"):
            key, value = {
                "meta-start": ("week_start", "2026-03-02"),
                "meta-end": ("week_end", "2026-03-08"),
                "meta-effective": ("effective_facility_config_date", "2026-03-02"),
                "meta-week-code": ("week_code", "2026-03@2026-03-02~2026-03-08"),
                "meta-date-invalid": ("effective_facility_config_date", "not-a-date"),
            }[fault]
            write_meta(session, fixture, lambda meta: meta.update({key: value}))
    if fault in {"json-deleted-version", "json-changed-version"}:
        master = json.loads(fixture.master_path.read_bytes())
        root = master["facilities"][0]
        # A valid root template is present but cannot replace the selected version.
        root.update(deepcopy(fixture.authored))
        if fault == "json-deleted-version":
            root["versions"] = []
        else:
            root["versions"][0]["facility"]["facility_name"] = "Changed same authored version"
        fixture.master_path.write_text(json.dumps(master, ensure_ascii=False), encoding="utf-8")


def guard(fixture):
    with fixture.session_scope() as session:
        order = session.get(Order, fixture.order_id)
        state = session.get(OrderWorkflowState, fixture.order_id)
        return workflow._require_existing_workflow_template_version(
            session, order=order, workflow=state, meta=workflow._workflow_meta(state),
        )


def assert_valid_reads(fixture, expected_saved, state_name):
    with unchanged_read(fixture):
        assert guard(fixture) == (fixture.projection_id, None)
        current, error = workflow.get_workflow_readonly(fixture.order_id)
        assert error is None
        assert current["state"] == state_name
        assert current["template_version_id"] == fixture.projection_id
        assert current["selected_ocr_result_id"] == fixture.evidence_id
        assert current["saved_sheet_id"] == fixture.draft_id
        assert current["blockers"] == []
        results, error = workflow.list_ocr_results(fixture.order_id)
        assert error is None
        assert results["candidate_template_version_id"] == fixture.projection_id
        assert results["hidden_template_mismatch_result_count"] == 0
        assert [(row["ocr_result_id"], row["template_version_id"], row["selectable"])
                for row in results["results"]] == [(fixture.evidence_id, fixture.projection_id, True)]
        saved, error = workflow.get_saved_sheet(fixture.order_id)
        assert error is None
        assert (saved["saved_sheet_id"], saved["template_version_id"], saved["sheet"]) == (
            fixture.draft_id, fixture.projection_id, expected_saved,
        )
        inspection, error = workflow.get_inspection(fixture.order_id)
        assert error is None
        assert inspection["workflow"]["template_version_id"] == fixture.projection_id
        assert inspection["saved_sheet"]["sheet"] == expected_saved
        assert_saved(fixture, expected_saved)


@pytest.mark.parametrize("rename_root", [False, True])
def test_real_bagging_confirmed_reads_keep_selected_version_and_all_quantities(accepted_phase, rename_root):
    fixture, candidate, phase = accepted_phase
    original = fixture.master_path.read_bytes()
    saved = fixture.saved()
    try:
        master = json.loads(original)
        versions = deepcopy(master["facilities"][0]["versions"])
        if rename_root:
            master["facilities"][0]["facility_name"] = "Different unversioned root"
            fixture.master_path.write_text(json.dumps(master, ensure_ascii=False), encoding="utf-8")
        assert json.loads(fixture.master_path.read_bytes())["facilities"][0]["versions"] == versions
        assert [(line["source_row_index"], line["source_col_index"], line["quantity_original"])
                for line in candidate["lines"]] == [(0, 4, 7), (3, 4, 0)]
        assert_valid_reads(fixture, saved, "confirmed" if phase == "confirmed" else "output_review")
        with fixture.session_scope() as session:
            lines = session.execute(select(OrderLine).where(
                OrderLine.order_id == fixture.order_id,
            ).order_by(OrderLine.menu_name)).scalars().all()
            actual = [(line.menu_name, line.quantity_original, line.quantity_corrected) for line in lines]
            assert actual == ([("選択献立0", 7, None), ("選択献立3", 0, None)] if phase == "confirmed" else [])
    finally:
        fixture.master_path.write_bytes(original)


def test_absent_meta_version_is_not_mandatory_and_real_confirmation_keeps_zero(selected_owned):
    fixture = selected_owned
    with fixture.session_scope() as session:
        write_meta(session, fixture, lambda meta: meta.pop("template_version_id"))
    saved = fixture.saved()
    assert_valid_reads(fixture, saved, "sheet_saved")
    actual_bagging(fixture)
    result, error = workflow.final_confirm(fixture.order_id, expected_revision=revision(fixture))
    assert error is None and result is not None
    assert_valid_reads(fixture, saved, "confirmed")
    with fixture.session_scope() as session:
        quantities = session.execute(select(OrderLine.quantity_original).where(
            OrderLine.order_id == fixture.order_id,
        ).order_by(OrderLine.menu_name)).scalars().all()
        assert quantities == [7, 0]


@pytest.mark.parametrize("fault,reason", FAULTS, ids=[item[0] for item in FAULTS])
def test_shared_guard_and_public_reads_reject_direct_faults_without_writes(selected_owned, fault, reason):
    fixture = selected_owned
    saved = fixture.saved()
    pristine = database_snapshot()
    inject_fault(fixture, fault)
    if not fault.startswith("json-"):
        assert database_snapshot() != pristine
    with unchanged_read(fixture):
        assert guard(fixture) == (None, reason)
        current, error = workflow.get_workflow_readonly(fixture.order_id)
        assert error is None
        assert current["blockers"] == [reason]
        assert current["state"] == reason
        assert current["menu_amount_resolution"] == {
            "status": "unresolved", "blocker": reason, "message": reason, "affected_lines": [],
        }
        results, error = workflow.list_ocr_results(fixture.order_id)
        assert error is None
        assert results["blockers"] == [reason]
        assert results["candidate_template_version_id"] is None
        assert results["results"] == []
        assert results["hidden_template_mismatch_result_count"] == 1
        assert workflow.get_saved_sheet(fixture.order_id) == (None, reason)
        assert workflow.get_inspection(fixture.order_id) == (None, reason)
        assert_saved(fixture, saved)


COMMAND_FAULTS = [item for item in FAULTS if item[0] in {
    "row-none-meta-valid", "meta-other", "order-other", "order-none",
    "json-changed-version", "month-only", "meta-effective",
}]


def command(fixture, name):
    expected_revision = revision(fixture)
    if name == "queue":
        return workflow.mark_ocr_run_queued(
            fixture.order_id, "COMPONENT-UNRESERVED-JOB", input_reference=str(fixture.pdf),
            request_mode="ocr_rerun", expected_revision=expected_revision,
        )
    if name == "select":
        return workflow.select_ocr_result(fixture.order_id, fixture.evidence_id, expected_revision=expected_revision)
    if name == "save":
        return workflow.save_sheet(order_id=fixture.order_id, sheet=fixture.saved(), expected_revision=expected_revision)
    return getattr(workflow, name)(fixture.order_id, expected_revision=expected_revision)


@pytest.mark.parametrize("name", ["queue", "select", "save", "run_bagging", "final_confirm"])
@pytest.mark.parametrize("fault,reason", COMMAND_FAULTS, ids=[item[0] for item in COMMAND_FAULTS])
def test_adoption_stops_before_job_draft_or_quantity_writes(selected_owned, name, fault, reason):
    fixture = selected_owned
    if name == "final_confirm":
        actual_bagging(fixture)
    saved = fixture.saved()
    inject_fault(fixture, fault)
    before = database_snapshot()
    result, error = command(fixture, name)
    assert error == reason
    after = database_snapshot()
    # Queue/select/save may persist their existing explicit workflow blocker.
    # Every other table, including jobs/deliveries/artifacts/OrderLines, is exact.
    assert {key: value for key, value in after.items() if key != "order_workflow_states"} == {
        key: value for key, value in before.items() if key != "order_workflow_states"
    }
    if name in {"run_bagging", "final_confirm"}:
        assert result is None and after == before
    else:
        assert result["blockers"] == [reason]
        with fixture.session_scope() as session:
            state = session.get(OrderWorkflowState, fixture.order_id)
            previous = next(row for row in before["order_workflow_states"] if row["order_id"] == fixture.order_id)
            assert state.template_version_id == previous["template_version_id"]
            assert (state.evidence_run_id, state.draft_id, state.confirmed_snapshot_id) == (None, None, None)
    assert_saved(fixture, saved)


@pytest.mark.parametrize("name,fault,reason", [
    ("select", "evidence", "template_version_mismatch"),
    ("save", "evidence", "template_version_mismatch"),
    ("build", "evidence", "template_version_mismatch"),
    ("saved", "draft", "saved_sheet_template_mismatch"),
    ("run_bagging", "draft", "saved_sheet_template_mismatch"),
    ("final_confirm", "draft", "saved_sheet_template_mismatch"),
])
def test_evidence_and_saved_sheet_cannot_supply_a_different_version(selected_owned, name, fault, reason):
    fixture = selected_owned
    if name == "final_confirm":
        actual_bagging(fixture)
    other = alternate_projection(fixture)
    with fixture.session_scope() as session:
        model, identifier = (OrderOcrEvidenceRun, fixture.evidence_id) if fault == "evidence" else (OrderSheetDraft, fixture.draft_id)
        session.get(model, identifier).template_version_id = other
    before = database_snapshot()
    if name == "build":
        result, error = workflow.build_sheet_from_selected_ocr(fixture.order_id)
    elif name == "saved":
        result, error = workflow.get_saved_sheet(fixture.order_id)
    else:
        result, error = command(fixture, name)
    assert error == reason
    after = database_snapshot()
    assert {key: value for key, value in after.items() if key != "order_workflow_states"} == {
        key: value for key, value in before.items() if key != "order_workflow_states"
    }
    if name not in {"select", "save"}:
        assert result is None and after == before
    else:
        assert result["blockers"] == [reason]
        with fixture.session_scope() as session:
            state = session.get(OrderWorkflowState, fixture.order_id)
            assert state.template_version_id == fixture.projection_id
            assert (state.evidence_run_id, state.draft_id, state.confirmed_snapshot_id) == (None, None, None)
    assert_saved(fixture, fixture.payload)


def test_template_only_guard_needs_no_monthly_data_or_policy(selected_owned, monkeypatch, tmp_path):
    fixture = selected_owned
    missing = tmp_path / "explicitly-absent-ingest-policy.json"
    assert not missing.exists()
    monkeypatch.setattr(config_service, "INGEST_POLICY_PATH", missing)
    with fixture.session_scope() as session:
        for model in (MonthlyMenuEntry, MonthlyMenuItem, MonthlyMenu):
            session.query(model).delete(synchronize_session=False)
        for model in (MonthlyMenuEntry, MonthlyMenuItem, MonthlyMenu):
            assert session.query(model).count() == 0
    with unchanged_read(fixture):
        assert guard(fixture) == (fixture.projection_id, None)


@pytest.mark.parametrize("fault", ["required-other", "row-none", "meta-other"])
def test_evidence_resolver_checks_required_row_and_meta_without_restoration(selected_owned, fault):
    fixture = selected_owned
    other = alternate_projection(fixture)
    if fault == "row-none":
        inject_fault(fixture, "row-none-meta-valid")
    elif fault == "meta-other":
        with fixture.session_scope() as session:
            write_meta(session, fixture, lambda meta: meta.update(template_version_id=other))
    with unchanged_read(fixture), fixture.session_scope() as session:
        state = session.get(OrderWorkflowState, fixture.order_id)
        evidence = session.get(OrderOcrEvidenceRun, fixture.evidence_id)
        meta = workflow._workflow_meta(state)
        required = other if fault == "required-other" else fixture.projection_id
        assert workflow._resolve_evidence_template_version(
            evidence, state, meta, required_template_version_id=required,
        ) == (None, "template_version_required" if fault == "row-none" else "template_version_mismatch")
        assert workflow._effective_workflow_template_version_id(state, meta) == (
            fixture.projection_id if fault == "required-other" else None
        )


def test_failed_explicit_context_keeps_old_order_selection_until_operator_reconfirms(selected_owned):
    fixture = selected_owned
    master = json.loads(fixture.master_path.read_bytes())
    master["facilities"][0].update(deepcopy(fixture.authored))
    fixture.master_path.write_text(json.dumps(master, ensure_ascii=False), encoding="utf-8")
    with unchanged_read(fixture), fixture.session_scope() as session:
        assert config_service.get_facility_config("F")["fax_template"] == fixture.authored["fax_template"]
        selected, selection_error = facility_template_version_service.resolve_single_active_template_version(
            session, "F", effective_date="2026-03-01",
        )
        assert selection_error is None
        assert selected.id == fixture.projection_id
        assert selected.source == lineage.SOURCE
        assert selected.template_id == fixture.authored["fax_template"]["template_id"]
    result, error = workflow.confirm_context(
        order_id=fixture.order_id, facility_id="F", week_start="2026-03-01", week_end="2026-03-07",
        template_id="COMPONENT-UNDECLARED-TEMPLATE",
        expected_revision=revision(fixture),
    )
    assert result is None and error == "template_version_mismatch"
    with unchanged_read(fixture):
        current, error = workflow.get_workflow_readonly(fixture.order_id)
        assert error is None
        assert current["state"] == "template_version_mismatch"
        assert current["template_version_id"] is None
        with fixture.session_scope() as session:
            state = session.get(OrderWorkflowState, fixture.order_id)
            assert state.template_version_id is None
            assert state.secondary_actions_json["workflow_v2"]["template_version_id"] is None
            assert session.get(Order, fixture.order_id).template_version_id == fixture.projection_id
    result, error = workflow.confirm_context(
        order_id=fixture.order_id, facility_id="F", week_start="2026-03-01", week_end="2026-03-07",
        template_id=fixture.authored["fax_template"]["template_id"],
        expected_revision=revision(fixture),
    )
    assert error is None
    assert (result["state"], result["template_version_id"]) == ("context_confirmed", fixture.projection_id)
    with unchanged_read(fixture):
        assert guard(fixture) == (fixture.projection_id, None)
        with fixture.session_scope() as session:
            state = session.get(OrderWorkflowState, fixture.order_id)
            assert state.template_version_id == session.get(Order, fixture.order_id).template_version_id == fixture.projection_id
            assert state.secondary_actions_json["workflow_v2"]["template_version_id"] == fixture.projection_id


@pytest.mark.parametrize("duplicate", ["missing", "blank", "matching"])
def test_optional_confirmed_duplicate_preserves_public_reads_and_quantities(confirmed_receipt, duplicate):
    fixture, _candidate = confirmed_receipt
    saved = fixture.saved()
    with fixture.session_scope() as session:
        state = session.get(OrderWorkflowState, fixture.order_id)
        snapshot_id = state.confirmed_snapshot_id
        assert snapshot_id
        snapshot = session.get(OrderConfirmedSnapshot, snapshot_id)
        assert artifacts.confirmed_snapshot_artifact_payloads(
            session, snapshot, expected_order_id=fixture.order_id,
        ) is not None

        def set_duplicate(meta):
            if duplicate == "missing":
                meta.pop("confirmed_snapshot_id", None)
            else:
                meta["confirmed_snapshot_id"] = " \t " if duplicate == "blank" else snapshot_id
        write_meta(session, fixture, set_duplicate)
    assert_valid_reads(fixture, saved, "confirmed")
    with unchanged_read(fixture), fixture.session_scope() as session:
        assert session.get(OrderWorkflowState, fixture.order_id).confirmed_snapshot_id == snapshot_id
        lines = session.execute(select(OrderLine).where(
            OrderLine.order_id == fixture.order_id,
        ).order_by(OrderLine.menu_name)).scalars().all()
        assert [(line.menu_name, line.quantity_original, line.quantity_corrected, line.confirmed_snapshot_id)
                for line in lines] == [
            ("選択献立0", 7, None, snapshot_id), ("選択献立3", 0, None, snapshot_id),
        ]


@pytest.mark.parametrize("fault,reason", [
    ("duplicate-other", "confirmed_snapshot_lineage_mismatch"),
    ("snapshot-digest", "confirmed_snapshot_lineage_mismatch"),
    ("artifact-missing", "confirmed_snapshot_lineage_mismatch"),
    ("artifact-tampered", "confirmed_snapshot_lineage_mismatch"),
])
def test_direct_confirmed_db_corruption_remains_blocked_without_read_writes(confirmed_receipt, fault, reason):
    """Deliberate DB corruption, not a normal operator save or provider result."""
    fixture, _candidate = confirmed_receipt
    saved = fixture.saved()
    pristine = database_snapshot()
    with fixture.session_scope() as session:
        state = session.get(OrderWorkflowState, fixture.order_id)
        snapshot = session.get(OrderConfirmedSnapshot, state.confirmed_snapshot_id)
        output_id = snapshot.output_bundle_id
        assert artifacts.confirmed_snapshot_artifact_payloads(
            session, snapshot, expected_order_id=fixture.order_id,
        ) is not None
        if fault == "duplicate-other":
            write_meta(session, fixture, lambda meta: meta.update(confirmed_snapshot_id="COMPONENT-FOREIGN-SNAPSHOT"))
        elif fault == "snapshot-digest":
            snapshot.snapshot_digest = "0" * 64
        elif fault == "artifact-tampered":
            artifact = session.get(OrderOutputBundle, output_id)
            payload = deepcopy(artifact.payload_json)
            payload["status"] = "COMPONENT-TAMPERED"
            artifact.payload_json = payload
            assert artifacts.payload_digest(payload) != artifact.payload_digest
    if fault == "artifact-missing":
        # Deliberately break the FK-backed receipt in this disposable SQLite DB.
        # Restore enforcement before any production reader runs.
        with fixture.engine.connect() as connection:
            connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
            connection.commit()
            try:
                assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 0
                deleted = connection.execute(OrderOutputBundle.__table__.delete().where(
                    OrderOutputBundle.id == output_id,
                ))
                assert deleted.rowcount == 1
                connection.commit()
            finally:
                connection.rollback()
                connection.exec_driver_sql("PRAGMA foreign_keys=ON")
                assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
                connection.commit()
    assert database_snapshot() != pristine
    with unchanged_read(fixture):
        current, error = workflow.get_workflow_readonly(fixture.order_id)
        assert error is None
        assert current["state"] == reason
        assert current["blockers"] == [reason]
        results, error = workflow.list_ocr_results(fixture.order_id)
        assert error is None
        assert results["workflow_state"] == reason
        assert results["blockers"] == [reason]
        inspection, error = workflow.get_inspection(fixture.order_id)
        assert error is None
        assert inspection["workflow"]["state"] == reason
        assert inspection["workflow"]["blockers"] == [reason]
        assert inspection["saved_sheet"]["sheet"] == saved
        draft, error = workflow.get_saved_sheet(fixture.order_id)
        assert error is None and draft["sheet"] == saved
        assert_saved(fixture, saved)
        with fixture.session_scope() as session:
            snapshot = session.get(OrderConfirmedSnapshot, session.get(
                OrderWorkflowState, fixture.order_id,
            ).confirmed_snapshot_id)
            packet = artifacts.confirmed_snapshot_artifact_payloads(
                session, snapshot, expected_order_id=fixture.order_id,
            )
            assert (packet is not None) == (fault == "duplicate-other")
            lines = session.execute(select(OrderLine).where(
                OrderLine.order_id == fixture.order_id,
            ).order_by(OrderLine.menu_name)).scalars().all()
            assert [(line.menu_name, line.quantity_original, line.quantity_corrected)
                    for line in lines] == [("選択献立0", 7, None), ("選択献立3", 0, None)]
