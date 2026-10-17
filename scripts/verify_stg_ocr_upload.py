"""Upload explicitly verified FAX fixtures through the staging operator API."""

import argparse
import base64
from datetime import date, datetime, timezone
import hashlib
import importlib.util
import json
import math
import os
import re
from pathlib import Path
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from urllib.parse import quote, urlencode
import uuid

BASE = 'https://web-stg-avlnzjjrca-dt.a.run.app/api'
BUCKET = 'gs://sawahospitalsystem-stg-raw/'
_codec_spec = importlib.util.spec_from_file_location(
    'verification_storage_uri', Path(__file__).resolve().parents[1] / 'ocr_pipeline/app/storage_uri.py')
_codec = importlib.util.module_from_spec(_codec_spec)
_codec_spec.loader.exec_module(_codec)


def request(path, body=None, content_type=None, method=None, revision=None):
    if BASE != 'https://web-stg-avlnzjjrca-dt.a.run.app/api':
        raise ValueError('Verification API must be staging')
    headers = {'Authorization': 'Bearer ' + os.environ['VERIFICATION_TOKEN']}
    if revision is not None:
        if type(revision) is not int or revision < 1:
            raise ValueError('Positive workflow revision required')
        headers['If-Match'] = f'"{revision}"'
    if content_type:
        headers['Content-Type'] = content_type
    req = urllib.request.Request(BASE + path, data=body, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=180) as response:
        return json.load(response)


def staging_cli_uri(uri):
    """Validate one exact staging storage identity before any artifact read."""
    source = _codec.parse_gcs_uri(uri)
    if source.bucket != 'sawahospitalsystem-stg-raw':
        raise ValueError('Verification artifacts must belong to staging')
    expected_query = '' if source.generation is None else 'generation=' + source.generation
    if source.query != expected_query or '#' in uri:
        raise ValueError('Verification artifact query/fragment is not an exact storage identity')
    if any(char in source.object_name for char in '*?[]#') or any(
            ord(char) < 32 or ord(char) == 127 for char in source.object_name):
        raise ValueError('Verification artifact object contains ambiguous CLI characters')
    cli_uri = 'gs://' + source.bucket + '/' + source.object_name
    if source.generation is not None:
        cli_uri += '#' + source.generation
    return cli_uri


def download(uri, path):
    subprocess.run(['gcloud', 'storage', 'cp', staging_cli_uri(uri), str(path)], check=True)


def validate_staging_artifact_uris(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if key in ('uri', 'storage_uri'):
                staging_cli_uri(item)
            else:
                validate_staging_artifact_uris(item)
    elif isinstance(value, list):
        for item in value:
            validate_staging_artifact_uris(item)


def save_json(folder, name, value):
    def public(item):
        if isinstance(item, dict):
            return {key: public(val) for key, val in item.items() if key not in ('lease_token', 'lease_owner')}
        if isinstance(item, list):
            return [public(val) for val in item]
        return item
    (folder / name).write_text(json.dumps(public(value), ensure_ascii=False, indent=2))


def verification_case_id(case):
    """An output label only; never an order, upload or document owner."""
    if not isinstance(case, dict):
        raise ValueError('Verification case identity invalid')
    keys = [key for key in ('source_order_id', 'case_id') if key in case]
    if len(keys) != 1:
        raise ValueError('Exactly one verification case identity required')
    value = case[keys[0]]
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}', value):
        raise ValueError('Verification case identity unsafe')
    return value


def verification_case_folder(root, case):
    folder = root / verification_case_id(case)
    if folder.is_symlink() or folder.resolve().parent != root.resolve():
        raise ValueError('Verification case folder escapes output root')
    return folder


def required_raw_expectations(expected):
    if not isinstance(expected, list) or not expected:
        raise ValueError('Required raw cells invalid')
    identities = []
    for cell in expected:
        if (not isinstance(cell, dict) or set(cell) != {'date', 'daypart', 'menu_name', 'field', 'expected_raw'}
                or any(not isinstance(cell[key], str) or not cell[key] for key in cell)
                or cell['expected_raw'] != '1'):
            raise ValueError('Required raw cell expectation invalid')
        if date.fromisoformat(cell['date']).isoformat() != cell['date']:
            raise ValueError('Required raw cell date invalid')
        identities.append(tuple(cell[key] for key in ('date', 'daypart', 'menu_name', 'field')))
    if len(set(identities)) != len(identities):
        raise ValueError('Required raw cell expectations duplicate')
    return identities


def reject_unmatched_terminal(workflow, evidence, expected_outcome):
    status = (workflow.get('ocr_job') or {}).get('status')
    expected_status = 'failed' if expected_outcome == 'recognition_review_required' else 'done'
    if evidence and status in ('done', 'failed', 'error', 'cancelled', 'canceled') and (
            expected_outcome == 'done_or_review_required' or status != expected_status):
        raise RuntimeError('Unexpected terminal OCR outcome; workflow and evidence saved for review')


def case_expectation(case):
    if not isinstance(case, dict):
        raise ValueError('Verification case invalid')
    outcome = case.get('expected_outcome', 'done')
    if outcome not in ('done', 'recognition_review_required', 'done_or_review_required'):
        raise ValueError('Unknown expected OCR outcome')
    if outcome == 'done_or_review_required':
        if ('case_id' not in case or 'source_order_id' in case or 'required_raw_cells' not in case
                or any(key in case for key in ('native_empty_resolution', 'review_resolution', 'blank_resolution'))):
            raise ValueError('Union outcome requires exclusive verified source case')
        verification_case_id(case)
        required_raw_expectations(case['required_raw_cells'])
        return outcome, lambda *values: actual_case_outcome(*values) is not None
    verify_result = review_required_evidence if outcome == 'recognition_review_required' else completed_evidence
    if 'native_empty_resolution' in case:
        if 'review_resolution' in case or 'blank_resolution' in case or outcome != 'recognition_review_required':
            raise ValueError('Native empty resolution requires exclusive review outcome')
        if not isinstance(case['native_empty_resolution'], dict):
            raise ValueError('Native empty expectation invalid')
        verify_result = lambda *values: review_required_evidence(*values, review_resolution=True)
    if 'review_resolution' in case:
        if outcome != 'recognition_review_required' or not isinstance(case['review_resolution'], dict):
            raise ValueError('Review resolution requires explicit review outcome')
        verify_result = lambda *values: review_required_evidence(*values, review_resolution=True)
    if 'required_raw_cells' in case:
        required_raw_expectations(case['required_raw_cells'])
        if outcome == 'recognition_review_required':
            verify_result = lambda *values: review_required_evidence(*values, review_resolution=True)
    return outcome, verify_result


def resolve_selection(case):
    if case.get('identity_verified') is not True:
        raise ValueError('FAX facility/week must be visually verified before upload')
    start, end = case['week_id'].split('@', 1)[1].split('~')
    day = date.fromisoformat(case['effective_date'])
    if not date.fromisoformat(start) <= day <= date.fromisoformat(end):
        raise ValueError('Effective date outside explicit order week')
    resolved = request('/facilities/' + quote(case['facility_id'], safe='') + '/resolved?'
                       + urlencode({'effective_date': case['effective_date']}))
    version = resolved.get('version') or {}
    if (resolved.get('source') != 'canonical_json'
            or resolved.get('facility_id') != case['facility_id']
            or resolved.get('effective_date') != case['effective_date']
            or not isinstance(resolved.get('revision'), str) or not resolved['revision']
            or not isinstance(version.get('version_id'), str) or not version['version_id']
            or (version.get('valid_from') and day < date.fromisoformat(version['valid_from']))
            or (version.get('valid_to') and day > date.fromisoformat(version['valid_to']))):
        raise ValueError('Canonical facility selection mismatch')
    return resolved


