"""Actual public service mutations with owned SQLite/PDF/dated JSON inputs.

OCR selection is manually seeded by the unchanged surface fixture. These are
not HTTP/operator/native recognition/formal PG/staging proofs. Real bagging,
artifact persistence, revision fences and final materialization are not mocked.
The diagnostic-only rejection test injects one output-enrichment exception.
"""

from copy import deepcopy

import pytest
from sqlalchemy import select

from test_modernization_workflow_surface_parity import database_snapshot, surface
from src.models.document import OrderDocument
from src.models.order import Order, OrderLine
from src.models.order_confirmed_snapshot import OrderConfirmedSnapshot
from src.models.order_output_artifact import OrderBaggingResult, OrderOutputBundle
from src.models.order_sheet_draft import OrderSheetDraft
from src.models.order_workflow_state import OrderWorkflowState
from src.services import order_output_artifact_service as artifacts
from src.services import order_service as orders
from src.services import order_workflow_v2_service as workflow


@pytest.mark.parametrize('reason',['monthly_entry_source_row_unresolved','private arbitrary text'])
def test_rejection_logs_only_symbolic_cause(monkeypatch,reason):
    from types import SimpleNamespace
    from src.services import output_builder
    messages=[]
    monkeypatch.setattr(workflow.logger,'warning',lambda *args,**kwargs:messages.append(args))
    def reject(*args,**kwargs):
        raise ValueError(reason)
    monkeypatch.setattr(output_builder,'build_order_lines_for_outputs',reject)
    order=SimpleNamespace(id='unit',facility_code='unit-facility',week_code='2026-11',received_at=None)
    with pytest.raises(workflow.BaggingOutputUnresolved,match='bagging_output_enrichment_failed') as caught:
        workflow._build_bag_rows_for_candidate(order=order,lines=[],materialization_candidate={})
    assert str(caught.value.__cause__)==reason
    assert len(messages)==1
    assert messages[0][1]==('monthly_entry_source_row_unresolved' if reason.startswith('monthly_') else 'non_symbolic_error')
    assert messages[0][2]=='ValueError'


@pytest.fixture
def owned_bagging(surface):
    fixture, _client = surface
    # Explicit synthetic business configuration, published through real lineage.
    fixture.authored["bag_types"] = [
        {"bag_type_id": "UNIT-1000G", "label": "Unit 1000g", "max_qty": 1000, "unit": "g"},
    ]
    fixture.authored["packaging_policy"] = {
        "split_key": ["facility", "date", "daypart", "menu_name", "diet_type", "area_id", "bag_type"],
    }
    fixture.authored['daily_label_comparable_diet_types'] = ['regular', 'no_meat', 'no_fish', 'staff']
    fixture.authored["invoice_template"] = {"include_menu_name": True, "columns": [
        {"name": "Date", "source": "date"},
        {"name": "Menu", "source": "menu_name"},
        {"name": "Quantity", "source": "quantity", "diet_type": "regular", "area_id": "X"},
    ]}
    fixture.republish()
    with fixture.session_scope() as session:
        orders._record_current_order_version(
            session, session.get(Order, fixture.order_id), session.get(OrderDocument, "UNIT-DOC"),
        )
    return fixture


def revision(fixture):
    with fixture.session_scope() as session:
        return session.get(OrderWorkflowState, fixture.order_id).revision


