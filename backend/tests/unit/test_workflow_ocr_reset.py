"""Non-destructive OCR reference retirement and its ordinary command boundaries."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from src.db import session_scope
from src.models.order import Order, OrderLine
from src.models.order_current_state import OrderCurrentState
from src.models.order_confirmed_snapshot import OrderConfirmedSnapshot
from src.models.order_ocr_evidence_run import OrderOcrEvidenceRun
from src.models.order_sheet_draft import OrderSheetDraft
from src.models.order_sheet_patch_candidate import OrderSheetPatchCandidate
from src.models.order_output_artifact import OrderBaggingResult, OrderOutputBundle
from src.models.order_workflow_state import OrderWorkflowState
from src.models.ocr_job import OcrJob
from src.models.user import AuditLog
from src.services import order_workflow_v2_service as service, workflow_state_service
from test_order_workflow_v2_service import (
    _create_order_with_evidence, _current_workflow_revision, _stamp_evidence_with_workflow_template,
    authored_april_context,
    _standard_active_template_for_workflow_tests,
)

MODELS=(OrderOcrEvidenceRun,OrderSheetDraft,OrderSheetPatchCandidate,
        OrderConfirmedSnapshot,OrderBaggingResult,OrderOutputBundle,OrderLine)


def histories(order_id):
    with session_scope() as session:
        return {model.__tablename__:[deepcopy({c.key:getattr(row,c.key) for c in model.__table__.columns})
            for row in session.query(model).filter(model.order_id==order_id).order_by(model.id)]
            for model in MODELS}


def prepared_order(saved=True):
    order_id,evidence,_=_create_order_with_evidence()
    _,error=service.confirm_context(order_id=order_id,facility_id='FAC00001',
        week_start='2026-04-26',week_end='2026-04-30',template_id='template-fac00001',
        expected_revision=_current_workflow_revision(order_id))
    assert error is None
    _stamp_evidence_with_workflow_template(order_id,evidence)
    selected,error=service.select_ocr_result(order_id,evidence,expected_revision=_current_workflow_revision(order_id))
    assert error is None
    if saved:
        selected,error=service.save_sheet(order_id=order_id,
            sheet={'rows':[{'menu':'手動編集を保持','qty':'009','note':'空欄・記号も保持'}]},
            edited_by='original-human',expected_revision=selected['revision'])
        assert error is None
        selected=selected['workflow']
    return order_id,evidence,selected


@pytest.mark.parametrize('saved',[True,False])
def test_reset_keeps_every_history_and_detaches_current_refs(authored_april_context,saved):
    order,evidence,workflow=prepared_order(saved)
    sibling,_,_ = prepared_order(False)
    original,sibling_original=histories(order),histories(sibling)
    cache={'manual':['009','-',''], 'source':'unchanged'}
    with session_scope() as session:
        current=session.get(OrderCurrentState,order)
        if current is None:
            current=OrderCurrentState(order_id=order,snapshot_version='v2')
            session.add(current)
        current.template_version_id=workflow['template_version_id']
        current.evidence_run_id=evidence
        current.draft_id=workflow['saved_sheet_id']
        current.state_json=cache
    result,error=service.reset_ocr_selection(order,actor='human@example.invalid',expected_revision=workflow['revision'])
    assert error is None
    assert result['state']=='context_confirmed' and result['primary_action']=='run_ocr'
    assert result['revision']==workflow['revision']+1
    assert result['selected_ocr_result_id'] is None and result['saved_sheet_id'] is None
    assert result['confirmed_snapshot_id'] is None
    assert result['ocr_reset']['retained_sheet_id']==workflow['saved_sheet_id']
    assert histories(order)==original and histories(sibling)==sibling_original
    with session_scope() as session:
        assert session.get(Order,order).status=='要確認'
        current=session.get(OrderCurrentState,order)
        assert current.evidence_run_id is None and current.draft_id is None
        assert current.state_json==cache
        audit=session.get(AuditLog,result['ocr_reset']['audit_id'])
        assert audit.actor=='human@example.invalid' and audit.target==order
        assert audit.metadata_json['previous']['evidence_run_id']==evidence
        assert audit.metadata_json['previous']['draft_id']==workflow['saved_sheet_id']
        assert audit.metadata_json['history_preserved'] is True
    read,error=service.get_workflow(order)
    assert error is None and read['selected_ocr_result_id'] is None
    assert read['ocr_job'] is None and read['common_job_status'] is None
    refreshed=workflow_state_service.refresh_workflow_state(order)
    assert refreshed['state']=='context_confirmed' and refreshed['evidence_run_id'] is None
    again,error=service.reset_ocr_selection(order,actor='human@example.invalid',expected_revision=read['revision'])
    assert error is None and again['revision']==read['revision']
    assert histories(order)==original


@pytest.mark.parametrize('status',['running','queued','pending','retrying','unknown-future-state'])
def test_active_or_unknown_execution_cannot_be_cleared(authored_april_context,status):
    order,_,workflow=prepared_order()
    with session_scope() as session:
        session.add(OcrJob(id='reset-'+order,order_id=order,status=status,input_reference='synthetic-test-only.pdf'))
    before=histories(order)
    result,error=service.reset_ocr_selection(order,actor='operator',expected_revision=workflow['revision'])
    assert result is None and error=='ocr_reset_execution_active'
    assert histories(order)==before
    with session_scope() as session:
        assert session.get(OrderWorkflowState,order).draft_id==workflow['saved_sheet_id']
        assert session.query(AuditLog).filter(AuditLog.target==order,
            AuditLog.action=='workflow_ocr_selection_reset').count()==0


def test_revision_and_saved_history_guards_remain(authored_april_context):
    order,evidence,workflow=prepared_order()
    assert service.reset_ocr_selection(order,actor='operator')==(None,service.WORKFLOW_REVISION_REQUIRED)
    result,error=service.reset_ocr_selection(order,actor='operator',expected_revision=workflow['revision']-1)
    assert error==service.WORKFLOW_REVISION_CONFLICT
    reset,error=service.reset_ocr_selection(order,actor='operator',expected_revision=workflow['revision'])
    assert error is None
    assert service.delete_ocr_result(order,evidence,expected_revision=reset['revision'])==(None,'ocr_result_saved_history_conflict')


def test_api_passes_authenticated_actor_and_revision(authored_april_context):
    from src.api.orders import reset_order_workflow_v2_ocr
    order,_,workflow=prepared_order()
    result=reset_order_workflow_v2_ocr(order,expected_revision=workflow['revision'],
        user=SimpleNamespace(account='authenticated@example.invalid'))
    with session_scope() as session:
        assert session.get(AuditLog,result['ocr_reset']['audit_id']).actor=='authenticated@example.invalid'


@pytest.mark.parametrize('saved',[True,False])
def test_reset_restore_keeps_history_and_monotonic_revision(authored_april_context,saved):
    order,evidence,workflow=prepared_order(saved)
    before=histories(order)
    reset,error=service.reset_ocr_selection(order,actor='operator',expected_revision=workflow['revision'])
    assert error is None
    restored,error=service.restore_ocr_selection(order,actor='operator',expected_revision=reset['revision'])
    assert error is None
    assert restored['revision']==workflow['revision']+2
    assert restored['selected_ocr_result_id']==evidence
    assert restored['saved_sheet_id']==workflow['saved_sheet_id']
    assert restored['state']==workflow['state']
    assert histories(order)==before
    assert service.restore_ocr_selection(order,actor='operator',expected_revision=restored['revision'])==(None,'ocr_reset_restore_conflict')


def test_restore_refuses_active_execution(authored_april_context):
    order,_,workflow=prepared_order()
    reset,error=service.reset_ocr_selection(order,actor='operator',expected_revision=workflow['revision'])
    assert error is None
    with session_scope() as session:
        session.add(OcrJob(id='restore-'+order,order_id=order,status='queued',input_reference='synthetic-only.pdf'))
    assert service.restore_ocr_selection(order,actor='operator',expected_revision=reset['revision'])==(None,'ocr_reset_execution_active')
