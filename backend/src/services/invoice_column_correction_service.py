"""Reviewed invoice columns; accepted quantities and authored versions stay immutable."""
from copy import deepcopy
import json
import os
from pathlib import Path
import re
import secrets


def prepare_correction(facility, *, source_columns, invoice_columns, excluded_source_columns=()):
    if not isinstance(facility, dict) or not isinstance(facility.get('fax_template'), dict):
        raise ValueError('reviewed_source_columns_required')
    if any(not isinstance(columns, (list, tuple)) or len(columns) > 50
           or any(not isinstance(column, dict) for column in columns)
           for columns in (source_columns, excluded_source_columns, invoice_columns)):
        raise ValueError('reviewed_source_columns_required')
    actual = facility.get('fax_template', {}).get('columns', [])
    if not isinstance(actual, list) or any(not isinstance(column, dict) for column in actual):
        raise ValueError('reviewed_source_columns_changed')
    authored = [column for column in actual if column.get('role') == 'quantity'
                and (column.get('semantic') or {}).get('aggregation_role', 'include') != 'exclude']
    reviewed = [*source_columns, *excluded_source_columns]
    if not source_columns or any(
        type(column.get('source_index')) is not int or column['source_index'] < 0
        or any(not isinstance(column.get(key), str) or not column[key].strip()
               for key in ('header', 'diet_type', 'area_id')) for column in reviewed
    ):
        raise ValueError('reviewed_source_columns_required')
    if len({c['source_index'] for c in reviewed}) != len(reviewed):
        raise ValueError('reviewed_source_columns_ambiguous')
    def identities(columns):
        return [(c.get('source_index'), c.get('header'), c.get('diet_type'), c.get('area_id')) for c in columns]
    if identities(authored) != identities(sorted(reviewed, key=lambda c: c['source_index'])):
        raise ValueError('reviewed_source_columns_changed')
    expected = [(c['diet_type'], c['area_id']) for c in source_columns]
    targets = [c for c in invoice_columns if c.get('source') == 'quantity']
    if len(set(expected)) != len(expected) or [(c.get('diet_type'), c.get('area_id')) for c in targets] != expected:
        raise ValueError('invoice_requires_explicit_one_to_one_source_mapping')
    if ([c.get('name') for c in targets] != [c['header'] for c in source_columns]
            or any(c.get('header', c.get('name')) != c.get('name') for c in targets)):
        raise ValueError('invoice_headers_must_match_reviewed_original')
    current = facility.get('invoice_template')
    if not isinstance(current, dict) or not isinstance(current.get('columns'), list):
        raise ValueError('existing_invoice_template_required')
    if [c for c in current['columns'] if c.get('source') != 'quantity'] != [
            c for c in invoice_columns if c.get('source') != 'quantity']:
        raise ValueError('nonquantity_invoice_columns_must_be_preserved')
    result = deepcopy(facility)
    result['invoice_template']['columns'] = deepcopy(invoice_columns)
    return result


def _review_template(facility, sheet, candidate, review, context):
    if (not isinstance(review, dict) or set(review) != {'saved_sheet_id','source_sha256','source_columns','excluded_source_columns','invoice_columns','reason'}
            or review['saved_sheet_id'] != context['saved_sheet_id']
            or review['source_sha256'] != context['source_sha256']):
        raise ValueError('trial_invoice_source_changed')
    if not isinstance(review['reason'], str) or not review['reason'].strip():
        raise ValueError('trial_invoice_review_reason_required')
    corrected = prepare_correction(facility, source_columns=review['source_columns'],
        excluded_source_columns=review['excluded_source_columns'], invoice_columns=review['invoice_columns'])
    fields, headers = sheet.get('fields', []), sheet.get('header', [])
    if (not isinstance(fields, list) or not isinstance(headers, list)
            or any(not isinstance(field, str) for field in fields)
            or len(fields) != len(headers) or len(set(fields)) != len(fields)):
        raise ValueError('trial_invoice_saved_columns_ambiguous')
    reviewed_indices = {c['source_index'] for c in review['source_columns']}
    for column in facility['fax_template']['columns']:
        if column.get('role') != 'quantity' or column.get('source_index') not in reviewed_indices:
            continue
        field = column.get('name')
        if not field or fields.count(field) != 1 or headers[fields.index(field)] != column.get('header'):
            raise ValueError('trial_invoice_saved_columns_changed')
    allowed = {(c['diet_type'], c['area_id']) for c in review['source_columns']}
    if any((line.get('diet_type'), line.get('area_id')) not in allowed for line in candidate['output_lines']):
        raise ValueError('trial_invoice_output_quantity_unmapped')
    from src.services.config_validator import _validate_invoice_columns
    errors = []
    _validate_invoice_columns(corrected['invoice_template']['columns'], 'invoice_template.columns', errors)
    if errors:
        raise ValueError('trial_invoice_columns_invalid: '+'; '.join(errors))
    return corrected['invoice_template']


