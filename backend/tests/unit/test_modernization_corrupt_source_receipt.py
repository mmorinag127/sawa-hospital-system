"""Real owned SQLite source receipts; no OCR or PostgreSQL proof."""
from copy import deepcopy

import pytest

from tests.unit.test_modernization_selected_template_reads import (
    owned as owned, unchanged_reads, reject_selected, blocked_context,
    public_readers, seed_current_state, assert_selected_saved_blocked,
)
from src.models.facility_template_version import FacilityTemplateVersion
from src.services.canonical_facility_store import CanonicalStoreError
from src.services import workflow_attempt_guard_service as guard


REASON = 'canonical_lineage_snapshot_mismatch'


@pytest.mark.parametrize('persisted', [False, True])
@pytest.mark.parametrize('reader', public_readers(), ids=lambda reader: reader.__name__)
def test_corrupt_projection_public_reads_bind_empty_owned_receipt_without_writes(owned, persisted, reader):
    if persisted:
        seed_current_state(owned)
    reject_selected(owned, 'corrupt-projection')
    with unchanged_reads(owned):
        context = reader(owned.order_id)
        assert_selected_saved_blocked(owned, context, REASON)
        receipt = context.input_receipt
        assert receipt['input']['sheet_source_unavailable'] == REASON
        assert receipt['input']['order']['id'] == owned.order_id
        assert receipt['input']['template']['id'] == owned.projection_id
        assert 'configured_input' not in receipt['input']
        with owned.session_scope() as session:
            assert guard.validate_current_sheet_input(session, owned.order_id, context).id == owned.order_id


def test_changed_raw_broken_projection_rejects_captured_receipt(owned):
    reject_selected(owned, 'corrupt-projection')
    with unchanged_reads(owned):
        captured = guard.capture_current_sheet_input(owned.order_id)
        context = guard.bind_current_sheet_input(blocked_context(owned.order_id, REASON), captured)
    with owned.session_scope() as session:
        projection = session.get(FacilityTemplateVersion, owned.projection_id)
        changed = deepcopy(projection.config_json)
        changed['fax_template']['header_rows'] = 4
        projection.config_json = changed
    with unchanged_reads(owned):
        with owned.session_scope() as session:
            with pytest.raises(guard.CurrentSheetInputChanged, match='^current_sheet_input_changed$'):
                guard.validate_current_sheet_input(session, owned.order_id, context)


@pytest.mark.parametrize('malformation', ['usable-rows', 'usable-draft'])
def test_unavailable_receipt_cannot_validate_usable_context(owned, malformation):
    reject_selected(owned, 'corrupt-projection')
    with unchanged_reads(owned):
        captured = guard.capture_current_sheet_input(owned.order_id)
        context = blocked_context(owned.order_id, REASON)
        if malformation == 'usable-rows':
            context['blockers'] = []
            context['fields'] = deepcopy(owned.payload['fields'])
            context['rows'] = deepcopy(owned.payload['rows'])
        else:
            context['draft_payload'] = deepcopy(owned.payload)
        bound = guard.bind_current_sheet_input(context, captured)
        with owned.session_scope() as session:
            with pytest.raises(guard.CurrentSheetInputChanged, match='^current_sheet_source_unavailable$'):
                guard.validate_current_sheet_input(session, owned.order_id, bound)


def test_valid_receipt_then_corrupt_projection_preserves_named_exception(owned):
    with unchanged_reads(owned):
        captured = guard.capture_current_sheet_input(owned.order_id)
        assert 'sheet_source_unavailable' not in captured
        context = guard.bind_current_sheet_input({'order_id': owned.order_id}, captured)
        with owned.session_scope() as session:
            assert guard.validate_current_sheet_input(session, owned.order_id, context).id == owned.order_id
    reject_selected(owned, 'corrupt-projection')
    with unchanged_reads(owned):
        with owned.session_scope() as session:
            with pytest.raises(CanonicalStoreError, match='^canonical_lineage_snapshot_mismatch$'):
                guard.validate_current_sheet_input(session, owned.order_id, context)
