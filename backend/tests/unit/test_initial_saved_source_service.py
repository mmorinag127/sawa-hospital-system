"""Synthetic owned SQLite proof of selected saved-data reads, not live migration."""

from copy import deepcopy
from datetime import date, datetime
import json
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event, insert, select, update
from sqlalchemy.orm import Session

from src.models.facility import Facility
from src.models.facility_template_version import FacilityTemplateVersion
from src.models.order import Order, OrderLine
from src.models.order_confirmed_snapshot import OrderConfirmedSnapshot
from src.models.order_ocr_evidence_run import OrderOcrEvidenceRun
from src.models.order_output_artifact import OrderBaggingResult, OrderOutputBundle
from src.models.order_sheet_draft import OrderSheetDraft
from src.models.order_workflow_state import OrderWorkflowState
from src.services import initial_saved_source_service as service


MODELS = (Facility, FacilityTemplateVersion, Order, OrderOcrEvidenceRun, OrderSheetDraft,
          OrderBaggingResult, OrderOutputBundle, OrderConfirmedSnapshot, OrderWorkflowState, OrderLine)
SHEET = {"fields": ["unknown", "qty.regular_x", "remarks"],
         "rows": [[None, 0, "raw"], ["?", 6, ""]], "row_ids": ["row-B", "row-A"],
         "template_version_id": "legacy-template", "unknown": {"bool": True, "float": 0.0}}


@pytest.fixture
def saved(monkeypatch, tmp_path):
    engine = create_engine("sqlite:///" + str(tmp_path / "saved-source.sqlite"))
    @event.listens_for(engine, "connect")
    def foreign_keys(dbapi, _):
        dbapi.execute("PRAGMA foreign_keys=ON")
        assert dbapi.execute("PRAGMA foreign_keys").fetchone()[0] == 1

    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    with monkeypatch.context() as migration_environment:
        migration_environment.setenv("ALEMBIC_DB_URI", str(engine.url))
        migration_environment.setenv("DB_URI", str(engine.url))
        with engine.connect() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
    with Session(engine, autoflush=True) as session:
        def add(model, **values):
            session.execute(insert(model.__table__).values(**values))
        add(Facility, id="F", name="Synthetic saved facility")
        for identity in ("T", "T-new"):
            add(FacilityTemplateVersion, id=identity, facility_id="F", version=identity,
                status="archived", template_id="old-template", source="saved-old",
                columns_json=[{"name": "qty.regular_x"}], cells_json=[{"raw": 0}],
                config_json={"unknown": None}, template_digest="original-digest" if identity == "T" else "unselected-digest",
                valid_from=None, valid_to=None)
        for identity in ("O", "OTHER"):
            add(Order, id=identity, facility_code="F", template_version_id="T",
                document_uri="saved.pdf", message_id="synthetic", week_code="saved-week")
        for identity in ("E", "E-new"):
            add(OrderOcrEvidenceRun, id=identity, order_id="O", template_version_id="T",
                schema_version="old", source="saved-old-ocr", payload_json={"raw": "unchanged"},
                artifact_digest="saved-digest" if identity == "E" else "unselected-evidence-digest")
        for identity, evidence in (("D", "E"), ("D-new", "E-new")):
            add(OrderSheetDraft, id=identity, order_id="O", template_version_id="T",
                base_evidence_run_id=evidence, draft_sheet_json=deepcopy(SHEET), draft_state="saved")
        add(OrderBaggingResult, id="B", order_id="O", source_saved_sheet_id="D",
            source_ocr_result_id="E", template_version_id="T", payload_digest="raw-bag-digest",
            payload_json={"materialization_candidate": {"lines": [{"quantity": 0}, {"quantity": 6}],
                          "unknown": None}, "bag_rows": [{"quantity": 6, "raw": "?"}]})
        add(OrderOutputBundle, id="P", order_id="O", source_bagging_result_id="B",
            source_saved_sheet_id="D", source_ocr_result_id="E", template_version_id="T",
            payload_json={"files": ["saved.pdf"], "unknown": None}, payload_digest="raw-output-digest")
        add(OrderConfirmedSnapshot, id="C", order_id="O", template_version_id="T", draft_id="D",
            saved_sheet_id="D", bagging_result_id="B", output_bundle_id="P",
            snapshot_json={"quantity": 6, "zero": 0, "unknown": None}, snapshot_digest="saved-confirmed")
        add(OrderWorkflowState, order_id="O", template_version_id="T", evidence_run_id="E", draft_id="D",
            confirmed_snapshot_id="C", state="confirmed", secondary_actions_json={"workflow_v2": {
                "bagging_result_id": "B", "output_bundle_id": "P", "unknown": None}})
        for identity, quantity in (("L2", None), ("L1", 0)):
            add(OrderLine, id=identity, order_id="O", confirmed_snapshot_id="C", date=date(2026, 1, 2),
                quantity_original=6, quantity_corrected=quantity, change_note="?", line_digest="raw")
        session.commit()
        yield session
    engine.dispose()


