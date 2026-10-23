"""Owned SQLite check of saved reads after a new, unadopted OCR request."""
from copy import deepcopy
import pytest
from test_initial_saved_source_service import saved, all_rows
from test_initial_saved_import_service import editable_import, command
from src.models.order import Order
from src.services import order_service, order_workflow_v2_service as workflow
from src.services import initial_saved_import_service as imports


def test_new_unadopted_ocr_selection_preserves_saved_sheet_read_and_configuration(editable_import):
    session, sheet, _ = editable_import
    original = deepcopy(imports.get_binding(session, 'O'))
    before = all_rows(session)
    assert order_service.get_ocr_sheet('O') == (sheet, None)

    with command():
        row = workflow._get_workflow(session, 'O')
        imports.prepare_new_ocr_selection(session, session.get(Order, 'O'), row)
        assert row.template_version_id == original['snapshot']['template']['id']
        assert imports.get_binding(session, 'O')['configuration'] == original['configuration']
        assert workflow._finalize_workflow_mutation(session, row) is None
        session.commit()
    assert imports.get_binding(session, 'O')['configuration'] == original['configuration']
    after = all_rows(session)
    for table in ('order_sheet_drafts', 'order_confirmed_snapshots', 'order_lines', 'order_ocr_evidence_runs', 'facility_template_versions'):
        assert after[table] == before[table]
    assert order_service.get_ocr_sheet('O') == (sheet, None)


@pytest.mark.parametrize('changed', ['draft', 'monthly', 'unowned'])
def test_candidate_queue_snapshot_rejects_changed_business_input(editable_import, changed):
    from src.models.menu import MonthlyMenuEntry
    from src.models.order_sheet_draft import OrderSheetDraft
    session, sheet, _ = editable_import
    captured = imports.processing_input(session, imports.get_binding(session, 'O'))
    if changed == 'unowned':
        with pytest.raises(ValueError, match='ownership_rejected'):
            imports.capture_queued_processing_input(session, 'O', captured)
        return
    with command():
        workflow._get_workflow(session, 'O')
        if changed == 'draft':
            modified = deepcopy(sheet)
            modified['rows'][1][3] = 8
            session.get(OrderSheetDraft, 'D').draft_sheet_json = modified
        else:
            session.get(MonthlyMenuEntry, 'R1').name = 'Changed explicit monthly row'
        session.flush()
        with pytest.raises(ValueError, match='initial_saved_ocr_candidate_source_changed'):
            imports.capture_queued_processing_input(session, 'O', captured)
        session.rollback()