@pytest.mark.parametrize('diet,column',[('diabetes',6),('staff',7)])
def test_shared_registered_rule_passes_real_bagging_and_confirmation(owned_bagging,diet,column):
    from src.models.menu import MenuRule
    fixture=owned_bagging
    # Forbidden-diet quantities are intentionally excluded from materialization.
    # Publish a distinct synthetic diabetes column to exercise a counted diet.
    if diet == 'diabetes':
        fixture.authored['fax_template']['columns'][column]['diet_type'] = diet
        fixture.authored['daily_label_comparable_diet_types'].append(diet)
        fixture.republish()
    with fixture.session_scope() as session:
        session.add(MenuRule(id='UNIT-COMMON',rule_type='global',daypart='朝',category=None,
            diet_type=None,unit_type='g',qty_per_serving=17,priority=0,active=True))
    sheet=fixture.saved()
    if diet == 'diabetes':
        sheet['fields'][column] = 'qty.diabetes_x'
    sheet['rows'][0][column]='1'
    for method in (workflow.run_sheet_anomaly_review,workflow.confirm_sheet_review):
        kwargs={'use_llm':False} if method is workflow.run_sheet_anomaly_review else {}
        _,error=method(order_id=fixture.order_id,sheet=sheet,expected_revision=revision(fixture),**kwargs)
        assert error is None,error
    _,error=workflow.save_sheet(order_id=fixture.order_id,sheet=sheet,
        require_pre_save_checks=True,expected_revision=revision(fixture))
    assert error is None,error
    bagged,error=workflow.run_bagging(fixture.order_id,expected_revision=revision(fixture))
    assert error is None,error
    lines=bagged['bagging_result']['materialization_candidate']['output_lines']
    specific=[line for line in lines if (line.get('source_diet_type') or line.get('diet_type'))==diet]
    assert specific and all(line['menu_qty_per_serving']==17 for line in specific)
    assert sum(row['quantity'] for row in bagged['bagging_result']['bag_rows'] if (row.get('source_diet_type') or row.get('diet_type'))==diet)==1
    confirmed,error=workflow.final_confirm(fixture.order_id,expected_revision=revision(fixture))
    assert error is None,error
    assert confirmed['confirmed_snapshot_id']
    with fixture.session_scope() as session:
        state=session.get(OrderWorkflowState,fixture.order_id)
        assert session.get(OrderSheetDraft,state.draft_id).draft_sheet_json==sheet


def assert_no_confirmation(fixture):
    with fixture.session_scope() as session:
        assert session.execute(select(OrderLine).where(OrderLine.order_id == fixture.order_id)).scalars().all() == []
        assert session.execute(select(OrderConfirmedSnapshot).where(OrderConfirmedSnapshot.order_id == fixture.order_id)).scalars().all() == []
        assert session.get(OrderWorkflowState, fixture.order_id).confirmed_snapshot_id is None


def actual_bagging(fixture):
    saved = fixture.saved()
    initial_revision = revision(fixture)
    result, error = workflow.run_bagging(fixture.order_id, expected_revision=initial_revision)
    assert error is None, error
    assert result is not None
    assert revision(fixture) == initial_revision + 1
    bagging = result["bagging_result"]
    bundle = result["output_bundle"]
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


def test_real_bagging_then_final_confirmation_persist_owned_quantities_and_snapshots(owned_bagging):
    fixture = owned_bagging
    saved = fixture.saved()
    bagged = actual_bagging(fixture)
    before_revision = revision(fixture)
    result, error = workflow.final_confirm(
        fixture.order_id, confirmed_by="synthetic-unit-operator", expected_revision=before_revision,
    )
    assert error is None, error
    assert result is not None
    with fixture.session_scope() as session:
        state = session.get(OrderWorkflowState, fixture.order_id)
        assert state.revision == before_revision + 1
        assert state.state == "confirmed"
        assert state.confirmed_snapshot_id == result["confirmed_snapshot_id"]
        snapshot = session.get(OrderConfirmedSnapshot, state.confirmed_snapshot_id)
        assert snapshot.order_id == fixture.order_id
        assert snapshot.saved_sheet_id == snapshot.draft_id == fixture.draft_id
        assert snapshot.template_version_id == fixture.projection_id
        assert snapshot.confirmed_by == "synthetic-unit-operator"
        assert snapshot.bagging_result_id == bagged["bagging_result"]["bagging_result_id"]
        assert snapshot.output_bundle_id == bagged["output_bundle"]["output_bundle_id"]
        assert snapshot.snapshot_json["saved_sheet"] == saved
        assert snapshot.snapshot_json["bagging_result"] == bagged["bagging_result"]
        assert snapshot.snapshot_digest == artifacts.payload_digest(snapshot.snapshot_json)
        assert artifacts.confirmed_snapshot_artifact_payloads(session, snapshot, expected_order_id=fixture.order_id) is not None
        lines = session.execute(
            select(OrderLine).where(OrderLine.order_id == fixture.order_id).order_by(OrderLine.menu_name),
        ).scalars().all()
        assert [(line.menu_name, line.quantity_original, line.quantity_corrected) for line in lines] == [
            ("選択献立0", 7, None), ("選択献立3", 0, None),
        ]
        assert all(line.confirmed_snapshot_id == snapshot.id for line in lines)
        assert [(line["menu_name"], line["quantity_original"], line["quantity_corrected"])
                for line in snapshot.snapshot_json["order_lines"]] == [
                    ("選択献立0", 7, None), ("選択献立3", 0, None),
                ]
        assert "bagging_input_proof" not in state.secondary_actions_json["workflow_v2"]
    assert fixture.saved() == saved