def all_rows(session):
    with session.no_autoflush:
        return {model.__tablename__: [dict(row) for row in session.execute(
            select(*model.__table__.c).order_by(*model.__table__.primary_key.columns)
        ).mappings()] for model in MODELS}


def test_exact_saved_values_selected_only_json_dates_and_no_writes(saved):
    before = all_rows(saved)
    statements = []
    def audit(_connection, _cursor, statement, *_args):
        statements.append(statement)
        assert statement.lstrip().upper().startswith("SELECT")
    event.listen(saved.bind, "before_cursor_execute", audit)
    try:
        snapshot = service.capture_saved_source(saved, "O")
        receipt = service.source_receipt(snapshot)
        assert service.validate_saved_source(saved, receipt) == snapshot
    finally:
        event.remove(saved.bind, "before_cursor_execute", audit)
    assert statements
    assert set(snapshot) == {"order", "workflow", "draft", "evidence", "template", "confirmed", "bagging", "output", "lines"}
    assert snapshot["draft"]["id"] == "D" and snapshot["evidence"]["id"] == "E"
    assert snapshot["template"]["valid_from"] is None
    assert snapshot["template"]["cells_json"] == [{"raw": 0}]
    assert snapshot["evidence"]["source"] == "saved-old-ocr"
    assert [row["id"] for row in snapshot["lines"]] == ["L1", "L2"]
    assert snapshot["lines"][0]["date"] == "2026-01-02"
    assert set(snapshot["lines"][0]) == set(OrderLine.__table__.c.keys())
    assert json.loads(json.dumps(snapshot, allow_nan=False)) == snapshot
    sheet = service.current_sheet_payload(snapshot)
    assert json.dumps(sheet, separators=(",", ":")) == json.dumps(SHEET, separators=(",", ":"))
    sheet["rows"][0][1] = 99
    output = service.accepted_output_payload(snapshot)
    assert output == {"materialization_candidate": snapshot["bagging"]["payload_json"]["materialization_candidate"],
                      "bag_rows": [{"quantity": 6, "raw": "?"}],
                      "confirmed_snapshot": {"quantity": 6, "zero": 0, "unknown": None}}
    output["bag_rows"][0]["quantity"] = 99
    assert snapshot["draft"]["draft_sheet_json"] == SHEET
    assert snapshot["bagging"]["payload_json"]["bag_rows"][0]["quantity"] == 6
    assert all_rows(saved) == before


def test_dirty_identity_cache_and_pending_rows_never_autoflush(saved):
    cached = saved.get(Order, "O")
    cached.week_code = "pending-change"
    pending = Order(id="pending", document_uri="pending.pdf", message_id="pending")
    saved.add(pending)
    before = all_rows(saved)
    snapshot = service.capture_saved_source(saved, "O")
    assert snapshot["order"]["week_code"] == "saved-week"
    assert cached.week_code == "pending-change" and cached in saved.dirty and pending in saved.new
    assert all_rows(saved) == before


def test_absent_optional_selections_remain_none(saved):
    saved.execute(update(OrderWorkflowState.__table__).values(state="output_review", confirmed_snapshot_id=None, secondary_actions_json=None))
    snapshot = service.capture_saved_source(saved, "O")
    assert snapshot["confirmed"] is snapshot["bagging"] is snapshot["output"] is None
    assert service.source_receipt(snapshot)["selected"] == {
        "draft_id": "D", "evidence_run_id": "E", "template_version_id": "T",
        "confirmed_snapshot_id": None, "bagging_result_id": None, "output_bundle_id": None}
    with pytest.raises(ValueError, match="output_invalid"):
        service.accepted_output_payload(snapshot)