def verify_owner(case, state, order):
    document_id = state.get('current_document_id')
    if (not document_id or state.get('current_order_id') != order.get('id')
            or state.get('content_sha256') != case['sha256']
            or order.get('facility') != case['facility_id']
            or order.get('week_value') != case['week_id']
            or order.get('document_id') != document_id
            or (order.get('current_version') or {}).get('document_id') != document_id):
        raise ValueError('Registered document owner/facility/week/hash mismatch')
    return document_id


def workflow_revision(workflow, order_id):
    revision = workflow.get('revision')
    if workflow.get('order_id') != order_id or type(revision) is not int or revision < 1:
        raise ValueError('Workflow owner/revision mismatch')
    return revision


def canonical_projection_id(case, selection):
    def digest(value):
        return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                         separators=(',', ':'), allow_nan=False).encode()).hexdigest()
    # Internal canonical_facility_lineage projection identity, checked against real artifacts.
    return 'CFV' + digest({'facility_id': case['facility_id'],
                          'authored_version_id': selection['version']['version_id'],
                          'snapshot_digest': digest(selection['version'])})


UNSELECTED_RESULT_FIELDS = ('selected_ocr_result_id', 'saved_sheet_id', 'confirmed_snapshot_id',
                          'ocr_job', 'output_bundle_id', 'bagging_result_id')


def require_no_selected_results(workflow):
    for key in UNSELECTED_RESULT_FIELDS:
        if key not in workflow:
            raise ValueError('Existing workflow result fields missing')
        if key == 'ocr_job':
            if workflow[key] is not None and (
                    not isinstance(workflow[key], dict) or workflow.get('template_version_id') is None):
                raise ValueError('Existing workflow job/context invalid')
        elif workflow[key] is not None:
            raise ValueError('Existing selected workflow results must not be overwritten')


def verify_hakodate_existing(case, selection, folder, *, require_context=False):
    state = request('/ingest/uploads/' + quote(case['existing_uploaded_pdf_id'], safe=''))
    save_json(folder, 'upload-state.json', state)
    oid = quote(case['expected_order_id'], safe='')
    order = request(f'/orders/{oid}')
    workflow = request(f'/orders/{oid}/workflow-v2')
    verify_resume_identity(case, selection, state, order, workflow, folder)
    if 'lease_expires_at' not in state:
        raise ValueError('Upload lease state missing')
    expiry = state['lease_expires_at']
    if expiry is not None:
        try:
            until = datetime.fromisoformat(expiry)
            if until.tzinfo is None:
                until = until.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            raise ValueError('Upload lease expiry invalid') from None
        if until > datetime.now(timezone.utc):
            raise ValueError('Upload lease is running')
    if state.get('status') == 'processing':
        raise ValueError('Upload processing must stop before existing-order verification')
    current_pdf = folder / 'existing-original.pdf'
    download(state['storage_uri'], current_pdf)
    if hashlib.sha256(current_pdf.read_bytes()).hexdigest() != case['sha256']:
        raise ValueError('Existing stored FAX hash mismatch')
    if resolve_selection(case) != selection:
        raise ValueError('Canonical facility selection changed')
    if require_context and workflow.get('template_version_id') != canonical_projection_id(case, selection):
        raise ValueError('Selected workflow context required after confirmation')
    return state, order, workflow


def prepare_existing_hakodate(case, selection, folder):
    state, order, workflow = verify_hakodate_existing(case, selection, folder)
    require_no_selected_results(workflow)
    start, end = case['week_id'].split('@', 1)[1].split('~')
    if workflow['template_version_id'] is None:
        at_start = resolve_selection({**case, 'effective_date': start})
        if at_start['version'] != selection['version'] or at_start['revision'] != selection['revision']:
            raise ValueError('Context date canonical selection mismatch')
        template_id = selection['version']['facility']['fax_template'].get('template_id')
        if not isinstance(template_id, str) or not template_id:
            raise ValueError('Canonical context template required')
        body = {'facility_id': case['facility_id'], 'week_start': start,
                'week_end': end, 'template_id': template_id}
        confirmed = request(f'/orders/{order["id"]}/workflow-v2/context',
                            json.dumps(body).encode(), 'application/json',
                            revision=workflow_revision(workflow, order['id']))
        verify_resume_identity(case, selection, state, order, confirmed, folder)
        if confirmed.get('template_version_id') != canonical_projection_id(case, selection):
            raise ValueError('Context confirmation did not select canonical version')
        save_json(folder, 'context-confirm.json', confirmed)
    _, _, workflow = verify_hakodate_existing(case, selection, folder, require_context=True)
    require_no_selected_results(workflow)
    return workflow


def verify_resume_identity(case, selection, state, order, workflow, folder=None):
    try:
        _verify_resume_identity(case, selection, state, order, workflow)
    except Exception:
        if folder is not None:
            # Predicates only: never persist raw API payloads, suggestions or lease credentials.
            comparisons = {
                'upload_id': (state.get('id'), case.get('existing_uploaded_pdf_id')),
                'order_id': (order.get('id'), case.get('expected_order_id')),
                'upload_order_id': (state.get('current_order_id'), case.get('expected_order_id')),
                'document_id': (order.get('document_id'), case.get('expected_document_id')),
                'upload_document_id': (state.get('current_document_id'), case.get('expected_document_id')),
                'hash': (state.get('content_sha256'), case.get('sha256')),
                'facility': (order.get('facility'), case.get('facility_id')),
                'week': (order.get('week_value'), case.get('week_id')),
                'skip_ocr': (state.get('skip_ocr'), False),
                'workflow_order_id': (workflow.get('order_id'), case.get('expected_order_id')),
            }
            fields = ('facility_id', 'week_start', 'week_end', 'template_version_id', *UNSELECTED_RESULT_FIELDS)
            blockers = workflow.get('blockers')
            try:
                canonical_matches = order.get('template_version_id') == canonical_projection_id(case, selection)
            except Exception:
                canonical_matches = False
            save_json(folder, 'resume-identity-failure.json', {
                'phase': 'verify_resume_identity', 'reason': 'identity_validation_failed',
                'matches': {key: type(actual) is type(expected) and actual == expected
                            for key, (actual, expected) in comparisons.items()},
                'workflow_uploaded': workflow.get('state') == 'uploaded',
                'canonical_projection_matches': canonical_matches,
                'markers': {key: isinstance(blockers, list) and key in blockers
                            for key in ('workflow_not_initialized', 'template_unresolved')},
                'workflow_fields': {key: {'present': key in workflow, 'is_null': workflow.get(key) is None}
                                    for key in fields},
            })
        raise


