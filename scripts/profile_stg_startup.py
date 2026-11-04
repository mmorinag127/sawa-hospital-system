"""Bounded Actions-only startup profiling of unchanged, already-serving images."""
import argparse
import base64
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile
import time

from local_receipt_signing import canonical, verify_signed

PROJECT = 'sawahospitalsystem'
REGION = 'asia-northeast2'
TARGETS = {
    'worker-stg': ['src.http_entrypoint:app', '--host', '0.0.0.0', '--port', '8080', '--proxy-headers', '--forwarded-allow-ips', '*'],
    'price-api-stg': ['src.main:app', '--host', '0.0.0.0', '--port', '8080', '--proxy-headers', '--forwarded-allow-ips', '*'],
    'shift-api-stg': ['shift_scheduler_backend.main:app', '--host', '0.0.0.0', '--port', '8080'],
}

def cli(*args, data=False):
    result = subprocess.run(['gcloud', *args], capture_output=True, text=True)
    if result.returncode:
        # A service export contains secrets. Never print commands, exports or stderr.
        raise RuntimeError('gcloud operation failed: '+args[0]+' '+args[1])
    return json.loads(result.stdout) if data else result.stdout

def describe(service):
    if service not in TARGETS:
        raise ValueError('only the three staging API services are allowed')
    result = cli('run','services','describe',service,'--project',PROJECT,'--region',REGION,'--format=json',data=True)
    revision = result['status']['latestReadyRevisionName']
    resolved = cli('run','revisions','describe',revision,'--project',PROJECT,'--region',REGION,'--format=json',data=True)
    result['status']['_profile_image_digest'] = resolved['status']['imageDigest']
    return result

def image_and_revision(service):
    containers = service['spec']['template']['spec']['containers']
    if len(containers)!=1 or containers[0].get('command') or containers[0].get('args'):
        raise ValueError('unexpected runtime command or container count')
    image = service['status'].get('_profile_image_digest',containers[0]['image'])
    if not re.fullmatch(r'asia-northeast2-docker.pkg.dev/sawahospitalsystem/[\w/-]+@sha256:[a-f0-9]{64}',image):
        raise ValueError('an immutable existing project image is required')
    revision = service['status']['latestReadyRevisionName']
    traffic = service['status']['traffic']
    if len(traffic)!=1 or traffic[0].get('revisionName')!=revision or traffic[0].get('percent')!=100 or traffic[0].get('tag'):
        raise ValueError('the existing service must have one untagged 100% revision')
    return image, revision

def spec_hash(service):
    return sha256(canonical(service['spec'])).hexdigest()

def inventory():
    result = {}
    for name in TARGETS:
        service = describe(name)
        image, revision = image_and_revision(service)
        result[name] = {'image':image,'revision':revision,'spec_sha256':spec_hash(service)}
    return result

def probe_document(original, name, suffix):
    image, revision = image_and_revision(original)
    result = deepcopy(original)
    result.pop('status',None)
    template = result['spec']['template']
    template.setdefault('metadata',{})['name'] = name+'-profile-'+suffix
    container = template['spec']['containers'][0]
    container['image'] = image
    container['command'] = ['/bin/sh']
    command = 'printf "startup_container_entry epoch=%s\\n" "$(date +%s.%N)" >&2; exec python -X importtime -m uvicorn '+shlex.join(TARGETS[name])
    container['args'] = ['-c',command]
    # The diagnostic revision never receives the user's traffic.
    result['spec']['traffic'] = [{'revisionName':revision,'percent':100}]
    return result

def replace(document, directory):
    path = Path(directory)/'private-service.json'
    path.write_text(json.dumps(document))
    path.chmod(0o600)
    try:
        cli('run','services','replace',str(path),'--project',PROJECT,'--region',REGION,'--quiet')
    finally:
        path.unlink(missing_ok=True)

def restored_document(original, current, probe):
    # Never restore over an unrelated writer or a changed database binding.
    if canonical(current['spec']) != canonical(probe['spec']):
        raise RuntimeError('service changed outside this profiling run; refusing stale restore')
    result = deepcopy(current)
    result.pop('status',None)
    result['spec'] = deepcopy(original['spec'])
    result['spec']['template'].setdefault('metadata',{})['name'] = original['status']['latestReadyRevisionName']
    # Keep the original concrete traffic while re-selecting its existing template.
    result['spec']['traffic'] = [{'revisionName':original['status']['latestReadyRevisionName'],'percent':100}]
    return result

def require_actions(receipt, expected):
    if (os.environ.get('GITHUB_ACTIONS')!='true' or os.environ.get('GITHUB_REPOSITORY')!='mmorinag127/sawa-hospital-system'
            or os.environ.get('GITHUB_REF')!='refs/heads/develop'
            or os.environ.get('GITHUB_ACTOR')!=os.environ.get('GITHUB_REPOSITORY_OWNER') or not os.environ.get('GITHUB_ACTOR')):
        raise ValueError('profiling updates are only allowed in the owner-dispatched staging Actions workflow')
    payload = verify_signed(receipt)
    head = os.environ['GITHUB_SHA']
    script = Path(__file__)
    if (payload.get('kind')!='stg-startup-profile' or payload.get('source_sha')!=head
            or payload.get('inventory_sha256')!=sha256(canonical(expected)).hexdigest()
            or payload.get('script_sha256')!=sha256(script.read_bytes()).hexdigest()
            or type(payload.get('test_exit_code')) is not int or payload['test_exit_code']!=0
            or type(payload.get('linux_launcher_exit_code')) is not int or payload['linux_launcher_exit_code']!=0):
        raise ValueError('exact-source successful local profiling checks are required')
    if set(expected)!=set(TARGETS):
        raise ValueError('exact staging target inventory required')

