"""Upload explicitly verified FAX fixtures through the staging operator API."""

import argparse
import base64
from datetime import date, datetime, timezone
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import subprocess
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


def download(uri, path):
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
    subprocess.run(['gcloud', 'storage', 'cp', cli_uri, str(path)], check=True)


def save_json(folder, name, value):
    def public(item):
        if isinstance(item, dict):
            return {key: public(val) for key, val in item.items() if key not in ('lease_token', 'lease_owner')}
        if isinstance(item, list):
            return [public(val) for val in item]
        return item
    (folder / name).write_text(json.dumps(public(value), ensure_ascii=False, indent=2))


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


def review_required_evidence(evidence, workflow, started, order_id, document_id, previous_id):
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
        if (source.get('recognizer_input_evidence') or {}).get('all_white') is True and source.get('recognizer_raw_text'):
            expected.append({'sheet_cell': metadata.get('sheet_cell'), 'raw_text': record.get('raw_text'),
                             'reason': 'hakodate_blank_input_nonempty_prediction'})
    return bool(expected and cells == expected and len({cell['sheet_cell'] for cell in cells}) == len(cells))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = args.output_dir / 'manifest.json'
    download(args.manifest, manifest_path)
    for case in json.loads(manifest_path.read_text())['cases']:
        outcome = case.get('expected_outcome', 'done')
        if outcome not in ('done', 'recognition_review_required'):
            raise ValueError('Unknown expected OCR outcome')
        verify_result = review_required_evidence if outcome == 'recognition_review_required' else completed_evidence
        folder = args.output_dir / case['source_order_id']
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
            if not explicit_hakodate and state.get('status') in ('failed', 'error', 'manual_review'):
                raise RuntimeError('Upload stopped; sanitized upload-state artifact saved')
            time.sleep(15)
        if not canonical_started or not evidence or not verify_result(evidence, workflow, started_workflow, oid, document_id, previous_evidence_id):
            raise TimeoutError('OCR evidence did not complete')
        (folder / 'evidence.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2))
        (folder / 'workflow.json').write_text(json.dumps(workflow, ensure_ascii=False, indent=2))
        payload = evidence['payload_json']
        save_json(folder, 'ocr-payload.json', payload)
        if explicit_hakodate:
            verify_hakodate_existing(case, selection, folder, require_context=True)
        download(payload['hakodate_overlay']['uri'], folder / 'overlay.png')
        download(payload['hakodate_overlay']['sheet_review_base_uri'], folder / 'corrected.png')
        if outcome == 'recognition_review_required':
            outputs = payload['hakodate_canonical_pipeline']['outputs']
            download(outputs['records'], folder / 'recognizer-records.json')
            download(outputs['contact_sheet'], folder / 'recognizer-contact.png')
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
        print(json.dumps({'source_order':case['source_order_id'], 'stg_order':oid, 'commit':os.environ.get('GITHUB_SHA'), 'row_axis':metrics['row_axis']}, ensure_ascii=False), flush=True)


def upload_new(case, selection, raw):
    boundary = uuid.uuid4().hex
    parts = []
    for name, value in [('facility_hint', case['facility_id']), ('week_hint', case['week_id']),
                        ('effective_date', case['effective_date']),
                        ('expected_canonical_revision', selection['revision']),
                        ('selected_version_id', selection['version']['version_id'])]:
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="pdf_file"; filename="{case["source_order_id"]}.pdf"\r\nContent-Type: application/pdf\r\n\r\n'.encode() + raw + b'\r\n')
    parts.append(f'--{boundary}--\r\n'.encode())
    return request('/ingest/upload', b''.join(parts), 'multipart/form-data; boundary=' + boundary)


if __name__ == '__main__':
    main()
