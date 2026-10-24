"""Formal isolated SQL artifact integrity; no production quantities or outputs."""
from copy import deepcopy

import pytest
from sqlalchemy import update

from src.db import session_scope
from src.models.order import Order
from src.models.order_sheet_draft import OrderSheetDraft
from src.models.order_confirmed_snapshot import OrderConfirmedSnapshot
from src.models.order_output_artifact import OrderBaggingResult, OrderOutputBundle
from src.models.order_workflow_state import OrderWorkflowState
from src.services import order_output_artifact_service as artifacts


@pytest.fixture
def saved(isolated_formal_schema, monkeypatch, tmp_path):
    from datetime import date
    from tests.helpers.ingest_original import authored_sources
    from src.services.order_workflow_v2_service import _canonical_payload_digest

    # Artifact corruption is separate from missing canonical configuration.
    # This source is explicit and scoped to this component, never a global default.
    authored_sources(monkeypatch, tmp_path, facility_id="synthetic-artifact-facility",
                     menu_date=date(2026, 10, 7), seed_menu=False)
    # Synthetic artifact-only input, not a publishable order-output candidate.
    candidate = {
        "order_id": "order", "source_saved_sheet_id": "sheet",
        "configured_input": {
            "facility": {"id": "synthetic-artifact-facility"},
            "policy": {"id": "synthetic-artifact-policy"},
        },
        "quantities": [0, None],
    }
    digest = _canonical_payload_digest(candidate)
    bag = {"bagging_result_id": "bag", "order_id": "order", "source_saved_sheet_id": "sheet",
        "source_ocr_result_id": None, "template_version_id": None, "quantities": [0, None],
        "materialization_candidate": candidate, "materialization_digest": digest}
    output = {"output_bundle_id": "output", "order_id": "order", "source_saved_sheet_id": "sheet",
        "source_bagging_result_id": "bag", "source_ocr_result_id": None, "template_version_id": None,
        "materialization_digest": digest, "status": "ready", "quantities": [0, None]}
    with session_scope() as session:
        session.add(Order(id="order", document_uri="fixture://input", message_id="message"))
        session.flush()
        session.add(OrderSheetDraft(id="sheet", order_id="order", draft_sheet_json={"rows": []}))
        session.flush()
        artifacts.save_bagging_result_artifact(session, payload=bag)
        artifacts.save_output_bundle_artifact(session, payload=output)
    return bag, output


@pytest.mark.parametrize("model,reader,identity", [
    (OrderBaggingResult, artifacts.load_bagging_result_payload, "bagging_result_id"),
    (OrderOutputBundle, artifacts.load_output_bundle_payload, "output_bundle_id")])
@pytest.mark.parametrize("damage", ["digest", "id", "order", "sheet", "ocr", "template", "shape"])
def test_reader_refuses_digest_and_column_payload_mismatches(saved, model, reader, identity, damage):
    payload = deepcopy(saved[0 if model is OrderBaggingResult else 1])
    artifact_id = payload[identity]
    values = {}
    if damage == "digest":
        values["payload_digest"] = "bad"
    else:
        if damage == "shape":
            payload = []
        else:
            key = {"id": identity, "order": "order_id", "sheet": "source_saved_sheet_id",
                "ocr": "source_ocr_result_id", "template": "template_version_id"}[damage]
            payload[key] = "foreign"
        values.update(payload_json=payload, payload_digest=artifacts.payload_digest(payload))
    with session_scope() as session:
        session.execute(update(model).where(model.id == artifact_id).values(**values))
    with session_scope() as session:
        assert reader(session, artifact_id, expected_order_id="order") is None
        meta = artifacts.enrich_workflow_meta_with_artifacts(session, {
            "bagging_result_id": "bag", "output_bundle_id": "output",
            "bagging_result": {"legacy": True}, "output_bundle": {"legacy": True}}, expected_order_id="order")
        assert meta["bagging_result" if model is OrderBaggingResult else "output_bundle"] is None


