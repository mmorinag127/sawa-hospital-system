"""Upload explicitly verified FAX fixtures through the staging operator API."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.error
import urllib.request
import uuid

BASE = 'https://web-stg-avlnzjjrca-dt.a.run.app/api'
BUCKET = 'gs://sawahospitalsystem-stg-raw/'


def request(path, body=None, content_type=None):
    headers = {'Authorization': 'Bearer ' + os.environ['VERIFICATION_TOKEN']}
    if content_type:
        headers['Content-Type'] = content_type
    req = urllib.request.Request(BASE + path, data=body, headers=headers)
    with urllib.request.urlopen(req, timeout=180) as response:
        return json.load(response)


def download(uri, path):
    if not uri.startswith(BUCKET):
        raise ValueError('Verification artifacts must belong to staging')
    subprocess.run(['gcloud', 'storage', 'cp', uri, str(path)], check=True)


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
        boundary = uuid.uuid4().hex
        parts = []
        for name, value in [('facility_hint', case['facility_id']), ('week_hint', case['week_id'])]:
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="pdf_file"; filename="{case["source_order_id"]}.pdf"\r\nContent-Type: application/pdf\r\n\r\n'.encode() + raw + b'\r\n')
        parts.append(f'--{boundary}--\r\n'.encode())
        upload = request('/ingest/upload', b''.join(parts), 'multipart/form-data; boundary=' + boundary)
        (folder / 'upload.json').write_text(json.dumps(upload, ensure_ascii=False, indent=2))
        if upload.get('duplicate_blocked'):
            raise RuntimeError('Duplicate FAX; no implicit rerun is permitted')
        uploaded_id = upload['uploaded_pdf_id']
        evidence = None
        deadline = time.monotonic() + 1200
        while time.monotonic() < deadline:
            state = request('/ingest/uploads/' + uploaded_id)
            (folder / 'upload-state.json').write_text(json.dumps(state, ensure_ascii=False, indent=2))
            oid = state.get('current_order_id')
            if oid:
                try:
                    evidence = request(f'/orders/{oid}/evidence')
                except urllib.error.HTTPError as error:
                    if error.code != 404:
                        raise
                if evidence and evidence.get('status') == 'done':
                    break
            if state.get('status') in ('failed', 'error', 'manual_review'):
                raise RuntimeError('Upload stopped: ' + json.dumps(state, ensure_ascii=False))
            time.sleep(15)
        if not evidence or evidence.get('status') != 'done':
            raise TimeoutError('OCR evidence did not complete')
        (folder / 'evidence.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2))
        workflow = request(f'/orders/{oid}/workflow-v2')
        (folder / 'workflow.json').write_text(json.dumps(workflow, ensure_ascii=False, indent=2))
        payload = evidence['payload_json']
        metrics = payload['hakodate_canonical_pipeline']['metrics']
        assert metrics['row_axis_source'] == 'fax_intersections', metrics
        assert metrics['row_axis_draft_body_row_count'] == case['expected_menu_rows'], metrics
        download(payload['hakodate_overlay']['uri'], folder / 'overlay.png')
        download(payload['hakodate_overlay']['sheet_review_base_uri'], folder / 'corrected.png')
        print(json.dumps({'source_order':case['source_order_id'], 'stg_order':oid, 'commit':os.environ.get('GITHUB_SHA'), 'row_axis':metrics['row_axis']}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