def _verify_resume_identity(case, selection, state, order, workflow):
    for key in ('existing_uploaded_pdf_id', 'expected_order_id', 'expected_document_id'):
        if not isinstance(case.get(key), str) or not case[key].strip():
            raise ValueError('Explicit existing upload/order/document identities required')
    document_id = verify_owner(case, state, order)
    version = order.get('current_version') or {}
    selected_input = version.get('selected_version_input')
    identity_keys = ('document_id', 'order_id', 'facility_code', 'week_code',
                     'is_current', 'message_id', 'storage_uri')
    if not isinstance(selected_input, dict) or any(selected_input.get(key) != version.get(key) for key in identity_keys):
        raise ValueError('Current version selected input identity mismatch')
    start, end = case['week_id'].split('@', 1)[1].split('~')
    projection = canonical_projection_id(case, selection)
    if (state.get('id') != case['existing_uploaded_pdf_id']
            or state.get('current_order_id') != case['expected_order_id']
            or document_id != case['expected_document_id']
            or state.get('skip_ocr') is not False
            or state.get('facility_hint') != case['facility_id']
            or state.get('week_hint') != case['week_id']
            or not state.get('message_id') or order.get('message_id') != state['message_id']
            or version.get('message_id') != state['message_id']
            or version.get('order_id') != order['id'] or version.get('is_current') is not True
            or version.get('facility_code') != case['facility_id'] or version.get('week_code') != case['week_id']
            or not state.get('storage_uri') or order.get('document') != state['storage_uri']
            or version.get('storage_uri') != state['storage_uri']
            or order.get('template_version_id') != projection):
        raise ValueError('Existing upload identity/canonical selection mismatch')
    expected_context = {'facility_id': case['facility_id'], 'week_start': start,
                        'week_end': end, 'template_version_id': projection}
    if not all(key in workflow for key in expected_context):
        raise ValueError('Workflow context fields missing')
    observed_context = {key: workflow.get(key) for key in expected_context}
    blockers = workflow.get('blockers')
    if not isinstance(blockers, list):
        raise ValueError('Workflow blockers must be an explicit list')
    marker = 'workflow_not_initialized' in blockers
    if marker:
        if workflow.get('state') != 'uploaded' or any(value is not None for value in observed_context.values()):
            raise ValueError('Uninitialized workflow marker/context mismatch')
    elif ('template_unresolved' in blockers
          and all(value is None for value in observed_context.values())):
        if (workflow.get('state') != 'uploaded'
                or any(key not in workflow or workflow[key] is not None for key in UNSELECTED_RESULT_FIELDS)):
            raise ValueError('Unselected workflow result/state mismatch')
    elif observed_context != expected_context:
        raise ValueError('Existing workflow selection conflicts with upload owner')
    workflow_revision(workflow, case['expected_order_id'])


def resume_upload(case, selection, folder):
    try:
        return _resume_upload(case, selection, folder)
    except Exception:
        save_json(folder, 'resume-failure.json', {'phase': 'resume_upload', 'reason': 'resume_stopped'})
        raise


def _resume_upload(case, selection, folder):
    if case.get('identity_verified') is not True:
        raise ValueError('FAX facility/week must be visually verified before retry')
    for key in ('existing_uploaded_pdf_id', 'expected_order_id', 'expected_document_id'):
        if not isinstance(case.get(key), str) or not case[key].strip():
            raise ValueError('Explicit existing upload/order/document identities required')
    uploaded_id = case['existing_uploaded_pdf_id']
    path = '/ingest/uploads/' + quote(uploaded_id, safe='')
    state = request(path)
    oid = quote(case['expected_order_id'], safe='')
    order = request(f'/orders/{oid}')
    workflow = request(f'/orders/{oid}/workflow-v2')
    verify_resume_identity(case, selection, state, order, workflow, folder)
    current_pdf = folder / 'existing-original.pdf'
    download(state['storage_uri'], current_pdf)
    if hashlib.sha256(current_pdf.read_bytes()).hexdigest() != case['sha256']:
        raise ValueError('Existing stored FAX hash mismatch')
    if resolve_selection(case) != selection:
        raise ValueError('Canonical facility selection changed before retry')
    public_state = {key: value for key, value in state.items() if key not in ('lease_owner', 'lease_token')}
    save_json(folder, 'resume-before.json', {'upload': public_state, 'order': order, 'workflow': workflow})
    if state.get('status') != 'completed':
        # No lease override or fallback upload: the operator API owns restart guards.
        result = request(path + '/retry', method='POST')
        if result.get('accepted') is not True or (result.get('item') or {}).get('id') != uploaded_id:
            raise RuntimeError('Existing upload retry not accepted')
        public_result = {**result, 'item': {key: value for key, value in result['item'].items()
                                          if key not in ('lease_owner', 'lease_token')}}
        save_json(folder, 'resume-retry.json', public_result)
    return {'uploaded_pdf_id': uploaded_id, 'resumed': True}


def apply_verified_quad(case, order_id, document_id=None, folder=None):
    if 'approved_quad' not in case:
        return None
    quad = case['approved_quad']
    if (
        not isinstance(quad, dict)
        or case.get('identity_verified') is not True
        or quad.get('coordinate_space') != {'mode': 'render_width', 'width': 1864}
        or quad.get('decision') not in ('approved_estimate', 'manual_override')
        or not isinstance(quad.get('quad_px'), list)
        or len(quad['quad_px']) != 4
        or any(not isinstance(point, list) or len(point) != 2 for point in quad['quad_px'])
        or any(type(value) not in (int, float) or not math.isfinite(value)
               for point in quad['quad_px'] for value in point)
    ):
        raise ValueError('Verified operator quad with render_width=1864 is required')
    if not document_id:
        raise ValueError('Explicit document required')
    review = request(f'/orders/{order_id}/workflow-v2/quad-review?'
                     + urlencode({'document_id': document_id}))
    if folder is not None:
        save_json(folder, 'quad-source.json', review)
        if review.get('image_png_base64'):
            (folder / 'quad-source.png').write_bytes(base64.b64decode(review['image_png_base64'], validate=True))
    receipt = review.get('source_receipt') or {}
    pdf = receipt.get('selected_pdf_receipt') or {}
    if (review.get('order_id') != order_id or pdf.get('order_id') != order_id
            or pdf.get('document_id') != document_id or pdf.get('sha256') != case['sha256']
            or receipt.get('order_input_identity') != {'facility_code': case['facility_id'], 'week_code': case['week_id']}
            or review.get('coordinate_space') != quad['coordinate_space']
            or not receipt.get('geometry_source_digest')
            or (quad['decision'] == 'approved_estimate' and review.get('suggested_quad_px') != quad['quad_px'])):
        raise ValueError('Approved quad source mismatch')
    revision = workflow_revision(request(f'/orders/{order_id}/workflow-v2'), order_id)
    if receipt.get('workflow_revision') != revision:
        raise ValueError('Quad receipt revision changed')
    return request(
        f'/orders/{order_id}/workflow-v2/quad-review',
        json.dumps({'decision': quad['decision'], 'quad_px': quad['quad_px'], 'source_receipt': receipt}).encode(),
        'application/json', method='PUT', revision=revision,
    )