def test_new_unselected_facility_version_does_not_rebind_saved_result(saved):
    snapshot = service.capture_saved_source(saved, "O")
    receipt = service.source_receipt(snapshot)
    saved.execute(update(FacilityTemplateVersion.__table__).where(FacilityTemplateVersion.id == "T-new").values(
        valid_from=date(2026, 10, 14), config_json={"changed_setting": 99}, columns_json=[{"new": True}]))
    assert service.validate_saved_source(saved, receipt) == snapshot
    assert service.current_sheet_payload(snapshot) == SHEET
    assert snapshot["template"]["valid_from"] is None


def test_saved_outputs_without_selected_confirmation_keep_none(saved):
    saved.execute(update(OrderWorkflowState.__table__).values(state="output_review", confirmed_snapshot_id=None))
    snapshot = service.capture_saved_source(saved, "O")
    output = service.accepted_output_payload(snapshot)
    assert output["confirmed_snapshot"] is None
    assert output["bag_rows"] == [{"quantity": 6, "raw": "?"}]


@pytest.mark.parametrize("state", ["output_review", "confirmed"])
def test_old_confirmation_is_history_until_current_workflow_confirmed(saved, state):
    saved.execute(insert(OrderBaggingResult.__table__).values(
        id="B-old", order_id="O", source_saved_sheet_id="D-new", source_ocr_result_id="E-new",
        template_version_id="T-new", payload_json={"historical": True}, payload_digest="old-bag"))
    saved.execute(insert(OrderOutputBundle.__table__).values(
        id="P-old", order_id="O", source_bagging_result_id="B-old", source_saved_sheet_id="D-new",
        source_ocr_result_id="E-new", template_version_id="T-new",
        payload_json={"historical": True}, payload_digest="old-output"))
    saved.execute(update(OrderConfirmedSnapshot.__table__).values(
        draft_id="D-new", saved_sheet_id="D-new", template_version_id="T-new",
        bagging_result_id="B-old", output_bundle_id="P-old"))
    saved.execute(update(OrderWorkflowState.__table__).values(state=state))
    before = all_rows(saved)
    if state == "confirmed":
        with pytest.raises(ValueError, match="confirmed_selection_mismatch"):
            service.capture_saved_source(saved, "O")
    else:
        snapshot = service.capture_saved_source(saved, "O")
        receipt = service.source_receipt(snapshot)
        assert service.validate_saved_source(saved, receipt) == snapshot
        assert snapshot["workflow"]["state"] == "output_review"
        assert snapshot["workflow"]["secondary_actions_json"] == {"workflow_v2": {
            "bagging_result_id": "B", "output_bundle_id": "P", "unknown": None}}
        assert snapshot["draft"]["id"] == "D" and snapshot["draft"]["draft_sheet_json"] == SHEET
        assert snapshot["bagging"]["id"] == "B" and snapshot["output"]["id"] == "P"
        assert snapshot["confirmed"]["draft_id"] == "D-new"
        assert snapshot["confirmed"]["bagging_result_id"] == "B-old"
        assert snapshot["confirmed"]["output_bundle_id"] == "P-old"
        assert snapshot["confirmed"]["template_version_id"] == "T-new"
        assert snapshot["confirmed"]["snapshot_digest"] == "saved-confirmed"
        assert snapshot["confirmed"]["snapshot_json"] == {"quantity": 6, "zero": 0, "unknown": None}
        assert service.accepted_output_payload(snapshot) == {
            "materialization_candidate": snapshot["bagging"]["payload_json"]["materialization_candidate"],
            "bag_rows": [{"quantity": 6, "raw": "?"}],
            "confirmed_snapshot": None}
    assert all_rows(saved) == before


