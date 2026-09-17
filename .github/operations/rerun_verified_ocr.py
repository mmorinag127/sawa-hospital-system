"""Sequential OCR reruns of explicitly reviewed originals; never change context."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
import urllib.error
import urllib.request


def validate_case(case, order, workflow, include_confirmed):
    if case.get('identity_verified') is not True:
        raise ValueError('Original identity has not been reviewed')
    if order['id'] != case['order_id'] or order['facility'] != case['facility_id']:
        raise ValueError('Order/facility changed')
    if order['document_id'] != case['document_id'] or order['document'] != case['pdf_uri']:
        raise ValueError('Current original changed')
    if workflow['facility_id'] != case['facility_id'] or any(
        workflow[key] != case[key] for key in ('week_start', 'week_end')
    ):
        raise ValueError('Original period/facility does not match workflow')
    if order.get('is_archived') or workflow.get('blockers'):
        raise ValueError('Archived or blocked order')
    if workflow['state'] == 'ocr_running':
        raise ValueError('OCR already running')
    if not case.get('expected_job_updated_at') or (workflow.get('ocr_job') or {}).get('updated_at') != case['expected_job_updated_at']:
        raise ValueError('OCR job changed since review; this manifest cannot be replayed')
    if (order.get('status') == '確定' or workflow.get('confirmed_snapshot_id') or workflow.get('saved_sheet_id')) and not include_confirmed:
        raise ValueError('Saved/confirmed state requires explicit approval')


def download(uri, path, target):
    if not uri.startswith(f'gs://sawahospitalsystem-{target}-raw/'):
        raise ValueError('GCS object does not belong to selected environment')
    subprocess.run(['gcloud', 'storage', 'cp', uri, str(path)], check=True, capture_output=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--target', choices=['stg', 'prod'], required=True)
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--manifest-sha256', required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--include-confirmed', action='store_true')
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    out = args.output_dir
    out.mkdir(parents=True, exist_ok=True)
    path = out / 'manifest.json'
    download(args.manifest, path, args.target)
    if hashlib.sha256(path.read_bytes()).hexdigest() != args.manifest_sha256:
        raise ValueError('Reviewed manifest hash mismatch')
    manifest = json.loads(path.read_text())
    if manifest['target'] != args.target or not manifest['cases']:
        raise ValueError('Manifest target mismatch or empty selection')
    ids = [case['order_id'] for case in manifest['cases']]
    if len(set(ids)) != len(ids) or any(not re.fullmatch(r'ORD[0-9a-f]+', oid) for oid in ids):
        raise ValueError('Duplicate/invalid order IDs')

    def request(path, body=None):
        headers = {'Authorization': 'Bearer ' + os.environ['VERIFICATION_TOKEN']}
        if body is not None:
            headers['Content-Type'] = 'application/json'
        req = urllib.request.Request(
            f'https://web-{args.target}-avlnzjjrca-dt.a.run.app/api' + path,
            data=json.dumps(body).encode() if body is not None else None, headers=headers,
        )
        with urllib.request.urlopen(req, timeout=180) as response:
            return json.load(response)

    def save(folder, name, value):
        (folder / name).write_text(json.dumps(value, ensure_ascii=False, indent=2))

    # Preflight every selected original before the first mutating request.
    for case in manifest['cases']:
        oid = case['order_id']
        folder = out / oid
        folder.mkdir(exist_ok=True)
        order = request(f'/orders/{oid}')
        workflow = request(f'/orders/{oid}/workflow-v2')
        save(folder, 'order-before.json', order)
        save(folder, 'workflow-before.json', workflow)
        validate_case(case, order, workflow, args.include_confirmed)
        download(case['pdf_uri'], folder / 'original.pdf', args.target)
        if hashlib.sha256((folder / 'original.pdf').read_bytes()).hexdigest() != case['pdf_sha256']:
            raise ValueError(f'{oid}: original hash mismatch')
    if not args.execute:
        print('Read-only preflight passed:', len(ids))
        return
    results = []
    for case in manifest['cases']:
        oid = case['order_id']
        folder = out / oid
        if (folder / 'start.json').exists():
            raise ValueError('Existing request must be inspected before retry')
        validate_case(case, request(f'/orders/{oid}'), request(f'/orders/{oid}/workflow-v2'), args.include_confirmed)
        try:
            previous = request(f'/orders/{oid}/evidence')
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                raise
            previous = {}
        save(folder, 'evidence-before.json', previous)
        started = request(f'/orders/{oid}/workflow-v2/ocr-runs', {
            'mode': 'hakodate', 'document_id': case['document_id'], 'stale_action': 'wait', 'force': False,
        })
        save(folder, 'start.json', started)
        print(oid, 'accepted', flush=True)
        deadline = time.monotonic() + 1200
        while time.monotonic() < deadline:
            time.sleep(15)
            workflow = request(f'/orders/{oid}/workflow-v2')
            save(folder, 'workflow.json', workflow)
            if workflow['state'] == 'ocr_failed':
                raise RuntimeError(f'{oid}: OCR failed; remaining orders not submitted')
            if workflow['state'] != 'ocr_completed':
                continue
            evidence = request(f'/orders/{oid}/evidence')
            if evidence['id'] == previous.get('id') or evidence['status'] != 'done':
                raise RuntimeError('No new completed evidence')
            save(folder, 'evidence.json', evidence)
            overlay = evidence['payload_json']['hakodate_overlay']
            for key, filename in [('uri', 'overlay.png'), ('sheet_review_base_uri', 'corrected.png')]:
                download(overlay[key], folder / filename, args.target)
            results.append({'order_id': oid, 'evidence_id': evidence['id'], 'state': workflow['state']})
            save(out, 'results.json', results)
            print(oid, 'done', evidence['id'], flush=True)
            break
        else:
            raise TimeoutError(f'{oid}: pending; remaining orders not submitted')


if __name__ == '__main__':
    main()
