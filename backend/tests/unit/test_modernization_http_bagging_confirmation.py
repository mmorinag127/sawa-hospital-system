"""Registered HTTP routes with real owned bagging/confirmation services.

The unchanged fixtures seed synthetic auth/manual OCR evidence. This is not
native/provider/operator/staging proof; no service or ownership double is used.
"""

from copy import deepcopy

import pytest
from sqlalchemy import select

from test_modernization_real_bagging_confirmation import (
    assert_no_confirmation,
    owned_bagging,
    revision,
)
from test_modernization_workflow_surface_parity import database_snapshot, surface
from src.models.order import OrderLine
from src.models.order_confirmed_snapshot import OrderConfirmedSnapshot
from src.models.order_sheet_draft import OrderSheetDraft
from src.models.order_workflow_state import OrderWorkflowState
from src.services import order_output_artifact_service as artifacts


def post(client, fixture, action, expected_revision, **kwargs):
    return client.post(
        f"/orders/{fixture.order_id}/workflow-v2/{action}",
        headers={"If-Match": f'"{expected_revision}"'},
        **kwargs,
    )


def http_bagging(fixture, client):
    saved = fixture.saved()
    before_revision = revision(fixture)
    response = post(client, fixture, "bagging", before_revision)
    assert response.status_code == 200, response.text
    result = response.json()
    bagging, bundle = result["bagging_result"], result["output_bundle"]
    assert revision(fixture) == before_revision + 1
    assert bagging["order_id"] == bundle["order_id"] == fixture.order_id
    assert bagging["source_saved_sheet_id"] == bundle["source_saved_sheet_id"] == fixture.draft_id
    assert bagging["source_ocr_result_id"] == bundle["source_ocr_result_id"] == fixture.evidence_id
    assert bagging["template_version_id"] == bundle["template_version_id"] == fixture.projection_id
    assert bundle["source_bagging_result_id"] == bagging["bagging_result_id"]
    assert [(line["source_row_index"], line["source_col_index"], line["quantity_original"])
            for line in bagging["materialization_candidate"]["lines"]] == [(0, 4, 7), (3, 4, 0)]
    assert bagging["summary"]["total_quantity"] == 7
    assert bagging["bag_rows"]
    assert sum(row["quantity"] for row in bagging["bag_rows"]) == 7
    with fixture.session_scope() as session:
        assert artifacts.load_bagging_result_payload(
            session, bagging["bagging_result_id"], expected_order_id=fixture.order_id,
        ) == bagging
        assert artifacts.load_output_bundle_payload(
            session, bundle["output_bundle_id"], expected_order_id=fixture.order_id,
        ) == bundle
        state = session.get(OrderWorkflowState, fixture.order_id)
        proof = state.secondary_actions_json["workflow_v2"]["bagging_input_proof"]
        assert proof["owned_revision"] == state.revision
        assert proof["bagging_result_id"] == bagging["bagging_result_id"]
        assert proof["materialization_digest"] == bundle["materialization_digest"]
        assert proof["start_input"]["draft_id"] == fixture.draft_id
        assert state.state == "output_review"
    assert fixture.saved() == saved
    assert [row[4] for row in saved["rows"]] == ["7", "", "", "0"]
    assert_no_confirmation(fixture)
    return result


def test_http_bagging_then_confirmation_preserves_owned_saved_quantities(owned_bagging, surface):
    fixture = owned_bagging
    shared_fixture, client = surface
    assert shared_fixture is fixture
    saved = fixture.saved()
    bagged = http_bagging(fixture, client)
    before_revision = revision(fixture)
    response = post(client, fixture, "confirm", before_revision,
                    json={"confirmed_by": "synthetic-http-operator"})
    assert response.status_code == 200, response.text
    result = response.json()
    with fixture.session_scope() as session:
        state = session.get(OrderWorkflowState, fixture.order_id)
        assert state.revision == before_revision + 1
        assert state.state == "confirmed"
        assert state.confirmed_snapshot_id == result["confirmed_snapshot_id"]
        snapshot = session.get(OrderConfirmedSnapshot, state.confirmed_snapshot_id)
        assert snapshot.order_id == fixture.order_id
        assert snapshot.saved_sheet_id == snapshot.draft_id == fixture.draft_id
        assert snapshot.template_version_id == fixture.projection_id
        assert snapshot.confirmed_by == "synthetic-http-operator"
        assert snapshot.bagging_result_id == bagged["bagging_result"]["bagging_result_id"]
        assert snapshot.output_bundle_id == bagged["output_bundle"]["output_bundle_id"]
        assert snapshot.snapshot_json["saved_sheet"] == saved
        assert snapshot.snapshot_json["bagging_result"] == bagged["bagging_result"]
        assert snapshot.snapshot_digest == artifacts.payload_digest(snapshot.snapshot_json)
        assert artifacts.confirmed_snapshot_artifact_payloads(
            session, snapshot, expected_order_id=fixture.order_id,
        ) is not None
        lines = session.execute(
            select(OrderLine).where(OrderLine.order_id == fixture.order_id).order_by(OrderLine.menu_name),
        ).scalars().all()
        expected = [("選択献立0", 7, None), ("選択献立3", 0, None)]
        assert [(line.menu_name, line.quantity_original, line.quantity_corrected)
                for line in lines] == expected
        assert all(line.confirmed_snapshot_id == snapshot.id for line in lines)
        assert [(line["menu_name"], line["quantity_original"], line["quantity_corrected"])
                for line in snapshot.snapshot_json["order_lines"]] == expected
        assert "bagging_input_proof" not in state.secondary_actions_json["workflow_v2"]
    assert fixture.saved() == saved
    assert [row[4] for row in fixture.saved()["rows"]] == ["7", "", "", "0"]


@pytest.mark.parametrize("action", ["bagging", "confirm"])
def test_http_stale_revision_stops_without_database_mutation(owned_bagging, surface, action):
    fixture = owned_bagging
    shared_fixture, client = surface
    assert shared_fixture is fixture
    if action == "confirm":
        http_bagging(fixture, client)
    before = database_snapshot()
    response = post(client, fixture, action, revision(fixture) - 1)
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert set(detail) == {"error", "workflow"}
    assert detail["error"] == "workflow_revision_conflict"
    assert isinstance(detail["workflow"], dict)
    assert database_snapshot() == before
    assert_no_confirmation(fixture)


def test_http_changed_saved_quantity_after_bagging_refuses_confirmation(owned_bagging, surface):
    fixture = owned_bagging
    shared_fixture, client = surface
    assert shared_fixture is fixture
    http_bagging(fixture, client)
    with fixture.session_scope() as session:
        draft = session.get(OrderSheetDraft, fixture.draft_id)
        changed = deepcopy(draft.draft_sheet_json)
        changed["rows"][0][4] = "9"
        draft.draft_sheet_json = changed
    before = database_snapshot()
    response = post(client, fixture, "confirm", revision(fixture),
                    json={"confirmed_by": "synthetic-http-operator"})
    assert response.status_code == 400, response.text
    assert response.json() == {"detail": (
        "saved_sheet_materialization_failed:captured materialization input changed before adoption"
    )}
    assert fixture.saved() == changed
    assert [row[4] for row in fixture.saved()["rows"]] == ["9", "", "", "0"]
    assert database_snapshot() == before
    assert_no_confirmation(fixture)
