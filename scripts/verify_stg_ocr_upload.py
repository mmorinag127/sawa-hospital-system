"""Upload explicitly verified FAX fixtures through the staging operator API."""

import argparse
import base64
from datetime import date
import hashlib
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
    if not uri.startswith(BUCKET):
        raise ValueError('Verification artifacts must belong to staging')
    subprocess.run(['gcloud', 'storage', 'cp', uri, str(path)], check=True)


def save_json(folder, name, value):
    (folder / name).write_text(json.dumps(value, ensure_ascii=False, indent=2))


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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = args.output_dir / 'manifest.json'
    download(args.manifest, manifest_path)
    for case in json.loads(manifest_path.read_text())['cases']:
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
        boundary = uuid.uuid4().hex
        parts = []
        for name, value in [('facility_hint', case['facility_id']), ('week_hint', case['week_id']),
                            ('effective_date', case['effective_date']),
                            ('expected_canonical_revision', selection['revision']),
                            ('selected_version_id', selection['version']['version_id'])]:
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="pdf_file"; filename="{case["source_order_id"]}.pdf"\r\nContent-Type: application/pdf\r\n\r\n'.encode() + raw + b'\r\n')
        parts.append(f'--{boundary}--\r\n'.encode())
        upload = request('/ingest/upload', b''.join(parts), 'multipart/form-data; boundary=' + boundary)
        (folder / 'upload.json').write_text(json.dumps(upload, ensure_ascii=False, indent=2))
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
            (folder / 'upload-state.json').write_text(json.dumps(state, ensure_ascii=False, indent=2))
            oid = state.get('current_order_id')
            if oid:
                order = request(f'/orders/{oid}')
                save_json(folder, 'order.json', order)
                document_id = verify_owner(case, state, order)
                workflow = request(f'/orders/{oid}/workflow-v2')
                save_json(folder, 'workflow.json', workflow)
                try:
                    evidence = request(f'/orders/{oid}/evidence')
                except urllib.error.HTTPError as error:
                    if error.code != 404:
                        raise
                    evidence = None
                if evidence:
                    (folder / 'evidence.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2))
                if state.get('status') == 'completed' and not canonical_started:
                    previous_evidence_id = evidence.get('id') if evidence else None
                    quad_review = apply_verified_quad(case, oid, document_id, folder)
                    if quad_review is not None:
                        (folder / 'quad-review.json').write_text(json.dumps(quad_review, ensure_ascii=False, indent=2))
                        workflow = quad_review['workflow']
                    started = request(f'/orders/{oid}/workflow-v2/ocr-runs', json.dumps({
                        'mode': 'hakodate', 'document_id': document_id, 'stale_action': 'retry', 'force': True,
                    }).encode(), 'application/json', revision=workflow_revision(workflow, oid))
                    (folder / 'ocr-start.json').write_text(json.dumps(started, ensure_ascii=False, indent=2))
                    started_workflow = started['workflow']
                    workflow_revision(started_workflow, oid)
                    canonical_started = True
                    deadline = time.monotonic() + 1200
                    evidence = None
                    continue
                if canonical_started and evidence and completed_evidence(evidence, workflow, started_workflow, oid, document_id, previous_evidence_id):
                    break
                if canonical_started and (workflow.get('ocr_job') or {}).get('status') == 'failed':
                    raise RuntimeError('OCR failed; workflow and evidence saved for review')
            if state.get('status') in ('failed', 'error', 'manual_review'):
                raise RuntimeError('Upload stopped: ' + json.dumps(state, ensure_ascii=False))
            time.sleep(15)
        if not canonical_started or not evidence or not completed_evidence(evidence, workflow, started_workflow, oid, document_id, previous_evidence_id):
            raise TimeoutError('OCR evidence did not complete')
        (folder / 'evidence.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2))
        (folder / 'workflow.json').write_text(json.dumps(workflow, ensure_ascii=False, indent=2))
        payload = evidence['payload_json']
        save_json(folder, 'ocr-payload.json', payload)
        download(payload['hakodate_overlay']['uri'], folder / 'overlay.png')
        download(payload['hakodate_overlay']['sheet_review_base_uri'], folder / 'corrected.png')
        metrics = payload['hakodate_canonical_pipeline']['metrics']
        assert metrics['row_axis_source'] == 'fax_intersections', metrics
        assert metrics['row_axis_draft_body_row_count'] == case['expected_menu_rows'], metrics
        print(json.dumps({'source_order':case['source_order_id'], 'stg_order':oid, 'commit':os.environ.get('GITHUB_SHA'), 'row_axis':metrics['row_axis']}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