def test_output_review_still_rejects_foreign_historical_confirmation(saved):
    saved.execute(update(OrderWorkflowState.__table__).values(state="output_review"))
    saved.execute(update(OrderConfirmedSnapshot.__table__).values(order_id="OTHER"))
    before = all_rows(saved)
    with pytest.raises(ValueError, match="foreign_order"):
        service.capture_saved_source(saved, "O")
    assert all_rows(saved) == before


def test_confirmed_requires_selected_confirmation_in_capture_and_output(saved):
    snapshot = service.capture_saved_source(saved, "O")
    saved.execute(update(OrderWorkflowState.__table__).values(confirmed_snapshot_id=None))
    before = all_rows(saved)
    with pytest.raises(ValueError, match="confirmed_missing"):
        service.capture_saved_source(saved, "O")
    snapshot["workflow"]["confirmed_snapshot_id"] = None
    snapshot["confirmed"] = None
    with pytest.raises(ValueError, match="confirmed_missing"):
        service.accepted_output_payload(snapshot)
    assert all_rows(saved) == before


@pytest.mark.parametrize("missing", ["template_version_id", "bagging_result_id", "output_bundle_id", "both-draft-ids"])
def test_confirmed_cannot_claim_unbound_current_saved_sources(saved, missing):
    snapshot = service.capture_saved_source(saved, "O")
    values = {"draft_id": None, "saved_sheet_id": None} if missing == "both-draft-ids" else {missing: None}
    saved.execute(update(OrderConfirmedSnapshot.__table__).values(**values))
    before = all_rows(saved)
    with pytest.raises(ValueError, match="confirmed_selection_mismatch"):
        service.capture_saved_source(saved, "O")
    snapshot["confirmed"].update(values)
    with pytest.raises(ValueError, match="confirmed_selection_mismatch"):
        service.accepted_output_payload(snapshot)
    assert all_rows(saved) == before


@pytest.mark.parametrize("absent", ["draft_id", "saved_sheet_id"])
def test_confirmed_keeps_existing_single_explicit_draft_binding(saved, absent):
    saved.execute(update(OrderConfirmedSnapshot.__table__).values(**{absent: None}))
    snapshot = service.capture_saved_source(saved, "O")
    assert snapshot["confirmed"][absent] is None
    assert service.accepted_output_payload(snapshot)["confirmed_snapshot"] == {
        "quantity": 6, "zero": 0, "unknown": None}


def test_output_review_never_exposes_history_as_current_even_when_references_match(saved):
    saved.execute(update(OrderWorkflowState.__table__).values(state="output_review"))
    before = all_rows(saved)
    snapshot = service.capture_saved_source(saved, "O")
    assert snapshot["confirmed"]["id"] == "C"
    assert snapshot["confirmed"]["snapshot_json"] == {"quantity": 6, "zero": 0, "unknown": None}
    assert service.accepted_output_payload(snapshot)["confirmed_snapshot"] is None
    assert all_rows(saved) == before


def test_output_review_historical_contents_remain_part_of_receipt_hash(saved):
    saved.execute(update(OrderWorkflowState.__table__).values(state="output_review"))
    receipt = service.source_receipt(service.capture_saved_source(saved, "O"))
    saved.execute(update(OrderConfirmedSnapshot.__table__).values(snapshot_json={"quantity": 8}))
    before = all_rows(saved)
    snapshot = service.capture_saved_source(saved, "O")
    assert snapshot["confirmed"]["snapshot_json"] == {"quantity": 8}
    assert service.source_receipt(snapshot)["source_sha256"] != receipt["source_sha256"]
    assert service.accepted_output_payload(snapshot)["confirmed_snapshot"] is None
    with pytest.raises(ValueError, match="^initial_saved_source_changed$"):
        service.validate_saved_source(saved, receipt)
    assert all_rows(saved) == before


def test_internal_receipt_transport_only_is_excluded_from_hash_not_snapshot(saved):
    original = service.capture_saved_source(saved, "O")
    receipt = service.source_receipt(original)
    for transport in (receipt, {"source": "transport-only", "unknown": None}):
        metadata = deepcopy(original["workflow"]["secondary_actions_json"])
        metadata["_initial_saved_import_receipt"] = transport
        saved.execute(update(OrderWorkflowState.__table__).values(secondary_actions_json=metadata))
        snapshot = service.capture_saved_source(saved, "O")
        before = deepcopy(snapshot)
        assert snapshot["workflow"]["secondary_actions_json"]["_initial_saved_import_receipt"] == transport
        assert service.source_receipt(snapshot) == receipt
        assert service.validate_saved_source(saved, receipt) == snapshot
        assert snapshot == before
    assert "_initial_saved_import_receipt" not in original["workflow"]["secondary_actions_json"]