def test_missing_and_cross_order_never_retain_inline(saved):
    with session_scope() as session:
        for owner in ("order", "foreign", ""):
            meta = artifacts.enrich_workflow_meta_with_artifacts(session, {
                "bagging_result_id": "missing", "output_bundle_id": "missing",
                "bagging_result": saved[0], "output_bundle": saved[1]}, expected_order_id=owner)
            assert meta["bagging_result"] is None and meta["output_bundle"] is None
        assert artifacts.load_bagging_result_payload(session, "bag", expected_order_id="foreign") is None
        assert artifacts.load_output_bundle_payload(session, "output", expected_order_id="foreign") is None
        assert artifacts.load_output_bundle_payload(session, "output", expected_order_id="order")["quantities"] == [0, None]


@pytest.mark.parametrize("index,saver", [(0, artifacts.save_bagging_result_artifact),
    (1, artifacts.save_output_bundle_artifact)])
def test_same_id_is_idempotent_only_for_identical_payload(saved, index, saver):
    payload = deepcopy(saved[index])
    with session_scope() as session:
        assert saver(session, payload=payload).payload_json == payload
        payload["quantities"] = [1, None]
        with pytest.raises(ValueError, match="conflict"):
            saver(session, payload=payload)


def test_replace_allows_confirmation_but_not_identity_changes(saved):
    payload = {**deepcopy(saved[1]), "status": "confirmed", "confirmed_snapshot_id": "snapshot"}
    with session_scope() as session:
        row = artifacts.replace_output_bundle_payload(session, artifact_id="output", payload=payload, expected_order_id="order")
        assert row.payload_digest == artifacts.payload_digest(payload)
        assert row.payload_json["quantities"] == [0, None]
        for key in ("output_bundle_id", "order_id", "source_saved_sheet_id", "source_bagging_result_id",
                "source_ocr_result_id", "template_version_id", "materialization_digest"):
            changed = {**payload, key: "foreign"}
            with pytest.raises(ValueError, match="identity_mismatch"):
                artifacts.replace_output_bundle_payload(session, artifact_id="output", payload=changed, expected_order_id="order")
        assert artifacts.load_output_bundle_payload(session, "output", expected_order_id="order") == payload


@pytest.mark.parametrize("damage", [None, "snapshot_digest", "snapshot_owner", "snapshot_inline", "bag_missing",
    "output_missing", "output_digest", "output_owner", "bag_link", "materialization", "snapshot_ref"])
def test_confirmed_snapshot_requires_both_owned_canonical_artifacts(saved, damage):
    bag, output = deepcopy(saved)
    snapshot_payload = {"source": "workflow_v2", "order_id": "order", "saved_sheet_id": "sheet", "template_version_id": None,
        "bagging_result": bag, "output_bundle": output}
    snapshot = OrderConfirmedSnapshot(id="snapshot", order_id="order", saved_sheet_id="sheet", draft_id="sheet",
        bagging_result_id="bag", output_bundle_id="output", template_version_id=None,
        snapshot_json=snapshot_payload, snapshot_digest=artifacts.payload_digest(snapshot_payload))
    with session_scope() as session:
        artifacts.replace_output_bundle_payload(session, artifact_id="output",
            payload={**output, "confirmed_snapshot_id": "snapshot"}, expected_order_id="order")
        session.add(snapshot)
        session.flush()
        if damage == "snapshot_digest":
            snapshot.snapshot_digest = "bad"
        elif damage == "snapshot_owner":
            session.add(Order(id="foreign", document_uri="fixture://foreign-input", message_id="foreign-message"))
            session.flush()
            snapshot.order_id = "foreign"
        elif damage == "snapshot_inline":
            snapshot.snapshot_json = {**snapshot_payload, "bagging_result": {**bag, "quantities": [99]}}
            snapshot.snapshot_digest = artifacts.payload_digest(snapshot.snapshot_json)
        elif damage in ("bag_missing", "output_missing"):
            setattr(snapshot, "bagging_result_id" if damage == "bag_missing" else "output_bundle_id", "missing")
        elif damage is not None:
            altered = {**output, "confirmed_snapshot_id": "snapshot"}
            changes = {"output_owner": ("order_id", "foreign"), "bag_link": ("source_bagging_result_id", "other"),
                "materialization": ("materialization_digest", "foreign"), "snapshot_ref": ("confirmed_snapshot_id", "other")}
            if damage in changes:
                key, value = changes[damage]
                altered[key] = value
            session.execute(update(OrderOutputBundle).where(OrderOutputBundle.id == "output").values(
                payload_json=altered, payload_digest="bad" if damage == "output_digest" else artifacts.payload_digest(altered)))
        result = artifacts.confirmed_snapshot_artifact_payloads(session, snapshot, expected_order_id="order")
        if damage is None:
            assert result == (bag, {**output, "confirmed_snapshot_id": "snapshot"})
        else:
            assert result is None
            from src.services import order_pipeline_lineage_repair_service as repair
            workflow = OrderWorkflowState(order_id="order", state="confirmed", draft_id="sheet",
                confirmed_snapshot_id="snapshot", secondary_actions_json={"workflow_v2": {}})
            plan, rebuilt = repair._plan_confirmed_workflow_v2_lineage_repair(session=session, workflow=workflow)
            assert plan.status == "blocked" and rebuilt is None
            assert not any(action.action_type.startswith("create") for action in plan.actions)