def _binding(order_id, candidate):
    from src.db import SessionLocal
    from src.models.order_workflow_state import OrderWorkflowState
    from src.models.order_sheet_draft import OrderSheetDraft
    from src.models.order_confirmed_snapshot import OrderConfirmedSnapshot
    from src.services.order_workflow_v2_service import _canonical_payload_digest
    with SessionLocal() as session:
        state = session.get(OrderWorkflowState, order_id)
        draft = session.get(OrderSheetDraft, state.draft_id) if state and state.draft_id else None
        snapshot = session.get(OrderConfirmedSnapshot, state.confirmed_snapshot_id) if state and state.confirmed_snapshot_id else None
        if (not draft or not snapshot or state.state != 'confirmed' or draft.order_id != order_id
                or snapshot.order_id != order_id or snapshot.saved_sheet_id != draft.id
                or draft.base_evidence_run_id != state.evidence_run_id):
            raise ValueError('trial_invoice_confirmed_sheet_required')
        pdf = snapshot.snapshot_json.get('materialization_candidate', {}).get('materialization_input_receipt', {}).get('selected_pdf_receipt', {})
        if pdf.get('order_id') != order_id or not re.fullmatch('[0-9a-f]{64}', str(pdf.get('sha256', ''))):
            raise ValueError('trial_invoice_original_receipt_required')
        return {'order_id':order_id, 'saved_sheet_id':draft.id, 'evidence_run_id':state.evidence_run_id,
                'template_version_id':draft.template_version_id, 'confirmed_snapshot_id':snapshot.id,
                'confirmed_snapshot_digest':snapshot.snapshot_digest,
                'sheet_sha256':_canonical_payload_digest(draft.draft_sheet_json),
                'candidate_sha256':_canonical_payload_digest(candidate), 'source_sha256':pdf['sha256']}, deepcopy(draft.draft_sheet_json)


def trial_invoice_template(order_id, candidate, registration=None):
    from src.services import agent_trial_transport
    if not agent_trial_transport.enabled():
        if registration is not None:
            raise ValueError('isolated_trial_only')
        return None
    root = Path(agent_trial_transport.configuration()['root'])
    if not isinstance(order_id, str) or not re.fullmatch('[A-Za-z0-9_-]+', order_id):
        raise ValueError('trial_invoice_order_invalid')
    folder = root/'owner-invoice-columns'
    path = folder/(order_id+'.json')
    if registration is None and not path.exists():
        return None
    if not isinstance(candidate, dict) or not isinstance(candidate.get('configured_input', {}).get('facility'), dict):
        raise ValueError('trial_invoice_accepted_input_required')
    context, sheet = _binding(order_id, candidate)
    facility = candidate['configured_input']['facility']
    if registration is not None:
        template = _review_template(facility, sheet, candidate, registration, context)
        record = {**context, 'review':deepcopy(registration), 'invoice_template':template}
        folder.mkdir(exist_ok=True)
        temporary = folder/('.register-'+secrets.token_hex(8))
        try:
            temporary.write_text(json.dumps(record, ensure_ascii=False, sort_keys=True, allow_nan=False))
            temporary.chmod(0o600)
            try:
                os.link(temporary, path)
            except FileExistsError:
                if json.loads(path.read_text()) != record:
                    raise ValueError('trial_invoice_registration_already_exists')
        finally:
            temporary.unlink(missing_ok=True)
    saved = json.loads(path.read_text())
    if any(saved.get(key) != value for key, value in context.items()):
        raise ValueError('trial_invoice_accepted_input_changed')
    template = _review_template(facility, sheet, candidate, saved.get('review'), context)
    if saved.get('invoice_template') != template:
        raise ValueError('trial_invoice_registration_changed')
    return template