@pytest.mark.parametrize("change", ["metadata", "nested-same-name", "revision", "confirmed-time"])
def test_only_exact_internal_transport_key_is_excluded(saved, change):
    receipt = service.source_receipt(service.capture_saved_source(saved, "O"))
    if change in {"metadata", "nested-same-name"}:
        actions = deepcopy(service.capture_saved_source(saved, "O")["workflow"]["secondary_actions_json"])
        if change == "metadata":
            actions["ordinary_unknown"] = {"value": None}
        else:
            actions["workflow_v2"]["_initial_saved_import_receipt"] = "ordinary nested metadata"
        saved.execute(update(OrderWorkflowState.__table__).values(secondary_actions_json=actions))
    elif change == "revision":
        saved.execute(update(OrderWorkflowState.__table__).values(revision=2))
    else:
        saved.execute(update(OrderConfirmedSnapshot.__table__).values(confirmed_at=datetime(2026, 1, 3, 12, 0)))
    assert service.source_receipt(service.capture_saved_source(saved, "O"))["source_sha256"] != receipt["source_sha256"]
    with pytest.raises(ValueError, match="^initial_saved_source_changed$"):
        service.validate_saved_source(saved, receipt)


@pytest.mark.parametrize("change", ["missing-order", "missing-draft", "missing-bagging", "missing-output",
    "foreign-draft", "foreign-evidence", "foreign-confirmed", "foreign-bagging", "foreign-output",
    "foreign-template", "template-mismatch", "wrong-base-evidence", "wrong-bag-source",
    "wrong-output-source", "wrong-confirmed-source", "unsaved", "missing-rows", "missing-fields", "missing-row-ids"])
def test_missing_foreign_or_conflicting_selected_sources_stop(saved, change):
    if change == "missing-order":
        with pytest.raises(ValueError):
            service.capture_saved_source(saved, "absent")
        return
    if change == "missing-draft":
        saved.execute(update(OrderWorkflowState.__table__).values(draft_id=None))
    elif change in {"missing-bagging", "missing-output"}:
        meta = {"bagging_result_id": "B", "output_bundle_id": "P"}
        meta["bagging_result_id" if change == "missing-bagging" else "output_bundle_id"] = "missing"
        saved.execute(update(OrderWorkflowState.__table__).values(secondary_actions_json={"workflow_v2": meta}))
    elif change.startswith("foreign-") and change != "foreign-template":
        model = {"foreign-draft": OrderSheetDraft, "foreign-evidence": OrderOcrEvidenceRun,
                 "foreign-confirmed": OrderConfirmedSnapshot, "foreign-bagging": OrderBaggingResult,
                 "foreign-output": OrderOutputBundle}[change]
        saved.execute(update(model.__table__).values(order_id="OTHER"))
    elif change == "foreign-template":
        saved.execute(insert(Facility.__table__).values(id="G", name="Other facility"))
        saved.execute(update(FacilityTemplateVersion.__table__).values(facility_id="G"))
    elif change == "template-mismatch":
        saved.execute(update(OrderSheetDraft.__table__).values(template_version_id="T-new"))
    elif change == "wrong-base-evidence":
        saved.execute(update(OrderSheetDraft.__table__).where(OrderSheetDraft.id == "D").values(base_evidence_run_id="E-new"))
    elif change == "wrong-bag-source":
        saved.execute(update(OrderBaggingResult.__table__).values(source_saved_sheet_id="D-new"))
    elif change == "wrong-output-source":
        saved.execute(update(OrderOutputBundle.__table__).values(source_ocr_result_id="E-new"))
    elif change == "wrong-confirmed-source":
        saved.execute(update(OrderConfirmedSnapshot.__table__).values(draft_id="D-new"))
    elif change == "unsaved":
        saved.execute(update(OrderSheetDraft.__table__).values(draft_state="draft_ready"))
    else:
        sheet = deepcopy(SHEET)
        del sheet[{"missing-rows": "rows", "missing-fields": "fields", "missing-row-ids": "row_ids"}[change]]
        saved.execute(update(OrderSheetDraft.__table__).values(draft_sheet_json=sheet))
    before = all_rows(saved)
    with pytest.raises(ValueError, match="initial_saved_source_"):
        service.capture_saved_source(saved, "O")
    assert all_rows(saved) == before