@pytest.mark.parametrize("quantities,error", [
    (["7", "?", "", "0"], "sheet_ocr_review_required"),
    (["", "", "", ""], "draft_lines_empty"),
])
def test_unresolved_or_empty_saved_quantities_stop_before_real_artifacts(owned_bagging, quantities, error):
    fixture = owned_bagging
    with fixture.session_scope() as session:
        draft = session.get(OrderSheetDraft, fixture.draft_id)
        payload = deepcopy(draft.draft_sheet_json)
        for row, quantity in zip(payload["rows"], quantities, strict=True):
            row[4] = quantity
        draft.draft_sheet_json = payload
    before = database_snapshot()
    result, actual_error = workflow.run_bagging(fixture.order_id, expected_revision=revision(fixture))
    assert result is None
    assert actual_error == error
    assert database_snapshot() == before
    with fixture.session_scope() as session:
        assert session.execute(select(OrderBaggingResult)).scalars().all() == []
        assert session.execute(select(OrderOutputBundle)).scalars().all() == []
    assert_no_confirmation(fixture)


@pytest.mark.parametrize('value,ready', [('-', False), ('一', False), ('12_', False), ('· 99', False), ('12', True), ('', True)])
def test_pre_save_quantity_report_matches_native_bagging_without_mutation(owned_bagging, value, ready):
    fixture = owned_bagging
    saved = fixture.saved()
    proposed = deepcopy(saved)
    proposed['rows'][0][4] = value
    before = database_snapshot()
    report, error = workflow.get_sheet_pre_save_status(order_id=fixture.order_id, sheet=proposed)
    assert error is None
    expected = orders._materialization_quantity_cell_issues(
        fields=proposed['fields'], rows_payload=proposed['rows'], template=fixture.authored['fax_template'])
    assert report['materialization'] == {'quantity_ready': ready, 'cell_issues': expected}
    assert database_snapshot() == before
    assert fixture.saved() == saved


@pytest.mark.parametrize("command", ["run_bagging", "final_confirm"])
def test_stale_command_revision_rejects_without_database_mutation(owned_bagging, command):
    fixture = owned_bagging
    if command == "final_confirm":
        actual_bagging(fixture)
    before = database_snapshot()
    _result, error = getattr(workflow, command)(fixture.order_id, expected_revision=revision(fixture) - 1)
    assert error == "workflow_revision_conflict"
    assert database_snapshot() == before
    assert_no_confirmation(fixture)


@pytest.mark.parametrize("retirement,error", [
    ("pdf", "saved_sheet_materialization_failed:captured materialization input changed before adoption"),
    ("draft-pointer", "saved_sheet_required"),
    ("draft-contents", "saved_sheet_materialization_failed:captured materialization input changed before adoption"),
])
def test_inputs_retired_after_actual_bagging_refuse_confirmation_without_fallback(owned_bagging, retirement, error):
    fixture = owned_bagging
    actual_bagging(fixture)
    with fixture.session_scope() as session:
        if retirement == "pdf":
            session.get(Order, fixture.order_id).current_document_id = None
        elif retirement == "draft-pointer":
            session.get(OrderWorkflowState, fixture.order_id).draft_id = None
        else:
            draft = session.get(OrderSheetDraft, fixture.draft_id)
            payload = deepcopy(draft.draft_sheet_json)
            payload["rows"][0][4] = "9"
            draft.draft_sheet_json = payload
    before = database_snapshot()
    result, actual_error = workflow.final_confirm(fixture.order_id, expected_revision=revision(fixture))
    assert result is None
    assert actual_error == error
    assert database_snapshot() == before
    assert_no_confirmation(fixture)
