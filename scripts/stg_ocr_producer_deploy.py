"""Replace only the staging producer image, retaining its exported specification."""

import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

import yaml

PROJECT = 'sawahospitalsystem'
REGION = 'asia-northeast2'
SERVICE = 'ocr-pipeline-stg'
RUNTIME = f'ocr-pipeline-exec-stg@{PROJECT}.iam.gserviceaccount.com'
DEPLOY = f'sawa-github-deploy-stg@{PROJECT}.iam.gserviceaccount.com'
IMAGE_PREFIX = f'{REGION}-docker.pkg.dev/{PROJECT}/backend/ocr-pipeline'
PHASE = 'arguments'


class Blocked(RuntimeError):
    def __init__(self, reason, *, phase=None, returncode=None):
        super().__init__(reason)
        self.phase = phase or PHASE
        self.returncode = returncode


def command(args):
    result = subprocess.run(args, capture_output=True, text=True, check=False)
    if result.returncode:
        phase = ' '.join(args[:4] if args[0] == 'gcloud' else args[:2])
        raise Blocked('command_failed_no_IAM_repair', phase=phase, returncode=result.returncode)
    return result.stdout


def cloud(*args, output='json'):
    raw = command(['gcloud', *args, f'--project={PROJECT}', f'--format={output}', '--quiet'])
    return yaml.safe_load(raw) if output == 'export' else json.loads(raw)


def context(image, source_sha):
    expected = {'GITHUB_ACTIONS': 'true', 'GITHUB_REF': 'refs/heads/develop',
                'GITHUB_REPOSITORY': 'mmorinag127/sawa-hospital-system',
                'PROJECT_ID': PROJECT, 'REGION': REGION}
    if any(os.environ.get(key) != value for key, value in expected.items()):
        raise Blocked('Exact staging Actions context required')
    if not re.fullmatch(re.escape(IMAGE_PREFIX) + r'@sha256:[0-9a-f]{64}', image):
        raise Blocked('Staging producer immutable image required')
    if not re.fullmatch(r'[0-9a-f]{40}', source_sha) or source_sha != os.environ.get('GITHUB_SHA'):
        raise Blocked('Producer source SHA mismatch')
    if command(['git', 'rev-parse', 'HEAD']).strip() != source_sha:
        raise Blocked('Checkout source SHA mismatch')
    if os.environ.get('CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT'):
        raise Blocked('Credential override forbidden')
    accounts = cloud('auth', 'list', '--filter=status:ACTIVE')
    if [entry['account'] for entry in accounts] != [DEPLOY]:
        raise Blocked('Existing staging WIF identity required')
    command(['docker', 'pull', image])
    inspected = json.loads(command(['docker', 'image', 'inspect', image]))
    if len(inspected) != 1 or image not in inspected[0].get('RepoDigests', []):
        raise Blocked('Producer image digest mismatch')
    labels = inspected[0].get('Config', {}).get('Labels', {})
    if labels.get('sawa.git_sha') != source_sha or labels.get('sawa.release_branch') != 'develop':
        raise Blocked('Producer image source mismatch')


def service():
    return cloud('run', 'services', 'describe', SERVICE, f'--region={REGION}')


def exported():
    return cloud('run', 'services', 'describe', SERVICE, f'--region={REGION}', output='export')


def ready_revision(value):
    status = value.get('status', {})
    revision = status.get('latestReadyRevisionName')
    traffic = status.get('traffic', [])
    if (not revision or status.get('latestCreatedRevisionName') != revision
            or not any(item.get('type') == 'Ready' and item.get('status') == 'True'
                       for item in status.get('conditions', []))
            or len(traffic) != 1 or traffic[0].get('revisionName') != revision
            or traffic[0].get('percent') != 100 or traffic[0].get('latestRevision') is not True):
        raise Blocked('Producer ready revision / 100 percent latest traffic required')
    return revision


def comparable(spec):
    result = deepcopy(spec)
    # Revision names are server identities, not runtime configuration.
    result['spec']['template'].get('metadata', {}).pop('name', None)
    return result


def replacement(before, image):
    if before.get('kind') != 'Service' or before.get('metadata', {}).get('name') != SERVICE:
        raise Blocked('Producer service target mismatch')
    result = comparable(before)
    runtime = result['spec']['template']['spec']
    if runtime.get('serviceAccountName') != RUNTIME or len(runtime.get('containers', [])) != 1:
        raise Blocked('Producer runtime/container mismatch')
    runtime['containers'][0]['image'] = image
    return result


def deploy(image, source_sha):
    global PHASE
    PHASE = 'source_identity_validation'
    context(image, source_sha)
    PHASE = 'capture_service_settings'
    initial = service()
    revision = ready_revision(initial)
    before = exported()
    project = cloud('projects', 'describe', PROJECT)
    if str(before.get('metadata', {}).get('namespace')) not in {PROJECT, str(project['projectNumber'])}:
        raise Blocked('Producer namespace mismatch')
    iam = cloud('run', 'services', 'get-iam-policy', SERVICE, f'--region={REGION}')
    desired = replacement(before, image)
    PHASE = 'pre_replace_revision_validation'
    # Detect revision or configuration changes after capture, before replacing.
    if ready_revision(service()) != revision or exported() != before:
        raise Blocked('Producer changed during capture')
    with tempfile.TemporaryDirectory(prefix='stg-ocr-spec-', dir=os.environ.get('RUNNER_TEMP')) as directory:
        path = Path(directory) / 'service.json'
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'w') as handle:
            json.dump(desired, handle)
        PHASE = 'replace_service'
        cloud('run', 'services', 'replace', str(path), f'--region={REGION}')
    PHASE = 'verify_ready_settings_iam_digest'
    current = service()
    ready = ready_revision(current)
    if comparable(exported()) != desired:
        raise Blocked('Producer settings changed during deployment')
    if cloud('run', 'services', 'get-iam-policy', SERVICE, f'--region={REGION}') != iam:
        raise Blocked('Producer IAM changed during deployment')
    observed = cloud('run', 'revisions', 'describe', ready, f'--region={REGION}')
    if observed.get('status', {}).get('imageDigest') != image:
        raise Blocked('Ready producer image digest mismatch')
    return {'source_sha': source_sha, 'image': image, 'previous_revision': revision, 'ready_revision': ready}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--image', required=True)
    parser.add_argument('--source-sha', required=True)
    args = parser.parse_args()
    try:
        receipt = deploy(args.image, args.source_sha)
    except Blocked as error:
        print(json.dumps({'status': 'blocked', 'phase': error.phase,
                          'reason': str(error), 'returncode': error.returncode}))
        return 1
    except Exception:
        # Exported settings may contain credentials: never log exceptions/descriptors.
        print(json.dumps({'status': 'blocked', 'phase': PHASE,
                          'reason': 'unexpected_failure_details_suppressed', 'returncode': None}))
        return 1
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