@pytest.mark.parametrize("change", ["quantity", "state", "selected-ids", "old-cells", "line", "source", "hash", "missing-key", "invalid-id"])
def test_receipt_rejects_source_changes_or_forgery(saved, change):
    receipt = service.source_receipt(service.capture_saved_source(saved, "O"))
    if change == "quantity":
        sheet = deepcopy(SHEET)
        sheet["rows"][1][1] = 8
        saved.execute(update(OrderSheetDraft.__table__).values(draft_sheet_json=sheet))
    elif change == "state":
        saved.execute(update(OrderWorkflowState.__table__).values(state="changed"))
    elif change == "selected-ids":
        saved.execute(update(OrderWorkflowState.__table__).values(state="output_review", draft_id="D-new", evidence_run_id="E-new",
                       confirmed_snapshot_id=None, secondary_actions_json=None))
    elif change == "old-cells":
        saved.execute(update(FacilityTemplateVersion.__table__).values(cells_json=[{"raw": 1}]))
    elif change == "line":
        saved.execute(update(OrderLine.__table__).values(quantity_corrected=8))
    elif change == "source":
        receipt["source"] = "past-ocr-proof"
    elif change == "hash":
        receipt["source_sha256"] = "0" * 64
    elif change == "missing-key":
        del receipt["selected"]["bagging_result_id"]
    else:
        receipt["selected"]["draft_id"] = None
    before = all_rows(saved)
    if change in {"quantity", "state", "selected-ids", "old-cells", "line"}:
        assert service.source_receipt(service.capture_saved_source(saved, "O"))["source_sha256"] != receipt["source_sha256"]
    with pytest.raises(ValueError, match="^initial_saved_source_changed$"):
        service.validate_saved_source(saved, receipt)
    assert all_rows(saved) == before


@pytest.mark.parametrize("bad", [None, {}, {"materialization_candidate": None, "bag_rows": []},
                                  {"materialization_candidate": {}, "bag_rows": None},
                                  {"materialization_candidate": {}, "bag_rows": [None]}])
def test_malformed_output_stops_without_recalculation(saved, bad):
    snapshot = service.capture_saved_source(saved, "O")
    snapshot["bagging"]["payload_json"] = bad
    before = deepcopy(snapshot)
    with pytest.raises(ValueError, match="output_invalid"):
        service.accepted_output_payload(snapshot)
    assert snapshot == before


def test_capture_preserves_duplicate_saved_row_ids_and_detects_changes(saved):
    sheet = deepcopy(SHEET)
    sheet["row_ids"] = ["saved-row", "saved-row"]
    sheet["rows"] = [[None, "?", ""], ["raw", "", "manual"]]
    saved.execute(update(OrderSheetDraft.__table__).where(OrderSheetDraft.id == "D").values(draft_sheet_json=sheet))
    from src.services.order_service import _explicit_draft_row_identities
    with pytest.raises(ValueError, match="saved_draft_row_identity_duplicate"):
        _explicit_draft_row_identities(sheet, sheet["rows"])
    before = all_rows(saved)
    snapshot = service.capture_saved_source(saved, "O")
    receipt = service.source_receipt(snapshot)
    assert service.current_sheet_payload(snapshot) == sheet
    assert service.validate_saved_source(saved, receipt) == snapshot
    assert all_rows(saved) == before
    changed = deepcopy(sheet)
    changed["row_ids"][1] = "changed-row"
    saved.execute(update(OrderSheetDraft.__table__).where(OrderSheetDraft.id == "D").values(draft_sheet_json=changed))
    with pytest.raises(ValueError, match="initial_saved_source_changed"):
        service.validate_saved_source(saved, receipt)