def completed_evidence(evidence, workflow, started, order_id, document_id, previous_id):
    job = workflow.get('ocr_job') or {}
    own = started.get('ocr_job') or {}
    execution = (evidence.get('payload_json') or {}).get('_ocr_execution') or {}
    pdf = execution.get('selected_pdf_receipt') or {}
    return bool(own.get('ocr_job_id') and own.get('workflow_attempt_id')
        and workflow.get('order_id') == order_id
        and job.get('order_id') == order_id and job.get('order_document_id') == document_id
        and job.get('ocr_job_id') == own['ocr_job_id']
        and job.get('workflow_attempt_id') == own['workflow_attempt_id'] and job.get('status') == 'done'
        and evidence.get('id') and evidence['id'] != previous_id
        and evidence.get('status') == 'done' and evidence.get('producer_version') == 'hakodate_best_method_pipeline'
        and execution.get('job_id') == own['ocr_job_id']
        and execution.get('workflow_attempt_id') == own['workflow_attempt_id']
        and execution.get('evidence_run_id') == evidence['id']
        and pdf.get('order_id') == order_id and pdf.get('document_id') == document_id)


def review_required_evidence(evidence, workflow, started, order_id, document_id, previous_id, *, review_resolution=False):
    job = workflow.get('ocr_job') or {}
    own = started.get('ocr_job') or {}
    payload = evidence.get('payload_json') or {}
    execution = payload.get('_ocr_execution') or {}
    pdf = execution.get('selected_pdf_receipt') or {}
    review = payload.get('hakodate_recognition_review') or {}
    cells = review.get('cells')
    if not (own.get('ocr_job_id') and own.get('workflow_attempt_id')
            and workflow.get('order_id') == order_id
            and job.get('order_id') == order_id and job.get('order_document_id') == document_id
            and job.get('ocr_job_id') == own['ocr_job_id']
            and job.get('workflow_attempt_id') == own['workflow_attempt_id']
            and job.get('status') == 'failed' and job.get('result_state') == 'review_required'
            and job.get('recovery_action') == 'review_recognition_cells'
            and evidence.get('id') and evidence['id'] != previous_id
            and evidence.get('status') == 'diagnostic_only' and evidence.get('selectable') is False
            and evidence.get('producer_version') == 'hakodate_best_method_pipeline'
            and execution.get('job_id') == own['ocr_job_id']
            and execution.get('workflow_attempt_id') == own['workflow_attempt_id']
            and execution.get('evidence_run_id') == evidence['id']
            and pdf.get('order_id') == order_id and pdf.get('document_id') == document_id
            and isinstance(cells, list) and cells and type(review.get('count')) is int
            and review['count'] == len(cells)):
        return False
    expected = []
    for record in payload.get('hakodate_ocr_evidence_records', []):
        metadata = record.get('engine_metadata') or {}
        source = metadata.get('recognizer_output') or {}
        if review_resolution and source.get('resolution_state') == 'review_required':
            expected.append({'sheet_cell': metadata.get('sheet_cell'), 'raw_text': record.get('raw_text'),
                             'reason': source.get('quantity_source_issue')})
        elif not review_resolution and (source.get('recognizer_input_evidence') or {}).get('all_white') is True and source.get('recognizer_raw_text'):
            expected.append({'sheet_cell': metadata.get('sheet_cell'), 'raw_text': record.get('raw_text'),
                             'reason': 'hakodate_blank_input_nonempty_prediction'})
    return bool(expected and cells == expected and len({cell['sheet_cell'] for cell in cells}) == len(cells))


def actual_case_outcome(*values):
    """The new case policy accepts two contracts, never an ambiguous terminal."""
    evidence, workflow = values[:2]
    payload = evidence.get('payload_json') or {}
    done = completed_evidence(*values)
    review = review_required_evidence(*values, review_resolution=True)
    if done:
        job = workflow.get('ocr_job') or {}
        if (job.get('result_state') != 'evidence_ready' or job.get('processing_stage') != 'evidence_ready'
                or any(key not in job or job[key] is not None
                       for key in ('error', 'error_message', 'error_detail', 'error_user_message'))):
            return None
        cells = (payload.get('hakodate_recognition_review') or {}).get('cells')
        sources = [(row.get('engine_metadata') or {}).get('recognizer_output') or {}
                   for row in payload.get('hakodate_ocr_evidence_records', [])]
        if (cells or (workflow.get('ocr_job') or {}).get('result_state') == 'review_required'
                or any(source.get('resolution_state') == 'review_required' for source in sources)):
            return None
    if done == review:
        return None
    return 'done' if done else 'recognition_review_required'


def native_ingest_outcome(evidence, workflow, case):
    job = workflow.get('ocr_job') or {}
    native = job.get('native_ingest') or {}
    terminal = native.get('terminal_receipt')
    payload = evidence.get('payload_json') or {}
    execution = payload.get('_ocr_execution') or {}
    pdf = execution.get('selected_pdf_receipt') or {}
    if (case.get('expected_execution_mode') != 'native_ingest'
            or native.get('mode') != 'hakodate' or not isinstance(terminal, dict)
            or not native.get('execution_id') or terminal.get('execution_id') != native['execution_id']
            or terminal.get('job_id') != job.get('ocr_job_id')
            or terminal.get('order_id') != workflow.get('order_id') or terminal.get('status') != job.get('status')
            or terminal.get('error') != job.get('error_message')
            or terminal.get('evidence_run_id') != evidence.get('id')
            or execution.get('job_id') != job.get('ocr_job_id')
            or execution.get('nonworkflow_execution_id') != native['execution_id']
            or execution.get('workflow_attempt_id') is not None or terminal.get('workflow_attempt_id') is not None
            or execution.get('evidence_run_id') != evidence.get('id')
            or pdf.get('order_id') != workflow.get('order_id') or pdf.get('document_id') != job.get('order_document_id')
            or pdf.get('sha256') != case['sha256']
            or evidence.get('producer_version') != 'hakodate_best_method_pipeline'):
        return None
    if job.get('status') == 'done':
        if (job.get('result_state') != 'evidence_ready' or job.get('processing_stage') != 'evidence_ready'
                or terminal.get('error') is not None or evidence.get('status') != 'done'
                or any(key not in job or job[key] is not None
                       for key in ('error', 'error_message', 'error_detail', 'error_user_message'))
                or (payload.get('hakodate_recognition_review') or {}).get('cells')
                or any(((row.get('engine_metadata') or {}).get('recognizer_output') or {}).get('resolution_state') == 'review_required'
                       for row in payload.get('hakodate_ocr_evidence_records', []))):
            return None
        return 'done'
    if (job.get('status') != 'failed' or job.get('result_state') != 'review_required'
            or not job.get('error_message')
            or job.get('recovery_action') != 'review_recognition_cells'
            or evidence.get('status') != 'diagnostic_only' or evidence.get('selectable') is not False):
        return None
    review = payload.get('hakodate_recognition_review') or {}
    expected = []
    resolved_review = any(key in case for key in ('native_empty_resolution', 'review_resolution', 'required_raw_cells'))
    for row in payload.get('hakodate_ocr_evidence_records', []):
        metadata = row.get('engine_metadata') or {}
        source = metadata.get('recognizer_output') or {}
        if resolved_review and source.get('resolution_state') == 'review_required':
            expected.append({'sheet_cell': metadata.get('sheet_cell'), 'raw_text': row.get('raw_text'), 'reason': source.get('quantity_source_issue')})
        elif not resolved_review and (source.get('recognizer_input_evidence') or {}).get('all_white') is True and source.get('recognizer_raw_text'):
            expected.append({'sheet_cell': metadata.get('sheet_cell'), 'raw_text': row.get('raw_text'), 'reason': 'hakodate_blank_input_nonempty_prediction'})
    if (not expected or review.get('cells') != expected or type(review.get('count')) is not int
            or review['count'] != len(expected) or len({row['sheet_cell'] for row in expected}) != len(expected)):
        return None
    return 'recognition_review_required'


