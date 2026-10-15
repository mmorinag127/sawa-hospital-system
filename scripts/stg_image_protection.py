"""Actions-only additive cleanup protection; never deletes images or changes IAM."""
import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import re
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'backend')]

from stg_ocr_producer_deploy import Blocked, cloud, command, PROJECT, REGION, DEPLOY
from registry_image_digest import resolve_image_digest

PREFIX = f'{REGION}-docker.pkg.dev/{PROJECT}/backend/'
REPOSITORY = f'projects/{PROJECT}/locations/{REGION}/repositories/backend'
POLICY_ID = 'keep-explicit-stg-runtime'
POLICY = {'id': POLICY_ID, 'action': 'KEEP',
          'condition': {'tagState': 'TAGGED', 'tagPrefixes': ['stg-protected-']}}
SERVICES = {'web-stg': 'frontend', 'worker-stg': 'backend', 'ocr-pipeline-stg': 'ocr-pipeline'}


def require(ok, reason):
    if not ok:
        raise Blocked(reason)


def require_context():
    for key, value in {'GITHUB_ACTIONS': 'true', 'GITHUB_REF': 'refs/heads/develop',
                       'GITHUB_REPOSITORY': 'mmorinag127/sawa-hospital-system',
                       'PROJECT_ID': PROJECT, 'REGION': REGION}.items():
        require(os.environ.get(key) == value, 'staging_actions_context_required')
    sha = os.environ.get('GITHUB_SHA', '')
    require(bool(re.fullmatch('[0-9a-f]{40}', sha)), 'source_sha_required')
    require(command(['git', 'rev-parse', 'HEAD']).strip() == sha, 'checkout_source_mismatch')
    require(command(['git', 'rev-parse', 'origin/develop']).strip() == sha, 'develop_source_mismatch')
    require(not os.environ.get('CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT'), 'identity_override_rejected')
    require([x['account'] for x in cloud('auth', 'list', '--filter=status:ACTIVE')] == [DEPLOY],
            'existing_staging_wif_required')


def immutable_image(image, component):
    require(component in SERVICES.values(), 'image_component_rejected')
    require(bool(re.fullmatch(re.escape(PREFIX + component) + r'@sha256:[0-9a-f]{64}', image)),
            'staging_immutable_image_required')
    return image


def merge_policy(repository):
    require(repository.get('name') == REPOSITORY and repository.get('format') == 'DOCKER',
            'repository_target_mismatch')
    policies = deepcopy(repository.get('cleanupPolicies', {}))
    require(isinstance(policies, dict), 'cleanup_policy_shape_unknown')
    require(POLICY_ID not in policies or policies[POLICY_ID] == POLICY, 'keep_policy_conflict')
    policies[POLICY_ID] = deepcopy(POLICY)
    return policies


def repository_request(method='GET', policies=None):
    token = command(['gcloud', 'auth', 'print-access-token']).strip()
    require(bool(token), 'registry_identity_unavailable')
    url = 'https://artifactregistry.googleapis.com/v1/' + REPOSITORY
    body = None
    if method == 'PATCH':
        url += '?updateMask=cleanupPolicies'
        body = json.dumps({'cleanupPolicies': policies}).encode()
    request = urllib.request.Request(url, data=body, method=method,
        headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def ensure_policy():
    before = repository_request()
    desired = merge_policy(before)
    if before.get('cleanupPolicies', {}) != desired:
        current = repository_request()
        require(current.get('cleanupPolicies', {}) == before.get('cleanupPolicies', {})
                and current.get('cleanupPolicyDryRun', False) == before.get('cleanupPolicyDryRun', False),
                'cleanup_policy_changed_during_capture')
        updated = repository_request('PATCH', desired)
        require(updated.get('name') == REPOSITORY and updated.get('cleanupPolicies') == desired,
                'policy_update_not_completed')
    after = repository_request()
    require(after.get('cleanupPolicies') == desired, 'keep_policy_not_verified')
    require(after.get('cleanupPolicyDryRun', False) == before.get('cleanupPolicyDryRun', False),
            'cleanup_mode_changed')


def protect_image(image, component):
    immutable_image(image, component)
    tag = image.split('@')[0] + ':stg-protected-' + image.split(':')[-1]
    command(['gcloud', 'artifacts', 'docker', 'tags', 'add', image, tag,
             f'--project={PROJECT}', '--quiet'])
    require(resolve_image_digest(tag) == image, 'protected_tag_digest_mismatch')


def protect_current():
    for service, component in SERVICES.items():
        data = cloud('run', 'services', 'describe', service, f'--region={REGION}')
        status = data.get('status', {})
        revisions = {x.get('revisionName') for x in status.get('traffic', [])}
        revisions.add(status.get('latestReadyRevisionName'))
        revisions.add(status.get('latestCreatedRevisionName'))
        require(revisions and all(isinstance(x, str) and x.startswith(service + '-') for x in revisions),
                'staging_revision_source_unknown')
        for revision in sorted(revisions):
            descriptor = cloud('run', 'revisions', 'describe', revision, f'--region={REGION}')
            protect_image(descriptor.get('status', {}).get('imageDigest', ''), component)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--image')
    parser.add_argument('--component', choices=sorted(set(SERVICES.values())))
    parser.add_argument('--current', action='store_true')
    args = parser.parse_args()
    try:
        require_context()
        require(args.current != bool(args.image) and bool(args.image) == bool(args.component),
                'one_protection_mode_required')
        if args.image:
            immutable_image(args.image, args.component)
        ensure_policy()
        if args.current:
            protect_current()
        else:
            protect_image(args.image, args.component)
    except Exception as error:
        print(json.dumps({'status': 'blocked',
                          'reason': str(error) if isinstance(error, Blocked) else 'details_suppressed',
                          'returncode': error.returncode if isinstance(error, Blocked) else None}))
        return 1
    print('stg image cleanup protection verified (not a manual-deletion IAM restriction)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