def test_unserializable_payload_is_invalid_without_fallback(saved):
    bag, _ = saved
    row = OrderBaggingResult(id="bag", order_id="order", source_saved_sheet_id="sheet",
        source_ocr_result_id=None, template_version_id=None, payload_digest=artifacts.payload_digest(bag))
    for value in ({"bad": object()}, {"bad": {1, 2}}):
        assert artifacts._valid_payload(row, {**bag, **value}, expected_order_id="order") is False
    recursive = {**bag}
    recursive["cycle"] = recursive
    assert artifacts._valid_payload(row, recursive, expected_order_id="order") is False


@pytest.mark.parametrize("damage", ["missing", "corrupt", "foreign"])
def test_output_audit_and_repair_share_stop_instead_of_inline_resurrection(saved, damage):
    from src.services import order_pipeline_lineage_audit_service as audit
    from src.services import order_pipeline_lineage_repair_service as repair
    from src.services import output_builder
    bag, output = deepcopy(saved)
    meta = {"bagging_result_id": "bag", "output_bundle_id": "output", "bagging_result": bag,
        "output_bundle": output}
    if damage == "missing":
        meta["output_bundle_id"] = "missing"
    with session_scope() as session:
        if damage == "corrupt":
            session.execute(update(OrderOutputBundle).where(OrderOutputBundle.id == "output").values(payload_digest="bad"))
        if damage == "foreign":
            meta["output_bundle_id"] = "foreign-output"
        workflow = OrderWorkflowState(order_id="order", state="output_review", draft_id="sheet",
            secondary_actions_json={"workflow_v2": meta})
        order = session.get(Order, "order")
        draft = session.get(OrderSheetDraft, "sheet")
        from tests.unit.test_disabled_facility_columns import _snapshot
        before = _snapshot(session.get_bind())
        with pytest.raises(ValueError, match="output bundle artifact missing or invalid"):
            output_builder._workflow_v2_materialization_candidate(session, {"id": order.id}, workflow, draft)
        issues = audit._audit_bagging_and_output(session=session, order=order, workflow=workflow,
            meta=meta, workflow_template_version_id=None)
        assert any(issue.issue_type == "output_bundle_payload_missing" and issue.repair_safety == "blocked" for issue in issues)
        plan = repair._plan_step4_artifact_backfill(session=session, workflow=workflow)
        assert plan.status == "blocked" and plan.reason == "artifact_missing_or_mismatch"
        assert workflow.secondary_actions_json["workflow_v2"]["output_bundle"] == output
        assert session.query(OrderOutputBundle).count() == 1
        assert output["quantities"] == [0, None]
        assert _snapshot(session.get_bind()) == before