def verify_review_resolution(expected, records, payload, contact):
    """Explicit new-producer acceptance; old review verification stays unchanged."""
    from PIL import Image
    white = expected['blank_cells']
    numeric = expected['numeric_cells']
    nonnumeric = expected['review_cells']
    if (len(white) != 325 or len(set(white)) != 325 or len(numeric) != 57 or len(nonnumeric) != 10
            or set(white) & set(numeric) or set(white) & set(nonnumeric) or set(numeric) & set(nonnumeric)
            or expected.get('blank_raw_text') != '1' or numeric.get('E31') != '26' or numeric.get('E63') != '26'
            or expected.get('note_cells') != {}):
        raise ValueError('Review resolution expectation invalid')
    by_cell = {row['sheet_cell']: row for row in records}
    evidence = payload['hakodate_ocr_evidence_records']
    api = {(row.get('engine_metadata') or {}).get('sheet_cell'): row for row in evidence}
    if (len(records) != 392 or len(by_cell) != 392 or len(evidence) != 392
            or set(by_cell) != set(white) | set(numeric) | set(nonnumeric) or set(api) != set(by_cell)
            or payload['hakodate_ocr_evidence']['records'] != evidence
            or payload['hakodate_canonical_pipeline']['metrics']['raw_prediction_count'] != 392):
        raise ValueError('Review resolution record identities mismatch')
    observed_white = set()
    expected_review = []
    with Image.open(contact) as image:
        image = image.convert('RGB')
        for cell, source in by_cell.items():
            record = api[cell]
            if record['engine_metadata']['recognizer_output'] != source:
                raise ValueError('Review resolution artifact API mismatch')
            score = source.get('recognizer_score')
            if (isinstance(score, bool) or not isinstance(score, (int,float)) or not math.isfinite(score)
                    or not isinstance(source.get('recognizer_candidates'), list)):
                raise ValueError('Review resolution model diagnostics missing')
            x0, y0, x1, y1 = source['ocr_contact_crop_box']
            if not (0 <= x0 < x1 <= image.width and 0 <= y0 < y1 <= image.height):
                raise ValueError('Review resolution contact bounds invalid')
            crop = image.crop((x0, y0, x1, y1))
            proof = source['recognizer_input_evidence']
            all_white = crop.getextrema() == ((255,255),(255,255),(255,255))
            if (proof != {'contract': 'hakodate_contact_crop/v1', 'bbox': [x0,y0,x1,y1],
                          'width': x1-x0, 'height': y1-y0, 'all_white': all_white,
                          'rgb_sha256': hashlib.sha256(crop.tobytes()).hexdigest()}):
                raise ValueError('Review resolution contact proof mismatch')
            if all_white:
                observed_white.add(cell)
            raw = '1' if cell in white else numeric[cell] if cell in numeric else nonnumeric[cell]
            if source['recognizer_raw_text'] != raw or source['raw_text'] != raw or record['raw_text'] != raw:
                raise ValueError('Review resolution raw changed')
            if 'blank_decision' in source or record['normalized_value'] == '1':
                raise ValueError('Review resolution automatic value prohibited')
            if cell in numeric:
                if source['resolution_state'] != 'resolved' or record.get('resolution_state') != 'resolved' or source['resolved_value'] != raw or source['pred_digits'] != raw or record['normalized_value'] != raw:
                    raise ValueError('Review resolution numeric changed')
            else:
                reason = 'hakodate_blank_input_nonempty_prediction' if cell in white else 'hakodate_ocr_quantity_non_numeric'
                if (source['resolution_state'] != 'review_required' or record.get('resolution_state') != 'review_required' or source['resolved_value'] is not None
                        or source['pred_digits'] is not None or record['normalized_value'] is not None
                        or source['quantity_source_issue'] != reason):
                    raise ValueError('Review resolution unresolved quantity adopted')
                expected_review.append({'sheet_cell': cell, 'raw_text': raw, 'reason': reason})
    if observed_white != set(white) or payload['hakodate_recognition_review'] != {'count': 335, 'cells': expected_review}:
        raise ValueError('Review resolution review or white cells mismatch')
    return {'white_pixels': 325, 'review_required': 335, 'numeric': 57, 'automatic_blank': 0, 'adopted_one': 0}