def restore(original, probe, directory):
    name = original['metadata']['name']
    current = describe(name)
    if spec_hash(current)==spec_hash(original):
        return
    restored = restored_document(original,current,probe)
    revision = original['status']['latestReadyRevisionName']
    replace(restored,directory)
    current = describe(name)
    if current['status']['latestReadyRevisionName']!=revision:
        raise RuntimeError('original revision was not re-selected')
    if current['spec']['traffic'] != original['spec'].get('traffic'):
        current.pop('status',None)
        current['spec']['traffic'] = deepcopy(original['spec']['traffic'])
        replace(current,directory)
    final = describe(name)
    if spec_hash(final)!=spec_hash(original) or image_and_revision(final)!=image_and_revision(original):
        raise RuntimeError('original staging settings were not restored exactly')

def restore_saved(private):
    for path in sorted(private.glob('*.json')):
        document=json.loads(path.read_text())
        original,probe=document['original'],document['probe']
        if original['metadata']['name'] not in TARGETS:
            raise ValueError('invalid private restore target')
        with tempfile.TemporaryDirectory(prefix='sawa-private-restore-') as directory:
            restore(original,probe,directory)
        path.unlink()
        status=private.parent/'startup-profile'/(original['metadata']['name']+'-status.json')
        record=json.loads(status.read_text()) if status.exists() else {'service':original['metadata']['name']}
        record.update(restored=True,recovery_restored=True)
        status.parent.mkdir(parents=True,exist_ok=True)
        status.write_text(json.dumps(record,indent=2))
        print(json.dumps({'service':original['metadata']['name'],'recovery_restored':True}),flush=True)

def profile(expected, output):
    suffix = os.environ['GITHUB_RUN_ID']+'-'+os.environ['GITHUB_RUN_ATTEMPT']
    if not re.fullmatch(r'\d+-\d+',suffix):
        raise ValueError('invalid Actions run identity')
    output.mkdir(parents=True,exist_ok=True)
    private=output.parent/'startup-profile-private'
    private.mkdir(mode=0o700,exist_ok=True)
    for name in TARGETS:
        original = describe(name)
        image, revision = image_and_revision(original)
        if expected[name] != {'image':image,'revision':revision,'spec_sha256':spec_hash(original)}:
            raise ValueError('current service differs from reviewed inventory')
        probe = probe_document(original,name,suffix)
        candidate = probe['spec']['template']['metadata']['name']
        record = {'service':name,'original_revision':revision,'profile_revision':candidate,'image':image,'restored':False}
        private_path=private/(name+'.json')
        private_path.write_text(json.dumps({'original':original,'probe':probe}))
        private_path.chmod(0o600)
        try:
            with tempfile.TemporaryDirectory(prefix='sawa-private-profile-') as directory:
                try:
                    replace(probe,directory)
                    query = ('resource.type="cloud_run_revision" AND resource.labels.revision_name="'+candidate+'" AND '
                             '(textPayload:"import time:" OR textPayload:"startup_container_entry" OR textPayload:"startup" '
                             'OR textPayload:"Started server" OR textPayload:"Starting new instance" OR textPayload:"Uvicorn running")')
                    for attempt in range(3):
                        logs = cli('logging','read',query,'--project',PROJECT,'--limit','10000','--order=asc',
                                   '--format=json(timestamp,textPayload,labels.instanceId)',data=True)
                        if (any('Application startup complete.' in r.get('textPayload','') for r in logs)
                                and any('startup_container_entry' in r.get('textPayload','') for r in logs)
                                and any(re.search(r'import time:\s+\d+\s*\|',r.get('textPayload','')) for r in logs)):
                            break
                        time.sleep(5)
                    else:
                        raise RuntimeError('complete startup logs did not arrive')
                    (output/(name+'-profile.json')).write_text(json.dumps(logs,indent=2))
                finally:
                    restore(original,probe,directory)
                    record['restored'] = True
                    private_path.unlink()
        finally:
            (output/(name+'-status.json')).write_text(json.dumps(record,indent=2))
        print(json.dumps(record),flush=True)

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--inventory',type=Path)
    parser.add_argument('--profile',action='store_true')
    parser.add_argument('--restore',action='store_true')
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    if not args.profile and not args.restore:
        if not args.inventory:
            parser.error('--inventory is required')
        args.inventory.write_text(json.dumps(inventory(),indent=2))
        return
    expected=json.loads(base64.b64decode(os.environ['STARTUP_PROFILE_INVENTORY'],validate=True))
    receipt=json.loads(os.environ['STARTUP_PROFILE_RECEIPT'])
    require_actions(receipt,expected)
    if not args.output:
        parser.error('--output is required')
    if args.restore:
        restore_saved(args.output.parent/'startup-profile-private')
    else:
        profile(expected,args.output)

if __name__=='__main__':
    main()