def _native_empty_records(expected, records, payload, contact):
    """Content checks; public acceptance also verifies source and contact bytes."""
    from PIL import Image
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
    from src.services.hakodate_ocr_evidence_service import validate_ocr_source_for_role
    try:
        from src.services.hakodate_preprocess_observation import validate_metadata
    except ImportError as exc:
        raise ValueError('Native empty observation validator unavailable') from exc
    if not isinstance(expected, dict) or set(expected) != {'blank_cells', 'numeric_cells', 'review_cells', 'expected_model'}:
        raise ValueError('Native empty expectation invalid')
    blanks, numbers, reviews = (expected[key] for key in ('blank_cells', 'numeric_cells', 'review_cells'))
    if (not isinstance(blanks, list) or not isinstance(numbers, dict) or not isinstance(reviews, dict)
            or not reviews or any(not isinstance(cell, str) or not cell for cell in [*blanks, *numbers, *reviews])
            or len(blanks) != len(set(blanks)) or set(blanks) & set(numbers)
            or set(blanks) & set(reviews) or set(numbers) & set(reviews)
            or any(not isinstance(raw, str) or not re.fullmatch(r'[0-9]+', raw) for raw in numbers.values())
            or any(not isinstance(raw, str) or not raw or re.fullmatch(r'[0-9]+', raw) for raw in reviews.values())):
        raise ValueError('Native empty cell sets invalid')
    root = Path(__file__).resolve().parents[1]
    provenance = json.loads((root / 'backend/src/data/ocr_models/hakodate_empty_sequence_v1/provenance.json').read_text())
    model_keys = ('contract', 'base_sha256', 'adapter_sha256', 'learned_source_sha256')
    if (expected['expected_model'] != {key: provenance[key] for key in model_keys}
            or hashlib.sha256((root / 'backend/src/data/ocr_models/hakodate_empty_sequence_v1/adapter.safetensors').read_bytes()).hexdigest() != provenance['adapter_sha256']
            or payload['hakodate_canonical_pipeline']['metrics'].get('recognition_model_metadata') != provenance):
        raise ValueError('Native empty model provenance mismatch')
    cells = set(blanks) | set(numbers) | set(reviews)
    evidence = payload['hakodate_ocr_evidence_records']
    if (not isinstance(records, list) or not isinstance(evidence, list)
            or any(not isinstance(row, dict) for row in records + evidence)):
        raise ValueError('Native empty records invalid')
    by_cell = {row.get('sheet_cell'): row for row in records}
    api = {(row.get('engine_metadata') or {}).get('sheet_cell'): row for row in evidence}
    pipeline = payload['hakodate_canonical_pipeline']
    if (len(records) != len(cells) or len(evidence) != len(cells) or set(by_cell) != cells or set(api) != cells
            or pipeline['expected_sheet_cells'] != list(by_cell)
            or payload['hakodate_ocr_evidence']['records'] != evidence
            or pipeline['metrics']['raw_prediction_count'] != len(cells)):
        raise ValueError('Native empty record identities mismatch')
    def finite(value):
        if isinstance(value, float) and not math.isfinite(value):
            return False
        if isinstance(value, dict):
            return 'blank_decision' not in value and all(finite(v) for v in value.values())
        return all(finite(v) for v in value) if isinstance(value, list) else True
    observed_white, review_rows = set(), []
    with Image.open(contact) as image:
        image = image.convert('RGB')
        for cell, source in by_cell.items():
            record = api[cell]
            if record['engine_metadata'].get('recognizer_output') != source or not finite(source) or not finite(record):
                raise ValueError('Native empty artifact API diagnostics mismatch')
            score, candidates = source.get('recognizer_score'), source.get('recognizer_candidates')
            if (type(score) not in (int, float) or not math.isfinite(score) or record.get('confidence') != score
                    or not isinstance(candidates, list)
                    or any(not isinstance(c, dict) or not isinstance(c.get('text'), str)
                           or type(c.get('score')) not in (int, float) or not math.isfinite(c['score']) for c in candidates)):
                raise ValueError('Native empty diagnostics invalid')
            box = source.get('ocr_contact_crop_box')
            if not isinstance(box, list) or len(box) != 4 or any(type(v) is not int for v in box):
                raise ValueError('Native empty contact bounds invalid')
            x0,y0,x1,y1 = box
            if not (0 <= x0 < x1 <= image.width and 0 <= y0 < y1 <= image.height):
                raise ValueError('Native empty contact bounds invalid')
            crop = image.crop(box)
            white = crop.getextrema() == ((255,255),)*3
            if source.get('recognizer_input_evidence') != {'contract':'hakodate_contact_crop/v1', 'bbox':box,
                    'width':x1-x0, 'height':y1-y0, 'all_white':white,
                    'rgb_sha256':hashlib.sha256(crop.tobytes()).hexdigest()}:
                raise ValueError('Native empty contact proof mismatch')
            if white:
                observed_white.add(cell)
            raw = '' if cell in blanks else numbers[cell] if cell in numbers else reviews[cell]
            if any(value != raw for value in (source.get('recognizer_raw_text'), source.get('raw_text'), record.get('raw_text'))):
                raise ValueError('Native empty raw changed')
            if (validate_metadata(source) is not None
                    or source.get('recognizer_candidate_accepted') is not False
                    or source.get('recognizer_accepted_candidate') is not None
                    or source.get('recognizer_decision_source') == 'topk_digits'):
                raise ValueError('Native empty observation or candidate authority invalid')
            if cell in reviews:
                if (source.get('role') != 'quantity' or source.get('resolution_state') != 'review_required' or record.get('resolution_state') != 'review_required'
                        or source.get('resolved_value') is not None or source.get('pred_digits') is not None
                        or record.get('normalized_value') is not None
                        or source.get('quantity_source_issue') != 'hakodate_ocr_quantity_non_numeric'):
                    raise ValueError('Native empty unresolved quantity adopted')
                review_rows.append({'sheet_cell':cell, 'raw_text':raw, 'reason':'hakodate_ocr_quantity_non_numeric'})
            elif (source.get('resolution_state') != 'resolved' or record.get('resolution_state') != 'resolved'
                    or any(value != raw for value in (source.get('resolved_value'), source.get('pred_digits'),
                        record.get('normalized_value')))
                    or any(source[key] != raw for key in ('ocr_text', 'ocr_normalized') if key in source)
                    or source.get('quantity_source_issue')):
                raise ValueError('Native empty resolved value changed')
            if cell not in reviews and validate_ocr_source_for_role(record, source.get('role')) != raw:
                raise ValueError('Native empty shared source validation mismatch')
    if not observed_white <= set(blanks) or payload['hakodate_recognition_review'] != {'count':len(reviews), 'cells':review_rows}:
        raise ValueError('Native empty white or review cells mismatch')
    return {'white_pixels':len(observed_white), 'source_blank_count':len(blanks),
            'numeric':len(numbers), 'review_required':len(reviews)}


def _verified_observation_bytes(payload):
    # Canonical loaders can also read local files and non-staging buckets.
    # Fence every supplied receipt before entering that shared boundary.
    validate_staging_artifact_uris(payload)
    pipeline = payload['hakodate_canonical_pipeline']
    for uri in pipeline['outputs'].values():
        staging_cli_uri(uri)
    receipt = pipeline['artifact_packet']['outputs']['preprocess_observation']
    with tempfile.TemporaryDirectory(prefix='stg-ocr-observation-') as folder:
        path = Path(folder) / 'observation.json'
        download(receipt['uri'], path)
        observation_bytes = path.read_bytes()
    if (len(observation_bytes) != receipt.get('size_bytes')
            or hashlib.sha256(observation_bytes).hexdigest() != receipt.get('sha256')):
        raise ValueError('Native empty observation bytes changed')
    validate_staging_artifact_uris(json.loads(observation_bytes))
    return observation_bytes


def _verify_record_source(records, payload, contact, observation_bytes):
    pipeline = payload['hakodate_canonical_pipeline']
    if (payload.get('_ocr_execution') or {}).get('selected_pdf_receipt') != payload['hakodate_repair_source_receipt']['selected_pdf_receipt']:
        raise ValueError('canonical_artifact_execution_source_changed')
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
    from src.services import canonical_ocr_artifact_service as artifacts
    verified = artifacts.validate_payload(payload)
    if (json.loads(verified['records']) != records or verified['contact_sheet'] != Path(contact).read_bytes()
            or 'preprocess_observation' not in pipeline['outputs']):
        raise ValueError('Native empty canonical source or observation artifact missing')
    try:
        from src.services.hakodate_preprocess_observation import validate_artifact
    except ImportError as exc:
        raise ValueError('Native empty observation validator unavailable') from exc
    validate_artifact(observation_bytes, records, verified['contact_sheet'], payload['hakodate_repair_source_receipt'])


def verify_native_empty_resolution(expected, records, payload, contact):
    observation_bytes = _verified_observation_bytes(payload)
    result = _native_empty_records(expected, records, payload, contact)
    _verify_record_source(records, payload, contact, observation_bytes)
    return result


def verify_required_raw_cells(expected, records, payload, contact):
    """Match authored business identities, not guessed worksheet addresses."""
    identities = required_raw_expectations(expected)
    observation_bytes = _verified_observation_bytes(payload)
    _verify_record_source(records, payload, contact, observation_bytes)
    evidence = payload['hakodate_ocr_evidence_records']
    if not isinstance(records, list) or not isinstance(evidence, list):
        raise ValueError('Required raw records invalid')
    checked = []
    for cell, identity in zip(expected, identities):
        matches = [row for row in records if isinstance(row, dict)
                   and tuple(row.get(key) for key in ('date', 'daypart', 'menu_name', 'field')) == identity]
        api_matches = [row for row in evidence if isinstance(row, dict)
                       and isinstance((row.get('engine_metadata') or {}).get('recognizer_output'), dict)
                       and tuple(row['engine_metadata']['recognizer_output'].get(key)
                                 for key in ('date', 'daypart', 'menu_name', 'field')) == identity]
        if len(matches) != 1 or len(api_matches) != 1:
            raise ValueError('Required raw cell identity missing or ambiguous')
        source, api = matches[0], api_matches[0]
        raw = cell['expected_raw']
        if (api['engine_metadata']['recognizer_output'] != source
                or any(source.get(key) != raw for key in ('recognizer_raw_text', 'raw_text', 'resolved_value'))
                or api.get('raw_text') != raw or api.get('normalized_value') != raw):
            raise ValueError('Required raw cell value changed')
        checked.append(dict(cell))
    return {'count': len(checked), 'cells': checked}


def fetch_own_evidence(order_id, workflow, started):
    """Fetch only the exact candidate emitted by this workflow attempt."""
    job = workflow.get('ocr_job') or {}
    own = started.get('ocr_job') or {}
    own_job_id = own.get('ocr_job_id')
    own_attempt_id = own.get('workflow_attempt_id')
    if (not isinstance(own_job_id, str) or not own_job_id
            or not isinstance(own_attempt_id, str) or not own_attempt_id
            or job.get('ocr_job_id') != own_job_id
            or job.get('workflow_attempt_id') != own_attempt_id):
        return None
    candidates = request(f'/orders/{quote(order_id, safe="")}/workflow-v2/ocr-results')
    matching = [candidate for candidate in candidates.get('results', [])
                if isinstance(candidate, dict)
                and (candidate.get('execution_lineage') or {}).get('job_id') == own_job_id
                and (candidate.get('execution_lineage') or {}).get('workflow_attempt_id') == own_attempt_id]
    if len(matching) > 1:
        raise ValueError('Multiple OCR evidence candidates match own execution')
    if not matching:
        return None
    evidence_id = matching[0].get('ocr_result_id')
    if not isinstance(evidence_id, str) or not evidence_id:
        raise ValueError('Exact OCR evidence id missing from own candidate')
    evidence = request(f'/orders/{quote(order_id, safe="")}/workflow-v2/ocr-results/{quote(evidence_id, safe="")}')
    execution = (evidence.get('payload_json') or {}).get('_ocr_execution') or {}
    if (evidence.get('id') != evidence_id or evidence.get('order_id') != order_id
            or execution.get('job_id') != own_job_id or execution.get('workflow_attempt_id') != own_attempt_id):
        raise ValueError('Exact OCR evidence retrieval identity mismatch')
    return evidence


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = args.output_dir / 'manifest.json'
    download(args.manifest, manifest_path)
    cases = json.loads(manifest_path.read_text())['cases']
    expectations = [case_expectation(case) for case in cases]
    labels = [verification_case_id(case) for case in cases]
    if len(labels) != len(set(labels)):
        raise ValueError('Verification case identity collision')
    for case, label, (outcome, verify_result) in zip(cases, labels, expectations):
        folder = verification_case_folder(args.output_dir, case)
        folder.mkdir(exist_ok=True)
        pdf = folder / 'original.pdf'
        download(case['pdf_uri'], pdf)
        raw = pdf.read_bytes()
        if hashlib.sha256(raw).hexdigest() != case['sha256']:
            raise ValueError('FAX hash mismatch')
        if case.get('identity_verified') is not True:
            raise ValueError('FAX facility/week must be visually verified before upload')
        selection = resolve_selection(case)
        save_json(folder, 'facility-selection.json', selection)
        resume_keys = ('existing_uploaded_pdf_id', 'expected_order_id', 'expected_document_id')
        explicit_hakodate = case.get('verification_mode') == 'existing_order_hakodate'
        if 'verification_mode' in case and not explicit_hakodate:
            raise ValueError('Unknown explicit verification mode')
        if 'expected_execution_mode' in case and case['expected_execution_mode'] != 'native_ingest':
            raise ValueError('Unknown expected execution mode')
        native_expected = not explicit_hakodate and case.get('expected_execution_mode') == 'native_ingest'
        if not explicit_hakodate:
            save_json(folder, 'verification-scope.json', {
                'verification_mode': 'native_ingest' if native_expected else 'v1_workflow_owned',
                'normal_native_ingest_verified': False,
            })
        if explicit_hakodate:
            if any(not isinstance(case.get(key), str) or not case[key].strip() for key in resume_keys):
                raise ValueError('Explicit existing upload/order/document identities required')
            save_json(folder, 'verification-scope.json', {
                'verification_mode': 'existing_order_hakodate',
                'upload_to_ocr_success': False, 'legacy_firstpass_verified': False,
                'upload_status_mutation': False,
            })
            verify_hakodate_existing(case, selection, folder)
            upload = {'uploaded_pdf_id': case['existing_uploaded_pdf_id'],
                      'verification_mode': 'existing_order_hakodate'}
        elif any(key in case for key in resume_keys):
            upload = resume_upload(case, selection, folder)
        else:
            upload = upload_new(case, selection, raw)
        save_json(folder, 'upload.json', upload)
        if upload.get('duplicate_blocked'):
            raise RuntimeError('Duplicate FAX; no implicit rerun is permitted')
        uploaded_id = upload['uploaded_pdf_id']
        evidence = None
        canonical_started = False
        previous_evidence_id = None
        document_id = None
        started_workflow = {}
        workflow = {}
        native_flow = False
        deadline = time.monotonic() + 1200
        while time.monotonic() < deadline:
            state = request('/ingest/uploads/' + uploaded_id)
            save_json(folder, 'upload-state.json', state)
            oid = state.get('current_order_id')
            if oid:
                order = request(f'/orders/{oid}')
                save_json(folder, 'order.json', order)
                document_id = verify_owner(case, state, order)
                workflow = request(f'/orders/{oid}/workflow-v2')
                if explicit_hakodate:
                    state, order, workflow = verify_hakodate_existing(
                        case, selection, folder, require_context=canonical_started)
                if any(key in case for key in resume_keys):
                    verify_resume_identity(case, selection, state, order, workflow, folder)
                save_json(folder, 'workflow.json', workflow)
                if not native_expected and not explicit_hakodate and 'native_ingest' in (workflow.get('ocr_job') or {}):
                    raise RuntimeError('V1 workflow executor cannot adopt native ingest lineage')
                if native_expected and 'native_ingest' not in (workflow.get('ocr_job') or {}):
                    if (state.get('status') in ('completed', 'failed', 'error', 'manual_review')
                            or (workflow.get('ocr_job') or {}).get('status') in ('done', 'failed', 'error')):
                        raise RuntimeError('Normal upload native lineage missing; no second OCR permitted')
                    time.sleep(15)
                    continue
                if native_expected and 'native_ingest' in (workflow.get('ocr_job') or {}):
                    native = workflow['ocr_job']['native_ingest']
                    if (not isinstance(native, dict) or native.get('mode') != 'hakodate'
                            or not isinstance(native.get('execution_id'), str) or not native['execution_id']):
                        raise RuntimeError('Normal upload native mode or execution invalid')
                    native_flow = True
                    canonical_started = True
                    verify_result = lambda value, current, *_: native_ingest_outcome(value, current, case) in (
                        ('done', 'recognition_review_required') if outcome == 'done_or_review_required' else (outcome,))
                    terminal = workflow['ocr_job']['native_ingest'].get('terminal_receipt')
                    if isinstance(terminal, dict):
                        evidence_id = terminal.get('evidence_run_id')
                        if not isinstance(evidence_id, str) or not evidence_id:
                            raise RuntimeError('Native ingest stopped without evidence; workflow saved')
                        try:
                            evidence = request(f'/orders/{oid}/workflow-v2/ocr-results/{quote(evidence_id, safe="")}')
                        except urllib.error.HTTPError as error:
                            if error.code != 404:
                                raise
                            time.sleep(15)
                            continue
                        save_json(folder, 'evidence.json', evidence)
                        if verify_result(evidence, workflow):
                            break
                        raise RuntimeError('Native ingest terminal contract mismatch; evidence saved')
                    if workflow['ocr_job'].get('status') in ('done', 'failed', 'error'):
                        raise RuntimeError('Native ingest terminal receipt missing')
                    time.sleep(15)
                    continue
                if canonical_started:
                    evidence = fetch_own_evidence(oid, workflow, started_workflow)
                else:
                    try:
                        evidence = request(f'/orders/{oid}/evidence')
                    except urllib.error.HTTPError as error:
                        if error.code != 404:
                            raise
                        evidence = None
                if evidence:
                    (folder / 'evidence.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2))
                if (explicit_hakodate or state.get('status') == 'completed') and not canonical_started:
                    if explicit_hakodate:
                        workflow = prepare_existing_hakodate(case, selection, folder)
                    previous_evidence_id = evidence.get('id') if evidence else None
                    quad_review = apply_verified_quad(case, oid, document_id, folder)
                    if quad_review is not None:
                        (folder / 'quad-review.json').write_text(json.dumps(quad_review, ensure_ascii=False, indent=2))
                        workflow = quad_review['workflow']
                    if explicit_hakodate:
                        _, _, workflow = verify_hakodate_existing(case, selection, folder, require_context=True)
                        require_no_selected_results(workflow)
                    started = request(f'/orders/{oid}/workflow-v2/ocr-runs', json.dumps({
                        'mode': 'hakodate', 'document_id': document_id, 'stale_action': 'retry', 'force': not explicit_hakodate,
                    }).encode(), 'application/json', revision=workflow_revision(workflow, oid))
                    (folder / 'ocr-start.json').write_text(json.dumps(started, ensure_ascii=False, indent=2))
                    started_workflow = started['workflow']
                    workflow_revision(started_workflow, oid)
                    if explicit_hakodate:
                        verify_resume_identity(case, selection, state, order, started_workflow, folder)
                        if started_workflow.get('template_version_id') != canonical_projection_id(case, selection):
                            raise ValueError('OCR start context mismatch')
                    canonical_started = True
                    deadline = time.monotonic() + 1200
                    evidence = None
                    continue
                if canonical_started and evidence and verify_result(evidence, workflow, started_workflow, oid, document_id, previous_evidence_id):
                    break
                if canonical_started and (workflow.get('ocr_job') or {}).get('status') == 'failed':
                    raise RuntimeError('OCR failed; workflow and evidence saved for review')
                if canonical_started:
                    reject_unmatched_terminal(workflow, evidence, outcome)
            if not explicit_hakodate and state.get('status') in ('failed', 'error', 'manual_review'):
                raise RuntimeError('Upload stopped; sanitized upload-state artifact saved')
            time.sleep(15)
        if not canonical_started or not evidence or not verify_result(evidence, workflow, started_workflow, oid, document_id, previous_evidence_id):
            raise TimeoutError('OCR evidence did not complete')
        (folder / 'evidence.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2))
        (folder / 'workflow.json').write_text(json.dumps(workflow, ensure_ascii=False, indent=2))
        payload = evidence['payload_json']
        if outcome == 'done_or_review_required':
            actual_outcome = (native_ingest_outcome(evidence, workflow, case) if native_flow else
                actual_case_outcome(evidence, workflow, started_workflow, oid, document_id, previous_evidence_id))
            if actual_outcome is None:
                raise ValueError('Ambiguous terminal OCR contract')
            save_json(folder, 'verification-outcome.json', {'manifest_policy': outcome, 'actual_outcome': actual_outcome})
        else:
            actual_outcome = outcome
        save_json(folder, 'ocr-payload.json', payload)
        if explicit_hakodate:
            verify_hakodate_existing(case, selection, folder, require_context=True)
        download(payload['hakodate_overlay']['uri'], folder / 'overlay.png')
        download(payload['hakodate_overlay']['sheet_review_base_uri'], folder / 'corrected.png')
        if outcome == 'recognition_review_required' or 'required_raw_cells' in case:
            outputs = payload['hakodate_canonical_pipeline']['outputs']
            download(outputs['records'], folder / 'recognizer-records.json')
            download(outputs['contact_sheet'], folder / 'recognizer-contact.png')
        if 'required_raw_cells' in case:
            result = verify_required_raw_cells(case['required_raw_cells'],
                json.loads((folder / 'recognizer-records.json').read_text()), payload,
                folder / 'recognizer-contact.png')
            save_json(folder, 'required-raw-cells.json', result)
        if actual_outcome == 'recognition_review_required':
            if 'native_empty_resolution' in case:
                result = verify_native_empty_resolution(case['native_empty_resolution'],
                    json.loads((folder / 'recognizer-records.json').read_text()), payload,
                    folder / 'recognizer-contact.png')
                save_json(folder, 'native-empty-resolution.json', result)
            if 'review_resolution' in case:
                result = verify_review_resolution(case['review_resolution'],
                    json.loads((folder / 'recognizer-records.json').read_text()), payload,
                    folder / 'recognizer-contact.png')
                save_json(folder, 'review-resolution.json', result)
            review = request(f'/orders/{oid}/workflow-v2/recognition-review?'
                            + urlencode({'document_id': document_id, 'ocr_result_id': evidence['id']}))
            receipt = review.get('source_receipt') or {}
            if (review.get('order_id') != oid or receipt.get('recognition_evidence_run_id') != evidence['id']
                    or (receipt.get('selected_pdf_receipt') or {}).get('document_id') != document_id
                    or not review.get('cells')):
                raise ValueError('Recognition review result identity mismatch')
            save_json(folder, 'recognition-review.json', review)
        metrics = payload['hakodate_canonical_pipeline']['metrics']
        assert metrics['row_axis_source'] == 'fax_intersections', metrics
        assert metrics['row_axis_draft_body_row_count'] == case['expected_menu_rows'], metrics
        if native_flow:
            save_json(folder, 'verification-scope.json', {
                'verification_mode': 'native_ingest', 'normal_native_ingest_verified': True,
            })
        label_key = 'source_order' if 'source_order_id' in case else 'case_id'
        print(json.dumps({label_key:label, 'stg_order':oid, 'commit':os.environ.get('GITHUB_SHA'), 'row_axis':metrics['row_axis']}, ensure_ascii=False), flush=True)


def upload_new(case, selection, raw):
    boundary = uuid.uuid4().hex
    parts = []
    for name, value in [('facility_hint', case['facility_id']), ('week_hint', case['week_id']),
                        ('effective_date', case['effective_date']),
                        ('expected_canonical_revision', selection['revision']),
                        ('selected_version_id', selection['version']['version_id'])]:
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="pdf_file"; filename="{verification_case_id(case)}.pdf"\r\nContent-Type: application/pdf\r\n\r\n'.encode() + raw + b'\r\n')
    parts.append(f'--{boundary}--\r\n'.encode())
    return request('/ingest/upload', b''.join(parts), 'multipart/form-data; boundary=' + boundary)


if __name__ == '__main__':
    main()
